# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""HO backdate entry lane.

Flow: cancel a shift's POS Closing Shift (the opening goes back to Open),
record a backdated sale or return on that shift through the regular invoice
API, then re-close via the normal closing flow (totals recompute from every
submitted invoice on the shift).

Every entrypoint here is gated on an HQ role AND the profile's POS Settings
``allow_change_posting_date`` flag. ``submit_backdate_invoice`` sets the
request-scoped ``frappe.flags.pos_next_backdate_entry`` marker, which is the
only thing the posting-date policy (api/invoices.py), the shift schedule gate
(shift_schedule.py) and the return validity check honour — a client cannot
forge server-side flags.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from pos_next.api.settings_resolver import get_effective_pos_setting

BACKDATE_ROLES = ("System Manager", "Nexus POS Manager")


def has_backdate_role():
	"""Administrator passes outright (same convention as hq_monitoring)."""
	if frappe.session.user == "Administrator":
		return True
	return bool(set(frappe.get_roles()) & set(BACKDATE_ROLES))


def allow_change_posting_date(pos_profile):
	"""The enabled row decides, else the global single (field default is off)."""
	if not pos_profile:
		return False
	return bool(cint(get_effective_pos_setting(pos_profile, "allow_change_posting_date")))


def _check_backdate_access(pos_profile):
	"""HO gate: an allowed role AND the profile's allow_change_posting_date."""
	if not has_backdate_role():
		frappe.throw(_("Not permitted to enter backdated invoices"), frappe.PermissionError)
	if not allow_change_posting_date(pos_profile):
		frappe.throw(
			_("Backdate entry is disabled for POS Profile {0}").format(pos_profile),
			title=_("Backdate Entry"),
		)


@frappe.whitelist()
def get_access(pos_profile=None):
	"""SPA menu gating. Without a profile only the role is judged — the
	per-profile setting is checked per shift (get_backdate_context / submit)."""
	roles_ok = has_backdate_role()
	if not pos_profile:
		return {"roles_ok": roles_ok, "setting_on": None, "allowed": roles_ok}
	setting_on = allow_change_posting_date(pos_profile)
	return {"roles_ok": roles_ok, "setting_on": setting_on, "allowed": roles_ok and setting_on}


@frappe.whitelist()
def get_backdate_context(pos_profile=None, company=None):
	"""Closed shifts (with their submitted closing) that HO may reopen.

	``company`` narrows the shift query (the 100-most-recent window is per
	bench, so a secondary company needs the filter to be reliably represented).
	``companies`` is always the distinct set across ALL closed shifts so the
	Desk filter dropdown stays stable regardless of the window.
	"""
	if not has_backdate_role():
		frappe.throw(_("Not permitted to enter backdated invoices"), frappe.PermissionError)

	filters = {"docstatus": 1, "status": "Closed"}
	if pos_profile:
		filters["pos_profile"] = pos_profile
	if company:
		filters["company"] = company
	shifts = frappe.get_all(
		"POS Opening Shift",
		filters=filters,
		fields=[
			"name",
			"pos_profile",
			"company",
			"user",
			"period_start_date",
			"period_end_date",
			"posting_date",
			"pos_closing_shift",
		],
		order_by="period_start_date desc",
		limit_page_length=100,
	)
	for row in shifts:
		row["setting_on"] = allow_change_posting_date(row.pos_profile)
		row["closing_docstatus"] = (
			frappe.db.get_value("POS Closing Shift", row.pos_closing_shift, "docstatus")
			if row.pos_closing_shift
			else None
		)
	# companies come from POS Profiles (the outlets), not from closed shifts:
	# a company whose outlets have no closed shift yet must still be offered —
	# picking it explains itself via the empty-shift state. Companies without
	# any POS Profile can never be backdated and stay out of the dropdown.
	companies = frappe.get_all(
		"POS Profile",
		filters={"disabled": 0},
		pluck="company",
		group_by="company",
		order_by="company asc",
	)
	return {"shifts": shifts, "companies": companies, "today": nowdate()}


