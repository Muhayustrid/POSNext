# Copyright (c) 2024, POS Next and contributors
# For license information, please see license.txt


import json

import frappe
from frappe import _
from frappe.utils import nowdate, nowtime, get_datetime, flt, cint
from pos_next.api.utilities import get_wallet_payment_modes
from pos_next.invoice_type import sales_invoice_item_union, sales_invoice_union


@frappe.whitelist()
def get_opening_dialog_data():
	"""Get data required for opening shift dialog"""
	data = {}

	# Get POS Profiles where current user is defined in POS Profile User table
	pos_profiles_data = frappe.db.sql(
		"""
		SELECT DISTINCT p.name, p.company, p.currency, p.warehouse, p.selling_price_list
		FROM `tabPOS Profile` p
		INNER JOIN `tabPOS Profile User` u ON u.parent = p.name
		WHERE p.disabled = 0 AND u.user = %s
		ORDER BY p.name
		""",
		frappe.session.user,
		as_dict=1,
	)

	data["pos_profiles_data"] = pos_profiles_data

	# Derive companies from accessible POS Profiles
	company_names = []
	for profile in pos_profiles_data:
		if profile.company and profile.company not in company_names:
			company_names.append(profile.company)
	data["companies"] = [{"name": c} for c in company_names]

	# Get payment methods for POS profiles (exclude wallet payment methods)
	pos_profiles_list = [p.name for p in pos_profiles_data]

	if pos_profiles_list:
		# Exclude wallet payment modes from opening balance
		wallet_modes = get_wallet_payment_modes()

		payment_filters = {"parent": ["in", pos_profiles_list]}
		if wallet_modes:
			payment_filters["mode_of_payment"] = ["not in", wallet_modes]

		data["payments_method"] = frappe.get_list(
			"POS Payment Method",
			filters=payment_filters,
			fields=["*"],
			limit_page_length=0,
			order_by="parent",
			ignore_permissions=True,
		)

		# Set currency from pos profile
		for mode in data["payments_method"]:
			mode["currency"] = frappe.get_cached_value("POS Profile", mode["parent"], "currency")
	else:
		data["payments_method"] = []

	return data


@frappe.whitelist()
def check_opening_shift(user=None):
	"""Check if user has an open shift"""
	if not user:
		user = frappe.session.user

	open_shifts = frappe.db.get_all(
		"POS Opening Shift",
		filters={
			"user": user,
			"pos_closing_shift": ["is", "not set"],
			"docstatus": 1,
			"status": "Open",
		},
		fields=["name", "pos_profile", "period_start_date"],
		order_by="period_start_date desc",
	)

	if not open_shifts:
		return None

	# Get the latest open shift
	shift_data = open_shifts[0]
	data = {}
	data["pos_opening_shift"] = frappe.get_doc("POS Opening Shift", shift_data["name"])
	data["pos_profile"] = frappe.get_doc("POS Profile", shift_data["pos_profile"])
	data["company"] = frappe.get_doc("Company", data["pos_profile"].company)
	# Include server timestamp so frontend can compute shift duration
	# without timezone mismatch (period_start_date is in server timezone)
	data["server_now"] = str(get_datetime())

	return data


@frappe.whitelist()
def create_opening_shift(pos_profile, company, balance_details):
	"""Create a new POS Opening Shift"""
	balance_details = json.loads(balance_details) if isinstance(balance_details, str) else balance_details

	# Check if user already has an open shift
	existing_shift = check_opening_shift(frappe.session.user)
	if existing_shift:
		frappe.throw(
			_("You already have an open shift: {0}").format(existing_shift["pos_opening_shift"].name)
		)

	new_pos_opening = frappe.get_doc(
		{
			"doctype": "POS Opening Shift",
			"period_start_date": get_datetime(),
			"posting_date": nowdate(),
			"posting_time": nowtime(),
			"user": frappe.session.user,
			"pos_profile": pos_profile,
			"company": company,
			"status": "Open",
		}
	)

	# Add balance details - map opening_amount to amount
	formatted_balance_details = []
	for detail in balance_details:
		formatted_balance_details.append(
			{"mode_of_payment": detail.get("mode_of_payment"), "amount": detail.get("opening_amount", 0)}
		)

	new_pos_opening.set("balance_details", formatted_balance_details)
	new_pos_opening.insert(ignore_permissions=True)
	new_pos_opening.submit()

	data = {}
	data["pos_opening_shift"] = new_pos_opening.as_dict()
	data["pos_profile"] = frappe.get_doc("POS Profile", pos_profile)
	data["company"] = frappe.get_doc("Company", company)
	# Server timestamp so the frontend can anchor the schedule deadline clock
	data["server_now"] = str(get_datetime())

	return data


