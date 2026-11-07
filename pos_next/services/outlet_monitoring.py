# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	get_datetime,
	get_time,
	getdate,
	now_datetime,
	today,
)


def get_settings() -> frappe._dict:
	"""Return POS Monitoring Settings as a dict."""
	return frappe.get_cached_doc("POS Monitoring Settings").as_dict()


def _active_labels(rows) -> list:
	"""Return label names from child rows, skipping disabled labels."""
	names = []
	for row in rows or []:
		name = row.label if hasattr(row, "label") else row.get("label")
		if not name or name in names:
			continue
		if frappe.db.get_value("POS Monitoring Label", name, "disabled"):
			continue
		names.append(name)
	return names


def resolve_label_options(checkpoint_doc) -> list:
	"""Resolve label options checkpoint -> category -> settings (§4.1)."""
	labels = _active_labels(getattr(checkpoint_doc, "labels", None))
	if not labels and getattr(checkpoint_doc, "category", None):
		cat = frappe.get_doc("POS Monitoring Category", checkpoint_doc.category)
		labels = _active_labels(getattr(cat, "labels", None))
	if not labels:
		settings = frappe.get_doc("POS Monitoring Settings")
		labels = _active_labels(getattr(settings, "default_labels", None))
	result = []
	for name in labels:
		color = frappe.db.get_value("POS Monitoring Label", name, "color") or "Gray"
		result.append({"label": name, "color": color})
	return result


# Kept for backward compatibility with callers outside Task 1-2 scope.
def resolve_labels(category=None) -> tuple[str, str, str]:
	settings = get_settings()
	defaults = [r.label for r in (settings.default_labels or [])]
	while len(defaults) < 3:
		defaults.append(defaults[0] if defaults else "Sesuai SOP")
	if not category:
		return tuple(defaults[:3])
	cat = frappe.get_cached_doc("POS Monitoring Category", category)
	names = [r.label for r in (cat.labels or [])] or defaults
	while len(names) < 3:
		names.append(names[0] if names else "Sesuai SOP")
	return tuple(names[:3])


def label_color(label_options, label) -> str | None:
	"""Resolve display color for a label from its snapshot (§5)."""
	if not label:
		return None
	options = label_options
	if isinstance(options, str):
		try:
			import json

			options = json.loads(options or "[]")
		except ValueError:
			return None
	for opt in options or []:
		if isinstance(opt, dict) and opt.get("label") == label:
			return opt.get("color")
	return None


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


def obligation_key(
	template: str,
	cadence: str,
	*,
	opening_shift=None,
	company=None,
	operational_date=None,
	doctype=None,
	name=None,
) -> str:
	"""Generate unique obligation key according to spec §4.4 (§4.8 for Manual)."""
	if cadence == "Per Shift":
		return f"{template}|shift|{opening_shift}"
	elif cadence == "Per Hari":
		return f"{template}|day|{company}|{operational_date}"
	elif cadence == "Manual":
		return f"{template}|manual|{doctype}|{name}"
	return f"{template}|{cadence}|{opening_shift or company}"


def compute_opens_at(t_doc, *, period_start=None, operational_date=None):
	"""Compute when a report becomes fillable (§4.9). None = fillable anytime."""
	if t_doc.cadence == "Per Shift":
		if period_start and cint(t_doc.open_after_minutes):
			return add_to_date(get_datetime(period_start), minutes=cint(t_doc.open_after_minutes))
		return None
	if t_doc.cadence == "Per Hari":
		if operational_date and t_doc.open_time:
			return get_datetime(f"{getdate(operational_date)} {t_doc.open_time}")
		return None
	return None


def assert_report_open(doc) -> None:
	"""Reject writes (media, cashier label, submit) before opens_at (§4.9)."""
	opens_at = doc.get("opens_at")
	if opens_at and now_datetime() < get_datetime(opens_at):
		frappe.throw(
			_("Report can be filled from {0}").format(frappe.utils.format_datetime(opens_at)),
			frappe.ValidationError,
		)


