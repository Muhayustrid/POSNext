# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Sales recap aggregation shared by the per-shift session summary and the
per-period (posting-date range) recap.

Every query reads the submitted-invoice union (POS Invoice + legacy
non-consolidated Sales Invoice, see pos_next.invoice_type) through a
RecapScope: the invoice WHERE fragment is pushed into every union branch so
the union stays index-sized, and the scope's opening shifts decide whose
drawer (opening float, Payment Entries) belongs to the recap.

Money semantics mirror POS Closing Shift._process_invoice: credit returns
without payment rows moved no money and are excluded from every money/count
aggregate; change given back leaves the drawer in the designated cash mode.
"""

from datetime import datetime, time, timedelta

import frappe
from frappe.utils import cint, flt, get_datetime, getdate

from pos_next.invoice_type import sales_invoice_item_union, sales_invoice_union

# Package component rows carry zero revenue and belong to their parent package;
# counting them would double both qty and line counts.
PACKAGE_COMPONENT_ROLE = "Package Item"
PACKAGE_PARENT_ROLE = "Package"

# Item rows printed under each category, matching the EOD sheet's shape. A
# category with more items than this is disclosed as truncated.
ITEMS_PER_CATEGORY = 50


class RecapScope:
	"""Which invoices (and whose drawers) one recap covers.

	where:  ``si.``-prefixed SQL pushed into each union branch. Only named
	        placeholders resolved from ``values`` — never inlined input.
	shifts: opening shifts whose opening float and Payment Entries count.
	"""

	def __init__(self, where, values, shifts):
		self.where = where
		self.values = dict(values)
		self.shifts = list(shifts)


def shift_scope(opening_shift):
	return RecapScope(
		"si.docstatus = 1 AND si.posa_pos_opening_shift = %(shift)s",
		{"shift": opening_shift},
		[opening_shift],
	)


def period_scope(pos_profile, from_date, to_date):
	"""Every submitted invoice posted in [from_date, to_date] under any shift of
	the profile (all cashiers), plus the drawers of the shifts opened in that
	window — a shift crossing midnight is attributed to its opening day, the
	same rule a per-shift closing follows.

	Outlet attribution goes through the shift link, the key every other recap
	surface (closing, session summary) uses — never through the invoice's own
	pos_profile column, which ERPNext may fill with a default profile.
	"""
	shifts = frappe.get_all(
		"POS Opening Shift",
		filters={
			"docstatus": 1,
			"pos_profile": pos_profile,
			"posting_date": ["between", [from_date, to_date]],
		},
		pluck="name",
	)
	return RecapScope(
		"si.docstatus = 1"
		" AND si.posting_date BETWEEN %(from_date)s AND %(to_date)s"
		" AND si.posa_pos_opening_shift IN ("
		"SELECT os.name FROM `tabPOS Opening Shift` os"
		" WHERE os.docstatus = 1 AND os.pos_profile = %(pos_profile)s)",
		{"pos_profile": pos_profile, "from_date": from_date, "to_date": to_date},
		shifts,
	)


def hourly_buckets(period_start, now, max_hours=24):
	"""Elapsed-hour buckets from shift start to now, materialized so an hour
	with no sales is still present in the payload (pure — no DB; see
	shift_hourly).

	One bucket per whole hour elapsed plus the partial hour containing ``now``;
	that final bucket is ``is_current`` and may be zero-length when ``now``
	falls exactly on a boundary — never a trailing bucket beyond it. A shift
	longer than max_hours (corrupt data) clamps to the most recent max_hours
	buckets.
	"""
	start, end = get_datetime(period_start), get_datetime(now)
	elapsed = max(0, int((end - start).total_seconds() // 3600))
	first = max(0, elapsed + 1 - max_hours)
	return [
		{"start": start + timedelta(hours=hour), "is_current": hour == elapsed}
		for hour in range(first, elapsed + 1)
	]


def build_recap(scope, cash_mode, pos_profile):
	"""All recap sections for a scope; both endpoints share these keys."""
	recap = {}
	recap.update(_aggregate_totals(scope))
	recap.update(_aggregate_payments(scope, cash_mode, pos_profile))
	recap.update(_aggregate_items(scope))
	recap.update(_aggregate_charges(scope))
	recap.update(_aggregate_categories(scope))
	# item_discount joins the invoice_discount already reported by totals
	recap.update(_aggregate_discounts(scope))
	recap["total_discount"] = flt(recap.get("item_discount", 0) + recap.get("invoice_discount", 0))
	return recap


def shift_hourly(scope, period_start):
	"""Net sales per elapsed shift hour for the shift dashboard.

	Same invoice universe as the money aggregates (drawer filter applied), so
	a no-drawer credit return doesn't print negative sales in an hour whose
	money never moved. Buckets come from hourly_buckets — zeros included,
	chronological, current hour flagged; hours outside a clamped (over-24h)
	window fall off the chart rather than being backdated into it. Negative
	bucket indexes (invoices timestamped before the shift start in
	pathological data) fold into bucket 0 so no money silently disappears.
	"""
	start = get_datetime(period_start)
	buckets = hourly_buckets(period_start, get_datetime())
	# hour offset of the first materialized bucket (the helper clamps long
	# shifts to their most recent 24 hours)
	first_index = int((buckets[0]["start"] - start).total_seconds() // 3600)
	by_hour: dict = {}
	for row in frappe.db.sql(
		f"""
		SELECT
			TIMESTAMPDIFF(HOUR, %(start)s, TIMESTAMP(si.posting_date, si.posting_time)) AS hour_index,
			SUM(si.base_grand_total) AS net_sales,
			SUM(CASE WHEN si.is_return = 0 THEN 1 ELSE 0 END) AS sales_count
		FROM {sales_invoice_union(_HOURLY_COLUMNS, where=scope.where)}
		WHERE {scope.where} AND {_NO_DRAWER_RETURN}
		GROUP BY hour_index
		""",
		dict(scope.values, start=start),
		as_dict=True,
	):
		index = max(cint(row.hour_index), 0)
		if index < first_index:
			continue
		acc = by_hour.setdefault(index, {"net_sales": 0.0, "sales_count": 0})
		acc["net_sales"] += flt(row.net_sales)
		acc["sales_count"] += cint(row.sales_count)
	return [
		{
			"start": bucket["start"].strftime("%Y-%m-%d %H:%M:%S"),
			"net_sales": flt(by_hour.get(first_index + hour, {}).get("net_sales")),
			"sales_count": by_hour.get(first_index + hour, {}).get("sales_count", 0),
			"is_current": bucket["is_current"],
		}
		for hour, bucket in enumerate(buckets)
	]


def period_buckets(from_date, to_date, now=None):
	"""Bucket grid for a posting-date window, materialized so an hour / day /
	month with no sales is still present in the payload (pure — no DB; see
	period_hourly). One bucket type per window: 24 hours on a single day, one
	per day up to 62 days, then calendar months clamped to the most recent 24.
	``is_current`` marks only the bucket containing ``now`` — none, for a
	window entirely in the past or future.
	"""
	start, end = getdate(from_date), getdate(to_date)
	now = get_datetime(now) if now else get_datetime()
	span = (end - start).days
	if span == 0:
		mode = "hour"
		starts = [datetime.combine(start, time(hour)) for hour in range(24)]
	elif span <= 62:
		mode = "day"
		starts = [
			datetime.combine(start + timedelta(days=offset), time(0))
			for offset in range(span + 1)
		]
	else:
		mode = "month"
		starts = []
		year, month = start.year, start.month
		while (year, month) <= (end.year, end.month):
			starts.append(datetime(year, month, 1))
			month += 1
			if month > 12:
				year, month = year + 1, 1
		starts = starts[-24:]

	def is_current(start):
		if mode == "hour":
			return start.date() == now.date() and start.hour == now.hour
		if mode == "day":
			return start.date() == now.date()
		return (start.year, start.month) == (now.year, now.month)

	return [{"start": start, "is_current": is_current(start)} for start in starts]


def period_hourly(scope, from_date, to_date):
	"""Net sales per bucket for the period dashboard.

	Same invoice universe as the money aggregates (drawer filter applied), so
	a no-drawer credit return doesn't print negative sales in a bucket whose
	money never moved. Bucket type follows the window (see period_buckets):
	elapsed hour on a single day — pathological pre-midnight timestamps fold
	into bucket 0 — posting date up to 62 days, calendar month beyond. Zeros
	included, chronological.
	"""
	buckets = period_buckets(from_date, to_date)
	if not buckets:
		return []

	start, end = getdate(from_date), getdate(to_date)
	span = (end - start).days
	if span == 0:
		group_expr = (
			"TIMESTAMPDIFF(HOUR, %(day_start)s, TIMESTAMP(si.posting_date, si.posting_time))"
		)
	elif span <= 62:
		group_expr = "si.posting_date"
	else:
		group_expr = "DATE_FORMAT(si.posting_date, '%%Y-%%m')"

	by_key: dict = {}
	for row in frappe.db.sql(
		f"""
		SELECT
			{group_expr} AS bucket_index,
			SUM(si.base_grand_total) AS net_sales,
			SUM(CASE WHEN si.is_return = 0 THEN 1 ELSE 0 END) AS sales_count
		FROM {sales_invoice_union(_HOURLY_COLUMNS, where=scope.where)}
		WHERE {scope.where} AND {_NO_DRAWER_RETURN}
		GROUP BY bucket_index
		""",
		dict(scope.values, day_start=datetime.combine(start, time(0))),
		as_dict=True,
	):
		key = max(cint(row.bucket_index), 0) if span == 0 else row.bucket_index
		acc = by_key.setdefault(key, {"net_sales": 0.0, "sales_count": 0})
		acc["net_sales"] += flt(row.net_sales)
		acc["sales_count"] += cint(row.sales_count)

	out = []
	for bucket in buckets:
		key = (
			bucket["start"].hour
			if span == 0
			else bucket["start"].date()
			if span <= 62
			else bucket["start"].strftime("%Y-%m")
		)
		acc = by_key.get(key, {})
		out.append(
			{
				"start": bucket["start"].strftime("%Y-%m-%d %H:%M:%S"),
				"net_sales": flt(acc.get("net_sales")),
				"sales_count": acc.get("sales_count", 0),
				"is_current": bucket["is_current"],
			}
		)
	return out


def shift_recent(scope, limit=10):
	"""Most recent invoices of the shift for the dashboard feed.

	Deliberately without the drawer filter: a no-drawer credit return moved no
	money (every aggregate skips it) but the cashier still sees it listed.
	payment_mode is the invoice's first payment row (smallest idx) or None —
	fetched for all rows in one query, never per invoice.
	"""
	invoices = frappe.db.sql(
		f"""
		SELECT
			si.name,
			TIMESTAMP(si.posting_date, si.posting_time) AS posting_dt,
			si.is_return,
			si.outstanding_amount,
			si.base_grand_total
		FROM {sales_invoice_union(_RECENT_COLUMNS, where=scope.where)}
		WHERE {scope.where}
		ORDER BY si.posting_date DESC, si.posting_time DESC, si.creation DESC
		LIMIT {cint(limit)}
		""",
		scope.values,
		as_dict=True,
	)
	if not invoices:
		return []

	first_mode = {}
	for row in frappe.db.sql(
		"""
		SELECT parent, mode_of_payment
		FROM `tabSales Invoice Payment`
		WHERE parent IN %(names)s AND parenttype IN ('POS Invoice', 'Sales Invoice')
		ORDER BY idx ASC
		""",
		{"names": [invoice.name for invoice in invoices]},
		as_dict=True,
	):
		first_mode.setdefault(row.parent, row.mode_of_payment)

	return [
		{
			"name": invoice.name,
			"posting_dt": str(invoice.posting_dt),
			"is_return": bool(invoice.is_return),
			"outstanding_amount": flt(invoice.outstanding_amount),
			"amount": flt(invoice.base_grand_total),
			"payment_mode": first_mode.get(invoice.name),
		}
		for invoice in invoices
	]


# Columns the recap queries read off the invoice union / item union.
_INVOICE_COLUMNS = (
	"si.name, si.docstatus, si.is_return, si.currency, si.grand_total,"
	" si.base_grand_total, si.base_net_total, si.base_total_taxes_and_charges,"
	" si.base_discount_amount, si.base_change_amount, si.total_qty,"
	" si.outstanding_amount, si.conversion_rate, si.posa_pos_opening_shift"
)
_ITEM_COLUMNS = (
	"sii.parent, sii.item_code, sii.item_name, sii.item_group, sii.qty,"
	" sii.base_net_amount, sii.price_list_rate, sii.rate, sii.pos_package_role"
)

# Dashboard rollups read the posting timestamp the shared projection doesn't
# carry; local column lists keep _INVOICE_COLUMNS (and its consumers) stable.
_HOURLY_COLUMNS = _INVOICE_COLUMNS + ", si.posting_date, si.posting_time"
_RECENT_COLUMNS = _INVOICE_COLUMNS + ", si.posting_date, si.posting_time, si.creation"


def _invoice_from(scope):
	"""Invoice source: POS Invoice + legacy Sales Invoice (non-consolidated)
	union, the scope filter pushed into every branch."""
	return sales_invoice_union(_INVOICE_COLUMNS, where=scope.where)


def _item_from(scope):
	return sales_invoice_item_union(_ITEM_COLUMNS, where=scope.where)


# Returns with no payment rows never touched the drawer; closing skips them,
# so exclude from every money/count aggregate (pattern reused in each query).
_NO_DRAWER_RETURN = (
	"NOT (si.is_return = 1 AND NOT EXISTS ("
	"SELECT 1 FROM `tabSales Invoice Payment` p WHERE p.parent = si.name))"
)


def _aggregate_totals(scope):
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
			FROM {_invoice_from(scope)}
			WHERE {_NO_DRAWER_RETURN}
		) si
		""",
		scope.values,
		as_dict=True,
	)[0]

	# Per-currency net sales so mixed-currency recaps stay visible, never summed
	currencies = frappe.db.sql(
		f"""
		SELECT si.currency, SUM(si.grand_total) AS amount
		FROM {_invoice_from(scope)}
		WHERE {_NO_DRAWER_RETURN}
		GROUP BY si.currency
		ORDER BY amount DESC
		""",
		scope.values,
		as_dict=True,
	)

	# Raw count of every submitted invoice in scope, before the drawer filter
	invoice_count = frappe.db.sql(
		f"SELECT COUNT(*) AS c FROM {_invoice_from(scope)}",
		scope.values,
		as_dict=True,
	)[0].c

	result = {key: flt(row.get(key) or 0) for key in row}
	result["invoice_count"] = cint(invoice_count)
	sales_count = int(result.get("sales_count") or 0)
	# gross over the sales-only count: netting returns into the numerator while
	# the denominator counts sales alone understates the basket (or flips sign)
	result["average_sale"] = flt(result.get("gross_sales")) / sales_count if sales_count else 0
	result["currency_breakdown"] = [{"currency": r.currency, "amount": flt(r.amount)} for r in currencies]
	return result


