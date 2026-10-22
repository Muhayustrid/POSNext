# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Thin whitelisted POS Reject Request endpoints: outlet access gate + input
parsing (design D-phase 5). All lifecycle logic lives in
pos_next.services.reject_request; the Head Office endpoints have no profile gate
— role and owner checks live in the service."""

import json
from collections import Counter

import frappe
from frappe import ValidationError, _
from frappe.utils import cint, flt, getdate

from pos_next.api.packages import _assert_profile_access
from pos_next.reject_request_setup import (
	APPROVER_ROLE,
	STATE_AWAITING,
	STATE_DRAFT,
)
from pos_next.services import reject_request

LIST_FIELDS = (
	"name", "purpose", "workflow_state", "incident_date", "deadline_date", "total_amount",
	"is_late", "over_limit", "approval_code", "borne_by", "work_order",
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


def _assert_reject_access(pos_profile):
	"""Outlet access check; approvers have company-wide access."""
	if frappe.session.user == "Administrator":
		return
	if APPROVER_ROLE in frappe.get_roles():
		return
	_assert_profile_access(pos_profile)


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
			filters.append(["workflow_state", "=", status])
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
	"""Full row: doc + items + evidence/destruction attachment summaries + actions."""
	doc = _doc(name)
	_assert_reject_access(doc.pos_profile)
	try:
		payload = doc.as_dict()
		payload["items"] = [row.as_dict() for row in doc.items]
		payload["evidence"] = _attached("evidence", name)
		payload["destruction"] = _attached("destruction", name)

		from frappe.model.workflow import get_transitions, has_approval_access

		try:
			transitions = get_transitions(doc)
			user = frappe.session.user
			payload["actions"] = list(
				dict.fromkeys(
					t.get("action")
					for t in transitions
					if has_approval_access(user, doc, t)
				)
			)
		except Exception:
			payload["actions"] = []

		return payload
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Reject Request Error")
		frappe.throw(_("Error fetching reject request: {0}").format(str(e)))


def _item_rows(value, incident_date=None):
	"""Validated item payload rows; refuses non-dict entries cleanly."""
	rows = _parse(value, [])
	if not isinstance(rows, list):
		frappe.throw(_("items must be a list"), ValidationError)
	out = []
	for row in rows:
		if not isinstance(row, dict):
			frappe.throw(_("Each item must be an object with item_code and qty"), ValidationError)
		item_code = row.get("item_code")
		qty = flt(row.get("qty"))
		prod_date = row.get("production_date")
		if prod_date and incident_date:
			if getdate(prod_date) > getdate(incident_date):
				frappe.throw(
					_("Item {0}: Production date {1} cannot be after incident date {2}").format(
						item_code, prod_date, incident_date
					),
					ValidationError,
				)
		out.append({
			"item_code": item_code,
			"qty": qty,
			"uom": row.get("uom") or None,
			"production_date": prod_date or None,
			"reject_reason": row.get("reject_reason") or None,
		})
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
	change purpose / reason / incident_date / items (never video_link or
	system-only fields: is_system_generated, work_order, owner, workflow_state, borne_by).
	"""
	data = _parse(data, {}) or {}
	name = data.get("name")
	try:
		if name:
			doc = _doc(name)
			_assert_profile_access(doc.pos_profile)
			if doc.docstatus != 0 or (doc.workflow_state and doc.workflow_state != STATE_DRAFT):
				frappe.throw(_("Only a Draft reject request can be edited"), ValidationError)
			if "purpose" in data and not doc.is_system_generated:
				_validate_crew_purpose(data.get("purpose"))
				doc.purpose = data.get("purpose")
			doc.reason = data.get("reason", doc.reason)
			if "incident_date" in data and not doc.is_system_generated:
				doc.incident_date = data.get("incident_date")
			if "items" in data and not doc.is_system_generated:
				doc.set("items", [])
				for row in _item_rows(data.get("items"), incident_date=doc.incident_date):
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
			for row in _item_rows(data.get("items"), incident_date=doc.incident_date):
				doc.append("items", row)

		if name:
			doc.save()
		else:
			doc.insert()

		if cint(submit):
			from frappe.model.workflow import apply_workflow

			doc = apply_workflow(doc, "Submit")

		return _summary(doc)
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Save Reject Request Error")
		frappe.throw(_("Error saving reject request: {0}").format(str(e)))


def _summary(doc):
	return {
		"name": doc.name,
		"workflow_state": doc.workflow_state or frappe.db.get_value("POS Reject Request", doc.name, "workflow_state"),
		"docstatus": doc.docstatus,
		"total_amount": doc.total_amount,
	}


@frappe.whitelist()
def apply_reject_action(name, action):
	"""Apply a crew workflow action (Withdraw or Send Proof)."""
	if action not in ("Withdraw", "Send Proof"):
		frappe.throw(_("Action {0} is not allowed").format(action), ValidationError)
	doc = _doc(name)
	_assert_profile_access(doc.pos_profile)
	from frappe.model.workflow import apply_workflow

	apply_workflow(doc, action)
	return get_reject_request(name)


@frappe.whitelist()
def save_reject_video(name, video_link):
	"""Save destruction video link while in Awaiting Destruction."""
	doc = _doc(name)
	_assert_profile_access(doc.pos_profile)
	if doc.workflow_state != STATE_AWAITING:
		frappe.throw(_("Video link can only be edited in Awaiting Destruction"), ValidationError)
	link = (video_link or "").strip()
	if not link.lower().startswith(("http://", "https://")):
		frappe.throw(_("Video link must start with http:// or https://"), ValidationError)
	doc.video_link = link
	doc.save()
	return {"name": doc.name, "video_link": doc.video_link}


@frappe.whitelist()
def set_reject_liability(name, borne_by, liabilities=None):
	"""Head Office prompt wrapper: set borne_by and optional crew liabilities."""
	doc = _doc(name)
	try:
		return reject_request.set_liability(doc, borne_by, _parse(liabilities, None))
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Set Reject Liability Error")
		frappe.throw(_("Error setting reject liability: {0}").format(str(e)))


@frappe.whitelist()
def record_return_reason(name, reason):
	"""Head Office prompt wrapper: record return reason before returning to crew."""
	doc = _doc(name)
	try:
		reject_request.record_return_reason(doc, reason)
		return {"name": name, "return_reason": doc.return_reason}
	except (ValidationError, frappe.PermissionError):
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Record Return Reason Error")
		frappe.throw(_("Error recording return reason: {0}").format(str(e)))


@frappe.whitelist()
def attach_reject_files(name, kind, files):
	"""Attach private Files to the request as `kind` (evidence / destruction).

	Evidence only while Draft (max 5 total), destruction only in Awaiting Destruction.
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
			if doc.workflow_state and doc.workflow_state != STATE_DRAFT:
				frappe.throw(_("Evidence photos can only be attached to a Draft"))
			if _count_files(name, "evidence") + len(parsed) > MAX_EVIDENCE:
				frappe.throw(_("At most {0} evidence photos are allowed").format(MAX_EVIDENCE))
		elif kind == "destruction":
			if doc.workflow_state != STATE_AWAITING:
				frappe.throw(_("Destruction photos can only be attached in Awaiting Destruction"))

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
