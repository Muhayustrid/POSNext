# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Shift schedule enforcement.

HQ configures optional shift hours on a POS Profile. At open time the schedule
is snapshotted onto the POS Opening Shift and re-resolved authoritatively at
submit; afterwards the snapshot is frozen (client edits to the fields are
discarded — only a direct frappe.db.set_value by an admin can extend a
deadline). While mandatory closing is enabled, sales / payments / refunds are
rejected server-side once the snapshot deadline passes (site timezone, server
clock) — the closing shift itself is never gated. Admission policy for
invoices (server-authoritative, no client timestamps): a transaction whose
server-set `creation` predates the deadline is still admitted at submit
(printed drafts settled during closing, stale drafts), anything created after
it is rejected (fail-closed). Schedule off (the default) changes nothing.
"""

import datetime

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime

SCHEDULE_FIELDS = (
    "pos_schedule_enabled",
    "pos_schedule_start",
    "pos_schedule_end",
    "pos_schedule_warning_minutes",
    "pos_schedule_enforce_closing",
)

GATE_TITLE = _("Shift Schedule")


def validate_group_link(group):
	"""A profile's linked group must exist.

	Groups are company-neutral schedule templates: any profile may follow any
	group, whatever its company.
	"""
	if not group:
		return
	if frappe.db.get_value("POS Profile Group", group) is None:
		frappe.throw(
			_("POS Profile Group {0} does not exist").format(group),
			title=GATE_TITLE,
		)


def get_effective_schedule(pos_profile):
	"""Return the schedule of a POS Profile (profile-level only).

	The Shift Group (POS Profile Group doctype) pushes its hours onto member
	profiles at group-save time, so the profile row stays the single source
	of truth read at shift open.
	"""
	values = frappe.db.get_value(
		"POS Profile", pos_profile, ("name", *SCHEDULE_FIELDS, "pos_profile_group"), as_dict=True
	)
	if not values:
		frappe.throw(_("POS Profile {0} does not exist").format(pos_profile))

	validate_group_link(values.pos_profile_group)
	validate_schedule_values(values)
	return values


def validate_schedule_values(schedule, label=None):
	"""Reject a broken schedule early: malformed times, negative warning.

	`label` names the failing document in the error (a POS Profile by
	default; the Shift Group passes its own name because it pushes the same
	values down to every member).
	"""
	if not cint(schedule.pos_schedule_enabled):
		return

	start = _as_time_delta(schedule.pos_schedule_start)
	end = _as_time_delta(schedule.pos_schedule_end)
	if start is None or end is None:
		frappe.throw(
			_("{0}: shift schedule is enabled but the start/end times are missing or invalid.").format(
				label or f"POS Profile {schedule.name}"
			),
			title=GATE_TITLE,
		)

	if cint(schedule.pos_schedule_warning_minutes) < 0:
		frappe.throw(
			_("Schedule Warning Minutes must be zero or greater."),
			title=GATE_TITLE,
		)


def resolve_window(now_dt, start_time, end_time):
	"""Return the (start, end) datetimes of the schedule window containing
	`now_dt`, else None.

	An overnight window (end <= start) runs into the next calendar day, so the
	window started on the previous day is also considered.
	"""
	start_time = _as_time_delta(start_time)
	end_time = _as_time_delta(end_time)
	if start_time is None or end_time is None:
		return None

	overnight = end_time <= start_time
	midnight = datetime.datetime.combine(now_dt.date(), datetime.time())
	for day_offset in (0, -1):
		day_start = midnight + datetime.timedelta(days=day_offset)
		window_start = day_start + start_time
		window_end = day_start + end_time
		if overnight:
			window_end += datetime.timedelta(days=1)
		if window_start <= now_dt < window_end:
			return window_start, window_end

	return None


def apply_schedule_snapshot(opening):
	"""Snapshot the effective schedule onto a POS Opening Shift.

	Runs on insert AND on submit, so the deadline always matches the profile
	at the moment the shift actually opens (a draft held overnight and
	submitted after hours is rejected, same as a fresh open). Schedule field
	edits made by the client between insert and submit are overwritten here.
	"""
	schedule = get_effective_schedule(opening.pos_profile)

	for field in SCHEDULE_FIELDS:
		setattr(opening, field, schedule[field])

	opening.pos_schedule_deadline = None
	if not cint(schedule.pos_schedule_enabled):
		return

	now = now_datetime()
	window = resolve_window(now, schedule.pos_schedule_start, schedule.pos_schedule_end)
	if window:
		opening.pos_schedule_deadline = window[1]
	elif cint(schedule.pos_schedule_enforce_closing):
		frappe.throw(
			_("{0} is outside the scheduled shift hours ({1} – {2}). A shift cannot be opened now.").format(
				now.strftime("%H:%M"),
				schedule.pos_schedule_start,
				schedule.pos_schedule_end,
			),
			title=GATE_TITLE,
		)


def freeze_schedule_snapshot(opening):
	"""Discard client edits to the snapshot on any save of an existing shift.

	Runs in validate for every non-first save, so the frozen deadline cannot
	be tampered with through the API or Desk once the shift exists. Admin
	recovery (extending a deadline) must use frappe.db.set_value, which
	bypasses this deliberately.
	"""
	if not opening.name or not frappe.db.exists("POS Opening Shift", opening.name):
		return

	stored = frappe.db.get_value(
		"POS Opening Shift", opening.name, (*SCHEDULE_FIELDS, "pos_schedule_deadline"), as_dict=True
	)
	for field in (*SCHEDULE_FIELDS, "pos_schedule_deadline"):
		setattr(opening, field, stored[field])


def get_shift_gate(opening_shift):
	"""Return enforcement info for an open shift, or None when sales are free."""
	if not opening_shift or not frappe.db.exists("POS Opening Shift", opening_shift):
		return None

	row = frappe.db.get_value(
		"POS Opening Shift",
		opening_shift,
		("pos_schedule_enabled", "pos_schedule_enforce_closing", "pos_schedule_deadline", "docstatus"),
		as_dict=True,
	)
	if (
		not row
		or row.docstatus != 1
		or not cint(row.pos_schedule_enabled)
		or not cint(row.pos_schedule_enforce_closing)
		or not row.pos_schedule_deadline
	):
		return None

	deadline = get_datetime(row.pos_schedule_deadline)
	return {
		"opening_shift": opening_shift,
		"deadline": deadline,
		"expired": now_datetime() >= deadline,
	}


def _gate_shifts(shift, profile):
	"""Return the open shift(s) a transaction must pass.

	The shift field is client-supplied, so it is only trusted when it points at
	an open shift of the transaction's own profile. Missing or tampered values
	(closed/cancelled shift, another profile's shift) fall back to every open
	shift of the profile: if any of them is past a mandatory deadline the
	transaction is gated. Deadlines are identical for shifts opened in the same
	window, so this can only over-block, never under-block (fail-closed).
	"""
	if shift:
		row = frappe.db.get_value(
			"POS Opening Shift", shift, ("pos_profile", "docstatus", "status"), as_dict=True
		)
		if row and row.docstatus == 1 and row.status == "Open" and (not profile or row.pos_profile == profile):
			return [shift]
	if not profile:
		return []
	return frappe.get_all(
		"POS Opening Shift",
		filters={"pos_profile": profile, "docstatus": 1, "status": "Open"},
		pluck="name",
	)


def assert_sales_allowed(opening_shift):
	"""Reject sales / payments / refunds at or past a mandatory deadline.

	Closing is intentionally not gated: the cashier must always be able to
	close the shift and count actual cash. Nothing is auto-submitted — the
	regular POS Closing Shift flow stays manual.
	"""
	gate = get_shift_gate(opening_shift)
	if gate and gate["expired"]:
		frappe.throw(
			_(
				"Shift {0} ended at {1}. Closing is required — new sales, payments and refunds are no longer accepted. Please close the shift with the actual cash count."
			).format(opening_shift, gate["deadline"].strftime("%H:%M")),
			title=GATE_TITLE,
		)


def assert_invoice_sales_allowed(invoice_name, doctype="Sales Invoice"):
	"""Gate helper for payment APIs that operate on an existing invoice.

	Payments and refunds after the deadline are always rejected — there is no
	creation-time grace for new financial movements.
	"""
	if not invoice_name or not frappe.db.exists(doctype, invoice_name):
		return
	if not (
		frappe.db.has_column(doctype, "posa_pos_opening_shift") and frappe.db.has_column(doctype, "pos_profile")
	):
		return
	shift, profile = frappe.db.get_value(doctype, invoice_name, ("posa_pos_opening_shift", "pos_profile"))
	for gate_shift in _gate_shifts(shift, profile):
		assert_sales_allowed(gate_shift)


def validate_invoice(doc, method=None):
	"""doc_events validate hook: block submitting sales/return invoices whose
	mandatory deadline has passed, whatever entrypoint drove the submit.

	Admission is judged by the server clock only: a transaction created
	(doc.creation) before the deadline is admitted — printed drafts settled by
	the closing flow and stale drafts stay submittable; anything created after
	the deadline is rejected (fail-closed). `posa_pos_opening_shift` edits by
	the client are neutralized via _gate_shifts.
	"""
	if doc.get("is_consolidated"):
		return
	if doc.docstatus != 1:
		return
	shift = doc.get("posa_pos_opening_shift") or doc.get("pos_opening_shift")
	profile = doc.get("pos_profile")
	if not shift and not profile:
		return

	creation = get_datetime(doc.creation) if getattr(doc, "creation", None) else None
	for gate_shift in _gate_shifts(shift, profile):
		gate = get_shift_gate(gate_shift)
		if gate and gate["expired"]:
			if creation and creation <= gate["deadline"]:
				continue
			frappe.throw(
				_(
					"Shift {0} ended at {1}. Closing is required — new sales, payments and refunds are no longer accepted. Please close the shift with the actual cash count."
				).format(gate_shift, gate["deadline"].strftime("%H:%M")),
				title=GATE_TITLE,
			)


def validate_group_membership(doc):
	"""The Shift Group's Members table is the single source of membership.

	A `pos_profile_group` link without a matching row in that group's Members
	table would falsely claim membership: the next group save would not sync
	it (and would unlink it outright), and it never received the group's
	hours. Reject that state at profile save — membership is edited on the
	Shift Group, the profile field is a read-only mirror.
	"""
	group = doc.get("pos_profile_group")
	if not group:
		return
	name = doc.get("name") or ""
	if not name or str(name).startswith("new-pos-profile"):
		return
	if not frappe.db.exists(
		"POS Profile Group Member",
		{"parent": group, "parenttype": "POS Profile Group", "pos_profile": doc.name},
	):
		frappe.throw(
			_("POS Profile {0} is not in the Members table of Shift Group {1}. Manage membership from the Shift Group.").format(
				doc.name, group
			),
			title=GATE_TITLE,
		)


def validate_profile_schedule(doc, method=None):
	"""doc_events validate hook on POS Profile: validate the schedule settings
	and the profile-group link at save time, not only when a shift opens."""
	validate_group_link(doc.get("pos_profile_group"))
	validate_group_membership(doc)
	validate_schedule_values(doc)


@frappe.whitelist()
def extend_deadline(opening_shift, new_deadline):
	"""HQ recovery: push a mandatory deadline forward on an open shift.

	The supported, audited alternative to a raw `frappe.db.set_value` bypass —
	e.g. to let offline invoices stranded by the deadline sync before closing.
	Forward-only, System Manager only, writes a Comment for the audit trail.
	"""
	if "System Manager" not in frappe.get_roles():
		frappe.throw(_("Only System Managers can extend a shift schedule deadline"), frappe.PermissionError)

	row = frappe.db.get_value(
		"POS Opening Shift",
		opening_shift,
		("pos_schedule_enabled", "pos_schedule_enforce_closing", "pos_schedule_deadline", "docstatus"),
		as_dict=True,
	)
	if (
		not row
		or row.docstatus != 1
		or not cint(row.pos_schedule_enabled)
		or not cint(row.pos_schedule_enforce_closing)
	):
		frappe.throw(_("Shift {0} has no mandatory schedule deadline").format(opening_shift), title=GATE_TITLE)

	new_dt = get_datetime(new_deadline)
	if row.pos_schedule_deadline and new_dt <= get_datetime(row.pos_schedule_deadline):
		frappe.throw(
			_("New deadline {0} must be after the current deadline {1}").format(
				new_dt.strftime("%Y-%m-%d %H:%M"), get_datetime(row.pos_schedule_deadline).strftime("%Y-%m-%d %H:%M")
			),
			title=GATE_TITLE,
		)

	frappe.db.set_value("POS Opening Shift", opening_shift, "pos_schedule_deadline", new_dt)
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "POS Opening Shift",
			"reference_name": opening_shift,
			"content": _("Shift schedule deadline extended to {0} by {1}").format(
				new_dt.strftime("%Y-%m-%d %H:%M"), frappe.session.user
			),
		}
	).insert(ignore_permissions=True)
	return {"opening_shift": opening_shift, "deadline": str(new_dt)}


def _as_time_delta(value):
	if isinstance(value, datetime.timedelta):
		return value
	if isinstance(value, datetime.time):
		return datetime.timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)
	if isinstance(value, str) and value.count(":") >= 2:
		try:
			hours, minutes, seconds = (int(p) for p in value.split(":")[:3])
		except ValueError:
			return None
		if not (0 <= hours <= 23 and 0 <= minutes <= 59 and 0 <= seconds <= 59):
			return None
		return datetime.timedelta(hours=hours, minutes=minutes, seconds=seconds)
	return None
