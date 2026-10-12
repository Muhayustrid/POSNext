# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Thin whitelisted POS Reject Request endpoints: outlet access gate + input
parsing (design D-phase 5). All lifecycle logic lives in
pos_next.services.reject_request; the Head Office decide endpoint has no
profile gate — role and owner checks are in apply_decision."""

import json
from collections import Counter

import frappe
from frappe import ValidationError, _
from frappe.utils import cint, flt

from pos_next.api.packages import _assert_profile_access
from pos_next.services import reject_request

LIST_FIELDS = (
	"name", "purpose", "status", "incident_date", "deadline_date", "total_amount",
	"is_late", "over_limit", "decision", "work_order",
)
MAX_EVIDENCE = reject_request.MAX_EVIDENCE
ATTACHMENT_KINDS = ("evidence", "destruction")


def _parse(value, default):
	if value is None or value == "":
		return default
	if isinstance(value, str):
		try:
			return json.loads(value)
		except (ValueError, TypeError):
			frappe.throw(_("Invalid request data"), ValidationError)
	return value


def _doc(name):
	doc = frappe.get_doc("POS Reject Request", name)
	return doc


def _attached(kind, name):
	"""Attachment summary for one field (evidence/destruction) — same shape as
	purchase_orders._attachments_summary, plus attached_to_field per row."""
	rows = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "POS Reject Request",
			"attached_to_name": name,
			"attached_to_field": kind,
		},
		fields=["name", "file_name", "file_url", "file_size", "attached_to_field"],
		order_by="creation asc",
	)
	return rows


def _count_files(name, kind):
	return frappe.db.count(
		"File",
		{
			"attached_to_doctype": "POS Reject Request",
			"attached_to_name": name,
			"attached_to_field": kind,
		},
	)


@frappe.whitelist()
def get_reject_requests(pos_profile, status=None, limit=30, offset=0):
	"""List rows for the outlet the caller is assigned to."""
	_assert_profile_access(pos_profile)
	try:
		filters = [["pos_profile", "=", pos_profile]]
		if status:
			filters.append(["status", "=", status])
		limit = min(max(cint(limit), 1), 100)
		offset = max(cint(offset), 0)
		rows = frappe.get_list(
			"POS Reject Request",
			filters=filters,
			fields=LIST_FIELDS,
			order_by="creation desc",
			limit_page_length=limit + 1,
			start=offset,
		)
		has_more = len(rows) > limit
		counts = _file_counts([r.name for r in rows])
		for row in rows:
			row.evidence_count = counts.get((row.name, "evidence"), 0)
			row.destruction_count = counts.get((row.name, "destruction"), 0)
		return {"requests": rows[:limit], "has_more": has_more}
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Reject Requests Error")
		frappe.throw(_("Error fetching reject requests: {0}").format(str(e)))


def _file_counts(names):
	"""(name, attached_to_field) -> count for the list rows."""
	if not names:
		return {}
	counter = Counter(
		(d.attached_to_name, d.attached_to_field)
		for d in frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "POS Reject Request",
				"attached_to_name": ("in", names),
				"attached_to_field": ("in", ATTACHMENT_KINDS),
			},
			fields=["attached_to_name", "attached_to_field"],
		)
	)
	return dict(counter)


@frappe.whitelist()
def get_reject_request(name):
	"""Full row: doc + items + evidence/destruction attachment summaries."""
	doc = _doc(name)
	_assert_profile_access(doc.pos_profile)
	try:
		payload = doc.as_dict()
		payload["items"] = [row.as_dict() for row in doc.items]
		payload["evidence"] = _attached("evidence", name)
		payload["destruction"] = _attached("destruction", name)
		return payload
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Reject Request Error")
		frappe.throw(_("Error fetching reject request: {0}").format(str(e)))


def _item_rows(value):
	"""Validated item payload rows; refuses non-dict entries cleanly."""
	rows = _parse(value, [])
	if not isinstance(rows, list):
		frappe.throw(_("items must be a list"), ValidationError)
	out = []
	for row in rows:
		if not isinstance(row, dict):
			frappe.throw(_("Each item must be an object with item_code and qty"), ValidationError)
		out.append({"item_code": row.get("item_code"), "qty": flt(row.get("qty"))})
	return out


def _validate_crew_purpose(purpose):
	"""Crew lane: the purpose must exist, be enabled and non-system."""
	if not purpose or not frappe.db.get_value("POS Reject Purpose", purpose):
		frappe.throw(_("A valid reject purpose is required"))
	if reject_request.is_system_purpose(purpose):
		frappe.throw(_("Purpose {0} can only be used by the system").format(purpose))
	if not frappe.db.get_value("POS Reject Purpose", purpose, "enabled"):
		frappe.throw(_("Purpose {0} is disabled").format(purpose))


@frappe.whitelist()
def save_reject_request(data, submit=0):
	"""Insert or update a crew draft (docstatus 0), optionally submit it.

	New rows require a non-system, enabled purpose; existing rows may only
	change purpose / reason / video_link / incident_date / items (never
	system-only fields: is_system_generated, work_order, owner, status).
	"""
	data = _parse(data, {}) or {}
	name = data.get("name")
	try:
		if name:
			doc = _doc(name)
			_assert_profile_access(doc.pos_profile)
			if doc.docstatus != 0:
				frappe.throw(_("Only a Draft reject request can be edited"))
			if "purpose" in data and not doc.is_system_generated:
				_validate_crew_purpose(data.get("purpose"))
				doc.purpose = data.get("purpose")
			doc.reason = data.get("reason", doc.reason)
			doc.video_link = data.get("video_link", doc.video_link)
			if "incident_date" in data and not doc.is_system_generated:
				doc.incident_date = data.get("incident_date")
			if "items" in data and not doc.is_system_generated:
				doc.set("items", [])
				for row in _item_rows(data.get("items")):
					doc.append("items", row)
		else:
			pos_profile = data.get("pos_profile")
			_assert_profile_access(pos_profile)
			_validate_crew_purpose(data.get("purpose"))
			doc = frappe.new_doc("POS Reject Request")
			doc.purpose = data.get("purpose")
			doc.pos_profile = pos_profile
			doc.incident_date = data.get("incident_date")
			doc.reason = data.get("reason")
			doc.video_link = data.get("video_link")
			for row in _item_rows(data.get("items")):
				doc.append("items", row)
		if name:
			doc.save()
		else:
			doc.insert()
		if cint(submit):
			doc.submit()
		return _summary(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save Reject Request Error")
		frappe.throw(_("Error saving reject request: {0}").format(str(e)))


def _summary(doc):
	return {
		"name": doc.name,
		"status": frappe.db.get_value("POS Reject Request", doc.name, "status"),
		"docstatus": doc.docstatus,
		"total_amount": doc.total_amount,
	}


@frappe.whitelist()
def attach_reject_files(name, kind, files):
	"""Attach private Files to the request as `kind` (evidence / destruction).

	Evidence only while Draft (max 5 total), destruction only once Approved.
	Outlet roles lack write-after-submit docperms, so the inserts run with
	ignore_permissions — the profile gate above is the access decision.
	"""
	doc = _doc(name)
	_assert_profile_access(doc.pos_profile)
	try:
		if kind not in ATTACHMENT_KINDS:
			frappe.throw(_("Attachments must be evidence or destruction"))
		parsed = _parse(files, [])
		if not parsed:
			frappe.throw(_("At least one file is required"))
		for f in parsed:
			if not (f.get("file_name") or "").strip() or not (f.get("filedata") or "").strip():
				frappe.throw(_("File name and data are required"))
		if kind == "evidence":
			if doc.docstatus != 0:
				frappe.throw(_("Evidence photos can only be attached to a Draft"))
			if _count_files(name, "evidence") + len(parsed) > MAX_EVIDENCE:
				frappe.throw(_("At most {0} evidence photos are allowed").format(MAX_EVIDENCE))
		elif doc.status not in reject_request.DECISIONS:
			frappe.throw(_("Destruction photos can only be attached after a decision"))

		for f in parsed:
			frappe.get_doc(
				{
					"doctype": "File",
					"attached_to_doctype": "POS Reject Request",
					"attached_to_name": name,
					"attached_to_field": kind,
					"file_name": f["file_name"],
					"is_private": 1,
					"content": f["filedata"],
					"decode": 1,
				}
			).insert(ignore_permissions=True)
		return {"evidence_count": _count_files(name, "evidence"), "destruction_count": _count_files(name, "destruction")}
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Attach Reject Files Error")
		frappe.throw(_("Error attaching files: {0}").format(str(e)))


@frappe.whitelist()
def cancel_reject_request(name):
	"""Cancel a Pending request (service guards the status, stock returns)."""
	doc = _doc(name)
	_assert_profile_access(doc.pos_profile)
	try:
		doc.flags.ignore_permissions = True
		doc.cancel()
		return _summary(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Cancel Reject Request Error")
		frappe.throw(_("Error cancelling reject request: {0}").format(str(e)))


@frappe.whitelist()
def close_reject_request(name):
	"""Close an Approved request after destruction proof (service guards)."""
	doc = _doc(name)
	_assert_profile_access(doc.pos_profile)
	try:
		return reject_request.close_request(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Close Reject Request Error")
		frappe.throw(_("Error closing reject request: {0}").format(str(e)))


@frappe.whitelist()
def decide_reject_request(name, decision, notes=None, liabilities=None):
	"""Head Office verdict — no profile gate; role/owner checks live in the
	service (apply_decision), the outlet never sees this form. `liabilities`
	is a JSON list of {user, amount} rows."""
	try:
		return reject_request.apply_decision(
			_doc(name), decision, notes, _parse(liabilities, None)
		)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Decide Reject Request Error")
		frappe.throw(_("Error deciding reject request: {0}").format(str(e)))


@frappe.whitelist()
def get_reject_purposes():
	"""Enabled non-system purposes for the POS dropdown."""
	try:
		return frappe.get_all(
			"POS Reject Purpose",
			filters={"enabled": 1, "is_system": 0},
			pluck="name",
			order_by="name asc",
		)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Reject Purposes Error")
		frappe.throw(_("Error fetching reject purposes: {0}").format(str(e)))
