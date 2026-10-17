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

import frappe
from frappe import ValidationError, _
from frappe.model.workflow import apply_workflow
from frappe.utils import (
	add_days,
	cint,
	flt,
	formatdate,
	get_datetime,
	get_time,
	getdate,
	now_datetime,
	nowdate,
	nowtime,
)

from pos_next.api.packages import _assert_profile_access
from pos_next.services.production import _resolve_profile
from pos_next.stock_opname_setup import STATE_APPROVED, STATE_DRAFT, STATE_PENDING_SM, STATE_REJECTED

DOCTYPE = "Stock Reconciliation"
REJECT_PREFIX = "Rejected: "
EDITABLE_STATES = (STATE_DRAFT, STATE_REJECTED)
TEMPLATE_HEADER = ["Item Code", "Item Name", "UOM", "Batch No", "Serial No", "Counted Qty"]
# Fallback backdate cap (days) when POS Settings has no `opname_max_backdate_days`.
MAX_BACKDATE_DAYS = 2


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
	"""Backdate cap in days: POS Settings `opname_max_backdate_days`, else the
	constant. The POS Settings field is not in the doctype yet, so the constant
	(amended D6 default) applies until an admin adds it."""
	cap = frappe.db.get_singles_dict("POS Settings").get("opname_max_backdate_days")
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


def _validated_rows(items, warehouse):
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
		if has_serial:
			# Empty count reconciles against all current serials (ERPNext
			# rejects use_serial_batch_fields rows without serial_no).
			row["reconcile_all_serial_batch"] = 0 if serial_nos else 1
			row["use_serial_batch_fields"] = 1 if serial_nos else 0
			row["serial_no"] = "\n".join(serial_nos) if serial_nos else None
		rows.append(row)
	return rows


def _payload(doc):
	meta_cache = {}
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
		"set_warehouse": doc.set_warehouse,
		"reject_reason": _reject_reason(doc.name) if doc.workflow_state == STATE_REJECTED else None,
		"items": items,
		"attachments": _attachments(doc.name),
	}


def _attachments(name):
	"""File/link rows attached to the opname, same shape as purchase_orders._attachments_summary."""
	if not name:
		return []
	rows = frappe.get_all(
		"File",
		filters={"attached_to_doctype": DOCTYPE, "attached_to_name": name},
		fields=["name", "file_name", "file_url", "file_size"],
		order_by="creation asc",
	)
	for row in rows:
		# a link's derived file_name is only its last path segment; show the URL
		if (row.file_url or "").startswith(("http://", "https://")):
			row.file_name = row.file_url
	return rows


@frappe.whitelist()
def list_opnames(pos_profile):
	"""Newest-first opnames of the profile warehouse (limit 50)."""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		rows = frappe.get_all(
			DOCTYPE,
			filters={"set_warehouse": warehouse},
			fields=["name", "posting_date", "posting_time", "workflow_state", "docstatus", "modified"],
			order_by="creation desc",
			limit=50,
		)
		out = []
		for row in rows:
			item_count = frappe.db.count("Stock Reconciliation Item", {"parent": row.name})
			out.append(
				{
					"name": row.name,
					"posting_date": row.posting_date,
					"posting_time": row.posting_time,
					"workflow_state": row.workflow_state,
					"docstatus": row.docstatus,
					"item_count": item_count,
					"modified": row.modified,
					"reject_reason": _reject_reason(row.name) if row.workflow_state == STATE_REJECTED else None,
					"processing": row.workflow_state == STATE_APPROVED and row.docstatus == 0,
				}
			)
		return out
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "List Stock Opnames Error")
		frappe.throw(_("Error fetching stock opnames: {0}").format(str(e)))


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
	"""Create or update a Draft/Rejected opname; Rejected revises to Draft first."""
	company, warehouse = _profile_warehouse(pos_profile)
	data = _parse_data(data)
	posting_date, posting_time = _guarded_posting(data)
	try:
		name = data.get("name")
		if name:
			doc = _get_doc(name)
			_assert_doc_warehouse(doc, warehouse)
			if doc.workflow_state not in EDITABLE_STATES:
				frappe.throw(_("Only a Draft or Rejected opname can be edited"), ValidationError)
			if "modified" not in data or not data.get("modified"):
				frappe.throw(_("modified is required to save an existing opname"), ValidationError)
			if str(doc.modified) != str(data["modified"]):
				raise frappe.TimestampMismatchError(
					_("This count changed on another device (modified, reload). Reload before saving.")
				)
			# Validate before any Revise transition, so invalid rows (or a frozen
			# posting date) cannot leave a Rejected doc flipped to Draft with
			# nothing applied.
			rows = _validated_rows(data.get("items"), warehouse)
			_assert_not_frozen(posting_date or doc.posting_date or nowdate())
			if doc.workflow_state == STATE_REJECTED:
				apply_workflow(doc, "Revise")
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
		else:
			rows = _validated_rows(data.get("items"), warehouse)
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