def template_companies(t_doc) -> list:
	"""Companies covered by a template: All = every company with active POS Profile."""
	if getattr(t_doc, "company_scope", None) == "All":
		return sorted(set(frappe.get_all("POS Profile", filters={"disabled": 0}, pluck="company")))
	return [r.company for r in t_doc.companies]


def profile_in_scope(t_doc, pos_profile) -> bool:
	"""Check pos_profile against template profile scope."""
	if getattr(t_doc, "profile_scope", None) != "Selected":
		return True
	return pos_profile in {r.pos_profile for r in t_doc.pos_profiles}


def shift_in_scope(t_doc, shift_slot) -> bool:
	"""Check shift slot against template shift scope."""
	if getattr(t_doc, "shift_type_scope", None) != "Selected":
		return True
	return shift_slot in {r.shift_type for r in t_doc.shift_types}


def recompute_evidence(doc: Document) -> None:
	"""Recompute evidence_complete on each checkpoint row from unpurged media."""
	media_list = getattr(doc, "media", [])
	for cp in doc.checkpoints:
		unpurged = [m for m in media_list if m.checkpoint_row == cp.name and not cint(m.purged)]
		cp.evidence_complete = 1 if len(unpurged) >= cint(cp.min_count) else 0


def build_report(template_doc: Document, cadence: str, **ctx) -> Document:
	"""Create a new Draft report with snapshot of active checkpoints from template."""
	import json

	settings = get_settings()
	report = frappe.new_doc("POS Monitoring Report")
	report.template = template_doc.name
	report.cadence = cadence
	report.company = ctx.get("company")
	report.pos_profile = ctx.get("pos_profile")
	report.opening_shift = ctx.get("opening_shift")
	report.operational_date = ctx.get("operational_date")
	report.due_at = ctx.get("due_at")
	report.opens_at = ctx.get("opens_at")
	report.reference_doctype = ctx.get("reference_doctype")
	report.reference_name = ctx.get("reference_name")
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
		options = resolve_label_options(cp)
		if not options:
			frappe.throw(
				_("Checkpoint {0} has no active labels").format(cp.name),
				frappe.ValidationError,
			)
		video_sec = cp.video_max_seconds or settings.video_max_seconds
		category = frappe.get_cached_doc("POS Monitoring Category", cp.category)
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
			"label_options": json.dumps(options),
			"cashier_can_label": category.cashier_can_label,
			"evidence_complete": 0,
		})
	report.flags.internal_report_build = True
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
		target_companies = set(template_companies(t_doc))

		for shift in open_shifts:
			if shift.company not in target_companies:
				continue
			if not profile_in_scope(t_doc, shift.pos_profile):
				continue
			if not shift_in_scope(t_doc, shift.shift_slot):
				continue

			key = obligation_key(t_name, "Per Shift", opening_shift=shift.name)
			if frappe.db.exists("POS Monitoring Report", {"obligation_key": key, "revision": 1}):
				continue

			due_at = add_to_date(shift.period_start_date, minutes=cint(t_doc.due_minutes_after_open))
			try:
				frappe.db.savepoint("create_obligation_shift")
				doc = build_report(
					t_doc,
					"Per Shift",
					company=shift.company,
					pos_profile=shift.pos_profile,
					opening_shift=shift.name,
					operational_date=shift.posting_date,
					due_at=due_at,
					opens_at=compute_opens_at(t_doc, period_start=shift.period_start_date),
					obligation_key=key,
				)
				doc.insert(ignore_permissions=True)
				created_reports.append(doc.name)
			except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
				frappe.db.rollback(save_point="create_obligation_shift")
			except Exception:
				frappe.db.rollback(save_point="create_obligation_shift")
				frappe.log_error(title="Outlet Monitoring obligation", message=frappe.get_traceback())

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
		target_companies = template_companies(t_doc)
		if target_profile_company:
			target_companies = [c for c in target_companies if c == target_profile_company]

		for company in target_companies:
			key = obligation_key(t_name, "Per Hari", company=company, operational_date=today_date)
			if frappe.db.exists("POS Monitoring Report", {"obligation_key": key, "revision": 1}):
				continue

			due_time = t_doc.due_time or "23:59:59"
			due_at = get_datetime(f"{today_date} {due_time}")
			try:
				frappe.db.savepoint("create_obligation_day")
				doc = build_report(
					t_doc,
					"Per Hari",
					company=company,
					operational_date=today_date,
					due_at=due_at,
					opens_at=compute_opens_at(t_doc, operational_date=today_date),
					obligation_key=key,
				)
				doc.insert(ignore_permissions=True)
				created_reports.append(doc.name)
			except (frappe.UniqueValidationError, frappe.DuplicateEntryError):
				frappe.db.rollback(save_point="create_obligation_day")
			except Exception:
				frappe.db.rollback(save_point="create_obligation_day")
				frappe.log_error(title="Outlet Monitoring obligation", message=frappe.get_traceback())

	return created_reports


