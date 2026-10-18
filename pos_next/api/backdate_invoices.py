# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""HO backdate entry lane.

Flow: cancel a shift's POS Closing Shift (the opening goes back to Open),
record a backdated sale or return on that shift through the regular invoice
API, then re-close via the normal closing flow (totals recompute from every
submitted invoice on the shift).

Every entrypoint here is gated on an HQ role — System Manager, POSNext
Manager, or the native HQ personas Sales Manager / Accounts Manager — AND the
profile's POS Settings ``allow_change_posting_date`` flag. ``submit_backdate_invoice`` sets the
request-scoped ``frappe.flags.pos_next_backdate_entry`` marker, which is the
only thing the posting-date policy (api/invoices.py), the shift schedule gate
(shift_schedule.py) and the return validity check honour — a client cannot
forge server-side flags.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate, now_datetime, nowdate, nowtime

from pos_next.api.settings_resolver import get_effective_pos_setting

BACKDATE_ROLES = ("System Manager", "POSNext Manager", "Sales Manager", "Accounts Manager")


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


def _reopen(pos_opening_shift):
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

	closing_doc = frappe.get_doc("POS Closing Shift", closing_name)
	closing_doc.flags.ignore_permissions = True
	closing_doc.ignore_linked_doctypes = ["POS Backdate Entry"]
	closing_doc.cancel()

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
def reopen_shift(pos_opening_shift):
	"""Cancel the shift's submitted POS Closing Shift; the opening goes back to
	Open. POS Closing Shift is a non-accounts document — on_cancel clears the
	invoice links so they stay editable, Payment Entries stay submitted — and
	re-closing later recomputes all totals from the submitted invoices.
	"""
	return _reopen(pos_opening_shift)


def _submit_backdated(invoice: dict):
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
	posting_time = invoice.get("posting_time") or nowtime()

	shift_start_dt = get_datetime(shift.period_start_date or shift.posting_date)
	now_dt = now_datetime()

	try:
		posting_datetime = get_datetime(f"{posting_date} {posting_time}")
	except Exception:
		posting_datetime = None

	if not posting_datetime or posting_datetime < shift_start_dt or posting_datetime > now_dt:
		frappe.throw(
			_("Backdated posting date {0} must fall within the shift period ({1} to today)").format(
				posting_datetime or posting_date, shift_start_dt
			),
			title=_("Backdate Entry"),
		)

	# Server-side authority: the shift owns the profile, the posting date and
	# the posting-time override — payload values never decide these.
	invoice["pos_profile"] = shift.pos_profile
	invoice["posting_date"] = str(posting_date)
	invoice["posting_time"] = str(posting_time)
	invoice["set_posting_time"] = 1

	from pos_next.api.invoices import submit_invoice

	prev_flag = getattr(frappe.flags, "pos_next_backdate_entry", None)
	frappe.flags.pos_next_backdate_entry = True
	try:
		return submit_invoice(invoice=json.dumps(invoice), data="{}")
	finally:
		frappe.flags.pos_next_backdate_entry = prev_flag


@frappe.whitelist()
def submit_backdate_invoice(invoice):
	"""Submit a backdated sale or return on a reopened shift via the regular
	invoice API. The shift link comes from the payload but the profile, the
	posting date and set_posting_time are decided server-side from the shift;
	the posting date must fall inside the shift's own period (through today).
	"""
	return _submit_backdated(invoice)