@frappe.whitelist()
def get_closing_shift_data(opening_shift):
	"""Get data for closing shift"""
	from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import make_closing_shift_from_opening

	try:
		# Get the opening shift document
		opening_shift_doc = frappe.get_doc("POS Opening Shift", opening_shift)

		# Convert to dict with proper datetime serialization
		opening_shift_dict = opening_shift_doc.as_dict()
		opening_shift_json = json.dumps(opening_shift_dict, default=str)

		# Create closing shift from opening shift (returns a dict)
		closing_data = make_closing_shift_from_opening(opening_shift_json)

		# Ensure datetime values are JSON serializable
		return json.loads(json.dumps(closing_data, default=str))
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Closing Shift Data Error")
		frappe.throw(_("Error getting closing shift data: {0}").format(str(e)))


@frappe.whitelist()
def submit_closing_shift(closing_shift):
	"""Submit closing shift"""
	from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
		submit_closing_shift as submit_shift,
	)

	try:
		# closing_shift is already a JSON string from frontend
		# If it's a dict, convert to JSON string
		if isinstance(closing_shift, dict):
			closing_shift = json.dumps(closing_shift)

		result = submit_shift(closing_shift)
		return {"name": result, "status": "success"}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Submit Closing Shift Error")
		frappe.throw(_("Error submitting closing shift: {0}").format(str(e)))


