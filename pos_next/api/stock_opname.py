# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Outlet Stock Opname API (Task 3): outlets submit draft ERPNext
Stock Reconciliations from the POS app; HQ approves via the Frappe Workflow
"POS Next Stock Opname". Every endpoint takes `pos_profile`, gates on it with
`_assert_profile_access` first, then raises PermissionError when the document's
`set_warehouse` differs from the profile warehouse.

`data` arrives as a dict (frappe-ui `call`) or a JSON string; both accepted.
"""

import math
import os

import frappe
from frappe import ValidationError, _
from frappe.model.workflow import (
	apply_workflow,
	get_transitions,
	get_workflow,
	get_workflow_name,
	has_approval_access,
)
from frappe.utils import (
	add_days,
	cint,
	flt,
	formatdate,
	get_datetime,
	get_fullname,
	get_time,
	getdate,
	now_datetime,
	nowdate,
	nowtime,
	strip_html,
)

from pos_next.api.packages import _assert_profile_access
from pos_next.services.production import _resolve_profile

DOCTYPE = "Stock Reconciliation"
REJECT_PREFIX = "Rejected: "
TEMPLATE_HEADER = ["Item Code", "Item Name", "UOM", "Batch No", "Serial No", "Counted Qty"]
# Fallback backdate cap (days) when POS Next Global Settings has no
# `opname_max_backdate_days`.
MAX_BACKDATE_DAYS = 2


def _workflow():
	"""Active Stock Reconciliation workflow doc, or None when none is active."""
	if not get_workflow_name(DOCTYPE):
		return None
	return get_workflow(DOCTYPE)


def _initial_state(wf):
	"""First state of the workflow: where a new doc starts."""
	return wf.states[0].state


def _returned_states(wf):
	"""Draft states a transition can send back to the initial state."""
	initial = _initial_state(wf)
	back = {t.state for t in wf.transitions if t.next_state == initial}
	return {
		s.state for s in wf.states if s.state != initial and cint(s.doc_status) == 0 and s.state in back
	}


def _back_transition(wf, state, roles):
	"""Action of the first `state -> initial` transition allowed for one of `roles`."""
	initial = _initial_state(wf)
	for transition in wf.transitions:
		if transition.state == state and transition.next_state == initial and transition.allowed in roles:
			return transition.action
	return None


def _state_flags(doc_or_row, wf, roles=None):
	"""editable / returned / processing for a doc or list row, from the workflow.

	Without an active workflow a draft stays editable and nothing is returned.
	`editable` (spec N14) is the initial state, or a returned state this user may
	send back — never an approval stage, whatever its `allow_edit` role says.
	"""
	docstatus = cint(doc_or_row.get("docstatus"))
	if wf is None:
		return {"editable": docstatus == 0, "returned": False, "processing": False}
	state = doc_or_row.get("workflow_state")
	state_rows = [s for s in wf.states if s.state == state]
	returned = state in _returned_states(wf)
	if roles is None:
		roles = frappe.get_roles()
	revivable = returned and _back_transition(wf, state, roles) is not None
	return {
		"editable": docstatus == 0 and (state == _initial_state(wf) or revivable),
		"returned": returned,
		# approved but not yet submitted (the >100-row background submit window)
		"processing": docstatus == 0 and any(cint(s.doc_status) == 1 for s in state_rows),
	}


def get_editable_open_opname(warehouse, roles=None, include_returned=False, exclude=None):
	"""Name of the open editable Stock Reconciliation for warehouse, or None.

	Only docstatus-0 docs in the workflow's initial or revivable (returned) state
	count; approval stages never do. Without an active workflow, any docstatus-0
	doc counts. `include_returned` also counts returned docs this user may not
	revise, so a Rejected count blocks a new one whatever the user's roles.
	"""
	if not warehouse:
		return None
	filters = {"docstatus": 0, "set_warehouse": warehouse}
	if exclude:
		filters["name"] = ("!=", exclude)
	open_docs = frappe.get_all(
		DOCTYPE,
		filters=filters,
		fields=["name", "workflow_state", "docstatus"],
		order_by="creation desc",
	)
	if not open_docs:
		return None
	wf = _workflow()
	if roles is None:
		roles = frappe.get_roles()
	for doc in open_docs:
		flags = _state_flags(doc, wf, roles)
		if flags["editable"] or (include_returned and flags["returned"]):
			return doc.name
	return None


def _doc_actions(doc, wf):
	"""Workflow actions the outlet may apply: submit from the initial state, or
	send back (revise) from a returned state; approval stages stay in Desk.

	One entry per action name: the workflow has a transition row per allowed
	role, so a user holding several roles would otherwise get duplicates.
	Self-approval is checked here so no action is offered that would fail.
	"""
	if wf is None or doc.is_new():
		return []
	state = doc.get("workflow_state")
	if state != _initial_state(wf) and state not in _returned_states(wf):
		return []
	actions = []
	for transition in get_transitions(doc, wf):
		if transition["action"] not in actions and has_approval_access(frappe.session.user, doc, transition):
			actions.append(transition["action"])
	return actions


def _parse_data(data):
	if data is None or data == "":
		return {}
	if isinstance(data, str):
		try:
			return frappe.parse_json(data) or {}
		except (ValueError, TypeError):
			frappe.throw(_("Invalid opname data"), ValidationError)
	if isinstance(data, dict):
		return data
	frappe.throw(_("Invalid opname data"), ValidationError)


def _max_backdate_days():
	"""Backdate cap in days from POS Next Global Settings `opname_max_backdate_days`
	(global-only), else the constant when the setting is unset/blank."""
	cap = frappe.db.get_single_value("POS Next Global Settings", "opname_max_backdate_days")
	return cint(cap) if cap else MAX_BACKDATE_DAYS


def _guarded_posting(data):
	"""Parse and guard `posting_date`/`posting_time` from an outlet save.

	Returns (date, time) strings, or (None, None) when the outlet set no posting
	datetime (the doc keeps its own / ERPNext's default). Rejects malformed
	input, datetimes in the future and backdates older than the cap. The future
	check uses the site timezone's `now_datetime()`, never the browser clock.
	"""
	date_raw = data.get("posting_date")
	time_raw = data.get("posting_time")
	if not date_raw and not time_raw:
		return None, None
	try:
		posting_date = getdate(date_raw) if date_raw else getdate(nowdate())
		posting_time = get_time(time_raw) if time_raw else get_time(nowtime())
	except Exception:
		frappe.throw(_("Invalid posting date or time"), ValidationError)
	# Both sides are naive site-local datetimes: the stored posting value and
	# now_datetime() (site timezone), so the comparison is TZ-correct.
	if get_datetime(f"{posting_date} {posting_time}") > now_datetime():
		frappe.throw(_("Posting date/time cannot be in the future"), ValidationError)
	cap = _max_backdate_days()
	if getdate(posting_date) < add_days(getdate(nowdate()), -cap):
		frappe.throw(_("Posting date cannot be more than {0} day(s) in the past").format(cap), ValidationError)
	return posting_date, posting_time


def _assert_not_frozen(posting_date):
	"""Mirror ERPNext's stock-freeze check for the outlet user (amended D6).

	An outlet user without Stock Settings `stock_auth_role` cannot post a Stock
	Reconciliation dated on/before `stock_frozen_upto` or older than
	`stock_frozen_upto_days`; a user holding the role may.
	"""
	settings = frappe.get_cached_doc("Stock Settings")
	roles = frappe.get_roles()
	if settings.stock_frozen_upto and getdate(posting_date) <= getdate(settings.stock_frozen_upto):
		if settings.stock_auth_role not in roles:
			frappe.throw(
				_("Stock transactions before {0} are frozen").format(formatdate(settings.stock_frozen_upto)),
				ValidationError,
			)
	frozen_days = cint(settings.stock_frozen_upto_days)
	if frozen_days and add_days(getdate(posting_date), frozen_days) <= getdate(nowdate()):
		if settings.stock_auth_role not in roles:
			frappe.throw(
				_("Not allowed to update stock transactions older than {0}").format(frozen_days),
				ValidationError,
			)


def _profile_warehouse(pos_profile):
	_assert_profile_access(pos_profile)
	company, warehouse = _resolve_profile(pos_profile)
	if not frappe.db.exists("POS Profile User", {"parent": pos_profile, "user": frappe.session.user}):
		# a manager counting another outlet: only for a company they may act for
		from pos_next.api.purchase_orders import _assert_purchase_company

		_assert_purchase_company(company)
	return company, warehouse


def _get_doc(name):
	try:
		return frappe.get_doc(DOCTYPE, name)
	except frappe.DoesNotExistError:
		frappe.throw(_("Stock opname {0} not found").format(name), ValidationError)


def _assert_doc_warehouse(doc, warehouse):
	if doc.set_warehouse != warehouse:
		raise frappe.PermissionError(_("You don't have access to stock opname {0}").format(doc.name))


def _reject_reason(name):
	"""Latest `Rejected: ...` comment on the doc, prefix stripped (Rejected only)."""
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": DOCTYPE,
			"reference_name": name,
			"comment_type": "Comment",
			"content": ("like", f"{REJECT_PREFIX}%"),
		},
		fields=["content"],
		order_by="creation desc",
		limit=1,
	)
	if not comments:
		return None
	return (comments[0].content or "")[len(REJECT_PREFIX):].strip() or None


def _activity_time(value):
	return get_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def _activity(doc):
	"""Oldest-first timeline: a synthetic `created` entry plus the doc's Workflow
	(state name), Comment (reject reason, prefix stripped) and Attachment (plain
	text, never HTML) comment rows."""
	activity = [
		{
			"type": "created",
			"user": doc.owner,
			"user_fullname": get_fullname(doc.owner),
			"at": _activity_time(doc.creation),
		}
	]
	for row in frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": DOCTYPE,
			"reference_name": doc.name,
			"comment_type": ("in", ("Workflow", "Comment", "Attachment")),
			# A Stock Reconciliation can reuse a deleted doc's name, leaving its
			# comments behind; the timeline must not predate this doc's creation.
			"creation": (">=", doc.creation),
		},
		fields=["content", "comment_type", "owner", "creation"],
		order_by="creation asc",
	):
		content = row.content or ""
		entry = {
			"type": row.comment_type.lower(),
			"user": row.owner,
			"user_fullname": get_fullname(row.owner),
			"at": _activity_time(row.creation),
		}
		if row.comment_type == "Workflow":
			entry["state"] = content
		elif row.comment_type == "Attachment":
			entry["content"] = strip_html(content)
		else:
			entry["content"] = content[len(REJECT_PREFIX):].strip() if content.startswith(REJECT_PREFIX) else content
		activity.append(entry)
	return activity


def _item_flags(item_code):
	return frappe.db.get_value(
		"Item", item_code, ["item_name", "stock_uom", "is_stock_item", "disabled", "has_batch_no", "has_serial_no"], as_dict=True
	)


def _counted_qty(value, item_code):
	"""Parse a counted qty into a finite float >= 0, per-row error otherwise."""
	try:
		qty = float(value)
	except (TypeError, ValueError):
		frappe.throw(_("Counted qty for {0} must be a number").format(item_code), ValidationError)
	if not math.isfinite(qty) or qty < 0:
		frappe.throw(_("Counted qty for {0} must be zero or more").format(item_code), ValidationError)
	return qty


def _uom_factor(item_code, custom_uom, stock_uom, has_uom_field):
	"""Factor from `custom_uom` to the stock UOM (1 when equal), else reject the row.

	Mirrors warehouse_app `apply_sr_inventory_uom`: the stock UOM is factor 1,
	any other UOM must be on the item's UOM Conversion table. Without the
	warehouse_app custom fields a non-stock UOM cannot be recorded, so it is
	rejected per row rather than posted as a stock-UOM count.
	"""
	if custom_uom == stock_uom:
		return 1
	if not has_uom_field:
		frappe.throw(
			_("UOM {0} for {1} needs the warehouse_app UOM fields, which are missing on this site").format(
				custom_uom, item_code
			),
			ValidationError,
		)
	factor = frappe.db.get_value("UOM Conversion Detail", {"parent": item_code, "uom": custom_uom}, "conversion_factor")
	if not factor:
		frappe.throw(
			_("UOM {0} for {1} is invalid: use the stock UOM or add it to the item's UOM Conversion table").format(
				custom_uom, item_code
			),
			ValidationError,
		)
	return factor


def _validated_rows(items, warehouse, doc_name=None):
	"""Validate outlet count rows; return ERPNext row dicts ready to append.

	Raises ValidationError with a per-row message (item code named, unknown
	serials listed) so Task 4 (Excel import) can report them per row.

	A row counted in a non-stock UOM (`custom_uom` + `custom_qty_after`) is
	converted to native qty here with the same factor the warehouse_app hook
	`apply_sr_inventory_uom` recomputes on save; the `custom_*` fields are only
	written when the Stock Reconciliation Item meta has them (warehouse_app
	installed), otherwise a non-stock UOM is rejected per row.
	"""
	if not isinstance(items, list) or not items:
		frappe.throw(_("At least one counted item is required"), ValidationError)
	sr_meta = frappe.get_meta("Stock Reconciliation Item")
	has_uom_field = sr_meta.has_field("custom_uom")
	has_photo_field = sr_meta.has_field("pos_opname_photo")
	qty_precision = sr_meta.get_field("qty").precision
	seen = set()
	rows = []
	for entry in items:
		if not isinstance(entry, dict):
			frappe.throw(_("Each counted row must be an object with item_code"), ValidationError)
		item_code = entry.get("item_code")
		if not item_code:
			frappe.throw(_("Each counted row needs an item_code"), ValidationError)
		meta = _item_flags(item_code)
		if not meta or not meta.is_stock_item or meta.disabled:
			frappe.throw(_("{0} is not an enabled stock item").format(item_code), ValidationError)
		has_batch = bool(meta.has_batch_no)
		has_serial = bool(meta.has_serial_no)
		batch_no = entry.get("batch_no")
		serial_nos = entry.get("serial_nos")
		if serial_nos is None:
			serial_nos = []
		if isinstance(serial_nos, str):
			serial_nos = [s.strip() for s in serial_nos.replace(",", "\n").split("\n") if s.strip()]
		if not isinstance(serial_nos, list):
			frappe.throw(_("serial_nos for {0} must be a list").format(item_code), ValidationError)
		# Dedupe within the row: a scan box double-Enter (or crafted payload)
		# must not inflate qty or write the same serial twice.
		serial_nos = list(dict.fromkeys(serial_nos))
		# Count UOM self-conversion: native qty = custom_qty_after * factor.
		# Serial rows count serials, never a UOM.
		custom_uom = (entry.get("custom_uom") or "").strip()
		custom_after = None
		factor = 1
		if custom_uom:
			if has_serial:
				frappe.throw(
					_("UOM {0} cannot be used for serial item {1}; count serials instead").format(custom_uom, item_code),
					ValidationError,
				)
			custom_after = _counted_qty(entry.get("custom_qty_after", entry.get("qty")), item_code)
			factor = _uom_factor(item_code, custom_uom, meta.stock_uom, has_uom_field)
		photo = (entry.get("pos_opname_photo") or "").strip()
		if photo:
			if not doc_name:
				frappe.throw(_("Proof photos can only be attached to an existing stock opname"), ValidationError)
			if not frappe.db.exists(
				"File",
				{
					"attached_to_doctype": DOCTYPE,
					"attached_to_name": doc_name,
					"attached_to_field": "pos_opname_photo",
					"file_url": photo,
				},
			):
				frappe.throw(_("Proof photo {0} is not attached to this stock opname").format(photo), ValidationError)
		if has_batch:
			if not batch_no:
				frappe.throw(_("Batch is required for {0}").format(item_code), ValidationError)
			if frappe.db.get_value("Batch", batch_no, "item") != item_code:
				frappe.throw(_("Batch {0} does not belong to {1}").format(batch_no, item_code), ValidationError)
			if frappe.db.get_value("Batch", batch_no, "disabled"):
				frappe.throw(_("Batch {0} is disabled").format(batch_no), ValidationError)
			qty = flt(custom_after * factor, qty_precision) if custom_after is not None else _counted_qty(entry.get("qty"), item_code)
		elif has_serial:
			unknown = [
				s
				for s in serial_nos
				if frappe.db.get_value("Serial No", s, "item_code") != item_code
			]
			if unknown:
				frappe.throw(
					_("Unknown serials for {0}: {1}").format(item_code, ", ".join(unknown)), ValidationError
				)
			qty = len(serial_nos)
			batch_no = None
			if serial_nos:
				serial_no = "\n".join(serial_nos)
				use_serial_batch_fields = 1
				reconcile_all = 0
			else:
				# No serial present: ERPNext rejects use_serial_batch_fields
				# rows without serial_no, so reconcile against all current
				# serials instead (missing = everything in stock).
				serial_no = None
				use_serial_batch_fields = 0
				reconcile_all = 1
		else:
			if batch_no or serial_nos:
				frappe.throw(_("{0} has no batch or serial; send only qty").format(item_code), ValidationError)
			qty = flt(custom_after * factor, qty_precision) if custom_after is not None else _counted_qty(entry.get("qty"), item_code)
		key = (item_code, batch_no)
		if key in seen:
			frappe.throw(_("Duplicate row for {0}").format(item_code), ValidationError)
		seen.add(key)
		row = {
			"item_code": item_code,
			"warehouse": warehouse,
			"qty": qty,
			"batch_no": batch_no,
			"use_serial_batch_fields": 1,
		}
		if custom_uom and has_uom_field:
			row["custom_uom"] = custom_uom
			row["custom_qty_after"] = custom_after
			row["custom_conversion_factor"] = factor
		if has_photo_field:
			row["pos_opname_photo"] = photo or None
		if has_serial:
			# Empty count reconciles against all current serials (ERPNext
			# rejects use_serial_batch_fields rows without serial_no).
			row["reconcile_all_serial_batch"] = 0 if serial_nos else 1
			row["use_serial_batch_fields"] = 1 if serial_nos else 0
			row["serial_no"] = "\n".join(serial_nos) if serial_nos else None
		rows.append(row)
	return rows


def _payload(doc):
	wf = _workflow()
	flags = _state_flags(doc, wf, frappe.get_roles())
	meta_cache = {}
	# alt UOMs per item, same shape as search_opname_items, so a reloaded row can still switch UOM
	uom_map = {}
	for u in frappe.get_all(
		"UOM Conversion Detail",
		filters={"parent": ["in", list({r.item_code for r in doc.items}) or [""]]},
		fields=["parent", "uom", "conversion_factor"],
	):
		uom_map.setdefault(u.parent, []).append({"uom": u.uom, "conversion_factor": u.conversion_factor})
	sr_meta = frappe.get_meta("Stock Reconciliation Item")
	has_photo_field = sr_meta.has_field("pos_opname_photo")
	items = []
	for row in doc.items:
		if row.item_code not in meta_cache:
			meta_cache[row.item_code] = _item_flags(row.item_code)
		meta = meta_cache[row.item_code] or {}
		serials = [s.strip() for s in (row.serial_no or "").split("\n") if s.strip()]
		stock_uom = row.stock_uom or meta.get("stock_uom")
		custom_uom = row.get("custom_uom")
		items.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name or meta.get("item_name"),
				"stock_uom": stock_uom,
				"qty": row.qty,
				"batch_no": row.batch_no,
				"serial_nos": serials,
				"has_batch_no": int(bool(meta.get("has_batch_no"))),
				"has_serial_no": int(bool(meta.get("has_serial_no"))),
				# Count UOM roundtrip: native rows fall back to stock UOM / qty.
				"custom_uom": custom_uom or stock_uom,
				"custom_qty_after": row.get("custom_qty_after") if custom_uom else row.qty,
				"uoms": [u for u in uom_map.get(row.item_code, []) if u["uom"] != stock_uom],
				**({"pos_opname_photo": row.get("pos_opname_photo")} if has_photo_field else {}),
			}
		)
	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state,
		"docstatus": doc.docstatus,
		"modified": doc.modified,
		# Read-only detail view header (Task 7): posting datetime + warehouse.
		"posting_date": doc.posting_date,
		"posting_time": doc.posting_time,
		"set_posting_time": doc.set_posting_time,
		"set_warehouse": doc.set_warehouse,
		# Metadata for the Desk-style detail view (N10): who created/last changed.
		"owner": doc.owner,
		"owner_fullname": get_fullname(doc.owner),
		"creation": doc.creation,
		"modified_by_fullname": get_fullname(doc.modified_by),
		"reject_reason": _reject_reason(doc.name) if flags["returned"] else None,
		"editable": flags["editable"],
		"returned": flags["returned"],
		"processing": flags["processing"],
		"actions": _doc_actions(doc, wf),
		"items": items,
		"attachments": _attachments(doc.name),
		"activity": _activity(doc),
	}


def _attachments(name):
	"""File/link rows attached to the opname, same shape as purchase_orders._attachments_summary."""
	if not name:
		return []
	rows = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": name,
			"attached_to_field": ["!=", "pos_opname_photo"],
		},
		fields=["name", "file_name", "file_url", "file_size"],
		order_by="creation asc",
	)
	for row in rows:
		# a link's derived file_name is only its last path segment; show the URL
		if (row.file_url or "").startswith(("http://", "https://")):
			row.file_name = row.file_url
	return rows


@frappe.whitelist()
def list_opnames(pos_profile, start=0, page_length=50, status=None, search=None):
	"""Newest-first opnames of the profile warehouse, one page at a time.

	`status` filters on the workflow state; `search` matches the opname ID or
	a counted item's code/name.
	"""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		filters = {"set_warehouse": warehouse}
		if status:
			filters["workflow_state"] = status
		or_filters = None
		search = (search or "").strip()
		if search:
			like = f"%{search}%"
			or_filters = [
				["name", "like", like],
				["Stock Reconciliation Item", "item_code", "like", like],
				["Stock Reconciliation Item", "item_name", "like", like],
			]
		rows = frappe.get_all(
			DOCTYPE,
			filters=filters,
			or_filters=or_filters,
			fields=["name", "posting_date", "posting_time", "workflow_state", "docstatus", "modified", "creation"],
			distinct=True,
			order_by="creation desc",
			limit_start=max(cint(start), 0),
			limit_page_length=min(max(cint(page_length), 1), 100),
		)
		wf = _workflow()
		roles = frappe.get_roles()
		out = []
		for row in rows:
			item_count = frappe.db.count("Stock Reconciliation Item", {"parent": row.name})
			flags = _state_flags(row, wf, roles)
			out.append(
				{
					"name": row.name,
					"posting_date": row.posting_date,
					"posting_time": row.posting_time,
					"workflow_state": row.workflow_state,
					"docstatus": row.docstatus,
					"item_count": item_count,
					"modified": row.modified,
					"reject_reason": _reject_reason(row.name) if flags["returned"] else None,
					"editable": flags["editable"],
					"returned": flags["returned"],
					"processing": flags["processing"],
				}
			)
		open_count = get_editable_open_opname(warehouse, include_returned=True)
		return {"opnames": out, "open_count": open_count}
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "List Stock Opnames Error")
		frappe.throw(_("Error fetching stock opnames: {0}").format(str(e)))


@frappe.whitelist()
def get_opname_states(pos_profile):
	"""Workflow state names in workflow order, for the list status filter."""
	_profile_warehouse(pos_profile)
	wf = _workflow()
	return [s.state for s in wf.states] if wf else []


@frappe.whitelist()
def get_opname(pos_profile, name):
	"""Blind-count payload: item name/UOM/counted qty only, never system qty."""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
		return _payload(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Stock Opname Error")
		frappe.throw(_("Error fetching stock opname: {0}").format(str(e)))


@frappe.whitelist()
def get_system_qty(name):
	"""HQ-only live system qty/difference per row of a draft opname (display only).

	Mirrors the helper ERPNext runs on submit (`get_stock_balance_for`), so the
	numbers match what approval posts. Read-only: nothing is persisted, so an
	outlet user can never reach the system quantities (spec U4, P3).
	"""
	from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_stock_balance_for

	from pos_next.overrides.stock_reconciliation import is_hq_user

	if not is_hq_user(frappe.session.user):
		raise frappe.PermissionError(_("Only HQ users can view system quantities"))
	doc = _get_doc(name)
	if not frappe.has_permission(DOCTYPE, "read", doc):
		raise frappe.PermissionError(_("You don't have access to stock opname {0}").format(name))
	if doc.docstatus != 0:
		frappe.throw(
			_("This stock opname is submitted; it already shows the system quantities."), ValidationError
		)
	posting_date = doc.posting_date or nowdate()
	posting_time = doc.posting_time or nowtime()
	items = []
	for row in doc.items:
		item_dict = get_stock_balance_for(
			row.item_code,
			row.warehouse,
			posting_date,
			posting_time,
			batch_no=row.batch_no,
			row=row,
			company=doc.company,
		)
		counted_qty = flt(row.qty)
		system_qty = flt(item_dict.get("qty"))
		valuation_rate = flt(item_dict.get("rate"))
		difference_qty = counted_qty - system_qty
		items.append(
			{
				"idx": row.idx,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"batch_no": row.batch_no,
				"counted_qty": counted_qty,
				"system_qty": system_qty,
				"difference_qty": difference_qty,
				"valuation_rate": valuation_rate,
				"amount_difference": flt(difference_qty * valuation_rate),
			}
		)
	return {
		"name": doc.name,
		"currency": frappe.get_cached_value("Company", doc.company, "default_currency"),
		"items": items,
	}


@frappe.whitelist()
def save_opname(pos_profile, data):
	"""Create or update an editable opname; a returned one revises to initial first."""
	company, warehouse = _profile_warehouse(pos_profile)
	data = _parse_data(data)
	posting_date, posting_time = _guarded_posting(data)
	try:
		name = data.get("name")
		if name:
			doc = _get_doc(name)
			_assert_doc_warehouse(doc, warehouse)
			wf = _workflow()
			roles = frappe.get_roles()
			flags = _state_flags(doc, wf, roles)
			if not flags["editable"]:
				frappe.throw(_("This stock opname can no longer be edited"), ValidationError)
			if "modified" not in data or not data.get("modified"):
				frappe.throw(_("modified is required to save an existing opname"), ValidationError)
			if str(doc.modified) != str(data["modified"]):
				raise frappe.TimestampMismatchError(
					_("This count changed on another device (modified, reload). Reload before saving.")
				)
			# Validate before any back transition, so invalid rows (or a frozen
			# posting date) cannot leave a returned doc flipped to initial with
			# nothing applied.
			rows = _validated_rows(data.get("items"), warehouse, doc_name=doc.name)
			_assert_not_frozen(posting_date or doc.posting_date or nowdate())
			if flags["returned"]:
				back = _back_transition(wf, doc.workflow_state, roles)
				if not back:
					frappe.throw(_("You cannot revise this count"), ValidationError)
				apply_workflow(doc, back)
				doc.reload()
				_assert_doc_warehouse(doc, warehouse)
			doc.set("items", [])
			for row in rows:
				doc.append("items", row)
			if posting_date:
				doc.posting_date = posting_date
				doc.posting_time = posting_time
				doc.set_posting_time = 1
			doc.save()
			_prune_row_photos(doc)
		else:
			rows = _validated_rows(data.get("items"), warehouse, doc_name=None)
			_assert_not_frozen(posting_date or nowdate())
			doc = frappe.new_doc(DOCTYPE)
			doc.company = company
			doc.purpose = "Stock Reconciliation"
			doc.set_warehouse = warehouse
			for row in rows:
				doc.append("items", row)
			if posting_date:
				doc.posting_date = posting_date
				doc.posting_time = posting_time
				doc.set_posting_time = 1
			doc.insert()
		return _payload(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save Stock Opname Error")
		frappe.throw(_("Error saving stock opname: {0}").format(str(e)))


def _prune_row_photos(doc):
	"""Delete proof photos no row references any more (removed or replaced)."""
	used = {row.get("pos_opname_photo") for row in doc.items}
	for f in frappe.get_all(
		"File",
		filters={"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name, "attached_to_field": "pos_opname_photo"},
		fields=["name", "file_url"],
	):
		if f.file_url not in used:
			frappe.delete_doc("File", f.name, ignore_permissions=True)


@frappe.whitelist()
def submit_opname(pos_profile, name, action=None):
	"""Apply an outlet workflow action and return the fresh payload.

	From the initial state (submit) an outlet-set posting date is preserved,
	else stamped now. From a returned state the action sends the count back
	to the initial state (revise) so the outlet can edit it again.
	"""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
		wf = _workflow()
		if wf is None:
			frappe.throw(_("No active workflow for Stock Reconciliation; ask HQ to activate one"), ValidationError)
		actions = _doc_actions(doc, wf)
		if not actions:
			frappe.throw(_("You are not allowed to submit this count, ask HQ"), ValidationError)
		if action:
			if action not in actions:
				frappe.throw(_("{0} is not a valid action for this count").format(action), ValidationError)
		elif len(actions) == 1:
			action = actions[0]
		else:
			frappe.throw(_("Choose an action: {0}").format(", ".join(actions)), ValidationError)
		if not wf or doc.workflow_state == _initial_state(wf):
			missing = [r for r in doc.items if not (r.get("pos_opname_photo") or "").strip()]
			if missing:
				names = ", ".join(f"row {r.idx} ({r.item_code})" for r in missing)
				frappe.throw(_("Proof photo is required for counted rows: {0}").format(names), ValidationError)
			_assert_not_frozen(doc.posting_date or nowdate())
			# Preserve an outlet backdate (save already capped and guarded it); only
			# stamp now when the posting date is still the save-moment default (today).
			if getdate(doc.posting_date or nowdate()) >= getdate(nowdate()):
				doc.posting_date = nowdate()
				doc.posting_time = nowtime()
			doc.set_posting_time = 1
			doc.save()
		apply_workflow(doc, action)
		doc.reload()
		return _payload(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Submit Stock Opname Error")
		frappe.throw(_("Error submitting stock opname: {0}").format(str(e)))


@frappe.whitelist()
def delete_opname(pos_profile, name):
	"""Delete an editable opname."""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
		if not _state_flags(doc, _workflow(), frappe.get_roles())["editable"]:
			frappe.throw(_("This stock opname can no longer be deleted"), ValidationError)
		frappe.delete_doc(DOCTYPE, name)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Delete Stock Opname Error")
		frappe.throw(_("Error deleting stock opname: {0}").format(str(e)))


def _editable_doc(pos_profile, name):
	_company, warehouse = _profile_warehouse(pos_profile)
	doc = _get_doc(name)
	_assert_doc_warehouse(doc, warehouse)
	if not _state_flags(doc, _workflow(), frappe.get_roles())["editable"]:
		frappe.throw(_("Attachments can only change on an editable opname"), ValidationError)
	return doc


@frappe.whitelist()
def attach_opname_files(pos_profile, name, files=None, link=None):
	"""Attach private files (`files`: JSON list of {file_name, filedata} base64)
	and/or one http(s) `link` to a Draft/Rejected opname; returns the attachments.

	Outlet roles get no File write via the doc's controller perms, so inserts
	run with ignore_permissions — the profile + warehouse gate is the decision.
	"""
	doc = _editable_doc(pos_profile, name)
	parsed = _parse_data(files) if files else []
	if not isinstance(parsed, list):
		frappe.throw(_("Invalid attachment data"), ValidationError)
	link = (link or "").strip()
	if not parsed and not link:
		frappe.throw(_("Add a file or a link"), ValidationError)
	if link and not link.startswith(("http://", "https://")):
		frappe.throw(_("Link must start with http:// or https://"), ValidationError)
	for f in parsed:
		if not isinstance(f, dict) or not (f.get("file_name") or "").strip() or not (f.get("filedata") or "").strip():
			frappe.throw(_("File name and data are required"), ValidationError)
		# base64 inflates by ~4/3; File.validate still enforces the site cap
		if (len(f["filedata"]) * 3) // 4 > 10 * 1024 * 1024:
			frappe.throw(_("File {0} exceeds the {1} MB limit").format(f["file_name"], 10), ValidationError)
	for f in parsed:
		frappe.get_doc(
			{
				"doctype": "File",
				"attached_to_doctype": DOCTYPE,
				"attached_to_name": doc.name,
				"file_name": f["file_name"],
				"is_private": 1,
				"content": f["filedata"],
				"decode": 1,
			}
		).insert(ignore_permissions=True)
	if link:
		frappe.get_doc(
			{"doctype": "File", "attached_to_doctype": DOCTYPE, "attached_to_name": doc.name, "file_url": link}
		).insert(ignore_permissions=True)
	return _attachments(doc.name)


@frappe.whitelist()
def remove_opname_attachment(pos_profile, name, file):
	"""Delete one attachment of a Draft/Rejected opname; returns the attachments."""
	doc = _editable_doc(pos_profile, name)
	owner = frappe.db.get_value("File", file, ["attached_to_doctype", "attached_to_name"], as_dict=True)
	if not owner or owner.attached_to_doctype != DOCTYPE or owner.attached_to_name != doc.name:
		raise frappe.PermissionError(_("Attachment {0} is not on stock opname {1}").format(file, doc.name))
	frappe.delete_doc("File", file, ignore_permissions=True)
	return _attachments(doc.name)


ALLOWED_PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


@frappe.whitelist()
def attach_opname_row_photo(pos_profile, name, file_name, filedata):
	"""Attach one proof photo to a Stock Reconciliation row."""
	doc = _editable_doc(pos_profile, name)
	file_name = (file_name or "").strip()
	filedata = (filedata or "").strip()
	if not file_name or not filedata:
		frappe.throw(_("File name and data are required"), ValidationError)
	ext = os.path.splitext(file_name)[1].lower()
	if ext not in ALLOWED_PHOTO_EXTENSIONS:
		frappe.throw(
			_("Only images ({0}) are allowed").format(", ".join(sorted(ALLOWED_PHOTO_EXTENSIONS))),
			ValidationError,
		)
	if (len(filedata) * 3) // 4 > 10 * 1024 * 1024:
		frappe.throw(_("File {0} exceeds the {1} MB limit").format(file_name, 10), ValidationError)
	f = frappe.get_doc(
		{
			"doctype": "File",
			"attached_to_doctype": DOCTYPE,
			"attached_to_name": doc.name,
			"attached_to_field": "pos_opname_photo",
			"file_name": file_name,
			"is_private": 1,
			"content": filedata,
			"decode": 1,
		}
	).insert(ignore_permissions=True)
	return {"file_url": f.file_url}


@frappe.whitelist()
def get_item_batches(pos_profile, item_code):
	"""Enabled batches of the item (batch no + expiry, no qty), oldest expiry first."""
	_company, _warehouse = _profile_warehouse(pos_profile)
	try:
		return frappe.get_all(
			"Batch",
			filters={"item": item_code, "disabled": 0},
			fields=["name as batch_no", "expiry_date"],
			order_by="expiry_date asc",
		)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Opname Batches Error")
		frappe.throw(_("Error fetching batches: {0}").format(str(e)))


@frappe.whitelist()
def get_item_serials(pos_profile, item_code):
	"""Serials of the item currently in the profile warehouse (tick list)."""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		return frappe.get_all(
			"Serial No", filters={"item_code": item_code, "warehouse": warehouse}, pluck="name", order_by="name asc"
		)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Opname Serials Error")
		frappe.throw(_("Error fetching serials: {0}").format(str(e)))


@frappe.whitelist()
def search_opname_items(pos_profile, search_term=None, start=0, limit=10):
	"""Blind item search for the count sheet: enabled stock items only.

	Word-AND match over name/item_name/item_group/description (FULLTEXT when the
	index exists, substring LIKE otherwise) plus an exact barcode match. Joins
	no stock table on purpose: an item with zero stock is still returned and no
	system quantity ever leaves the server (blind count, spec U4).
	"""
	_profile_warehouse(pos_profile)
	from pos_next.api.items import _item_fulltext_index_exists, build_item_search_condition

	try:
		conditions = ["i.disabled = 0", "i.is_stock_item = 1"]
		params = []
		term = (search_term or "").strip()
		if term:
			words = [w for w in term.split() if w]
			word_condition, word_params = build_item_search_condition(
				words, fulltext_available=_item_fulltext_index_exists()
			)
			# Exact barcode OR, added only when a barcode equals the term: the OR
			# otherwise stops MariaDB using the FULLTEXT index (probe as get_items).
			if frappe.db.sql("SELECT 1 FROM `tabItem Barcode` WHERE barcode = %s LIMIT 1", (term,)):
				word_condition = (
					f"(({word_condition}) OR i.name IN "
					"(SELECT parent FROM `tabItem Barcode` WHERE barcode = %s))"
				)
				word_params.append(term)
			conditions.append(word_condition)
			params.extend(word_params)

		rows = frappe.db.sql(
			f"""
			SELECT i.name AS item_code, i.item_name, i.stock_uom, i.has_batch_no, i.has_serial_no
			FROM `tabItem` i
			WHERE {" AND ".join(conditions)}
			ORDER BY i.item_name ASC
			LIMIT %s OFFSET %s
			""",
			tuple([*params, int(limit), int(start)]),
			as_dict=True,
		)

		uom_map = {}
		codes = [r.item_code for r in rows]
		if codes:
			for row in frappe.get_all(
				"UOM Conversion Detail",
				filters={"parent": ["in", codes]},
				fields=["parent", "uom", "conversion_factor"],
			):
				uom_map.setdefault(row.parent, []).append(
					{"uom": row.uom, "conversion_factor": row.conversion_factor}
				)
		for row in rows:
			row["has_batch_no"] = int(bool(row["has_batch_no"]))
			row["has_serial_no"] = int(bool(row["has_serial_no"]))
			# the stock UOM is offered separately by the UI; keep alternatives only
			row["uoms"] = [u for u in uom_map.get(row["item_code"], []) if u["uom"] != row["stock_uom"]]
		return rows
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Search Opname Items Error")
		frappe.throw(_("Error searching items: {0}").format(str(e)))


def _warehouse_items(warehouse, item_group=None):
	"""Count rows for every stock item of `item_group` (incl. sub-groups), or, with
	no group, every item with stock ledger history in the warehouse:
	one per stocked batch, one per serial item (its warehouse serials as options),
	one per plain item. Never carries a quantity (blind count)."""
	filters = {"is_stock_item": 1, "disabled": 0}
	if item_group:
		groups = [item_group, *(frappe.db.get_descendants("Item Group", item_group) or [])]
		filters["item_group"] = ("in", groups)
	else:
		item_codes = frappe.get_all(
			"Stock Ledger Entry",
			filters={"warehouse": warehouse, "is_cancelled": 0},
			pluck="item_code",
			distinct=True,
		)
		if not item_codes:
			return []
		filters["name"] = ("in", item_codes)
	items = frappe.get_all(
		"Item",
		filters=filters,
		fields=["item_code", "item_name", "stock_uom", "item_group", "has_batch_no", "has_serial_no"],
		order_by="item_code asc",
	)
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	rows = []
	for item in items:
		base = {
			"item_code": item.item_code,
			"item_name": item.item_name,
			"stock_uom": item.stock_uom,
			"has_batch_no": int(bool(item.has_batch_no)),
			"has_serial_no": int(bool(item.has_serial_no)),
		}
		if item.has_batch_no:
			for batch in get_batch_qty(item_code=item.item_code, warehouse=warehouse) or []:
				if (batch.get("qty") or 0) > 0:
					rows.append({**base, "batch_no": batch.get("batch_no")})
		elif item.has_serial_no:
			serials = frappe.get_all(
				"Serial No",
				filters={"item_code": item.item_code, "warehouse": warehouse},
				pluck="name",
				order_by="name asc",
			)
			rows.append({**base, "serial_nos": serials})
		else:
			rows.append(base)
	return rows


def _template_rows(warehouse, item_group=None):
	"""Blank count rows from `_warehouse_items`, one per stocked batch / warehouse serial."""
	rows = []
	for row in _warehouse_items(warehouse, item_group):
		head = [row["item_code"], row["item_name"], row["stock_uom"]]
		if "serial_nos" in row:
			rows.extend([*head, "", serial, ""] for serial in row["serial_nos"])
		else:
			rows.append([*head, row.get("batch_no") or "", "", ""])
	return rows


@frappe.whitelist()
def get_opname_item_groups(pos_profile):
	"""Every Item Group, tree order, for the fetch-by-group filter. An outlet
	counts all the stock it holds, not just what the POS Profile sells, so this
	is deliberately NOT the profile's item groups (items.get_item_groups)."""
	_profile_warehouse(pos_profile)
	return frappe.get_all("Item Group", fields=["name as item_group"], order_by="lft asc")


@frappe.whitelist()
def fetch_opname_items(pos_profile, item_group=None):
	"""Item list of the profile warehouse for the count sheet, like ERPNext's
	"Fetch Items from Warehouse" but without any quantity (blind count)."""
	_company, warehouse = _profile_warehouse(pos_profile)
	rows = _warehouse_items(warehouse, item_group)
	uom_map = {}
	for u in frappe.get_all(
		"UOM Conversion Detail",
		filters={"parent": ["in", list({r["item_code"] for r in rows}) or [""]]},
		fields=["parent", "uom", "conversion_factor"],
	):
		uom_map.setdefault(u.parent, []).append({"uom": u.uom, "conversion_factor": u.conversion_factor})
	for row in rows:
		row["uoms"] = [u for u in uom_map.get(row["item_code"], []) if u["uom"] != row["stock_uom"]]
	return rows


@frappe.whitelist()
def download_opname_template(pos_profile, item_group=None):
	"""Download the blank count sheet (.xlsx) for the profile warehouse (U7)."""
	_company, warehouse = _profile_warehouse(pos_profile)
	from frappe.utils.xlsxutils import build_xlsx_response

	return build_xlsx_response(
		[TEMPLATE_HEADER, *_template_rows(warehouse, item_group)], f"stock-opname-{warehouse}"
	)


def _import_target(name, warehouse):
	"""Resolve the doc to merge into: named, the open opname, or None (new).

	Non-editable targets raise; returned targets are handed back as-is and
	revised by the caller so U3 holds for uploads.
	"""
	if name:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
		if not _state_flags(doc, _workflow(), frappe.get_roles())["editable"]:
			frappe.throw(_("This stock opname can no longer be edited"), ValidationError)
		return doc
	open_name = get_editable_open_opname(warehouse)
	if not open_name:
		return None
	return _get_doc(open_name)


def _parse_import(rows, warehouse):
	"""Validate file rows into merge ops; collect per-row errors (never abort).

	Reuses `_validated_rows` per row and adds only the checks it lacks (blank
	qty skip, serial qty 0/1, serial in this warehouse, duplicate key).
	Returns (ops, skipped, errors).
	"""
	if not rows:
		frappe.throw(_("The file is empty"), ValidationError)
	header = [str(c).strip() if c is not None else "" for c in rows[0]]
	missing = [h for h in TEMPLATE_HEADER if h not in header]
	if missing:
		frappe.throw(_("Invalid template: missing column(s) {0}").format(", ".join(missing)), ValidationError)
	col = {h: header.index(h) for h in TEMPLATE_HEADER}

	def cell(row, key):
		i = col[key]
		return row[i] if i < len(row) else None

	ops, errors, skipped, seen = [], [], 0, set()
	for i, row in enumerate(rows[1:], start=2):  # header is row 1
		item_code = str(cell(row, "Item Code") or "").strip()
		qty_cell = cell(row, "Counted Qty")
		if not item_code or qty_cell is None or (isinstance(qty_cell, str) and not qty_cell.strip()):
			skipped += 1
			continue
		try:
			qty = float(qty_cell)
		except (TypeError, ValueError):
			errors.append(
				{"row": i, "item_code": item_code, "message": _("Counted qty for {0} must be a number").format(item_code)}
			)
			continue
		batch_no = str(cell(row, "Batch No") or "").strip() or None
		serial_no = str(cell(row, "Serial No") or "").strip() or None
		# Count UOM column: stock UOM or blank -> native qty as before; any other
		# UOM is validated (and converted) per row by `_validated_rows`.
		uom_cell = str(cell(row, "UOM") or "").strip() or None
		meta = _item_flags(item_code)
		is_serial = bool(meta and meta.has_serial_no)
		key = (item_code, serial_no if is_serial else batch_no)
		if key in seen:
			errors.append({"row": i, "item_code": item_code, "message": _("Duplicate row for {0}").format(item_code)})
			continue
		seen.add(key)
		if is_serial:
			if qty not in (0, 1):
				errors.append(
					{
						"row": i,
						"item_code": item_code,
						"message": _("Serial qty for {0} must be 0 or 1").format(item_code),
					}
				)
				continue
			if not serial_no:
				errors.append({"row": i, "item_code": item_code, "message": _("Serial no is required for {0}").format(item_code)})
				continue
			entry = {"item_code": item_code, "serial_nos": [serial_no]}
			if uom_cell:
				# Serial rows count serials, never a UOM: `_validated_rows` rejects
				# this per row rather than silently importing it as native qty.
				entry["custom_uom"] = uom_cell
			try:
				_validated_rows([entry], warehouse)
			except ValidationError as e:
				errors.append({"row": i, "item_code": item_code, "message": str(e)})
				continue
			if frappe.db.get_value("Serial No", serial_no, "warehouse") != warehouse:
				errors.append(
					{"row": i, "item_code": item_code, "message": _("Serial {0} is not in this warehouse").format(serial_no)}
				)
				continue
			ops.append({"item_code": item_code, "serial": serial_no, "present": bool(qty)})
		else:
			entry = {"item_code": item_code, "qty": qty}
			if batch_no:
				entry["batch_no"] = batch_no
			if uom_cell:
				entry["custom_uom"] = uom_cell
				entry["custom_qty_after"] = qty
			try:
				_validated_rows([entry], warehouse)
			except ValidationError as e:
				errors.append({"row": i, "item_code": item_code, "message": str(e)})
				continue
			op = {"item_code": item_code, "batch_no": batch_no, "qty": qty}
			if uom_cell:
				op["custom_uom"] = uom_cell
				op["custom_qty_after"] = qty
			ops.append(op)
	return ops, skipped, errors


def _merge_rows(doc, ops, warehouse):
	"""Merge file ops onto the doc's current rows (U8), keeping rows not in the file.

	Key (item_code, batch_no); serial items aggregate per item: qty 1 adds the
	serial, 0 removes it. A serial item touched by the file whose merged serial
	set is empty is written as a qty-0 serial row (counted, none present) — the
	same representation Task 3 uses. Returns (rows, applied) where applied counts
	file ops whose target survived into `rows`.
	"""
	current = {}
	for row in doc.items if doc else []:
		meta = _item_flags(row.item_code)
		photo = row.get("pos_opname_photo")
		if meta and meta.has_serial_no:
			current[(row.item_code, None)] = {
				"item_code": row.item_code,
				"serial_nos": {s.strip() for s in (row.serial_no or "").split("\n") if s.strip()},
				"pos_opname_photo": photo,
			}
		else:
			current[(row.item_code, row.batch_no)] = {
				"item_code": row.item_code,
				"batch_no": row.batch_no,
				"qty": row.qty,
				"custom_uom": row.get("custom_uom"),
				"custom_qty_after": row.get("custom_qty_after"),
				"pos_opname_photo": photo,
			}
	for op in ops:
		if "serial" in op:
			entry = current.setdefault((op["item_code"], None), {"item_code": op["item_code"], "serial_nos": set()})
			if op["present"]:
				entry["serial_nos"].add(op["serial"])
			else:
				entry["serial_nos"].discard(op["serial"])
		else:
			existing = current.get((op["item_code"], op["batch_no"]))
			existing_photo = existing.get("pos_opname_photo") if existing else None
			current[(op["item_code"], op["batch_no"])] = {
				"item_code": op["item_code"],
				"batch_no": op["batch_no"],
				"qty": op["qty"],
				"custom_uom": op.get("custom_uom"),
				"custom_qty_after": op.get("custom_qty_after"),
				"pos_opname_photo": existing_photo,
			}
	entries = []
	for val in current.values():
		if "serial_nos" in val:
			entries.append({
				"item_code": val["item_code"],
				"serial_nos": sorted(val["serial_nos"]),
				"pos_opname_photo": val.get("pos_opname_photo"),
			})
		else:
			entry = {
				"item_code": val["item_code"],
				"qty": val["qty"],
				"batch_no": val["batch_no"],
				"pos_opname_photo": val.get("pos_opname_photo"),
			}
			# Carry a count UOM through the merge so a UOM row is not silently
			# re-imported as a stock-UOM count.
			if val.get("custom_uom"):
				entry["custom_uom"] = val["custom_uom"]
				entry["custom_qty_after"] = val["custom_qty_after"]
			entries.append(entry)
	if not entries:
		return [], 0
	applied = sum(1 for op in ops if (op["item_code"], None if "serial" in op else op["batch_no"]) in current)
	return _validated_rows(entries, warehouse, doc_name=doc.name if doc else None), applied


@frappe.whitelist()
def import_opname_template(pos_profile, file_url, name=None):
	"""Merge an uploaded count sheet into the open draft (U8); delete the file after."""
	company, warehouse = _profile_warehouse(pos_profile)
	if not file_url:
		frappe.throw(_("Please attach a file"), ValidationError)
	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		frappe.throw(_("File not found"), ValidationError)
	if frappe.db.get_value("File", file_name, "owner") != frappe.session.user:
		raise frappe.PermissionError(_("You can only import your own upload"))
	try:
		from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

		rows = read_xlsx_file_from_attached_file(file_url=file_url)
		ops, skipped, errors = _parse_import(rows, warehouse)
		target = _import_target(name, warehouse)
		# Merge (and validate) before any state change, so a failure cannot
		# leave a Rejected doc revised to Draft with nothing applied.
		merged, applied = _merge_rows(target, ops, warehouse) if ops else ([], 0)
		if not merged:
			return {"name": target.name if target else None, "applied": 0, "skipped": skipped, "errors": errors}
		if target is None:
			doc = frappe.new_doc(DOCTYPE)
			doc.company = company
			doc.purpose = "Stock Reconciliation"
			doc.set_warehouse = warehouse
			for row in merged:
				doc.append("items", row)
			doc.insert()
		else:
			wf = _workflow()
			roles = frappe.get_roles()
			if _state_flags(target, wf, roles)["returned"]:
				back = _back_transition(wf, target.workflow_state, roles)
				if not back:
					frappe.throw(_("You cannot revise this count"), ValidationError)
				apply_workflow(target, back)
				target.reload()
				_assert_doc_warehouse(target, warehouse)
			target.set("items", [])
			for row in merged:
				target.append("items", row)
			target.save()
			doc = target
		return {"name": doc.name, "applied": applied, "skipped": skipped, "errors": errors}
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Import Stock Opname Error")
		frappe.throw(_("Error importing stock opname: {0}").format(str(e)))
	finally:
		try:
			frappe.delete_doc("File", file_name, ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "Delete Opname Upload Error")