@frappe.whitelist()
def get_shift_context(pos_opening_shift):
	"""Context for POS Backdate Entry: shift details, invoices, payment modes,
	default customer, confirmation code gate, and old closing counts."""
	shift = frappe.db.get_value(
		"POS Opening Shift",
		pos_opening_shift,
		[
			"name",
			"company",
			"pos_profile",
			"user",
			"period_start_date",
			"period_end_date",
			"posting_date",
			"pos_closing_shift",
		],
		as_dict=True,
	)
	if not shift:
		frappe.throw(_("POS Opening Shift {0} not found").format(pos_opening_shift))

	_check_backdate_access(shift.pos_profile)

	pos_profile_doc = frappe.get_cached_doc("POS Profile", shift.pos_profile)
	default_customer = pos_profile_doc.customer
	payment_modes = [p.mode_of_payment for p in (pos_profile_doc.payments or [])]

	from pos_next.api.discount_code import get_status

	code_gate = get_status(company=shift.company)

	closing_counts = []
	if shift.pos_closing_shift:
		closing_counts = frappe.get_all(
			"POS Closing Shift Detail",
			filters={"parent": shift.pos_closing_shift},
			fields=["mode_of_payment", "opening_amount", "expected_amount", "closing_amount", "difference"],
			order_by="idx",
		)

	from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import get_pos_invoices

	prev_flag = getattr(frappe.flags, "pos_next_backdate_entry", None)
	frappe.flags.pos_next_backdate_entry = True
	try:
		invoices = get_pos_invoices(pos_opening_shift)
	finally:
		frappe.flags.pos_next_backdate_entry = prev_flag

	for inv in invoices:
		inv_name = inv.get("name")
		inv["has_return"] = bool(
			frappe.db.exists("POS Invoice", {"return_against": inv_name, "docstatus": 1})
			or frappe.db.exists("Sales Invoice", {"return_against": inv_name, "docstatus": 1})
		)

	shift_period = f"{shift.period_start_date} - {shift.period_end_date or now_datetime()}"
	currency = frappe.get_cached_value("Company", shift.company, "default_currency")

	return {
		"shift": shift,
		"shift_period": shift_period,
		"default_customer": default_customer,
		"payment_modes": payment_modes,
		"code_gate": code_gate,
		"closing_counts": closing_counts,
		"shift_invoices": invoices,
		"warehouse": pos_profile_doc.warehouse,
		"currency": currency,
	}


@frappe.whitelist()
def get_item_rate(pos_opening_shift, item_code, uom=None, posting_date=None):
	"""Look up item rate and details using the profile's price list, warehouse and company."""
	pos_profile = (
		frappe.db.get_value("POS Opening Shift", pos_opening_shift, "pos_profile")
		if frappe.db.exists("POS Opening Shift", pos_opening_shift)
		else pos_opening_shift
	)
	_check_backdate_access(pos_profile)

	pos_profile_doc = frappe.get_cached_doc("POS Profile", pos_profile)
	item_doc = frappe.get_cached_doc("Item", item_code)

	item = {
		"item_code": item_code,
		"has_batch_no": item_doc.has_batch_no,
		"has_serial_no": item_doc.has_serial_no,
		"is_stock_item": item_doc.is_stock_item,
		"pos_profile": pos_profile,
		"qty": 1,
	}
	if uom:
		item["uom"] = uom

	from pos_next.api.items import get_item_detail

	details = get_item_detail(
		item=json.dumps(item),
		warehouse=pos_profile_doc.warehouse,
		price_list=pos_profile_doc.selling_price_list,
		company=pos_profile_doc.company,
	)

	if posting_date and pos_profile_doc.selling_price_list:
		from pos_next.api.invoices import _resolve_server_price_list_rate
		from pos_next.api.items import _fetch_uom_prices_map

		price_map = _fetch_uom_prices_map(
			[item_code], pos_profile_doc.selling_price_list, transaction_date=posting_date
		)
		if item_code in price_map:
			dated_price = _resolve_server_price_list_rate(
				price_map,
				{
					"item_code": item_code,
					"uom": uom or details.get("uom") or item_doc.stock_uom,
					"stock_uom": item_doc.stock_uom,
					"conversion_factor": details.get("conversion_factor") or 1,
				},
			)
			details["price_list_rate"] = dated_price
			details["rate"] = dated_price

	return details