@frappe.whitelist()
def get_shift_history(filters=None, limit=25, offset=0, pos_profile=None):
	"""Return paginated shift history for the current session user only.

	Args:
		filters: JSON string or dict with optional from_date / to_date.
		limit:   Page size (default 25, max 100).
		offset:  Number of records to skip (0-based, for page navigation).

	Returns:
		{
		  "rows":   [...],          # Current page rows
		  "totals": {               # Aggregates across ALL matching rows (no page cap)
		      "total_shifts":   int,
		      "total_sales":    float,
		      "total_cash_diff": float,
		  }
		}

	Security: The session user restriction is always enforced server-side.
	Clients cannot override it by passing filters.
	"""
	if not frappe.has_permission("POS Opening Shift", "read"):
		frappe.throw(_("Insufficient permissions to view shift history"))

	if isinstance(filters, str):
		filters = json.loads(filters)

	# Clamp page size: minimum 1, maximum 100
	page_size   = max(1, min(cint(limit) or 25, 100))
	page_offset = max(0, cint(offset) or 0)

	# Mandatory WHERE conditions — user is always enforced server-side
	conditions = [
		"os.docstatus = 1",
		"os.user = %(session_user)s",
	]
	values = {
		"session_user": frappe.session.user,
		"limit":        page_size,
		"offset":       page_offset,
	}

	if filters:
		if filters.get("from_date"):
			conditions.append("os.posting_date >= %(from_date)s")
			values["from_date"] = filters["from_date"]

		if filters.get("to_date"):
			conditions.append("os.posting_date <= %(to_date)s")
			values["to_date"] = filters["to_date"]

	if pos_profile:
		conditions.append("os.pos_profile = %(pos_profile)s")
		values["pos_profile"] = pos_profile

	where_clause = "WHERE " + " AND ".join(conditions)

	# ── Rows query (paginated) ────────────────────────────────────────────────
	# Pre-aggregated LEFT JOINs replace 3 correlated subqueries per row.
	rows_query = f"""
		SELECT
			os.name            AS opening_shift_name,
			cs.name            AS closing_shift_name,
			os.posting_date    AS date,
			os.pos_profile,
			os.user            AS cashier,
			os.period_start_date AS open_time,
			cs.period_end_date   AS close_time,
			COALESCE(osd.opening_amount, 0) AS opening_amount,
			COALESCE(csd.closing_amount, 0) AS closing_amount,
			COALESCE(cs.grand_total,     0) AS sales_total,
			COALESCE(csd.difference,     0) AS difference
		FROM `tabPOS Opening Shift` os
		LEFT JOIN `tabPOS Closing Shift` cs
			ON cs.pos_opening_shift = os.name
		LEFT JOIN (
			SELECT parent, SUM(amount) AS opening_amount
			FROM `tabPOS Opening Shift Detail`
			GROUP BY parent
		) osd ON osd.parent = os.name
		LEFT JOIN (
			SELECT
				parent,
				SUM(closing_amount) AS closing_amount,
				SUM(difference)     AS difference
			FROM `tabPOS Closing Shift Detail`
			GROUP BY parent
		) csd ON csd.parent = cs.name
		{where_clause}
		ORDER BY os.posting_date DESC, os.period_start_date DESC
		LIMIT %(limit)s OFFSET %(offset)s
	"""

	data = frappe.db.sql(rows_query, values, as_dict=True)

	for row in data:
		row.opening_amount = flt(row.opening_amount)
		row.closing_amount = flt(row.closing_amount)
		row.sales_total    = flt(row.sales_total)
		row.difference     = flt(row.difference)

	# ── Totals query (no LIMIT) ───────────────────────────────────────────────
	# Runs across the full filter set so summary cards are always accurate
	# regardless of which page the user is viewing.
	totals_query = f"""
		SELECT
			COUNT(*)                              AS total_shifts,
			COALESCE(SUM(cs.grand_total),    0)   AS total_sales,
			COALESCE(SUM(csd_t.difference),  0)   AS total_cash_diff
		FROM `tabPOS Opening Shift` os
		LEFT JOIN `tabPOS Closing Shift` cs
			ON cs.pos_opening_shift = os.name
		LEFT JOIN (
			SELECT parent, SUM(difference) AS difference
			FROM `tabPOS Closing Shift Detail`
			GROUP BY parent
		) csd_t ON csd_t.parent = cs.name
		{where_clause}
	"""
	# Exclude pagination keys from totals query values
	totals_values = {k: v for k, v in values.items() if k not in ("limit", "offset")}
	totals_row = frappe.db.sql(totals_query, totals_values, as_dict=True)
	totals = totals_row[0] if totals_row else {}

	return {
		"rows": data,
		"totals": {
			"total_shifts":    int(totals.get("total_shifts",    len(data))),
			"total_sales":     flt(totals.get("total_sales",     0)),
			"total_cash_diff": flt(totals.get("total_cash_diff", 0)),
		},
	}


# Package component rows carry zero revenue and belong to their parent package;
# counting them would double both qty and line counts in the session summary.
PACKAGE_COMPONENT_ROLE = "Package Item"
PACKAGE_PARENT_ROLE = "Package"