# Rows one sweep branch may lock. Every branch gets its own budget, so a branch
# that is full cannot starve the others; the cron runs every 5 minutes, so any
# remainder is picked up by the next tick.
SWEEP_BATCH = 100


def _draft_is_expired(draft, now_dt, grace_hours, day_missed_time) -> bool:
	"""Expiry rules (spec §4.8/§5.2), evaluated on an already-locked row."""
	is_revision = cint(draft.get("revision", 1)) > 1 or bool(draft.get("correction_of"))
	if draft.cadence == "Per Shift":
		if not is_revision and draft.opening_shift:
			if frappe.db.get_value("POS Opening Shift", draft.opening_shift, "status") == "Closed":
				return True
		return bool(draft.due_at) and now_dt > add_to_date(draft.due_at, hours=grace_hours)
	if draft.cadence == "Per Hari":
		if is_revision:
			# Spec §5.2: Revisions get a fresh submission window (due_at + grace_hours),
			# rather than inheriting the original report's operational_date cutoff.
			return bool(draft.due_at) and now_dt > add_to_date(draft.due_at, hours=grace_hours)
		if draft.operational_date:
			next_day = add_days(draft.operational_date, 1)
			return now_dt > get_datetime(f"{next_day} {day_missed_time}")
		return False
	# Spec §4.8: Manual Missed bila now > due_at + grace, tanpa shift.
	return bool(draft.due_at) and now_dt > add_to_date(get_datetime(draft.due_at), hours=grace_hours)


def _day_cutoff_exclusive(now_dt, day_missed_time) -> object:
	"""Latest excluded operational_date for a plain Per Hari draft.

	Spec §4.8: a plain Per Hari draft expires at `operational_date + 1 day`
	`day_missed_time`. Solving that for the date keeps the branch an indexed
	range instead of comparing a computed datetime per row.
	"""
	day = getdate(now_dt)
	if now_dt.time() > get_time(day_missed_time):
		# After today's cutoff, yesterday's reports are already late.
		return day
	return add_days(day, -1)