def _resolve_invoice_profile(invoice, doctype=None):
	if doctype:
		return frappe.db.get_value(doctype, invoice, "pos_profile")
	profile = frappe.db.get_value("POS Invoice", invoice, "pos_profile")
	if profile is None:
		profile = frappe.db.get_value("Sales Invoice", invoice, "pos_profile")
	return profile


@frappe.whitelist()
def get_return_items(invoice, doctype=None):
	"""Return returnable items with remaining qty for an original invoice."""
	pos_profile = _resolve_invoice_profile(invoice, doctype)
	_check_backdate_access(pos_profile)
	return _get_return_items(invoice, doctype=doctype)


def _get_return_items(invoice, doctype=None):
	from pos_next.api.invoices import prepare_return_invoice

	current_user = frappe.session.user
	prev_ignore = frappe.flags.ignore_permissions
	prev_flag = getattr(frappe.flags, "pos_next_backdate_entry", None)
	frappe.flags.ignore_permissions = True
	frappe.flags.pos_next_backdate_entry = True
	try:
		if current_user != "Administrator":
			frappe.set_user("Administrator")
		return_data = prepare_return_invoice(invoice)
	finally:
		if current_user != "Administrator":
			frappe.set_user(current_user)
		frappe.flags.ignore_permissions = prev_ignore
		frappe.flags.pos_next_backdate_entry = prev_flag

	items = return_data.get("items") or []
	for item in items:
		item["against_item_row"] = (
			item.get("sales_invoice_item")
			or item.get("pos_invoice_item")
			or item.get("name")
		)
	return items


@frappe.whitelist()
def get_backdate_packages(pos_opening_shift, posting_date=None):
	"""Package definitions available for the shift's profile without profile membership check."""
	pos_profile = (
		frappe.db.get_value("POS Opening Shift", pos_opening_shift, "pos_profile")
		if frappe.db.exists("POS Opening Shift", pos_opening_shift)
		else pos_opening_shift
	)
	_check_backdate_access(pos_profile)

	from pos_next.api.packages import _get_packages

	return _get_packages(pos_profile, on_date=posting_date)


@frappe.whitelist()
def quote_backdate_package(pos_opening_shift, package, choices, posting_date=None):
	"""Quote a package selection for the shift's profile without profile membership check."""
	pos_profile = (
		frappe.db.get_value("POS Opening Shift", pos_opening_shift, "pos_profile")
		if frappe.db.exists("POS Opening Shift", pos_opening_shift)
		else pos_opening_shift
	)
	_check_backdate_access(pos_profile)

	from pos_next.api.packages import _parse_json, quote

	return quote(package, _parse_json(choices, []), pos_profile, posting_date=posting_date)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def pos_opening_shift_query(doctype, txt, searchfield, start, page_len, filters):
	"""Query closed POS Opening Shifts whose profile allows backdate entry."""
	if not has_backdate_role():
		frappe.throw(_("Not permitted to search backdate shifts"), frappe.PermissionError)

	filters = filters or {}
	company = filters.get("company")
	company_cond = "AND pos.company = %(company)s" if company else ""

	shifts = frappe.db.sql(
		f"""
		SELECT pos.name, pos.pos_profile, pos.period_start_date, pos.user
		FROM `tabPOS Opening Shift` pos
		WHERE pos.docstatus = 1
			AND pos.status = 'Closed'
			AND ifnull(pos.pos_closing_shift, '') != ''
			{company_cond}
			AND pos.name LIKE %(txt)s
		ORDER BY pos.period_start_date DESC
		LIMIT %(page_len)s OFFSET %(start)s
		""",
		{"company": company, "txt": f"%{txt}%", "page_len": cint(page_len) or 20, "start": cint(start) or 0},
		as_dict=True,
	)
	result = []
	for s in shifts:
		if allow_change_posting_date(s.pos_profile):
			result.append([s.name, f"{s.name} ({s.pos_profile})"])
	return result
