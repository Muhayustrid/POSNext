# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import make_notification_logs
from frappe.utils import (
	cint,
	now_datetime,
)

from pos_next.hq_scope import resolve_company_scope
from pos_next.realtime_events import publish_to_profile
from pos_next.services.outlet_monitoring import get_settings, label_color
from pos_next.services.outlet_monitoring_access import (
	REVIEWER_ROLE,
	is_reviewer,
	reviewer_companies,
)


def _assert_reviewer_or_manager():
	user = frappe.session.user
	if user == "Administrator":
		return
	roles = frappe.get_roles(user)
	if "System Manager" in roles or is_reviewer(user):
		return
	frappe.throw(_("Not permitted: requires Reviewer or Manager role"), frappe.PermissionError)


def _publish(doc):
	"""Emit realtime event outlet_monitoring_updated."""
	payload = {"report": doc.name, "status": doc.status}
	if doc.pos_profile:
		publish_to_profile("outlet_monitoring_updated", payload, doc.pos_profile)
	else:
		profiles = frappe.get_all("POS Profile", filters={"company": doc.company, "disabled": 0}, pluck="name")
		for p in profiles:
			publish_to_profile("outlet_monitoring_updated", payload, p)


def on_report_submitted(doc):
	"""Hook called when report is submitted."""
	s = get_settings()
	if s.notify_reviewers_on_submit:
		reviewer_users = frappe.get_all(
			"Has Role",
			filters={"role": REVIEWER_ROLE, "parenttype": "User"},
			pluck="parent",
		)
		target_users = []
		for u in set(reviewer_users):
			if not frappe.db.get_value("User", u, "enabled"):
				continue
			companies = reviewer_companies(u)
			if companies is None or doc.company in companies:
				target_users.append(u)

		for u in target_users:
			try:
				make_notification_logs({
					"type": "Mention",
					"document_type": "POS Monitoring Report",
					"document_name": doc.name,
					"subject": _("POS Monitoring Report submitted: {0}").format(doc.name),
				}, [u])
			except Exception:
				pass

	_publish(doc)


@frappe.whitelist(methods=["POST"])
def finalize_review(name: str) -> dict:
	"""Finalize review labels for submitted report."""
	_assert_reviewer_or_manager()

	current_status = frappe.db.get_value("POS Monitoring Report", name, "status", for_update=True)
	if current_status != "Submitted":
		frappe.throw(_("Can only finalize Submitted reports"), frappe.ValidationError)

	doc = frappe.get_doc("POS Monitoring Report", name)
	frappe.has_permission("POS Monitoring Report", doc=doc, ptype="write", throw=True)

	s = get_settings()
	user = frappe.session.user
	if s.prevent_self_review and doc.submitted_by == user and user != "Administrator":
		frappe.throw(_("Self-review is not allowed"), frappe.PermissionError)

	from pos_next.pos_next.doctype.pos_monitoring_report.pos_monitoring_report import (
		assert_review_complete,
	)

	assert_review_complete(doc)

	if doc.correction_requested and not s.allow_correction:
		frappe.throw(_("Correction is not allowed by settings"), frappe.ValidationError)

	doc.reviewed_by = user
	doc.reviewed_at = now_datetime()
	doc.status = "Reviewed"
	doc.flags.ignore_permissions = True
	doc.flags.internal_review_finalize = True
	doc.save()

	if s.notify_reporter_on_review and doc.submitted_by:
		try:
			make_notification_logs({
				"type": "Mention",
				"document_type": "POS Monitoring Report",
				"document_name": doc.name,
				"subject": _("POS Monitoring Report reviewed: {0}").format(doc.name),
			}, [doc.submitted_by])
		except Exception:
			pass

	_publish(doc)
	return doc.as_dict()


@frappe.whitelist(methods=["POST"])
def waive(name: str, reason: str) -> dict:
	"""Waive an obligation report."""
	_assert_reviewer_or_manager()

	if not reason or not reason.strip():
		frappe.throw(_("Waive reason is required"), frappe.ValidationError)

	frappe.db.get_value("POS Monitoring Report", name, "name", for_update=True)
	doc = frappe.get_doc("POS Monitoring Report", name)
	frappe.has_permission("POS Monitoring Report", doc=doc, ptype="write", throw=True)

	if doc.status not in ("Submitted", "Missed"):
		frappe.throw(_("Only Submitted or Missed reports can be waived"), frappe.ValidationError)

	doc.status = "Waived"
	doc.waive_reason = reason.strip()
	doc.reviewed_by = frappe.session.user
	doc.reviewed_at = now_datetime()
	doc.flags.ignore_permissions = True
	doc.flags.internal_waive = True
	doc.save()

	_publish(doc)
	return doc.as_dict()


