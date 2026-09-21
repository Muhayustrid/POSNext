# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""HQ Sales Monitoring API.

One read-only whitelisted endpoint feeding the "HQ Sales Monitoring" desk page.
All metrics come from the same POS sales dataset:

- POS Invoices plus legacy Sales Invoices (``docstatus = 1``, ``is_pos = 1``,
  Sales Invoice side excluding ``is_consolidated`` — those duplicate the POS
  Invoices already in the union) — POS transactions only, never the non-POS
  ledger.
- Money is summed in company/base currency (``base_*`` fields). Companies with
  different base currencies are NEVER raw-summed: every monetary metric is
  returned as ``{by_currency: {...}, default_currency, default}``.
- Gross (non-return invoices) vs refunds (``is_return = 1``, absolute value)
  are tracked separately; refund invoices never count as new orders.
- Net (tax-incl) = SUM(base_grand_total) signed, so returns subtract.
- Pre-tax net = SUM(base_net_total); taxes = SUM(base_total_taxes_and_charges),
  both signed. Category/product figures are pre-tax item net amounts and
  therefore do not reconcile to the tax-incl totals — by design.
- Package components (``Sales Invoice Item.pos_package_role = 'Package Item'``)
  are excluded from item qty/amount so bundle revenue is not double counted.
- Quantities are signed: returns contribute negative qty.
- Two optional "top items within category" cards (``category_a``/``category_b``):
  one grouped query each over the full Item Group tree (sub-groups included),
  positive net revenue only, share % against the whole category. They are
  independent of the Product Ranking ``category`` filter and of each other.
- Sales channel: Sales Invoice has no real channel field, so the section is
  reported as unavailable rather than invented (no fake "Take Away", no
  orders == pax claim; no pax source exists either).

Permissions:
- Role gate (System Manager / Accounts Manager / Sales Manager / Nexus POS
  Manager), then company scope via User Permissions on "Company" and POS
  Profile scope via User Permissions on "POS Profile" (see pos_next.hq_scope).
