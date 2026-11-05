# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	get_datetime,
	getdate,
	now_datetime,
	today,
)


def get_settings() -> frappe._dict:
	"""Return POS Monitoring Settings as a dict."""
	return frappe.get_cached_doc("POS Monitoring Settings").as_dict()


def resolve_labels(category=None) -> tuple[str, str, str]:
	"""Resolve pass/fail/na labels for category falling back to settings."""
	settings = get_settings()
	default_pass = settings.default_pass_label or "Sesuai SOP"
	default_fail = settings.default_fail_label or "Tidak Sesuai SOP"
	default_na = settings.default_na_label or "Tidak Berlaku"

	if not category:
		return default_pass, default_fail, default_na

	cat = frappe.get_cached_doc("POS Monitoring Category", category)
	pass_label = cat.pass_label or default_pass
	fail_label = cat.fail_label or default_fail
	na_label = cat.na_label or default_na
	return pass_label, fail_label, na_label


def client_config() -> dict:
	"""Return config payload for POS client."""
	s = get_settings()
	return {
		"photo_max_edge": s.photo_max_edge,
		"thumbnail_max_edge": s.thumbnail_max_edge,
		"photo_quality": s.photo_quality,
		"photo_max_mb": s.photo_max_mb,
		"video_max_mb": s.video_max_mb,
		"video_max_seconds": s.video_max_seconds,
		"allow_webm": s.allow_webm,
		"show_menu_badge": s.show_menu_badge,
		"warn_on_shift_close": s.warn_on_shift_close,
		"allow_late_submission": s.allow_late_submission,
	}


def obligation_key(template: str, cadence: str, *, opening_shift=None, company=None, operational_date=None) -> str:
	"""Generate unique obligation key according to spec §4.4."""
	if cadence == "Per Shift":
		return f"{template}|shift|{opening_shift}"
	elif cadence == "Per Hari":
		return f"{template}|day|{company}|{operational_date}"
	return f"{template}|{cadence}|{opening_shift or company}"


def recompute_evidence(doc: Document) -> None:
	"""Recompute evidence_complete on each checkpoint row from unpurged media."""
	media_list = getattr(doc, "media", [])
	for cp in doc.checkpoints:
		unpurged = [m for m in media_list if m.checkpoint_row == cp.name and not cint(m.purged)]
		cp.evidence_complete = 1 if len(unpurged) >= cint(cp.min_count) else 0


def build_report(template_doc: Document, cadence: str, **ctx) -> Document:
	"""Create a new Draft report with snapshot of active checkpoints from template."""
	settings = get_settings()
	report = frappe.new_doc("POS Monitoring Report")
	report.template = template_doc.name
	report.cadence = cadence
	report.company = ctx.get("company")
	report.pos_profile = ctx.get("pos_profile")
	report.opening_shift = ctx.get("opening_shift")
	report.operational_date = ctx.get("operational_date")
	report.due_at = ctx.get("due_at")
	report.status = "Draft"
	report.revision = 1
	report.obligation_key = ctx.get("obligation_key") or obligation_key(
		template_doc.name,
		cadence,
		opening_shift=ctx.get("opening_shift"),
		company=ctx.get("company"),
		operational_date=ctx.get("operational_date"),
	)

	for row in template_doc.checkpoints:
		cp = frappe.get_cached_doc("POS Monitoring Checkpoint", row.checkpoint)
		if cp.disabled:
			continue
		pass_label, fail_label, na_label = resolve_labels(cp.category)
		video_sec = cp.video_max_seconds or settings.video_max_seconds
		report.append("checkpoints", {
			"checkpoint": cp.name,
			"checkpoint_name": cp.checkpoint_name,
			"category": cp.category,
			"instruction": cp.instruction,
			"media_type": cp.media_type,
			"min_count": cp.min_count,
			"max_count": cp.max_count,
			"video_max_seconds": video_sec,
			"required": row.required,
			"pass_label": pass_label,
			"fail_label": fail_label,
			"na_label": na_label,
			"evidence_complete": 0,
			"assessment": "Pending",
		})
	return report