@frappe.whitelist()
def get_session_summary(opening_shift):
	"""Pre-closing sales summary for an active POS session.

	Read-only preview of what POS Closing Shift would report: same invoice
	dataset (submitted invoices linked to this opening shift — POS Invoices or
	legacy non-consolidated Sales Invoices), same
	base-currency aggregation. Mirrors _process_invoice semantics — credit
	returns without payment rows are excluded because no money moved.

	Sections: shift info (name, cashier, opening/closing times), sales totals,
	cash ledger (opening balance, cash receipts, expense, cash in hand), all
	configured payment methods (zero amounts included) plus methods used but
	no longer configured, tax/charge classification, refund, and sales per
	item category.

	Security: only the shift owner (or a user with POS Opening Shift read
	access) may view it; the shift itself pins company/profile, so no
	cross-company access via arbitrary IDs.
	"""
	if not opening_shift:
		frappe.throw(_("Opening shift is required"))

	shift = frappe.db.get_value(
		"POS Opening Shift",
		opening_shift,
		["name", "user", "pos_profile", "company", "period_start_date", "status", "pos_schedule_deadline"],
		as_dict=True,
	)
	if not shift:
		frappe.throw(_("Opening shift not found"), frappe.DoesNotExistError)

	if (
		shift.user != frappe.session.user
		and not frappe.has_permission("POS Opening Shift", "read", doc=opening_shift)
	):
		# doc= enforces per-document user permissions (e.g. company scope),
		# matching what Desk would enforce on this exact shift
		frappe.throw(_("You can only view your own session summary"), frappe.PermissionError)

	company_currency = frappe.get_cached_value("Company", shift.company, "default_currency")
	profile_row = frappe.db.get_value(
		"POS Profile",
		shift.pos_profile,
		("posa_cash_mode_of_payment", "pos_profile_group"),
		as_dict=True,
	)
	cash_mode = profile_row.posa_cash_mode_of_payment or "Cash"

	summary = {
		"opening_shift": opening_shift,
		"pos_profile": shift.pos_profile,
		# Group comes from the profile live: accurate for the active session,
		# so only claimed while the shift is open (never as history).
		"shift_name": (profile_row.pos_profile_group or None) if shift.status == "Open" else None,
		"cashier": _cashier_full_name(shift.user),
		"company": shift.company,
		"company_currency": company_currency,
		"cash_mode_of_payment": cash_mode,
		"period_start_date": shift.period_start_date,
		"status": shift.status,
		"generated_at": get_datetime(),
	}
	summary.update(_closing_time_info(shift))
	summary.update(_aggregate_session_totals(opening_shift))
	summary.update(_aggregate_session_payments(opening_shift, cash_mode, shift.pos_profile))
	summary.update(_aggregate_session_items(opening_shift))
	summary.update(_aggregate_session_charges(opening_shift))
	summary.update(_aggregate_session_categories(opening_shift))
	# Combined discount overwrites the invoice-only subtotal from totals
	summary.update(_aggregate_session_discounts(opening_shift))
	summary["total_discount"] = flt(
		summary.get("item_discount", 0) + summary.get("invoice_discount", 0)
	)
	return summary


def _cashier_full_name(user):
	return frappe.db.get_value("User", user, "full_name") or user


def _closing_time_info(shift):
	"""Actual close time from the linked closing shift, else the frozen
	schedule deadline clearly marked as an estimate, else nothing — never a
	guessed time."""
	closing_time = frappe.db.get_value(
		"POS Closing Shift",
		{"pos_opening_shift": shift.name, "docstatus": 1},
		"period_end_date",
	)
	if closing_time:
		return {"closing_time": closing_time, "closing_source": "actual"}
	if shift.pos_schedule_deadline:
		return {"closing_time": shift.pos_schedule_deadline, "closing_source": "estimate"}
	return {"closing_time": None, "closing_source": None}


def _session_invoice_where(alias="si"):
	return f"{alias}.docstatus = 1 AND {alias}.posa_pos_opening_shift = %(shift)s"


# Shift filter pushed into every union branch so the union stays index-sized.
_SHIFT_BRANCH_WHERE = "si.docstatus = 1 AND si.posa_pos_opening_shift = %(shift)s"

# Columns the session-summary queries read off the invoice union / item union.
_SESSION_INVOICE_COLUMNS = (
	"si.name, si.docstatus, si.is_return, si.currency, si.grand_total,"
	" si.base_grand_total, si.base_net_total, si.base_total_taxes_and_charges,"
	" si.base_discount_amount, si.base_change_amount, si.total_qty,"
	" si.outstanding_amount, si.conversion_rate, si.posa_pos_opening_shift"
)
_SESSION_ITEM_COLUMNS = (
	"sii.parent, sii.item_code, sii.item_name, sii.item_group, sii.qty,"
	" sii.base_net_amount, sii.price_list_rate, sii.rate, sii.pos_package_role"
)


def _invoice_from():
	"""Invoice source for the session summary: POS Invoice + legacy Sales
	Invoice (non-consolidated) union, shift filter pushed into every branch."""
	return sales_invoice_union(_SESSION_INVOICE_COLUMNS, where=_SHIFT_BRANCH_WHERE)


def _item_from():
	return sales_invoice_item_union(_SESSION_ITEM_COLUMNS, where=_SHIFT_BRANCH_WHERE)


# Returns with no payment rows never touched the drawer; closing skips them,
# so exclude from every money/count aggregate (pattern reused in each query).
_NO_DRAWER_RETURN = (
	"NOT (si.is_return = 1 AND NOT EXISTS ("
	"SELECT 1 FROM `tabSales Invoice Payment` p WHERE p.parent = si.name))"
)