def _aggregate_payments(scope, cash_mode, pos_profile=None):
	payments = {}
	for row in frappe.db.sql(
		f"""
		SELECT sip.mode_of_payment, SUM(sip.base_amount) AS amount
		FROM `tabSales Invoice Payment` sip
		JOIN {_invoice_from(scope)} ON si.name = sip.parent
		GROUP BY sip.mode_of_payment
		""",
		scope.values,
		as_dict=True,
	):
		payments[row.mode_of_payment] = payments.get(row.mode_of_payment, 0) + flt(row.amount)

	# Payment Entries (partial payments) also enter the drawer, same as closing.
	# ponytail: reference_no is unindexed — fine at partial-payment volumes;
	# index the column if period recaps ever show up in the slow log.
	if scope.shifts:
		for row in frappe.db.sql(
			"""
			SELECT pe.mode_of_payment, SUM(pe.base_paid_amount) AS amount
			FROM `tabPayment Entry` pe
			WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive' AND pe.reference_no IN %(shifts)s
			GROUP BY pe.mode_of_payment
			""",
			{"shifts": scope.shifts},
			as_dict=True,
		):
			payments[row.mode_of_payment] = payments.get(row.mode_of_payment, 0) + flt(row.amount)

	# One lookup table: cash-ness comes from Mode of Payment.type, never from
	# hardcoded mode names
	mode_types = {row.name: row.type for row in frappe.get_all("Mode of Payment", fields=["name", "type"])}

	def is_cash(mode):
		return mode_types.get(mode) == "Cash"

	# Change given back leaves the drawer in the designated cash mode
	change = flt(
		frappe.db.sql(
			f"SELECT SUM(si.base_change_amount) AS c FROM {_invoice_from(scope)}",
			scope.values,
			as_dict=True,
		)[0].c
	)
	if change or cash_mode in payments:
		payments[cash_mode] = payments.get(cash_mode, 0) - change

	# Every configured method appears, zero included; methods used but no
	# longer on the profile follow, largest first
	configured = (
		frappe.get_all(
			"POS Payment Method",
			filters={"parent": pos_profile, "parenttype": "POS Profile"},
			fields=["mode_of_payment"],
			order_by="idx asc",
			pluck="mode_of_payment",
		)
		if pos_profile
		else []
	)

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

	# Opening float across every Cash-type mode of every shift in scope,
	# reported separately — never mixed into receipt totals
	opening_rows = (
		frappe.get_all(
			"POS Opening Shift Detail",
			filters={"parent": ["in", scope.shifts]},
			fields=["mode_of_payment", "amount"],
		)
		if scope.shifts
		else []
	)
	opening_cash = flt(sum(flt(row.amount) for row in opening_rows if is_cash(row.mode_of_payment)))

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