- A forged company (outside the user's scope) raises PermissionError.

Time: server timezone. Today's windows are cut at the current server time;
historical days are full days.

Period semantics (labeled explicitly in the UI):
- Monthly monitoring / turnover / targets: month-to-date of ``to_date``
  (``from_date`` is deliberately ignored there — MTD never shifts).
- Daily monitoring: ``to_date`` vs its prior weekday (same elapsed cutoff).
- Hero cards, peak hours, rankings and donuts: the selected range
  ``from_date .. to_date`` (cutoff at "now" when ``to_date`` is today).
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import flt, get_first_day, get_last_day, getdate, now_datetime, nowdate

from pos_next.hq_scope import (
	expand_company_descendants,
	get_permitted_companies,
	get_permitted_pos_profiles,
	resolve_company_scope,
)
from pos_next.invoice_type import sales_invoice_item_union, sales_invoice_union

HQ_ROLES = ("System Manager", "Accounts Manager", "Sales Manager", "Nexus POS Manager")

MAX_RANGE_DAYS = 366
MAX_PAGE_SIZE = 50
COMPONENT_ROLE = "Package Item"

MONEY_KEYS = ("gross", "refunds", "net_tax_incl", "net_pretax", "taxes")


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------


def ratio(part, whole):
	"""Percentage of part/whole, or None when the denominator is zero."""
	whole = flt(whole or 0)
	return round(flt(part or 0) / whole * 100, 2) if whole else None


def growth_pct(current, prior):
	"""Growth percentage vs prior, or None when prior is zero (never fake 0%)."""
	prior = flt(prior or 0)
	if not prior:
		return None
	return round((flt(current or 0) - prior) / prior * 100, 2)


def previous_weekday(day):
	"""The last weekday (Mon-Fri) strictly before ``day``."""
	day = getdate(day) - timedelta(days=1)
	while day.weekday() >= 5:  # 5=Sat, 6=Sun
		day = day - timedelta(days=1)
	return day


def split_by_currency(rows, key, currency_map):
	"""Aggregate ``row[key]`` per company currency; never mixes currencies."""
	out = {}
	for row in rows:
		ccy = currency_map.get(row.get("company"))
		if not ccy:
			continue
		out[ccy] = out.get(ccy, 0) + flt(row.get(key) or 0)
	return {k: round(v, 2) for k, v in out.items()}


def make_metric(by_currency, default_currency):
	"""Uniform monetary metric: per-currency groups plus a labeled default."""
	return {
		"by_currency": by_currency,
		"default_currency": default_currency,
		"default": by_currency.get(default_currency, 0.0),
	}


def metric_from_rows(rows, key, currency_map, default_ccy):
	return make_metric(split_by_currency(rows, key, currency_map), default_ccy)


def _to_int(value, default=1, lo=1, hi=None):
	try:
		value = int(value or default)
	except (TypeError, ValueError):
		value = default
	value = max(lo, value)
	if hi:
		value = min(value, hi)
	return value


def _parse_amount(value, label):
	"""Non-negative amount; blank/None = field not provided (leave untouched)."""
	if value in (None, ""):
		return None
	amount = flt(value)
	if amount < 0:
		frappe.throw(_("{0} cannot be negative").format(label))
	return amount


def _parse_count(value):
	"""Non-negative integer; blank/None = field not provided."""
	if value in (None, ""):
		return None
	try:
		return max(0, int(float(value)))
	except (TypeError, ValueError):
		frappe.throw(_("Invalid number: {0}").format(value))


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_sales_monitoring(
	company=None,
	from_date=None,
	to_date=None,
	include_descendants=0,
	category=None,
	category_a=None,
	category_b=None,
	product_page=1,
	page_size=10,
):
	"""All HQ Sales Monitoring panel data in a single read-only payload."""
	_check_hq_access()

	today = getdate(nowdate())
	to_date = _parse_date(to_date) or today
	from_date = _parse_date(from_date) or get_first_day(to_date)
	if to_date > today:
		to_date = today
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date"))
	if (to_date - from_date).days > MAX_RANGE_DAYS:
		frappe.throw(_("Date range is limited to {0} days").format(MAX_RANGE_DAYS))

	scope = _resolve_scope(company, include_descendants)
	if not scope["companies"] or scope["profiles"] == []:
		return _empty_payload(scope, _("No companies or POS Profiles are accessible for this user."))

	window = _build_windows(to_date)
	currency_map = _currency_map(scope["companies"])
	default_company = _default_company(scope["companies"])
	default_ccy = currency_map.get(default_company)
	profiles = scope["profiles"]

	# Every section reuses one of two windows + scope (no N+1 per row).
	mtd_where, mtd_params = _si_window_where(
		scope["companies"], profiles, window["month_start"], to_date, window["mtd_cutoff"]
	)
	mtd_rows = _totals_rows(mtd_where, mtd_params)
	monthly = _metrics_from_totals(mtd_rows, currency_map, default_ccy)

	# Selected range (hero cards, hours, rankings, donuts) — MTD above stays
	# fixed to the month even when the user narrows the range.
	range_where, range_params = _si_window_where(
		scope["companies"], profiles, from_date, to_date, window["mtd_cutoff"]
	)

	result = {
		"scope": {
			**scope,
			"currency_map": currency_map,
			"default_currency": default_ccy,
			"default_company": default_company,
			"from_date": str(from_date),
			"to_date": str(to_date),
		},
		"windows": window,
		"monthly": monthly,
		"range": {
			**_metrics_for_window(scope["companies"], profiles, from_date, to_date, window["mtd_cutoff"], currency_map, default_ccy),
			"cut_at": window["mtd_cutoff"].strftime("%H:%M") if window["mtd_cutoff"] else None,
		},
		"turnover": _turnover_section(scope, currency_map, default_ccy, window, mtd_rows),
		"daily": _daily_section(scope, currency_map, default_ccy, window),
		"hours": _hours_section(range_where, range_params),
		"favorite_product": _favorite_product(range_where, range_params),
		"product_ranking": _product_ranking(range_where, range_params, category, product_page, page_size),
		"outlet_ranking": _outlet_ranking(range_where, range_params, currency_map),
		"item_groups": _item_group_names(),
		"category_products": _category_products(
			scope["companies"],
			profiles,
			from_date,
			to_date,
			window["mtd_cutoff"],
			currency_map,
			default_ccy,
			{"a": _clean_category(category_a), "b": _clean_category(category_b)},
		),
		"category_top": _category_top(
			scope["companies"], profiles, from_date, to_date, window["mtd_cutoff"], currency_map, default_ccy
		),
		"channels": {
			"available": False,
			"notice": _(
				"Sales Invoice has no sales-channel field, so channel totals cannot be reported. Use the Outlet Ranking for per-outlet totals."
			),
		},
		"pax": {
			"available": False,
			"notice": _("No guest/pax source field exists on POS invoices; pax is not estimated."),
		},
		"generated_at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
	}
	result["highlights"] = {
		"biggest_outlet": result["outlet_ranking"][0] if result["outlet_ranking"] else None,
		"most_transactions_outlet": (
			max(result["outlet_ranking"], key=lambda r: r["orders"]) if result["outlet_ranking"] else None
		),
	}
	result["targets"] = _targets_section(scope["companies"], currency_map, default_ccy, window, monthly, mtd_rows)
	result["targets"]["overall"] = _overall_target_section(scope, currency_map)
	return result


@frappe.whitelist()
def set_outlet_target(
	company=None,
	month_start=None,
	target_sales=None,
	target_transactions=None,
	overall_target=None,
	overall_from=None,
):
	"""Set an outlet's targets from the HQ Sales Monitoring page.

	- Monthly: upserts the POS Monthly Target of ``month_start`` (default the
	  current month). A blank field keeps the stored value.
	- Overall (payback / "balik modal"): writes the Overall Sales Target custom
	  fields on the Company master. ``overall_target`` blank = untouched, 0
	  clears it; ``overall_from`` "" clears the counted-from date.

	Role gate is the read gate (HQ_ROLES) plus explicit write/create
	permissions per target store — Sales Manager can read the dashboard but
	not move targets.
	"""
	_check_hq_access()
	if not company or not frappe.db.exists("Company", company):
		frappe.throw(_("Please choose a valid outlet"))
	if not frappe.has_permission("Company", "read", doc=company):
		frappe.throw(_("Not permitted to access Company {0}").format(company), frappe.PermissionError)

	sales = _parse_amount(target_sales, _("Monthly Target Sales"))
	transactions = _parse_count(target_transactions)
	overall = _parse_amount(overall_target, _("Overall Sales Target"))
	start = _parse_date(overall_from)

	result = {"monthly": None, "overall_updated": False}

	if sales is not None or transactions is not None:
		month = _parse_date(month_start) or get_first_day(nowdate())
		if month != get_first_day(month):
			frappe.throw(_("Month Start must be the first day of the month"))
		name = frappe.db.exists("POS Monthly Target", {"company": company, "month_start": month})
		action = "write" if name else "create"
		if not frappe.has_permission("POS Monthly Target", action):
			frappe.throw(
				_("You need {0} permission on POS Monthly Target").format(_(action.title())),
				frappe.PermissionError,
			)
		doc = (
			frappe.get_doc("POS Monthly Target", name)
			if name
			else frappe.get_doc(
				{"doctype": "POS Monthly Target", "company": company, "month_start": str(month)}
			)
		)
		if sales is not None:
			doc.target_sales = sales
		if transactions is not None:
			doc.target_transactions = transactions
		doc.save()
		result["monthly"] = doc.name

	if overall is not None:
		if not frappe.has_permission("Company", "write", doc=company):
			frappe.throw(
				_("You need write permission on Company {0}").format(company),
				frappe.PermissionError,
			)
		frappe.db.set_value(
			"Company",
			company,
			{"pos_overall_sales_target": overall, "pos_overall_target_from": start},
		)
		result["overall_updated"] = True

	if result["monthly"] is None and not result["overall_updated"]:
		frappe.throw(_("Nothing to save — enter at least one target"))
	return result


@frappe.whitelist()
def get_outlet_targets(month_start=None):
	"""Per-outlet target sheet for the "Outlet Targets" desk page (read-only).

	One row for EVERY company in the user's full permission window (selling or
	not), covering one calendar month: monthly target vs month-to-date actual,
	linear projection to month end, and the payback (overall) target. The MTD
	dataset is the same one the monitoring page uses, so the numbers reconcile:
	current month stops at today (cut at "now"); a closed month reports its
	full actuals (projection = actual); a future month has no elapsed days, so
	its actuals are 0 and the projection is null.
	"""
	_check_hq_access()

	today = getdate(nowdate())
	month = _parse_date(month_start) or get_first_day(today)
	if month != get_first_day(month):
		frappe.throw(_("Month Start must be the first day of the month"))
	month_end = get_last_day(month)
	days_in_month = month_end.day

	# company_options = the user's full visible window, pre-narrowing.
	scope = _resolve_scope(None, 0)
	companies = scope["company_options"]
	currency_map = _currency_map(companies)

	if month == get_first_day(today):
		days_elapsed = (today - month).days + 1
		window_end, cutoff = today, now_datetime()
	elif month < get_first_day(today):
		days_elapsed = days_in_month
		window_end, cutoff = month_end, None
	else:
		days_elapsed = 0
		window_end, cutoff = month_end, None

	where, params = _si_window_where(companies, scope["profiles"], month, window_end, cutoff)
	mtd_by_company = {r.company: r for r in _totals_rows(where, params)}

	targets_by_company = {
		r.company: r
		for r in frappe.get_all(
			"POS Monthly Target",
			filters={"company": ["in", companies], "month_start": str(month)},
			fields=["company", "target_sales", "target_transactions"],
		)
	}
	overall_by_company = _overall_target_section(scope, currency_map)["by_company"]

	rows = []
	for company in companies:
		target = targets_by_company.get(company)
		actual = mtd_by_company.get(company)
		net = flt(actual.net_tax_incl) if actual else 0.0
		orders = int(actual.orders) if actual else 0
		target_sales = flt(target.target_sales) if target else None
		rows.append(
			{
				"company": company,
				"currency": currency_map.get(company),
				"monthly": {
					"target_sales": target_sales,
					"target_transactions": int(target.target_transactions or 0) if target else None,
					"missing": target is None,
				},
				"mtd_net_tax_incl": round(net, 2),
				"mtd_orders": orders,
				"achievement_sales_pct": ratio(net, target_sales) if target else None,
				# same linear projection as the monitoring targets section
				"projected_sales": (
					round(net / days_elapsed * days_in_month, 2) if target and days_elapsed else None
				),
				"overall": overall_by_company.get(company),
			}
		)

	return {
		"month_start": str(month),
		"month_end": str(month_end),
		"days_elapsed": days_elapsed,
		"rows": rows,
		"generated_at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
	}


# ---------------------------------------------------------------------------
# Scope / access
# ---------------------------------------------------------------------------


def _check_hq_access():
	if frappe.session.user == "Administrator":
		return
	if not set(frappe.get_roles()) & set(HQ_ROLES):
		frappe.throw(_("Not permitted to access HQ Sales Monitoring"), frappe.PermissionError)


def _resolve_scope(company, include_descendants):
	permitted = get_permitted_companies()
	# Filter dropdown options = the user's full visible window (pre-narrowing).
	options = permitted if permitted is not None else frappe.get_all("Company", pluck="name")

	companies, restricted = resolve_company_scope({"company": company})
	if companies is None:
		companies = options
	elif company and include_descendants and companies:
		companies = expand_company_descendants(company, permitted)

	profiles = get_permitted_pos_profiles()
	return {
		"companies": companies or [],
		"company_options": options or [],
		"restricted": restricted,
		"profile_restricted": profiles is not None,
		"profiles": profiles,
	}


def _currency_map(companies):
	rows = frappe.get_all("Company", filters={"name": ["in", companies]}, fields=["name", "default_currency"])
	return {r.name: r.default_currency for r in rows}


def _default_company(companies):
	default = frappe.db.get_single_value("Global Defaults", "default_company")
	if default not in companies:
		default = sorted(companies)[0]
	return default


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------


def _build_windows(to_date):
	today = getdate(nowdate())
	month_start = get_first_day(to_date)
	days_elapsed = (to_date - month_start).days + 1
	prev_month_start = get_first_day(month_start - timedelta(days=1))
	prev_end = prev_month_start + timedelta(days=days_elapsed - 1)

	# Today's windows are cut at "now"; historical days are full days.
	cutoff = now_datetime() if to_date == today else None

	return {
		"month_start": str(month_start),
		"days_in_month": get_last_day(to_date).day,
		"days_elapsed": days_elapsed,
		"prev_comparable_start": str(prev_month_start),
		"prev_comparable_end": str(prev_end),
		"day": str(to_date),
		"prior_weekday": str(previous_weekday(to_date)),
		"last_week_same": str(to_date - timedelta(days=7)),
		"mtd_cutoff": cutoff,
		"day_cutoff": cutoff,
		"compare_cutoff": cutoff,
	}


def _parse_date(value):
	if value in (None, ""):
		return None
	try:
		return getdate(value)
	except Exception:
		frappe.throw(_("Invalid date: {0}").format(value))


# ---------------------------------------------------------------------------
# Sales Invoice aggregates
# ---------------------------------------------------------------------------


def _si_window_where(companies, profiles, start, end, cutoff=None, alias="si"):
	"""Shared window WHERE: POS invoices, company + profile scope, date span."""
	params = {"companies": companies, "start": start, "end": end}
	where = [
		f"{alias}.docstatus = 1",
		f"{alias}.is_pos = 1",
		f"{alias}.company IN %(companies)s",
		f"{alias}.posting_date >= %(start)s",
		f"{alias}.posting_date <= %(end)s",
	]
	if cutoff:
		params["cutoff"] = cutoff
		where.append(f"TIMESTAMP({alias}.posting_date, {alias}.posting_time) <= %(cutoff)s")
	if profiles is not None:
		params["profiles"] = list(profiles)
		where.append(f"{alias}.pos_profile IN %(profiles)s")
	return " AND ".join(where), params


def _invoice_from():
	"""Invoice source for ``si`` aliases: report doctypes UNION ALL with the
	columns this module reads; legacy consolidated SIs are dropped inside the
	union (each one duplicates POS Invoices already present)."""
	return sales_invoice_union(
		"si.name, si.docstatus, si.is_pos, si.is_return, si.company, si.posting_date,"
		" si.posting_time, si.pos_profile, si.base_grand_total, si.base_net_total,"
		" si.base_total_taxes_and_charges",
		where="si.docstatus = 1 AND si.is_pos = 1",
	)


def _totals_rows(where, params):
	return frappe.db.sql(
		f"""
		SELECT
			si.company,
			SUM(CASE WHEN si.is_return = 0 THEN si.base_grand_total ELSE 0 END) AS gross,
			SUM(CASE WHEN si.is_return = 1 THEN ABS(si.base_grand_total) ELSE 0 END) AS refunds,
			SUM(si.base_grand_total) AS net_tax_incl,
			SUM(si.base_net_total) AS net_pretax,
			SUM(si.base_total_taxes_and_charges) AS taxes,
			COUNT(CASE WHEN si.is_return = 0 THEN 1 END) AS orders,
			COUNT(CASE WHEN si.is_return = 1 THEN 1 END) AS refund_orders
		FROM {_invoice_from()}
		WHERE {where}
		GROUP BY si.company
		""",
		params,
		as_dict=True,
	)


def _metrics_from_totals(rows, currency_map, default_ccy):
	out = {key: metric_from_rows(rows, key, currency_map, default_ccy) for key in MONEY_KEYS}
	out["orders"] = int(sum(r.orders for r in rows))
	out["refund_orders"] = int(sum(r.refund_orders for r in rows))

	# APC = tax-incl net / non-refund orders, per currency.
	by_ccy = {}
	for r in rows:
		ccy = currency_map.get(r.company)
		if ccy and r.orders:
			by_ccy[ccy] = round(by_ccy.get(ccy, 0) + flt(r.net_tax_incl) / r.orders, 2)
	out["apc"] = make_metric(by_ccy, default_ccy)
	return out


def _metrics_for_window(scope_companies, profiles, start, end, cutoff, currency_map, default_ccy):
	where, params = _si_window_where(scope_companies, profiles, start, end, cutoff)
	return _metrics_from_totals(_totals_rows(where, params), currency_map, default_ccy)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


def _turnover_section(scope, currency_map, default_ccy, window, mtd_rows):
	prev = _metrics_for_window(
		scope["companies"],
		scope["profiles"],
		window["prev_comparable_start"],
		window["prev_comparable_end"],
		None,
		currency_map,
		default_ccy,
	)

	change = {}
	net_by_ccy = split_by_currency(mtd_rows, "net_tax_incl", currency_map)
	for ccy, current in net_by_ccy.items():
		prior = prev["net_tax_incl"]["by_currency"].get(ccy)
		if prior:
			change[ccy] = growth_pct(current, prior)

	return {
		"this_month_net": metric_from_rows(mtd_rows, "net_tax_incl", currency_map, default_ccy),
		"prev_comparable_net": prev["net_tax_incl"],
		"change_pct_vs_prev_comparable": change,
		"taxes": metric_from_rows(mtd_rows, "taxes", currency_map, default_ccy),
		"net_pretax": metric_from_rows(mtd_rows, "net_pretax", currency_map, default_ccy),
		"refunds": metric_from_rows(mtd_rows, "refunds", currency_map, default_ccy),
		"orders": int(sum(r.orders for r in mtd_rows)),
		"refund_orders": int(sum(r.refund_orders for r in mtd_rows)),
		"note": _(
			"Net (tax-incl) = gross minus refunds; pre-tax and taxes are net figures and do not reconcile to tax-incl totals."
		),
	}


def _daily_section(scope, currency_map, default_ccy, window):
	profiles = scope["profiles"]
	day_metric = _metrics_for_window(
		scope["companies"],
		profiles,
		window["day"],
		window["day"],
		window["day_cutoff"],
		currency_map,
		default_ccy,
	)
	prior_metric = _metrics_for_window(
		scope["companies"],
		profiles,
		window["prior_weekday"],
		window["prior_weekday"],
		window["compare_cutoff"],
		currency_map,
		default_ccy,
	)
	last_week_metric = _metrics_for_window(
		scope["companies"],
		profiles,
		window["last_week_same"],
		window["last_week_same"],
		window["compare_cutoff"],
		currency_map,
		default_ccy,
	)
	return {
		"date": window["day"],
		"is_today": window["day_cutoff"] is not None,
		"cutoff": window["day_cutoff"].strftime("%H:%M") if window["day_cutoff"] else None,
		"totals": day_metric,
		"prior_weekday": {**prior_metric, "date": window["prior_weekday"]},
		"last_week_same": {**last_week_metric, "date": window["last_week_same"]},
		"growth_vs_prior_weekday_pct": _growth_by_ccy(day_metric, prior_metric),
		"growth_vs_last_week_pct": _growth_by_ccy(day_metric, last_week_metric),
		"daily_target_note": _(
			"Daily target is the monthly target pro-rated by days in month, not a separately set figure."
		),
	}


def _growth_by_ccy(current, prior):
	out = {}
	for ccy, now_value in current["net_tax_incl"]["by_currency"].items():
		out[ccy] = growth_pct(now_value, prior["net_tax_incl"]["by_currency"].get(ccy))
	return out


def _hours_section(where, params):
	rows = frappe.db.sql(
		f"""
		SELECT
			HOUR(si.posting_time) AS hour,
			SUM(si.base_grand_total) AS net_sales,
			COUNT(CASE WHEN si.is_return = 0 THEN 1 END) AS orders
		FROM {_invoice_from()}
		WHERE {where}
		GROUP BY HOUR(si.posting_time)
		ORDER BY hour
		""",
		params,
		as_dict=True,
	)
	rows = [
		{
			"hour": int(r.hour),
			"net_sales": flt(r.net_sales),
			"orders": int(r.orders),
			"apc": flt(flt(r.net_sales) / r.orders) if r.orders else None,
		}
		for r in rows
	]
	with_orders = [r for r in rows if r["orders"] > 0]
	top = sorted(with_orders, key=lambda r: (-r["net_sales"], r["hour"]))[:3]
	lowest = sorted(with_orders, key=lambda r: (r["net_sales"], r["hour"]))[:3]
	return {"rows": rows, "peak": top[0] if top else None, "top": top, "lowest": lowest}


def _item_from(where):
	# ponytail: the item union is joined to the invoice union through derived
	# tables (no indexes across them) — fine at POS volumes; push branches
	# into a real view if EXPLAIN ever disagrees.
	return f"""
	FROM {sales_invoice_item_union(
		"sii.parent, sii.item_code, sii.item_name, sii.item_group, sii.qty,"
		" sii.base_net_amount, sii.pos_package_role",
		where="si.docstatus = 1 AND si.is_pos = 1",
	)}
	INNER JOIN {_invoice_from()} ON si.name = sii.parent
	WHERE {where}
	  AND (sii.pos_package_role IS NULL OR sii.pos_package_role <> '{COMPONENT_ROLE}')
"""


def _favorite_product(where, params):
	row = frappe.db.sql(
		f"""
		SELECT
			sii.item_code,
			MAX(sii.item_name) AS item_name,
			MAX(sii.item_group) AS item_group,
			SUM(sii.qty) AS qty,
			SUM(sii.base_net_amount) AS net_amount
		{_item_from(where)}
		GROUP BY sii.item_code
		ORDER BY qty DESC, net_amount DESC, sii.item_code
		LIMIT 1
		""",
		params,
		as_dict=True,
	)
	if not row:
		return None
	r = row[0]
	return {
		"item_code": r.item_code,
		"item_name": r.item_name,
		"item_group": r.item_group,
		"qty": flt(r.qty),
		"net_amount": flt(r.net_amount),
	}


def _product_ranking(where, params, category, page, page_size):
	page = _to_int(page, default=1, lo=1)
	page_size = _to_int(page_size, default=10, lo=1, hi=MAX_PAGE_SIZE)
	item_from = _item_from(where)
	bind = dict(params)

	cat_where = ""
	if category:
		bind["item_groups"] = _item_group_and_descendants(category)
		cat_where = " AND sii.item_group IN %(item_groups)s"

	total = frappe.db.sql(f"SELECT COUNT(DISTINCT sii.item_code) AS total {item_from}{cat_where}", bind)[0][0]
	total_sales = (
		frappe.db.sql(f"SELECT SUM(sii.base_net_amount) AS total {item_from}{cat_where}", bind)[0][0] or 0
	)

	offset = (page - 1) * page_size
	rows = frappe.db.sql(
		f"""
		SELECT
			sii.item_code,
			MAX(sii.item_name) AS item_name,
			MAX(sii.item_group) AS item_group,
			SUM(sii.qty) AS qty,
			SUM(sii.base_net_amount) AS net_amount
		{item_from}{cat_where}
		GROUP BY sii.item_code
		ORDER BY net_amount DESC, qty DESC, sii.item_code
		LIMIT {page_size} OFFSET {offset}
		""",
		bind,
		as_dict=True,
	)
	return {
		"rows": [
			{
				"item_code": r.item_code,
				"item_name": r.item_name,
				"item_group": r.item_group,
				"qty": flt(r.qty),
				"net_amount": flt(r.net_amount),
				"share_pct": ratio(r.net_amount, total_sales),
			}
			for r in rows
		],
		"total": int(total),
		"page": page,
		"page_size": page_size,
		"category": category or None,
		"categories": _category_options(item_from, params),
		"scope_total_net": flt(total_sales),
	}


def _category_options(item_from, params):
	rows = frappe.db.sql(
		f"SELECT DISTINCT sii.item_group {item_from} ORDER BY sii.item_group", params, as_dict=True
	)
	return [r.item_group for r in rows if r.item_group]


def _item_group_and_descendants(group):
	row = frappe.db.get_value("Item Group", group, ["lft", "rgt"], as_dict=True)
	if not row:
		frappe.throw(_("Item Group {0} does not exist").format(group), frappe.DoesNotExistError)
	return frappe.get_all(
		"Item Group", filters={"lft": [">=", row.lft], "rgt": ["<=", row.rgt]}, pluck="name"
	)


def _item_group_names():
	"""All existing Item Groups (tree order) for the per-card category selects."""
	return frappe.get_all("Item Group", pluck="name", order_by="lft")


def _clean_category(value):
	"""Accept only a plain group name; junk (lists, blanks) becomes 'not chosen'."""
	if not isinstance(value, str):
		return None
	return value.strip() or None


def _empty_category_card(category=None, invalid=False, currency=None, excluded_currencies=None):
	return {
		"category": category,
		"invalid": invalid,
		"currency": currency,
		"method": _("net revenue, pre-tax, positive only, sub-groups included"),
		"items": [],
		"other_net": 0.0,
		"other_share_pct": None,
		"category_total": 0.0,
		"items_with_sales": 0,
		"excluded_nonpositive": 0,
		"excluded_currencies": excluded_currencies or [],
	}


def _category_products(companies, profiles, start, end, cutoff, currency_map, default_ccy, categories):
	"""Datasets for the two independent "top items within a category" cards.

	Each card is one grouped query over the FULL category (incl. sub-groups) —
	never the paginated Product Ranking — and slots "a"/"b" stay independent:
	choosing one never influences the other or the global ranking filter.
	Exactly one query per chosen category (max two, no N+1). Same currency rule
	as ``_category_top``: only companies whose base currency is the scope
	default are aggregated; others are listed, never merged. Negative/zero-net
	items are reported as excluded so shares are computed over positive
	revenue only (donut/bar values can never go negative).
	"""
	ccy_companies = [c for c in companies if currency_map.get(c) == default_ccy]
	other_ccy = sorted({currency_map.get(c) for c in companies if currency_map.get(c) != default_ccy} - {None})

	def card_for(category):
		if not category:
			return _empty_category_card(currency=default_ccy, excluded_currencies=other_ccy)
		if not ccy_companies:
			return _empty_category_card(
				category=category, currency=default_ccy, excluded_currencies=other_ccy
			)
		try:
			groups = _item_group_and_descendants(category)
		except frappe.DoesNotExistError:
			# stale stored preference: reported, not raised, so the page can
			# clear it and fall back without breaking the whole payload
			return _empty_category_card(
				category=category, invalid=True, currency=default_ccy, excluded_currencies=other_ccy
			)
		where, params = _si_window_where(ccy_companies, profiles, start, end, cutoff)
		params["item_groups"] = groups
		rows = frappe.db.sql(
			f"""
			SELECT
				sii.item_code,
				MAX(sii.item_name) AS item_name,
				SUM(sii.qty) AS qty,
				SUM(sii.base_net_amount) AS net_amount
			{_item_from(where)}
			  AND sii.item_group IN %(item_groups)s
			GROUP BY sii.item_code
			""",
			params,
			as_dict=True,
		)
		positive = [r for r in rows if flt(r.net_amount) > 0]
		positive.sort(key=lambda r: (-flt(r.net_amount), -flt(r.qty), r.item_code))
		total = sum(flt(r.net_amount) for r in positive)
		top = positive[:5]
		other = total - sum(flt(r.net_amount) for r in top)
		card = _empty_category_card(category=category, currency=default_ccy, excluded_currencies=other_ccy)
		card.update(
			{
				"items": [
					{
						"item_code": r.item_code,
						"item_name": r.item_name,
						"qty": flt(r.qty),
						"net_amount": flt(r.net_amount),
						"share_pct": ratio(r.net_amount, total),
					}
					for r in top
				],
				"other_net": flt(other),
				"other_share_pct": ratio(other, total),
				"category_total": flt(total),
				"items_with_sales": len(positive),
				"excluded_nonpositive": len(rows) - len(positive),
			}
		)
		return card

	return {slot: card_for(category) for slot, category in categories.items()}


def _outlet_ranking(where, params, currency_map):
	"""Outlet = Company (the store a customer knows), not the POS Profile.

	Profiles are POS registers; HQ reports rank companies and keep the profile
	breakdown nested so no number is hidden. Share % is computed inside one
	currency only — companies in other currencies are ordered but never summed
	with them.
	"""
	rows = frappe.db.sql(
		f"""
		SELECT
			si.company,
			SUM(CASE WHEN si.is_return = 0 THEN si.base_grand_total ELSE 0 END) AS gross,
			SUM(si.base_grand_total) AS net_tax_incl,
			COUNT(CASE WHEN si.is_return = 0 THEN 1 END) AS orders
		FROM {_invoice_from()}
		WHERE {where}
		GROUP BY si.company
		ORDER BY net_tax_incl DESC
		LIMIT 200
		""",
		params,
		as_dict=True,
	)
	# One extra grouped query for the whole profile breakdown (never per-row).
	profile_rows = frappe.db.sql(
		f"""
		SELECT
			si.company,
			si.pos_profile,
			SUM(si.base_grand_total) AS net_tax_incl,
			COUNT(CASE WHEN si.is_return = 0 THEN 1 END) AS orders
		FROM {_invoice_from()}
		WHERE {where}
		GROUP BY si.company, si.pos_profile
		ORDER BY net_tax_incl DESC
		""",
		params,
		as_dict=True,
	)
	profiles_by_company = {}
	for p in profile_rows:
		profiles_by_company.setdefault(p.company, []).append(
			{
				"pos_profile": p.pos_profile,
				"net_tax_incl": flt(p.net_tax_incl),
				"orders": int(p.orders),
			}
		)

	currency_totals = {}
	for r in rows:
		ccy = currency_map.get(r.company)
		if ccy:
			currency_totals[ccy] = currency_totals.get(ccy, 0) + flt(r.net_tax_incl)

	out = []
	for r in rows:
		ccy = currency_map.get(r.company)
		out.append(
			{
				"company": r.company,
				"currency": ccy,
				"gross": flt(r.gross),
				"net_tax_incl": flt(r.net_tax_incl),
				"orders": int(r.orders),
				"apc": flt(flt(r.gross) / r.orders) if r.orders else None,
				"share_pct": ratio(r.net_tax_incl, currency_totals.get(ccy)),
				"profiles": profiles_by_company.get(r.company, []),
			}
		)
	return out


def _category_top(companies, profiles, start, end, cutoff, currency_map, default_ccy):
	"""Top 5 item groups for the category donut.

	Donut segments must never mix currencies or go negative, so the aggregate
	runs only over companies whose base currency is the scope default and
	keeps positive-net groups only (a return-heavy group with net <= 0 is
	reported, not drawn). Method is labeled in the payload.
	"""
	ccy_companies = [c for c in companies if currency_map.get(c) == default_ccy]
	other_ccy = sorted({currency_map.get(c) for c in companies if currency_map.get(c) != default_ccy} - {None})
	if not ccy_companies:
		return {
			"currency": default_ccy,
			"method": _("net revenue, pre-tax, positive only"),
			"rows": [],
			"groups_with_sales": 0,
			"excluded_nonpositive": 0,
			"excluded_currencies": other_ccy,
		}
	where, params = _si_window_where(ccy_companies, profiles, start, end, cutoff)
	rows = frappe.db.sql(
		f"""
		SELECT
			sii.item_group AS item_group,
			SUM(sii.qty) AS qty,
			SUM(sii.base_net_amount) AS net_amount
		{_item_from(where)}
		GROUP BY sii.item_group
		ORDER BY net_amount DESC
		""",
		params,
		as_dict=True,
	)
	positive = [r for r in rows if flt(r.net_amount) > 0]
	return {
		"currency": default_ccy,
		"method": _("net revenue, pre-tax, positive only"),
		"rows": [
			{
				"item_group": r.item_group or _("Ungrouped"),
				"qty": flt(r.qty),
				"net_amount": flt(r.net_amount),
				"share_pct": ratio(r.net_amount, sum(flt(x.net_amount) for x in positive)),
			}
			for r in positive[:5]
		],
		"groups_with_sales": len(positive),
		"excluded_nonpositive": len(rows) - len(positive),
		"excluded_currencies": other_ccy,
	}


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------


def _targets_section(companies, currency_map, default_ccy, window, monthly, mtd_rows):
	month_start = window["month_start"]
	rows = frappe.get_all(
		"POS Monthly Target",
		filters={"company": ["in", companies], "month_start": month_start},
		fields=["company", "target_sales", "target_transactions"],
	)
	missing = [c for c in companies if c not in {r.company for r in rows}]

	# Per-outlet rows: unlike the aggregate below, a missing target only blanks
	# that outlet's cells — the table shows whatever is configured.
	targets_by_company = {r.company: r for r in rows}
	mtd_by_company = {r.company: r for r in mtd_rows}
	days_elapsed = window["days_elapsed"]
	days_in_month = window["days_in_month"]
	by_company = []
	for company in companies:
		target = targets_by_company.get(company)
		actual = mtd_by_company.get(company)
		net = flt(actual.net_tax_incl) if actual else 0.0
		orders = int(actual.orders) if actual else 0
		target_sales = flt(target.target_sales) if target else None
		target_tx = int(target.target_transactions or 0) if target else None
		projected = (
			round(net / days_elapsed * days_in_month, 2)
			if target and days_elapsed
			else None
		)
		by_company.append(
			{
				"company": company,
				"currency": currency_map.get(company),
				"missing": target is None,
				"target_sales": target_sales,
				"target_transactions": target_tx,
				"mtd_net_tax_incl": round(net, 2),
				"mtd_orders": orders,
				"mtd_apc": round(net / orders, 2) if orders else None,
				"achievement_sales_pct": ratio(net, target_sales) if target else None,
				"achievement_transactions_pct": ratio(orders, target_tx) if target else None,
				"projected_sales": projected,
				"projected_achievement_pct": (
					ratio(projected, target_sales) if target and projected is not None else None
				),
			}
		)

	sales_target = {}
	tx_target = 0
	for r in rows:
		ccy = currency_map.get(r.company)
		if ccy:
			sales_target[ccy] = sales_target.get(ccy, 0) + flt(r.target_sales)
		tx_target += int(r.target_transactions or 0)

	if not rows or missing:
		return {
			"available": False,
			"month_start": month_start,
			"missing_companies": missing[:10],
			"missing_count": len(missing),
			"notice": _("Monthly target is not set for every scoped company, so achievement is not shown."),
			"by_company": by_company,
		}

	target_metric = make_metric(sales_target, default_ccy)
	days_in_month = window["days_in_month"]

	achievement, surplus, projection = {}, {}, {}
	for ccy, target in sales_target.items():
		actual = monthly["net_tax_incl"]["by_currency"].get(ccy, 0)
		achievement[ccy] = ratio(actual, target)
		surplus[ccy] = round(actual - target, 2)
		if window["days_elapsed"]:
			projected = actual / window["days_elapsed"] * days_in_month
			projection[ccy] = {
				"projected_sales": round(projected, 2),
				"projected_achievement_pct": ratio(projected, target),
				"projected_surplus": round(projected - target, 2),
			}

	if window["days_elapsed"]:
		projected_orders = round(monthly["orders"] / window["days_elapsed"] * days_in_month)

	return {
		"available": True,
		"month_start": month_start,
		"by_company": by_company,
		"target_sales": target_metric,
		"target_transactions": tx_target,
		"achievement_sales_pct": achievement,
		"achievement_transactions_pct": ratio(monthly["orders"], tx_target),
		"surplus_sales": surplus,
		"apc_target": {
			ccy: (round(v / tx_target, 2) if tx_target else None) for ccy, v in sales_target.items()
		},
		"daily_target_sales": {
			ccy: (round(v / days_in_month, 2) if days_in_month else None) for ccy, v in sales_target.items()
		},
		"daily_target_note": _(
			"Daily target = monthly target / days in month (pro-rata), not a separately set figure."
		),
		"projection": projection,
		"projection_note": _(
			"Projection = MTD actual / days elapsed x days in month; days elapsed counts today."
		),
		"projected_orders": projected_orders if window["days_elapsed"] else None,
		"projected_achievement_transactions_pct": (
			ratio(projected_orders, tx_target) if window["days_elapsed"] else None
		),
		"projected_surplus_orders": (
			projected_orders - tx_target if window["days_elapsed"] and tx_target else None
		),
		"apc_projection_note": _(
			"APC is an average, so its projection equals the MTD figure (never day-extrapolated)."
		),
	}


def _overall_target_section(scope, currency_map):
	"""Per-outlet payback ("balik modal") target: cumulative POS net sales
	(tax incl.) vs the one-time Overall Sales Target on the Company master.

	Each outlet counts from its own "Counted From" date (empty = all time), so
	one query pins every company's lower bound instead of one query per outlet.
	Outlets without an overall target are simply absent from ``by_company``.
	"""
	# Guard: before the app's first migrate the custom fields do not exist yet;
	# reading them would error, so report the section unavailable instead.
	if not frappe.get_meta("Company").has_field("pos_overall_sales_target"):
		return {"available": False, "by_company": {}}
	configured = {
		r.name: r
		for r in frappe.get_all(
			"Company",
			filters={"name": ["in", scope["companies"]]},
			fields=["name", "pos_overall_sales_target", "pos_overall_target_from"],
		)
		if flt(r.pos_overall_sales_target) > 0
	}
	if not configured:
		return {"available": False, "by_company": {}}

	where = ["si.docstatus = 1", "si.is_pos = 1", "si.company IN %(companies)s"]
	params = {"companies": list(configured)}
	if scope["profiles"] is not None:
		params["profiles"] = list(scope["profiles"])
		where.append("si.pos_profile IN %(profiles)s")
	bounds = []
	for i, (name, row) in enumerate(configured.items()):
		if row.pos_overall_target_from:
			params[f"c{i}"] = name
			params[f"d{i}"] = row.pos_overall_target_from
			bounds.append(f"(si.company = %(c{i})s AND si.posting_date >= %(d{i})s)")
	if bounds:
		where.append("(" + " OR ".join(bounds) + ")")

	cumulative = {}
	for r in frappe.db.sql(
		f"""
		SELECT si.company,
			SUM(si.base_grand_total) AS net_tax_incl,
			COUNT(CASE WHEN si.is_return = 0 THEN 1 END) AS orders
		FROM {_invoice_from()}
		WHERE {" AND ".join(where)}
		GROUP BY si.company
		""",
		params,
		as_dict=True,
	):
		cumulative[r.company] = r

	by_company = {}
	for company, row in configured.items():
		cum = cumulative.get(company)
		net = flt(cum.net_tax_incl) if cum else 0.0
		orders = int(cum.orders) if cum else 0
		target = flt(row.pos_overall_sales_target)
		by_company[company] = {
			"currency": currency_map.get(company),
			"overall_target": target,
			"from_date": str(row.pos_overall_target_from) if row.pos_overall_target_from else None,
			"cumulative_net_tax_incl": round(net, 2),
			"cumulative_orders": orders,
			"achievement_pct": ratio(net, target),
			"remaining": round(target - net, 2),
		}
	return {"available": True, "by_company": by_company}


def _empty_payload(scope, notice):
	return {
		"scope": scope,
		"notice": notice,
		"windows": {},
		"monthly": {},
		"range": {},
		"turnover": {},
		"daily": {},
		"hours": {"rows": [], "peak": None, "top": [], "lowest": []},
		"favorite_product": None,
		"product_ranking": {"rows": [], "total": 0, "page": 1, "page_size": 10, "categories": []},
		"outlet_ranking": [],
		"item_groups": [],
		"category_products": {"a": {}, "b": {}},
		"category_top": {"rows": [], "currency": None, "groups_with_sales": 0},
		"highlights": {},
		"channels": {"available": False},
		"pax": {"available": False},
		"targets": {"available": False, "by_company": [], "overall": {"available": False, "by_company": {}}},
	}