def _aggregate_session_totals(opening_shift):
	values = {"shift": opening_shift}
	# Derived table so the no-drawer-return NOT EXISTS is evaluated once per
	# invoice instead of once per aggregate column.
	row = frappe.db.sql(
		f"""
		SELECT
			SUM(1) AS counted_invoices,
			SUM(CASE WHEN si.is_return = 0 THEN si.base_grand_total ELSE 0 END) AS gross_sales,
			SUM(CASE WHEN si.is_return = 1 THEN ABS(si.base_grand_total) ELSE 0 END) AS returns_total,
			SUM(CASE WHEN si.is_return = 0 THEN 1 ELSE 0 END) AS sales_count,
			SUM(CASE WHEN si.is_return = 1 THEN 1 ELSE 0 END) AS returns_count,
			SUM(si.base_grand_total) AS net_sales,
			SUM(si.base_net_total) AS net_total,
			SUM(si.base_total_taxes_and_charges) AS total_taxes_and_charges,
			SUM(si.base_discount_amount) AS invoice_discount,
			SUM(si.total_qty) AS total_qty,
			-- outstanding_amount is the authoritative, reconciliation-aware figure
			-- (updated by Payment Entries); convert per invoice with its own rate
			SUM(CASE WHEN si.is_return = 0 AND si.outstanding_amount > 0
				THEN si.outstanding_amount * ifnull(si.conversion_rate, 1) ELSE 0 END) AS credit_outstanding
		FROM (
			SELECT si.is_return, si.base_grand_total, si.base_net_total,
				si.base_total_taxes_and_charges, si.base_discount_amount,
				si.total_qty, si.outstanding_amount, si.conversion_rate
			FROM {_invoice_from()}
			WHERE {_session_invoice_where()} AND {_NO_DRAWER_RETURN}
		) si
		""",
		values,
		as_dict=True,
	)[0]

	# Per-currency net sales so mixed-currency sessions stay visible, never summed
	currencies = frappe.db.sql(
		f"""
		SELECT si.currency, SUM(si.grand_total) AS amount
		FROM {_invoice_from()}
		WHERE {_session_invoice_where()} AND {_NO_DRAWER_RETURN}
		GROUP BY si.currency
		ORDER BY amount DESC
		""",
		values,
		as_dict=True,
	)

	# Raw count of every submitted invoice in the shift, before the drawer filter
	invoice_count = frappe.db.sql(
		f"SELECT COUNT(*) AS c FROM {_invoice_from()} WHERE {_session_invoice_where()}",
		values,
		as_dict=True,
	)[0].c

	result = {key: flt(row.get(key) or 0) for key in row}
	result["invoice_count"] = cint(invoice_count)
	sales_count = int(result.get("sales_count") or 0)
	result["average_sale"] = flt(result.get("net_sales")) / sales_count if sales_count else 0
	result["currency_breakdown"] = [
		{"currency": r.currency, "amount": flt(r.amount)} for r in currencies
	]
	return result