def _locked_sweep_candidates(now_dt, grace_hours, day_missed_time) -> list:
	"""Lock only rows that are expired right now, bounded per branch.

	Five locking statements run per tick, each bounded to SWEEP_BATCH, yielding
	up to 500 candidate result rows before deduplication (a result budget, not a
	physical-lock guarantee). Each branch predicate targets an eligible range:
	1. Per Shift (original) on closed shifts (via om_sweep_shift_idx)
	2. Per Shift (due+grace) with due_at < cutoff (via om_sweep_due_idx)
	3. Manual (due+grace) with due_at < cutoff (via om_sweep_due_idx)
	4. Per Hari revisions with due_at < cutoff (§5.2 fresh window)
	5. Per Hari plain drafts with operational_date < day_cutoff (via om_sweep_day_idx)

	Per-branch budgets ensure a full branch cannot starve the others; unhandled
	rows are picked up on the next 5-minute tick.

	A plain pre-read cannot be used instead of the locking read: locking a row
	this transaction already read fails with ER_CHECKREAD once another
	transaction has committed it.
	"""
	fields = "name, cadence, opening_shift, due_at, operational_date, revision, correction_of"
	time_cutoff = add_to_date(now_dt, hours=-grace_hours)
	day_cutoff = _day_cutoff_exclusive(now_dt, day_missed_time)
	not_revision = "revision <= 1 and ifnull(correction_of, '') = ''"
	is_revision = f"not ({not_revision})"
	rows: dict[str, object] = {}

	def lock(predicate, values, hint="", order="due_at"):
		result = frappe.db.sql(
			f"""select {fields} from `tabPOS Monitoring Report` {hint}
			where docstatus = 0 and status = 'Draft' and {predicate}
			order by {order} limit {SWEEP_BATCH} for update""",
			values,
			as_dict=True,
		)
		for row in result:
			rows.setdefault(row.name, row)

	# Per Shift closes on the shift itself, whatever the template due window is
	# now: due_at is a snapshot, so a later template change must not move the
	# close-shift deadline of a report that already exists. Historical original
	# reports with NULL due_at are included so closed shifts expire them. The
	# shift list is derived from the reports' own snapshot due_at (or null), never
	# from a template, and `opening_shift in (...)` narrows the index range
	# itself — an EXISTS would be applied after the scan, and FOR UPDATE locks
	# every row it reads.
	closed_shifts = frappe.db.sql(
		f"""select distinct r.opening_shift from `tabPOS Monitoring Report` r
		inner join `tabPOS Opening Shift` s on s.name = r.opening_shift and s.status = 'Closed'
		where r.docstatus = 0 and r.status = 'Draft' and r.cadence = 'Per Shift'
			and r.{not_revision} and r.opening_shift > '' and (r.due_at is null or r.due_at >= %(cutoff)s)
		limit {SWEEP_BATCH}""",
		{"cutoff": time_cutoff},
		pluck=True,
	)
	if closed_shifts:
		lock(
			f"cadence = 'Per Shift' and {not_revision} and opening_shift in %(shifts)s",
			{"shifts": tuple(closed_shifts)},
			hint="force index (om_sweep_shift_idx)",
			order="opening_shift",
		)
	# Due + grace. One cadence per statement: `cadence in (...)` makes the
	# planner skip the index and lock the whole table.
	for cadence in ("Per Shift", "Manual"):
		lock(
			"cadence = %(cadence)s and due_at < %(cutoff)s",
			{"cadence": cadence, "cutoff": time_cutoff},
		)
	# Per Hari revisions also follow due + grace (§5.2 fresh window) ...
	lock(
		f"cadence = 'Per Hari' and {is_revision} and due_at < %(cutoff)s",
		{"cutoff": time_cutoff},
	)
	# ... while a plain Per Hari draft follows its next-day cutoff.
	lock(
		f"cadence = 'Per Hari' and {not_revision} and operational_date < %(day)s",
		{"day": day_cutoff},
		hint="force index (om_sweep_day_idx)",
		order="operational_date",
	)

	return list(rows.values())


def mark_missed(now=None, names: list[str] | None = None) -> list[str]:
	"""Submit drafts whose submission window has expired as Missed."""
	settings = get_settings()
	if not settings.enabled:
		return []

	now_dt = get_datetime(now) if now else now_datetime()
	grace_hours = cint(settings.shift_missed_grace_hours or 12)
	day_missed_time = str(settings.day_missed_next_day_time or "02:00:00")

	if names:
		# for_update on the FIRST read: locking after a plain read of the same
		# row fails with ER_CHECKREAD once a concurrent setter has committed.
		candidates = frappe.db.get_values(
			"POS Monitoring Report",
			filters={"name": ["in", names], "docstatus": 0, "status": "Draft"},
			fieldname=[
				"name",
				"cadence",
				"opening_shift",
				"due_at",
				"operational_date",
				"revision",
				"correction_of",
			],
			as_dict=True,
			for_update=True,
		)
	else:
		candidates = _locked_sweep_candidates(now_dt, grace_hours, day_missed_time)

	missed_names = []
	for draft in candidates:
		# Re-read under the lock and revalidate everything on that row: the
		# candidate filters above are only a bounded prefilter, never the
		# decision (a column they do not touch, e.g. operational_date, can still
		# make the row ineligible).
		doc = frappe.get_doc("POS Monitoring Report", draft.name)
		if doc.docstatus != 0 or doc.status != "Draft":
			continue
		if not _draft_is_expired(doc, now_dt, grace_hours, day_missed_time):
			continue
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