@frappe.whitelist()
def reopen_shift(pos_opening_shift):
	"""Cancel the shift's submitted POS Closing Shift; the opening goes back to
	Open. POS Closing Shift is a non-accounts document — on_cancel clears the
	invoice links so they stay editable, Payment Entries stay submitted — and
	re-closing later recomputes all totals from the submitted invoices.
	"""
	shift = frappe.db.get_value(
		"POS Opening Shift",
		pos_opening_shift,
		("pos_profile", "docstatus", "status", "pos_closing_shift"),
		as_dict=True,
	)
	if not shift:
		frappe.throw(_("POS Opening Shift {0} does not exist").format(pos_opening_shift))
	if shift.docstatus != 1 or shift.status != "Closed" or not shift.pos_closing_shift:
		frappe.throw(_("Shift {0} is not closed").format(pos_opening_shift), title=_("Backdate Entry"))

	_check_backdate_access(shift.pos_profile)

	closing_name = shift.pos_closing_shift
	if frappe.db.get_value("POS Closing Shift", closing_name, "docstatus") != 1:
		frappe.throw(
			_("POS Closing Shift {0} is not submitted").format(closing_name),
			title=_("Backdate Entry"),
		)

	frappe.get_doc("POS Closing Shift", closing_name).cancel()

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "POS Opening Shift",
			"reference_name": pos_opening_shift,
			"content": _(
				"POS Closing Shift {0} cancelled to reopen the shift for backdate entry by {1}"
			).format(closing_name, frappe.session.user),
		}
	).insert(ignore_permissions=True)

	return {
		"pos_opening_shift": pos_opening_shift,
		"cancelled_closing_shift": closing_name,
		"status": frappe.db.get_value("POS Opening Shift", pos_opening_shift, "status"),
	}


@frappe.whitelist()
def submit_backdate_invoice(invoice):
	"""Submit a backdated sale or return on a reopened shift via the regular
	invoice API. The shift link comes from the payload but the profile, the
	posting date and set_posting_time are decided server-side from the shift;
	the posting date must fall inside the shift's own period (through today).
	"""
	if isinstance(invoice, str):
		invoice = json.loads(invoice)
	if not isinstance(invoice, dict):
		frappe.throw(_("Invalid invoice format"))

	shift_name = invoice.get("posa_pos_opening_shift")
	shift = frappe.db.get_value(
		"POS Opening Shift",
		shift_name,
		("name", "pos_profile", "docstatus", "status", "period_start_date", "posting_date"),
		as_dict=True,
	)
	if not shift or shift.docstatus != 1 or shift.status != "Open":
		frappe.throw(
			_("Shift {0} must be open (reopened) before entering a backdated invoice").format(shift_name),
			title=_("Backdate Entry"),
		)

	_check_backdate_access(shift.pos_profile)

	if not invoice.get("posting_date"):
		frappe.throw(_("A posting date is required for backdate entry"), title=_("Backdate Entry"))
	posting_date = getdate(invoice.get("posting_date"))
	shift_start = getdate(shift.period_start_date or shift.posting_date)
	if posting_date < shift_start or posting_date > getdate(nowdate()):
		frappe.throw(
			_("Backdated posting date {0} must fall within the shift period ({1} to today)").format(
				posting_date, shift_start
			),
			title=_("Backdate Entry"),
		)

	# Server-side authority: the shift owns the profile, the posting date and
	# the posting-time override — payload values never decide these.
	invoice["pos_profile"] = shift.pos_profile
	invoice["posting_date"] = str(posting_date)
	invoice["set_posting_time"] = 1

	from pos_next.api.invoices import submit_invoice

	frappe.flags.pos_next_backdate_entry = True
	try:
		return submit_invoice(invoice=json.dumps(invoice), data="{}")
	finally:
		frappe.flags.pos_next_backdate_entry = None