def ensure_obligations(pos_profile=None, now=None) -> list[str]:
	"""Idempotently create required monitoring report drafts."""
	settings = get_settings()
	if not settings.enabled:
		return []

	today_date = getdate(now) if now else getdate(today())
	created_reports = []

	# 1. Per Shift
	shift_filters = {"status": "Open"}
	if pos_profile:
		shift_filters["pos_profile"] = pos_profile
	open_shifts = frappe.get_all(
		"POS Opening Shift",
		filters=shift_filters,
		fields=["name", "company", "pos_profile", "posting_date", "period_start_date", "shift_slot"],
	)

	shift_templates = frappe.get_all(
		"POS Monitoring Template",
		filters={"cadence": "Per Shift", "enabled": 1},
		pluck="name",
	)
	for t_name in shift_templates:
		t_doc = frappe.get_doc("POS Monitoring Template", t_name)
		target_companies = {r.company for r in t_doc.companies}
		if not target_companies:
			continue
		target_profiles = {r.pos_profile for r in t_doc.pos_profiles}
		target_shifts = {r.shift_type for r in t_doc.shift_types}

		for shift in open_shifts:
			if shift.company not in target_companies:
				continue
			if target_profiles and shift.pos_profile not in target_profiles:
				continue
			if target_shifts and shift.shift_slot not in target_shifts:
				continue

			key = obligation_key(t_name, "Per Shift", opening_shift=shift.name)
			if frappe.db.exists("POS Monitoring Report", {"obligation_key": key, "revision": 1}):
				continue

			due_at = add_to_date(shift.period_start_date, minutes=cint(t_doc.due_minutes_after_open))
			doc = build_report(
				t_doc,
				"Per Shift",
				company=shift.company,
				pos_profile=shift.pos_profile,
				opening_shift=shift.name,
				operational_date=shift.posting_date,
				due_at=due_at,
				obligation_key=key,
			)
			try:
				frappe.db.savepoint("create_obligation_shift")
				doc.insert(ignore_permissions=True)
				created_reports.append(doc.name)
			except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
				frappe.db.rollback(save_point="create_obligation_shift")

	# 2. Per Hari
	day_templates = frappe.get_all(
		"POS Monitoring Template",
		filters={"cadence": "Per Hari", "enabled": 1},
		pluck="name",
	)
	target_profile_company = None
	if pos_profile:
		target_profile_company = frappe.db.get_value("POS Profile", pos_profile, "company")

	for t_name in day_templates:
		t_doc = frappe.get_doc("POS Monitoring Template", t_name)
		target_companies = [r.company for r in t_doc.companies]
		if not target_companies:
			continue
		if target_profile_company:
			target_companies = [c for c in target_companies if c == target_profile_company]

		for company in target_companies:
			key = obligation_key(t_name, "Per Hari", company=company, operational_date=today_date)
			if frappe.db.exists("POS Monitoring Report", {"obligation_key": key, "revision": 1}):
				continue

			due_time = t_doc.due_time or "23:59:59"
			due_at = get_datetime(f"{today_date} {due_time}")
			doc = build_report(
				t_doc,
				"Per Hari",
				company=company,
				operational_date=today_date,
				due_at=due_at,
				obligation_key=key,
			)
			try:
				frappe.db.savepoint("create_obligation_day")
				doc.insert(ignore_permissions=True)
				created_reports.append(doc.name)
			except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
				frappe.db.rollback(save_point="create_obligation_day")

	return created_reports


def mark_missed(now=None, names: list[str] | None = None) -> list[str]:
	"""Submit drafts whose submission window has expired as Missed."""
	settings = get_settings()
	if not settings.enabled:
		return []

	now_dt = get_datetime(now) if now else now_datetime()
	filters = {"docstatus": 0, "status": "Draft"}
	if names:
		filters["name"] = ["in", names]

	drafts = frappe.get_all(
		"POS Monitoring Report",
		filters=filters,
		fields=["name", "cadence", "opening_shift", "due_at", "operational_date", "revision", "correction_of"],
	)
	missed_names = []
	grace_hours = cint(settings.shift_missed_grace_hours or 12)
	day_missed_time = str(settings.day_missed_next_day_time or "02:00:00")

	for draft in drafts:
		is_missed = False
		is_revision = cint(draft.get("revision", 1)) > 1 or bool(draft.get("correction_of"))
		if draft.cadence == "Per Shift":
			if not is_revision and draft.opening_shift:
				shift_status = frappe.db.get_value("POS Opening Shift", draft.opening_shift, "status")
				if shift_status == "Closed":
					is_missed = True
			if not is_missed and draft.due_at:
				cutoff = add_to_date(draft.due_at, hours=grace_hours)
				if now_dt > cutoff:
					is_missed = True
		elif draft.cadence == "Per Hari":
			if is_revision:
				# Spec §5.2: Revisions get a fresh submission window (due_at + grace_hours),
				# rather than inheriting the original report's operational_date cutoff.
				if draft.due_at:
					cutoff = add_to_date(draft.due_at, hours=grace_hours)
					if now_dt > cutoff:
						is_missed = True
			elif draft.operational_date:
				next_day = add_days(draft.operational_date, 1)
				cutoff = get_datetime(f"{next_day} {day_missed_time}")
				if now_dt > cutoff:
					is_missed = True

		if is_missed:
			doc = frappe.get_doc("POS Monitoring Report", draft.name)
			doc.status = "Missed"
			doc.flags.ignore_permissions = True
			doc.flags.ignore_mandatory = True
			doc.submit()
			missed_names.append(doc.name)

	return missed_names


def purge_expired_media(today=None) -> int:
	"""Purge media files older than retention policy from terminal reports."""
	settings = get_settings()
	if not settings.enabled:
		return 0

	retention_days = cint(settings.media_retention_days)
	if retention_days <= 0:
		return 0

	ref_date = getdate(today) if today else getdate()
	cutoff = add_days(ref_date, -retention_days)

	expired_media = frappe.db.sql(
		"""
		select m.name, m.file_url, m.thumbnail_url
		from `tabPOS Monitoring Report Media` m
		inner join `tabPOS Monitoring Report` r on r.name = m.parent
		where r.docstatus = 1
		  and r.status in ('Reviewed', 'Missed', 'Waived')
		  and m.purged = 0
		  and date(m.uploaded_at) < %(cutoff)s
		""",
		{"cutoff": cutoff},
		as_dict=True,
	)

	deleted_count = 0
	for row in expired_media:
		row_success = True
		for url in (row.file_url, row.thumbnail_url):
			if url:
				file_name = frappe.db.get_value("File", {"file_url": url}, "name")
				if file_name:
					try:
						frappe.flags.internal_monitoring_delete = True
						frappe.delete_doc("File", file_name, ignore_permissions=True, force=True)
					except Exception as e:
						frappe.log_error(f"Failed to delete File {file_name} during retention purge: {e}")
						row_success = False
					finally:
						frappe.flags.internal_monitoring_delete = False

		if row_success:
			frappe.db.set_value("POS Monitoring Report Media", row.name, "purged", 1, update_modified=False)
			deleted_count += 1

	return deleted_count