def _aggregate_items(scope, limit=100):
	where = f"""
		FROM {_item_from(scope)}
		JOIN {_invoice_from(scope)} ON si.name = sii.parent
		WHERE ifnull(sii.pos_package_role, '') <> '{PACKAGE_COMPONENT_ROLE}'
		GROUP BY sii.item_code, sii.pos_package_role
	"""

	# Distinct item/role groups so the UI can disclose a capped table
	total_groups = cint(
		frappe.db.sql(
			f"SELECT COUNT(*) AS c FROM (SELECT sii.item_code {where}) t",
			scope.values,
			as_dict=True,
		)[0].c
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
		scope.values,
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


def _aggregate_charges(scope):
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
		JOIN {_invoice_from(scope)} ON si.name = st.parent
		LEFT JOIN `tabAccount` acc ON acc.name = st.account_head
		WHERE {_NO_DRAWER_RETURN}
		GROUP BY st.account_head
		ORDER BY amount DESC
		""",
		scope.values,
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


def _aggregate_categories(scope, limit=20):
	"""Group by the invoice item's item_group snapshot (Item fallback for
	legacy rows). Bundle revenue sits on the parent row; components are
	excluded. Returns show up negative; amounts are net (pre-tax)."""
	category_expr = (
		"COALESCE(NULLIF(sii.item_group, ''),"
		" (SELECT item.item_group FROM `tabItem` item WHERE item.name = sii.item_code))"
	)
	where = f"""
		FROM {_item_from(scope)}
		JOIN {_invoice_from(scope)} ON si.name = sii.parent
		WHERE ifnull(sii.pos_package_role, '') <> '{PACKAGE_COMPONENT_ROLE}'
	"""

	total_groups = cint(
		frappe.db.sql(
			f"SELECT COUNT(*) AS c FROM (SELECT {category_expr} AS category {where} GROUP BY category) t",
			scope.values,
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
		scope.values,
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

	# Per-category item breakdown so a recap prints the same nested sheet the
	# EOD report does (category name, then its items under it). The full
	# per-category list is built first so the truncation note is exact; only
	# the first ITEMS_PER_CATEGORY rows are emitted, because an unbounded
	# month of items would be a metre of till roll.
	if categories:
		item_where = f"""
			FROM {_item_from(scope)}
			JOIN {_invoice_from(scope)} ON si.name = sii.parent
			WHERE ifnull(sii.pos_package_role, '') <> '{PACKAGE_COMPONENT_ROLE}'
		"""
		item_rows = frappe.db.sql(
			f"""
			SELECT
				{category_expr} AS category,
				sii.item_code,
				MAX(sii.item_name) AS item_name,
				SUM(sii.qty) AS qty,
				SUM(sii.base_net_amount) AS base_net_amount
			{item_where}
			GROUP BY category, sii.item_code
			ORDER BY category, base_net_amount DESC
			""",
			scope.values,
			as_dict=True,
		)
		by_category: dict = {}
		for row in item_rows:
			by_category.setdefault(row.category, []).append(
				{
					"item_code": row.item_code,
					"item_name": row.item_name,
					"qty": flt(row.qty),
					"base_net_amount": flt(row.base_net_amount),
				}
			)
		for category in categories:
			all_items = by_category.get(category["category"], [])
			category["items"] = all_items[:ITEMS_PER_CATEGORY]
			category["items_shown"] = len(category["items"])
			category["items_truncated"] = len(all_items) > ITEMS_PER_CATEGORY

	return {
		"categories": categories,
		"categories_shown": len(rows),
		"categories_total_groups": total_groups,
		"categories_truncated": total_groups > len(rows),
	}


def _aggregate_discounts(scope):
	"""Item-level discount ((list - rate) x qty) — the invoice-level additional
	discount is already reported by totals as invoice_discount; both are netted
	out of net_total/net_sales, so nothing is double-counted."""
	item_discount = flt(
		frappe.db.sql(
			f"""
			SELECT SUM((ifnull(sii.price_list_rate, sii.rate) - sii.rate) * sii.qty) AS item_discount
			FROM {_item_from(scope)}
			JOIN {_invoice_from(scope)} ON si.name = sii.parent
			WHERE {_NO_DRAWER_RETURN}
			""",
			scope.values,
			as_dict=True,
		)[0].item_discount
	)
	return {"item_discount": item_discount}
