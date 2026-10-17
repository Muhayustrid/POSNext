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
from frappe.utils import nowdate, nowtime

from pos_next.api.packages import _assert_profile_access
from pos_next.services.production import _resolve_profile
from pos_next.stock_opname_setup import STATE_APPROVED, STATE_DRAFT, STATE_PENDING_SM, STATE_REJECTED

DOCTYPE = "Stock Reconciliation"
REJECT_PREFIX = "Rejected: "
EDITABLE_STATES = (STATE_DRAFT, STATE_REJECTED)


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


def _validated_rows(items, warehouse):
	"""Validate outlet count rows; return ERPNext row dicts ready to append.

	Raises ValidationError with a per-row message (item code named, unknown
	serials listed) so Task 4 (Excel import) can report them per row.
	"""
	if not isinstance(items, list) or not items:
		frappe.throw(_("At least one counted item is required"), ValidationError)
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
		if has_batch:
			if not batch_no:
				frappe.throw(_("Batch is required for {0}").format(item_code), ValidationError)
			if frappe.db.get_value("Batch", batch_no, "item") != item_code:
				frappe.throw(_("Batch {0} does not belong to {1}").format(batch_no, item_code), ValidationError)
			if frappe.db.get_value("Batch", batch_no, "disabled"):
				frappe.throw(_("Batch {0} is disabled").format(batch_no), ValidationError)
			qty = entry.get("qty")
			try:
				qty = float(qty)
			except (TypeError, ValueError):
				frappe.throw(_("Counted qty for {0} must be a number").format(item_code), ValidationError)
			if not math.isfinite(qty) or qty < 0:
				frappe.throw(_("Counted qty for {0} must be zero or more").format(item_code), ValidationError)
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
			qty = entry.get("qty")
			try:
				qty = float(qty)
			except (TypeError, ValueError):
				frappe.throw(_("Counted qty for {0} must be a number").format(item_code), ValidationError)
			if not math.isfinite(qty) or qty < 0:
				frappe.throw(_("Counted qty for {0} must be zero or more").format(item_code), ValidationError)
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
		items.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name or meta.get("item_name"),
				"stock_uom": row.stock_uom or meta.get("stock_uom"),
				"qty": row.qty,
				"batch_no": row.batch_no,
				"serial_nos": serials,
				"has_batch_no": int(bool(meta.get("has_batch_no"))),
				"has_serial_no": int(bool(meta.get("has_serial_no"))),
			}
		)
	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state,
		"docstatus": doc.docstatus,
		"modified": doc.modified,
		"reject_reason": _reject_reason(doc.name) if doc.workflow_state == STATE_REJECTED else None,
		"items": items,
	}


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
def save_opname(pos_profile, data):
	"""Create or update a Draft/Rejected opname; Rejected revises to Draft first."""
	company, warehouse = _profile_warehouse(pos_profile)
	data = _parse_data(data)
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
				raise frappe.TimestampMismatchError
			if doc.workflow_state == STATE_REJECTED:
				apply_workflow(doc, "Revise")
				doc.reload()
				_assert_doc_warehouse(doc, warehouse)
			doc.set("items", [])
			for row in _validated_rows(data.get("items"), warehouse):
				doc.append("items", row)
			doc.save()
		else:
			rows = _validated_rows(data.get("items"), warehouse)
			doc = frappe.new_doc(DOCTYPE)
			doc.company = company
			doc.purpose = "Stock Reconciliation"
			doc.set_warehouse = warehouse
			for row in rows:
				doc.append("items", row)
			doc.insert()
		return _payload(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save Stock Opname Error")
		frappe.throw(_("Error saving stock opname: {0}").format(str(e)))


@frappe.whitelist()
def submit_opname(pos_profile, name):
	"""Stamp posting now, then workflow-Submit a Draft to pending approval."""
	_company, warehouse = _profile_warehouse(pos_profile)
	try:
		doc = _get_doc(name)
		_assert_doc_warehouse(doc, warehouse)
		if doc.workflow_state != STATE_DRAFT:
			frappe.throw(_("Only a Draft opname can be submitted"), ValidationError)
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