@frappe.whitelist()
def submit_opname(pos_profile, name):
	"""Preserve an outlet-set posting date, else stamp now; workflow-Submit to pending."""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
		if doc.workflow_state != STATE_DRAFT:
			frappe.throw(_("Only a Draft opname can be submitted"), ValidationError)
		_assert_not_frozen(doc.posting_date or nowdate())
		# Preserve an outlet backdate (save already capped and guarded it); only
		# stamp now when the posting date is still the save-moment default (today).
		if getdate(doc.posting_date or nowdate()) >= getdate(nowdate()):
			doc.posting_date = nowdate()
			doc.posting_time = nowtime()
		doc.set_posting_time = 1
		doc.save()
		apply_workflow(doc, "Submit")
		doc.reload()
		return {"name": doc.name, "workflow_state": doc.workflow_state}
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Submit Stock Opname Error")
		frappe.throw(_("Error submitting stock opname: {0}").format(str(e)))


@frappe.whitelist()
def delete_opname(pos_profile, name):
	"""Delete a Draft or Rejected opname."""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
		if doc.workflow_state not in EDITABLE_STATES:
			frappe.throw(_("Only a Draft or Rejected opname can be deleted"), ValidationError)
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
	if doc.workflow_state not in EDITABLE_STATES:
		frappe.throw(_("Attachments can only change on a Draft or Rejected opname"), ValidationError)
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


def _template_rows(warehouse, item_group=None):
	"""Blank count rows: one per item with a Bin, per stocked batch / warehouse serial."""
	item_codes = frappe.get_all("Bin", filters={"warehouse": warehouse}, pluck="item_code", distinct=True)
	if not item_codes:
		return []
	items = frappe.get_all(
		"Item",
		filters={"name": ("in", item_codes), "is_stock_item": 1, "disabled": 0},
		fields=["item_code", "item_name", "stock_uom", "item_group", "has_batch_no", "has_serial_no"],
		order_by="item_code asc",
	)
	if item_group:
		groups = set(frappe.db.get_descendants("Item Group", item_group) or [])
		groups.add(item_group)
		items = [i for i in items if i.item_group in groups]
	from erpnext.stock.doctype.batch.batch import get_batch_qty

	rows = []
	for item in items:
		if item.has_batch_no:
			for batch in get_batch_qty(item_code=item.item_code, warehouse=warehouse) or []:
				if (batch.get("qty") or 0) > 0:
					rows.append([item.item_code, item.item_name, item.stock_uom, batch.get("batch_no"), "", ""])
		elif item.has_serial_no:
			serials = frappe.get_all(
				"Serial No",
				filters={"item_code": item.item_code, "warehouse": warehouse},
				pluck="name",
				order_by="name asc",
			)
			for serial in serials:
				rows.append([item.item_code, item.item_name, item.stock_uom, "", serial, ""])
		else:
			rows.append([item.item_code, item.item_name, item.stock_uom, "", "", ""])
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

	Pending/Approved targets (not editable) raise; Rejected targets are returned
	as-is and revised by the caller so U3 holds for uploads.
	"""
	if name:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
	else:
		open_name = frappe.db.get_value(DOCTYPE, {"docstatus": 0, "set_warehouse": warehouse}, "name")
		if not open_name:
			return None
		doc = _get_doc(open_name)
	if doc.workflow_state not in EDITABLE_STATES:
		frappe.throw(_("Only a Draft or Rejected opname can be edited"), ValidationError)
	return doc


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
			try:
				_validated_rows([{"item_code": item_code, "serial_nos": [serial_no]}], warehouse)
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
			try:
				_validated_rows([entry], warehouse)
			except ValidationError as e:
				errors.append({"row": i, "item_code": item_code, "message": str(e)})
				continue
			ops.append({"item_code": item_code, "batch_no": batch_no, "qty": qty})
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
		if meta and meta.has_serial_no:
			current[(row.item_code, None)] = {
				"item_code": row.item_code,
				"serial_nos": {s.strip() for s in (row.serial_no or "").split("\n") if s.strip()},
			}
		else:
			current[(row.item_code, row.batch_no)] = {
				"item_code": row.item_code,
				"batch_no": row.batch_no,
				"qty": row.qty,
			}
	for op in ops:
		if "serial" in op:
			entry = current.setdefault((op["item_code"], None), {"item_code": op["item_code"], "serial_nos": set()})
			if op["present"]:
				entry["serial_nos"].add(op["serial"])
			else:
				entry["serial_nos"].discard(op["serial"])
		else:
			current[(op["item_code"], op["batch_no"])] = {
				"item_code": op["item_code"],
				"batch_no": op["batch_no"],
				"qty": op["qty"],
			}
	entries = []
	for val in current.values():
		if "serial_nos" in val:
			entries.append({"item_code": val["item_code"], "serial_nos": sorted(val["serial_nos"])})
		else:
			entries.append({"item_code": val["item_code"], "qty": val["qty"], "batch_no": val["batch_no"]})
	if not entries:
		return [], 0
	applied = sum(1 for op in ops if (op["item_code"], None if "serial" in op else op["batch_no"]) in current)
	return _validated_rows(entries, warehouse), applied


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
			if target.workflow_state == STATE_REJECTED:
				apply_workflow(target, "Revise")
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
