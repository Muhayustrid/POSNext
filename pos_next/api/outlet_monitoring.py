# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import os

import frappe
from frappe import _
from frappe.utils import (
	add_to_date,
	cint,
	get_datetime,
	now_datetime,
)

from pos_next.api.packages import _assert_profile_access
from pos_next.services.outlet_monitoring import (
	client_config,
	ensure_obligations,
	get_settings,
	recompute_evidence,
)
from pos_next.services.outlet_monitoring_access import cashier_profiles
from pos_next.services.outlet_monitoring_media import validate_media_file


def _check_enabled():
	if not get_settings().enabled:
		frappe.throw(_("Outlet Monitoring is disabled"), frappe.ValidationError)


@frappe.whitelist(methods=["GET", "POST"])
def get_my_tasks(pos_profile: str) -> dict:
	"""Fetch pending draft obligations for cashier."""
	_assert_profile_access(pos_profile)
	s = get_settings()
	if not s.enabled:
		return {"enabled": False, "config": {}, "count": 0, "tasks": []}

	ensure_obligations(pos_profile=pos_profile)
	company = frappe.db.get_value("POS Profile", pos_profile, "company")
	open_shift = frappe.db.get_value("POS Opening Shift", {"pos_profile": pos_profile, "status": "Open"}, "name")

	reports = []
	# 1. Per Shift for current open shift
	if open_shift:
		reports.extend(
			frappe.get_all(
				"POS Monitoring Report",
				filters={"opening_shift": open_shift, "docstatus": 0, "status": "Draft"},
				pluck="name",
			)
		)

	# 2. Per Hari for company
	if company:
		reports.extend(
			frappe.get_all(
				"POS Monitoring Report",
				filters={"cadence": "Per Hari", "company": company, "docstatus": 0, "status": "Draft"},
				pluck="name",
			)
		)

	# 3. Draft revisions for this pos_profile
	if pos_profile:
		revisions = frappe.get_all(
			"POS Monitoring Report",
			filters={
				"pos_profile": pos_profile,
				"docstatus": 0,
				"status": "Draft",
				"revision": [">", 1],
			},
			pluck="name",
		)
		reports.extend(revisions)

	# De-duplicate while preserving order
	unique_names = list(dict.fromkeys(reports))
	now_dt = now_datetime()
	tasks = []

	for r_name in unique_names:
		doc = frappe.get_doc("POS Monitoring Report", r_name)
		recompute_evidence(doc)
		is_late = bool(doc.due_at and now_dt > get_datetime(doc.due_at))
		req_total = len([cp for cp in doc.checkpoints if cp.required])
		req_done = len([cp for cp in doc.checkpoints if cp.required and cp.evidence_complete])

		tasks.append({
			"name": doc.name,
			"template": doc.template,
			"cadence": doc.cadence,
			"company": doc.company,
			"pos_profile": doc.pos_profile,
			"operational_date": str(doc.operational_date),
			"due_at": str(doc.due_at),
			"late": is_late,
			"required_total": req_total,
			"required_done": req_done,
		})

	tasks.sort(key=lambda t: t.get("due_at") or "")
	return {
		"enabled": True,
		"config": client_config(),
		"count": len(tasks),
		"tasks": tasks,
	}


@frappe.whitelist(methods=["GET", "POST"])
def get_history(pos_profile: str, start: int = 0, page_length: int = 20) -> dict:
	"""Fetch past submitted/terminal reports for the company."""
	_check_enabled()
	_assert_profile_access(pos_profile)
	company = frappe.db.get_value("POS Profile", pos_profile, "company")

	limit = cint(page_length) or 20
	offset = cint(start) or 0

	names = frappe.get_all(
		"POS Monitoring Report",
		filters={"company": company, "status": ["!=", "Draft"]},
		order_by="modified desc",
		limit_start=offset,
		limit_page_length=limit + 1,
		pluck="name",
	)
	has_more = len(names) > limit
	items = []
	for name in names[:limit]:
		doc = frappe.get_doc("POS Monitoring Report", name)
		items.append({
			"name": doc.name,
			"template": doc.template,
			"cadence": doc.cadence,
			"status": doc.status,
			"review_result": doc.review_result,
			"correction_requested": doc.correction_requested,
			"review_note": doc.review_note,
			"revision": doc.revision,
			"operational_date": str(doc.operational_date),
			"modified": str(doc.modified),
		})

	return {"items": items, "has_more": has_more}