def _aggregate_session_payments(opening_shift, cash_mode, pos_profile=None):
	values = {"shift": opening_shift}

	payments = {}
	for row in frappe.db.sql(
		f"""
		SELECT sip.mode_of_payment, SUM(sip.base_amount) AS amount
		FROM `tabSales Invoice Payment` sip
		JOIN {_invoice_from()} ON si.name = sip.parent
		WHERE {_session_invoice_where()}
		GROUP BY sip.mode_of_payment
		""",
		values,
		as_dict=True,
	):
		payments[row.mode_of_payment] = payments.get(row.mode_of_payment, 0) + flt(row.amount)

	# Payment Entries (partial payments) also enter the drawer, same as closing
	for row in frappe.db.sql(
		"""
		SELECT pe.mode_of_payment, SUM(pe.base_paid_amount) AS amount
		FROM `tabPayment Entry` pe
		WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND pe.reference_no = %(shift)s
		GROUP BY pe.mode_of_payment
		""",
		values,
		as_dict=True,
	):
		payments[row.mode_of_payment] = payments.get(row.mode_of_payment, 0) + flt(row.amount)

	# One lookup table: cash-ness comes from Mode of Payment.type, never from
	# hardcoded mode names
	mode_types = {
		row.name: row.type
		for row in frappe.get_all("Mode of Payment", fields=["name", "type"])
	}

	def is_cash(mode):
		return mode_types.get(mode) == "Cash"

	# Change given back leaves the drawer in the designated cash mode
	change = flt(
		frappe.db.sql(
			f"SELECT SUM(si.base_change_amount) AS c FROM {_invoice_from()} "
			f"WHERE {_session_invoice_where()}",
			values,
			as_dict=True,
		)[0].c
	)
	if change or cash_mode in payments:
		payments[cash_mode] = payments.get(cash_mode, 0) - change

	# Every configured method appears, zero included; methods used but no
	# longer on the profile follow, largest first
	configured = frappe.get_all(
		"POS Payment Method",
		filters={"parent": pos_profile, "parenttype": "POS Profile"},
		fields=["mode_of_payment"],
		order_by="idx asc",
		pluck="mode_of_payment",
	) if pos_profile else []

	rows = []
	seen = set()
	for mode in configured:
		seen.add(mode)
		rows.append(
			{
				"mode_of_payment": mode,
				"amount": flt(payments.get(mode, 0)),
				"is_cash": is_cash(mode),
				"configured": True,
			}
		)
	used_extra = sorted(
		((mode, amount) for mode, amount in payments.items() if mode not in seen),
		key=lambda kv: -kv[1],
	)
	for mode, amount in used_extra:
		rows.append(
			{
				"mode_of_payment": mode,
				"amount": flt(amount),
				"is_cash": is_cash(mode),
				"configured": False,
			}
		)

	total_cash = flt(sum(r["amount"] for r in rows if r["is_cash"]))
	total_non_cash = flt(sum(r["amount"] for r in rows if not r["is_cash"]))

	# Opening float across every Cash-type mode, reported separately — never
	# mixed into receipt totals
	opening_cash = flt(
		sum(
			flt(row.amount)
			for row in frappe.get_all(
				"POS Opening Shift Detail",
				filters={"parent": opening_shift},
				fields=["mode_of_payment", "amount"],
			)
			if is_cash(row.mode_of_payment)
		)
	)

	cash_collected = total_cash
	cash_in_hand = flt(opening_cash + cash_collected)
	return {
		"payments": rows,
		"opening_cash": opening_cash,
		"cash_collected": cash_collected,
		"cash_expected": cash_in_hand,
		"cash_in_hand": cash_in_hand,
		# No shift-scoped expense ledger exists in this app (closing print also
		# hardcodes 0): report None honestly instead of a fabricated zero —
		# cash_in_hand is the balance BEFORE any cash expense.
		"expense": None,
		"expense_supported": False,
		"total_cash": total_cash,
		"total_non_cash": total_non_cash,
		"methods_grand_total": flt(total_cash + total_non_cash),
	}


def _aggregate_session_items(opening_shift, limit=100):
	where = f"""
		FROM {_item_from()}
		JOIN {_invoice_from()} ON si.name = sii.parent
		WHERE {_session_invoice_where()}
			AND ifnull(sii.pos_package_role, '') <> '{PACKAGE_COMPONENT_ROLE}'
		GROUP BY sii.item_code, sii.pos_package_role
	"""

	# Distinct item/role groups so the UI can disclose a capped table
	total_groups = cint(
		frappe.db.sql(f"SELECT COUNT(*) AS c FROM (SELECT sii.item_code {where}) t", {"shift": opening_shift}, as_dict=True)[0].c
	)

	rows = frappe.db.sql(
		f"""
		SELECT
			sii.item_code,
			MAX(sii.item_name) AS item_name,
			sii.pos_package_role AS package_role,
			SUM(sii.qty) AS qty,
			SUM(sii.base_net_amount) AS base_net_amount
		{where}
		ORDER BY base_net_amount DESC
		LIMIT {cint(limit)}
		""",
		{"shift": opening_shift},
		as_dict=True,
	)

	items = []
	packages = []
	for row in rows:
		entry = {
			"item_code": row.item_code,
			"item_name": row.item_name,
			"qty": flt(row.qty),
			"base_net_amount": flt(row.base_net_amount),
		}
		if row.package_role == PACKAGE_PARENT_ROLE:
			packages.append(entry)
		else:
			items.append(entry)

	return {
		"items": items,
		"packages": packages,
		"items_shown": len(rows),
		"items_total_groups": total_groups,
		"items_truncated": total_groups > len(rows),
	}