@frappe.whitelist(methods=["GET", "POST"])
def get_gallery(filters=None, start: int = 0, page_length: int | None = None) -> dict:
	"""Fetch gallery items across submitted reports."""
	_assert_reviewer_or_manager()

	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	filters = dict(filters or {})

	companies, restricted = resolve_company_scope(filters)

	s = get_settings()
	default_len = cint(s.gallery_page_size) or 30
	limit = cint(page_length) or default_len
	limit = max(10, min(200, limit))
	offset = cint(start) or 0

	conditions = ["r.docstatus = 1", "m.purged = 0"]
	values = {"fetch_limit": limit + 1, "offset": offset}

	if restricted and companies:
		conditions.append("r.company in %(companies)s")
		values["companies"] = tuple(companies)
	elif filters.get("company"):
		conditions.append("r.company = %(company)s")
		values["company"] = filters["company"]

	if filters.get("pos_profile"):
		conditions.append("r.pos_profile = %(pos_profile)s")
		values["pos_profile"] = filters["pos_profile"]

	if filters.get("from_date"):
		conditions.append("r.operational_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]

	if filters.get("to_date"):
		conditions.append("r.operational_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	if filters.get("category"):
		conditions.append("c.category = %(category)s")
		values["category"] = filters["category"]

	if filters.get("checkpoint"):
		conditions.append("c.checkpoint = %(checkpoint)s")
		values["checkpoint"] = filters["checkpoint"]

	if filters.get("label"):
		conditions.append("c.label = %(label)s")
		values["label"] = filters["label"]

	if filters.get("media_type"):
		conditions.append("m.media_type = %(media_type)s")
		values["media_type"] = filters["media_type"]

	where_clause = " and ".join(conditions)

	sql = f"""
		select
			r.name as report,
			m.name as media_row,
			m.file_url,
			m.thumbnail_url,
			m.media_type,
			c.label,
			c.label_options,
			c.checkpoint_name,
			c.category,
			r.company,
			r.pos_profile,
			r.operational_date,
			m.uploaded_at,
			r.submitted_by
		from `tabPOS Monitoring Report Media` m
		inner join `tabPOS Monitoring Report` r on r.name = m.parent
		left join `tabPOS Monitoring Report Checkpoint` c on c.name = m.checkpoint_row
		where {where_clause}
		order by m.uploaded_at desc
		limit %(fetch_limit)s offset %(offset)s
	"""

	rows = frappe.db.sql(sql, values, as_dict=True)
	has_more = len(rows) > limit
	items = rows[:limit]

	for item in items:
		item["operational_date"] = str(item.get("operational_date") or "")
		item["uploaded_at"] = str(item.get("uploaded_at") or "")
		label_options = item.pop("label_options", None)
		item["color"] = label_color(label_options, item.get("label"))

	return {"items": items, "has_more": has_more}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def label_options_query(doctype, txt, searchfield, start, page_len, filters=None):
	"""Link query returning the row's own snapshot options (§5).

	Standard Link search hides `disabled = 1` labels, but a disabled label in
	a stored snapshot must stay selectable, so the Desk form reads options
	from the snapshot instead of the master.
	"""
	_assert_reviewer_or_manager()

	filters = frappe.parse_json(filters) if isinstance(filters, str) else dict(filters or {})
	report = filters.get("report")
	row = filters.get("checkpoint_row")
	if not report or not row or str(row).startswith("new-"):
		return []
	try:
		doc = frappe.get_doc("POS Monitoring Report", report)
	except frappe.DoesNotExistError:
		return []
	frappe.has_permission("POS Monitoring Report", doc=doc, ptype="write", throw=True)

	cp_row = next((cp for cp in doc.checkpoints if cp.name == row), None)
	if cp_row is None:
		return []
	try:
		options = json.loads(cp_row.label_options or "[]")
	except ValueError:
		return []

	txt = (txt or "").strip().lower()
	out = []
	for opt in options or []:
		if not isinstance(opt, dict) or not opt.get("label"):
			continue
		if txt and txt not in str(opt["label"]).lower():
			continue
		out.append([opt["label"], opt.get("color")])
	start = cint(start) or 0
	page_len = cint(page_len) or 20
	return out[start:start + page_len]