@frappe.whitelist(methods=["GET", "POST"])
def get_report(name: str) -> dict:
	"""Fetch full report detail with sanitized checkpoints and media."""
	_check_enabled()
	doc = frappe.get_doc("POS Monitoring Report", name)
	frappe.has_permission("POS Monitoring Report", doc=doc, ptype="read", throw=True)

	recompute_evidence(doc)
	is_reviewed = (doc.status == "Reviewed")

	checkpoints = []
	for cp in doc.checkpoints:
		row = {
			"name": cp.name,
			"checkpoint": cp.checkpoint,
			"checkpoint_name": cp.checkpoint_name,
			"category": cp.category,
			"instruction": cp.instruction,
			"media_type": cp.media_type,
			"min_count": cp.min_count,
			"max_count": cp.max_count,
			"video_max_seconds": cp.video_max_seconds,
			"required": cp.required,
			"pass_label": cp.pass_label,
			"fail_label": cp.fail_label,
			"na_label": cp.na_label,
			"evidence_complete": cp.evidence_complete,
		}
		if is_reviewed:
			row["assessment"] = cp.assessment
			row["assessment_note"] = cp.assessment_note
		checkpoints.append(row)

	media = []
	for m in getattr(doc, "media", []):
		if not cint(m.purged):
			media.append({
				"name": m.name,
				"checkpoint_row": m.checkpoint_row,
				"file_url": m.file_url,
				"thumbnail_url": m.thumbnail_url,
				"media_type": m.media_type,
				"file_size": m.file_size,
				"uploaded_at": str(m.uploaded_at),
			})

	data = doc.as_dict()
	data["checkpoints"] = checkpoints
	data["media"] = media
	if not is_reviewed:
		data.pop("review_result", None)
		data.pop("review_note", None)
		data.pop("reviewed_by", None)
		data.pop("reviewed_at", None)

	return data


def _safe_cleanup_file(fname: str | None, current_user: str, report_name: str) -> None:
	"""Clean up unreferenced file upload belonging to caller for this draft report."""
	if not fname or not frappe.db.exists("File", fname):
		return
	try:
		fdoc = frappe.get_doc("File", fname)
		if fdoc.owner != current_user and current_user != "Administrator":
			return
		if fdoc.attached_to_doctype != "POS Monitoring Report" or fdoc.attached_to_name != report_name:
			return
		if (
			frappe.db.exists("POS Monitoring Report Media", {"file_url": fdoc.file_url})
			or frappe.db.exists("POS Monitoring Report Media", {"thumbnail_url": fdoc.file_url})
		):
			return
		other_files = frappe.db.exists("File", {"file_url": fdoc.file_url, "name": ["!=", fname]})
		if not other_files:
			path = fdoc.get_full_path() if hasattr(fdoc, "get_full_path") else None
			if path and os.path.exists(path):
				try:
					os.remove(path)
				except Exception:
					pass

		# Delete File record using isolated connection so ambient request transaction is never committed
		from frappe.database.mariadb.mysqlclient import MariaDBDatabase
		c = frappe.conf
		db_cleanup = MariaDBDatabase(
			user=c.db_name,
			password=c.db_password,
			host=c.db_host,
			port=c.db_port,
			cur_db_name=c.db_name,
			socket=c.get("db_socket"),
		)
		db_cleanup.connect()
		try:
			db_cleanup.sql("DELETE FROM `tabFile` WHERE name = %s", (fname,))
			db_cleanup.commit()
		finally:
			db_cleanup.close()
		frappe.db.rollback()
	except Exception:
		pass


@frappe.whitelist(methods=["POST"])
def register_media(report: str, checkpoint_row: str, file_name: str, thumbnail_file_name: str | None = None) -> dict:
	"""Attach uploaded media file to report checkpoint."""
	_check_enabled()
	doc = frappe.get_doc("POS Monitoring Report", report)
	frappe.has_permission("POS Monitoring Report", doc=doc, ptype="write", throw=True)

	if doc.docstatus != 0 or doc.status != "Draft":
		frappe.throw(_("Cannot register media to non-draft report"), frappe.ValidationError)

	matching = [r for r in doc.checkpoints if r.name == checkpoint_row or r.checkpoint == checkpoint_row]
	if not matching:
		frappe.throw(_("Checkpoint not found in report"), frappe.ValidationError)
	cp_row = matching[0]

	settings = get_settings()
	user = frappe.session.user

	def _schedule_cleanup():
		for fn in (file_name, thumbnail_file_name):
			_safe_cleanup_file(fn, user, doc.name)

	try:
		if not frappe.db.exists("File", file_name):
			frappe.throw(_("File not found"), frappe.ValidationError)
		file_doc = frappe.get_doc("File", file_name)

		media_type = validate_media_file(file_doc, doc.name, cp_row.media_type, settings)

		existing_count = len([m for m in getattr(doc, "media", []) if m.checkpoint_row == cp_row.name and not cint(m.purged)])
		if existing_count >= cint(cp_row.max_count):
			frappe.throw(
				_("Maximum media count ({0}) reached for this checkpoint").format(cp_row.max_count),
				frappe.ValidationError,
			)

		thumb_url = None
		if thumbnail_file_name:
			if not frappe.db.exists("File", thumbnail_file_name):
				frappe.throw(_("Thumbnail file not found"), frappe.ValidationError)
			thumb_doc = frappe.get_doc("File", thumbnail_file_name)
			thumb_type = validate_media_file(thumb_doc, doc.name, "Photo", settings)
			if thumb_type != "Photo":
				frappe.throw(_("Thumbnail must be JPEG photo"), frappe.ValidationError)
			thumb_url = thumb_doc.file_url

		doc.append("media", {
			"checkpoint_row": cp_row.name,
			"file_url": file_doc.file_url,
			"thumbnail_url": thumb_url,
			"media_type": media_type,
			"file_size": file_doc.file_size or 0,
			"uploaded_at": now_datetime(),
			"purged": 0,
		})
		recompute_evidence(doc)
		doc.flags.ignore_permissions = True
		doc.flags.internal_media_change = True
		doc.save()
		return get_report(doc.name)
	except Exception:
		frappe.db.after_rollback(_schedule_cleanup)
		raise