def _aggregate_session_charges(opening_shift):
	"""Classify invoice tax rows by the Account's own account_type — "Tax"
	becomes the tax total; anything else is listed by account so a service
	charge (or any other charge) shows under its real name instead of a
	guessed keyword split."""
	rows = frappe.db.sql(
		f"""
		SELECT
			st.account_head,
			MAX(COALESCE(acc.account_name, st.account_head)) AS label,
			MAX(acc.account_type) AS account_type,
			SUM(st.base_tax_amount_after_discount_amount) AS amount
		FROM `tabSales Taxes and Charges` st
		JOIN {_invoice_from()} ON si.name = st.parent
		LEFT JOIN `tabAccount` acc ON acc.name = st.account_head
		WHERE {_session_invoice_where()} AND {_NO_DRAWER_RETURN}
		GROUP BY st.account_head
		ORDER BY amount DESC
		""",
		{"shift": opening_shift},
		as_dict=True,
	)

	tax_total = 0.0
	other_charges = []
	for row in rows:
		amount = flt(row.amount)
		if row.account_type == "Tax":
			tax_total = flt(tax_total + amount)
		else:
			other_charges.append(
				{
					"account_head": row.account_head,
					"label": row.label,
					"amount": amount,
				}
			)

	return {
		"tax_total": tax_total,
		"total_tax": tax_total,
		"other_charges": other_charges,
		"other_charges_total": flt(sum(o["amount"] for o in other_charges)),
	}


def _aggregate_session_categories(opening_shift, limit=20):
	"""Group by the invoice item's item_group snapshot (Item fallback for
	legacy rows). Bundle revenue sits on the parent row; components are
	excluded. Returns show up negative; amounts are net (pre-tax)."""
	category_expr = (
		"COALESCE(NULLIF(sii.item_group, ''),"
		" (SELECT item.item_group FROM `tabItem` item WHERE item.name = sii.item_code))"
	)
	where = f"""
		FROM {_item_from()}
		JOIN {_invoice_from()} ON si.name = sii.parent
		WHERE {_session_invoice_where()}
			AND ifnull(sii.pos_package_role, '') <> '{PACKAGE_COMPONENT_ROLE}'
	"""

	total_groups = cint(
		frappe.db.sql(
			f"SELECT COUNT(*) AS c FROM (SELECT {category_expr} AS category {where} GROUP BY category) t",
			{"shift": opening_shift},
			as_dict=True,
		)[0].c
	)

	rows = frappe.db.sql(
		f"""
		SELECT
			{category_expr} AS category,
			SUM(sii.qty) AS qty,
			SUM(sii.base_net_amount) AS base_net_amount
		{where}
		GROUP BY category
		ORDER BY base_net_amount DESC
		LIMIT {cint(limit)}
		""",
		{"shift": opening_shift},
		as_dict=True,
	)

	categories = [
		{
			"category": row.category,
			"qty": flt(row.qty),
			"base_net_amount": flt(row.base_net_amount),
		}
		for row in rows
	]
	return {
		"categories": categories,
		"categories_shown": len(rows),
		"categories_total_groups": total_groups,
		"categories_truncated": total_groups > len(rows),
	}


def _aggregate_session_discounts(opening_shift):
	"""Item-level discount ((list - rate) x qty) plus the invoice-level
	additional discount — the same split the closing print reports. Both are
	already netted out of net_total/net_sales, so nothing is double-counted.
	"""
	item_discount = flt(
		frappe.db.sql(
			f"""
			SELECT SUM((ifnull(sii.price_list_rate, sii.rate) - sii.rate) * sii.qty) AS item_discount
			FROM {_item_from()}
			JOIN {_invoice_from()} ON si.name = sii.parent
			WHERE {_session_invoice_where()} AND {_NO_DRAWER_RETURN}
			""",
			{"shift": opening_shift},
			as_dict=True,
		)[0].item_discount
	)
	return {"item_discount": item_discount}