@frappe.whitelist(methods=["POST"])
def remove_media(report: str, media_row: str) -> dict:
	"""Remove media item from draft report."""
	_check_enabled()
	doc = frappe.get_doc("POS Monitoring Report", report)
	frappe.has_permission("POS Monitoring Report", doc=doc, ptype="write", throw=True)

	if doc.docstatus != 0 or doc.status != "Draft":
		frappe.throw(_("Cannot remove media from non-draft report"), frappe.ValidationError)

	matching = [m for m in getattr(doc, "media", []) if m.name == media_row]
	if not matching:
		frappe.throw(_("Media row not found"), frappe.ValidationError)
	row = matching[0]

	for url in (row.file_url, row.thumbnail_url):
		if url:
			fname = frappe.db.get_value("File", {"file_url": url}, "name")
			if fname:
				try:
					frappe.flags.internal_monitoring_delete = True
					frappe.delete_doc("File", fname, ignore_permissions=True, force=True)
				except Exception:
					pass
				finally:
					frappe.flags.internal_monitoring_delete = False

	doc.remove(row)
	recompute_evidence(doc)
	doc.flags.ignore_permissions = True
	doc.flags.internal_media_change = True
	doc.save()
	return get_report(doc.name)


@frappe.whitelist(methods=["POST"])
def remove_orphan_file(file_name: str) -> None:
	"""Delete unreferenced upload file owned by user on draft report."""
	_check_enabled()
	if not frappe.db.exists("File", file_name):
		return
	f = frappe.get_doc("File", file_name)

	user = frappe.session.user
	if user != "Administrator" and f.owner != user:
		frappe.throw(_("Cannot remove file owned by another user"), frappe.PermissionError)

	if f.attached_to_doctype != "POS Monitoring Report":
		frappe.throw(_("Cannot remove file not attached to POS Monitoring Report"), frappe.ValidationError)

	rep_status = frappe.db.get_value("POS Monitoring Report", f.attached_to_name, "status")
	if rep_status != "Draft":
		frappe.throw(_("Cannot remove file from non-draft report"), frappe.ValidationError)

	is_ref = (
		frappe.db.exists("POS Monitoring Report Media", {"file_url": f.file_url})
		or frappe.db.exists("POS Monitoring Report Media", {"thumbnail_url": f.file_url})
	)
	if is_ref:
		frappe.throw(_("Cannot remove file that is referenced by media"), frappe.ValidationError)

	try:
		frappe.flags.internal_monitoring_delete = True
		frappe.delete_doc("File", f.name, ignore_permissions=True, force=True)
	finally:
		frappe.flags.internal_monitoring_delete = False


@frappe.whitelist(methods=["POST"])
def submit_report(name: str) -> dict:
	"""Validate evidence and submit report."""
	_check_enabled()
	doc = frappe.get_doc("POS Monitoring Report", name)
	frappe.has_permission("POS Monitoring Report", doc=doc, ptype="write", throw=True)

	if doc.docstatus != 0 or doc.status != "Draft":
		frappe.throw(_("Report is already submitted or closed"), frappe.ValidationError)

	recompute_evidence(doc)
	stored_checkpoints = frappe.get_all(
		"POS Monitoring Report Checkpoint", filters={"parent": name},
		fields=["name", "checkpoint", "checkpoint_name", "required", "min_count"],
	)
	stored_media = frappe.get_all(
		"POS Monitoring Report Media", filters={"parent": name, "purged": 0}, pluck="checkpoint_row",
	)
	for cp in stored_checkpoints:
		if cint(cp.required) and stored_media.count(cp.name) < cint(cp.min_count):
			frappe.throw(
				_("Checkpoint '{0}' requires evidence before submitting").format(cp.checkpoint_name or cp.checkpoint),
				frappe.ValidationError,
			)

	settings = get_settings()
	now_dt = now_datetime()
	is_late = bool(doc.due_at and now_dt > get_datetime(doc.due_at))
	if is_late and not settings.allow_late_submission:
		frappe.throw(_("Late submission is not allowed"), frappe.ValidationError)

	doc.submitted_at = now_dt
	doc.submitted_by = frappe.session.user
	doc.timeliness = "Late" if is_late else "On Time"
	doc.status = "Submitted"
	doc.flags.ignore_permissions = True
	doc.submit()

	import pos_next.api.outlet_monitoring_review as review_api
	review_api.on_report_submitted(doc)

	return get_report(doc.name)


@frappe.whitelist(methods=["POST"])
def create_revision(name: str) -> dict:
	"""Create a new draft revision for report requested for correction."""
	_check_enabled()
	old_doc = frappe.get_doc("POS Monitoring Report", name)
	frappe.has_permission("POS Monitoring Report", doc=old_doc, ptype="read", throw=True)

	if old_doc.status != "Reviewed":
		frappe.throw(_("Can only create revision for Reviewed reports"), frappe.ValidationError)

	if not old_doc.correction_requested:
		frappe.throw(_("Revision was not requested for this report"), frappe.ValidationError)

	settings = get_settings()
	if not settings.allow_correction:
		frappe.throw(_("Corrections are not allowed by settings"), frappe.ValidationError)

	if cint(old_doc.revision) >= 1 + cint(settings.max_revisions):
		frappe.throw(_("Maximum revision limit reached"), frappe.ValidationError)

	newer_exists = frappe.db.exists(
		"POS Monitoring Report",
		{"obligation_key": old_doc.obligation_key, "revision": cint(old_doc.revision) + 1},
	)
	if newer_exists:
		frappe.throw(_("Newer revision already exists"), frappe.ValidationError)

	new_doc = frappe.new_doc("POS Monitoring Report")
	new_doc.template = old_doc.template
	new_doc.cadence = old_doc.cadence
	new_doc.company = old_doc.company
	new_doc.pos_profile = old_doc.pos_profile
	new_doc.opening_shift = old_doc.opening_shift
	new_doc.operational_date = old_doc.operational_date
	grace_hours = cint(settings.shift_missed_grace_hours or 12)
	new_doc.due_at = add_to_date(now_datetime(), hours=grace_hours)
	new_doc.status = "Draft"
	new_doc.revision = cint(old_doc.revision) + 1
	new_doc.correction_of = old_doc.name
	new_doc.obligation_key = old_doc.obligation_key

	for cp in old_doc.checkpoints:
		new_doc.append("checkpoints", {
			"checkpoint": cp.checkpoint,
			"checkpoint_name": cp.checkpoint_name,
			"category": cp.category,
			"instruction": cp.instruction,
			"media_type": cp.media_type,
			"min_count": cp.min_count,
			"max_count": cp.max_count,
			"video_max_seconds": cp.video_max_seconds,
			"required": cp.required,
			"pass_label": cp.pass_label,
			"fail_label": cp.fail_label,
			"na_label": cp.na_label,
			"evidence_complete": 0,
			"assessment": "Pending",
		})

	new_doc.flags.ignore_permissions = True
	new_doc.insert()
	return get_report(new_doc.name)


@frappe.whitelist(methods=["GET", "POST"])
def get_shift_tasks(opening_shift: str) -> dict:
	"""Fetch pending Per Shift tasks for closing shift warning."""
	_check_enabled()
	shift = frappe.get_doc("POS Opening Shift", opening_shift)
	user = frappe.session.user
	if user != "Administrator" and user != shift.user and shift.pos_profile not in cashier_profiles(user):
		frappe.throw(_("Not permitted to view tasks for this shift"), frappe.PermissionError)

	drafts = frappe.get_all(
		"POS Monitoring Report",
		filters={"opening_shift": opening_shift, "docstatus": 0, "status": "Draft"},
		fields=["name", "template"],
	)
	items = []
	for d in drafts:
		rep = frappe.get_doc("POS Monitoring Report", d.name)
		recompute_evidence(rep)
		req_total = len([cp for cp in rep.checkpoints if cp.required])
		req_done = len([cp for cp in rep.checkpoints if cp.required and cp.evidence_complete])
		items.append({
			"name": rep.name,
			"template": rep.template,
			"required_total": req_total,
			"required_done": req_done,
		})

	return {"items": items}
