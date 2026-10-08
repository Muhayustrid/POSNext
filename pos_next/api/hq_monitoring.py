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
- Packages rank as their own item: the package line's qty plus the money of
  its whole instance (header + components), so legacy (money on the header)
  and allocation mode (money on the components) give the same figure.
  Component rows are not counted as standalone sales.
- Quantities are signed: returns contribute negative qty.
- Two optional "top items within category" cards (``category_a``/``category_b``):
  one grouped query each over the full Item Group tree (sub-groups included),
  positive net revenue only, share % against the whole category, independent
  of each other.
- Summary only: the full product list is the POS Product Sales Report and the
  return list the POS Return Report; both reuse this module's scope helpers.

Permissions:
- Role gate (System Manager / Accounts Manager / Sales Manager / POSNext
  Manager), then company scope via User Permissions on "Company" and POS
  Profile scope via User Permissions on "POS Profile" (see pos_next.hq_scope).
- A forged company (outside the user's scope) raises PermissionError.

Time: server timezone. Today's windows are cut at the current server time;
historical days are full days.

Period semantics (labeled explicitly in the UI):
- Monthly monitoring / targets: month-to-date of ``to_date``
  (``from_date`` is deliberately ignored there — MTD never shifts).
- Daily monitoring: ``to_date`` vs its prior weekday (same elapsed cutoff).
- Hero cards, peak hours, rankings and donuts: the selected range
  ``from_date .. to_date`` (cutoff at "now" when ``to_date`` is today).

Targets are measured against a configurable basis (POS Settings, see
pos_next.target_basis): Net Sales (default — the behaviour before the switch),
Gross Profit (HPP from the invoices' Stock Ledger Entries, net of returns,
with a zero-cost-row count) or Net Profit (the outlet company's whole books
from GL Entry; company level, so a POS Profile filter cannot narrow it).
Formulas (achievement, linear projection, daily pro-rata) never change with
the basis — only the numerator does.
"""

import hashlib
import json
from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import cint, flt, get_first_day, get_last_day, getdate, now_datetime, nowdate

from pos_next.hq_scope import (
	expand_company_descendants,
	get_permitted_companies,
	get_permitted_pos_profiles,
	resolve_company_scope,
)
from pos_next.invoice_type import (
	get_pos_invoice_doctype,
	package_sold_amount,
	package_sold_row_filter,
	sales_invoice_item_union,
	sales_invoice_union,
)
from pos_next.target_basis import (
	GROSS_PROFIT,
	NET_PROFIT,
	NET_SALES,
	TARGET_BASIS_LABELS,
	get_target_basis,
)

HQ_ROLES = ("System Manager", "Accounts Manager", "Sales Manager", "POSNext Manager")

MAX_RANGE_DAYS = 366

MONEY_KEYS = ("gross", "refunds", "net_tax_incl", "net_pretax", "taxes")

# Cache-aside TTL for the heavy read-only monitoring aggregates: Redis holds a
# computed section for at most this long, then the next request recomputes it.
# 5 minutes: fresh enough for a dashboard, long enough to absorb page reloads.
HQ_MONITORING_CACHE_PREFIX = "hq_sales_monitoring"
HQ_MONITORING_CACHE_TTL = 300  # seconds

# Live Activity shows the latest few invoices; the full list is the invoice
# list view.
HQ_RECENT_LIMIT = 8


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
# Cache-aside for the heavy read-only sections
# ---------------------------------------------------------------------------


def _monitoring_cache_key(section, scope, parts):
	"""Redis key for one cached section.

	The key material embeds the session user, the RESOLVED company /
	POS-profile scope and every filter that can change the section's numbers
	(company, from/to, category, page, sort), so a cached payload can never be
	served to a different user, permission scope or filter set. The cutoff
	time of day is deliberately left out — today's window would otherwise
	miss on every request — and the TTL bounds that staleness instead.
	"""
	material = {
		"user": frappe.session.user,
		"lang": getattr(frappe.local, "lang", None),
		"companies": sorted(scope["companies"]),
		"profiles": sorted(scope["profiles"]) if scope["profiles"] is not None else None,
		"parts": parts,
	}
	digest = hashlib.sha1(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()
	return f"{HQ_MONITORING_CACHE_PREFIX}:{frappe.session.user}:{section}:{digest}"


def _cached_section(section, scope, generator, **parts):
	"""Cache-aside read: the Redis value, or compute and store it with the TTL.

	Only pure aggregate reads pass through here — the role gate and the scope
	resolution in get_sales_monitoring stay uncached and run on every request.
	"""
	if frappe.flags.in_test:
		# Tests mutate invoices/targets between calls and re-read the payload;
		# serving a Redis-cached aggregate would hide those writes.
		return generator()
	cache = frappe.cache()
	key = _monitoring_cache_key(section, scope, parts)
	# Manual miss/store: frappe's get_value skips the generator when
	# expires=True, which is exactly the TTL mode we want (the Redis entry
	# carries expires_in_sec; the in-process copy dies with the request).
	value = cache.get_value(key, expires=True)
	if value is None:
		value = generator()
		cache.set_value(key, value, expires_in_sec=HQ_MONITORING_CACHE_TTL)
	return value


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@frappe.whitelist()
def get_sales_monitoring(
	company=None,
	from_date=None,
	to_date=None,
	include_descendants=0,
	category_a=None,
	category_b=None,
):
	"""HQ Sales Monitoring summary in a single read-only payload. Detail
	lists live in their own reports (POS Product Sales Report, POS Return
	Report) so this stays a fixed, small set of aggregates."""
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
	# Raw MTD rows feed monthly/targets: cache them once so both sections
	# share one snapshot and a cold cache runs the union query once.
	mtd_rows = _cached_section(
		"mtd_totals", scope, lambda: _totals_rows(mtd_where, mtd_params), to_date=to_date
	)
	monthly = _cached_section(
		"monthly",
		scope,
		lambda: _metrics_from_totals(mtd_rows, currency_map, default_ccy),
		to_date=to_date,
	)

	# Selected range (hero cards, hours, rankings, donuts) — MTD above stays
	# fixed to the month even when the user narrows the range.
	range_where, range_params = _si_window_where(
		scope["companies"], profiles, from_date, to_date, window["mtd_cutoff"]
	)
	# cut_at is request-time clock, not aggregate data: it stays outside the
	# cached dict so a cache hit never shows another request's cutoff time.
	range_metrics = _cached_section(
		"range",
		scope,
		lambda: _metrics_for_window(
			scope["companies"], profiles, from_date, to_date, window["mtd_cutoff"], currency_map, default_ccy
		),
		from_date=from_date,
		to_date=to_date,
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
			**range_metrics,
			"cut_at": window["mtd_cutoff"].strftime("%H:%M") if window["mtd_cutoff"] else None,
		},
		"daily": _cached_section(
			"daily", scope, lambda: _daily_section(scope, currency_map, default_ccy, window), to_date=to_date
		),
		"hours": _cached_section(
			"hours",
			scope,
			lambda: _hours_section(range_where, range_params),
			from_date=from_date,
			to_date=to_date,
		),
		"outlet_ranking": _cached_section(
			"outlet_ranking",
			scope,
			lambda: _outlet_ranking(range_where, range_params, currency_map),
			from_date=from_date,
			to_date=to_date,
		),
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
		"payments": _cached_section(
			"payments",
			scope,
			lambda: _payments_section(
				range_where, range_params, default_ccy, currency_map, scope["companies"]
			),
			from_date=from_date,
			to_date=to_date,
		),
		"recent": _recent_section(range_where, range_params, limit=HQ_RECENT_LIMIT),
		"returns": _returns_section(range_where, range_params, default_ccy),
		"category_top": _category_top(
			scope["companies"], profiles, from_date, to_date, window["mtd_cutoff"], currency_map, default_ccy
		),
		"generated_at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
	}
	result["target_basis"] = _target_basis_payload()
	# The payback (overall) target is all-time, so its key carries no date —
	# scope + user already pin it. A shallow copy keeps the cached targets dict
	# itself un-mutated.
	result["targets"] = {
		**_cached_section(
			"targets",
			scope,
			lambda: _targets_section(
				scope["companies"], currency_map, default_ccy, window, monthly, mtd_rows,
				profiles=scope["profiles"],
			),
			to_date=to_date,
		),
		"overall": _cached_section(
			"targets_overall", scope, lambda: _overall_target_section(scope, currency_map)
		),
	}
	result["shifts"] = _shifts_section(scope, currency_map)
	return result

@frappe.whitelist()
def set_outlet_target(
	company=None,
	month_start=None,
	target_sales=None,
	overall_target=None,
	overall_from=None,
):
	"""Set an outlet's targets from the Outlet Targets page.

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
	overall = _parse_amount(overall_target, _("Overall Sales Target"))
	start = _parse_date(overall_from)

	result = {"monthly": None, "overall_updated": False}

	if sales is not None:
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
		doc.target_sales = sales
		doc.save()
		result["monthly"] = doc.name

	if overall is not None or start:
		if not frappe.has_permission("Company", "write", doc=company):
			frappe.throw(
				_("You need write permission on Company {0}").format(company),
				frappe.PermissionError,
			)
		if overall is None:
			# Blank target = keep the stored one, so the counted-from date can
			# be (re)set on its own — same "blank keeps stored" rule as monthly.
			overall = flt(frappe.db.get_value("Company", company, "pos_overall_sales_target"))
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
def get_outlet_targets(
	month_start=None,
	company=None,
	search=None,
	start=0,
	page_length=None,
):
	"""Per-outlet target sheet for the "Outlet Targets" desk page (read-only).

	One row for EVERY company in the user's full permission window (selling or
	not), covering one calendar month: monthly target vs month-to-date actual,
	linear projection to month end, and the payback (overall) target. The MTD
	dataset is the same one the monitoring page uses, so the numbers reconcile:
	current month stops at today (cut at "now"); a closed month reports its
	full actuals (projection = actual); a future month has no elapsed days, so
	its actuals are 0 and the projection is null.

	Filtering/pagination run BEFORE the expensive aggregate queries: only the
	companies on the requested page are computed. ``page_length=None`` (the
	default) returns all rows — the old full-sheet behaviour.
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
	# Exact company pick wins over the substring search.
	if company:
		companies = [c for c in companies if c == company]
	elif search:
		needle = search.strip().lower()
		if needle:
			companies = [c for c in companies if needle in c.lower()]
	total_count = len(companies)

	start = cint(start)
	page_length = cint(page_length) if page_length not in (None, "", 0) else None
	if page_length:
		page = companies[start : start + page_length]
	else:
		page = companies[start:]
	if not page:
		return {
			"month_start": str(month),
			"month_end": str(month_end),
			"days_elapsed": 0,
			"total_count": total_count,
			"start": start,
			"page_length": page_length,
			"target_basis": _target_basis_payload(),
			"rows": [],
			"generated_at": now_datetime().strftime("%Y-%m-%d %H:%M:%S"),
		}

	currency_map = _currency_map(page)

	if month == get_first_day(today):
		days_elapsed = (today - month).days + 1
		window_end, cutoff = today, now_datetime()
	elif month < get_first_day(today):
		days_elapsed = days_in_month
		window_end, cutoff = month_end, None
	else:
		days_elapsed = 0
		window_end, cutoff = month_end, None

	where, params = _si_window_where(page, scope["profiles"], month, window_end, cutoff)
	mtd_rows = _totals_rows(where, params)
	mtd_by_company = {r.company: r for r in mtd_rows}
	# The MTD numerator follows the configured monthly target basis; the sales
	# columns below (mtd_net_tax_incl, mtd_orders) stay sales on every basis.
	actuals = _basis_actuals(
		page,
		scope["profiles"],
		get_target_basis("monthly"),
		month,
		window_end,
		cutoff,
		totals_rows=mtd_rows,
	)

	targets_by_company = {
		r.company: r
		for r in frappe.get_all(
			"POS Monthly Target",
			filters={"company": ["in", page], "month_start": str(month)},
			fields=["company", "target_sales"],
		)
	}
	# Payback section rides the same page slice: scope copy narrowed to the
	# page's companies so the cumulative query only touches what is shown.
	overall_by_company = _overall_target_section(
		{**scope, "companies": page}, currency_map
	)["by_company"]

	rows = []
	for company in page:
		target = targets_by_company.get(company)
		actual = mtd_by_company.get(company)
		net = flt(actual.net_tax_incl) if actual else 0.0
		orders = int(actual.orders) if actual else 0
		basis_row = actuals.get(company) or {}
		value = flt(basis_row.get("value"))
		target_sales = flt(target.target_sales) if target else None
		# same linear projection as the monitoring targets section, on the basis
		projected_value = (
			round(value / days_elapsed * days_in_month, 2) if target and days_elapsed else None
		)
		rows.append(
			{
				"company": company,
				"currency": currency_map.get(company),
				"monthly": {
					"target_sales": target_sales,
					"missing": target is None,
				},
				"mtd_net_tax_incl": round(net, 2),
				"mtd_orders": orders,
				"achievement_sales_pct": ratio(value, target_sales) if target else None,
				"projected_sales": projected_value,
				"mtd_value": round(value, 2),
				"target_value": target_sales,
				"projected_value": projected_value,
				"zero_cost_rows": basis_row.get("zero_cost_rows"),
				"overall": overall_by_company.get(company),
			}
		)

	return {
		"month_start": str(month),
		"month_end": str(month_end),
		"days_elapsed": days_elapsed,
		"total_count": total_count,
		"start": start,
		"page_length": page_length,
		"target_basis": _target_basis_payload(),
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


def _main_currency(rows, amount_key):
	"""Report charts/cards can't mix currencies: show the one carrying the most
	money; rows in other currencies stay in the table only."""
	totals = {}
	for r in rows:
		totals[r.get("currency")] = totals.get(r.get("currency"), 0) + abs(flt(r.get(amount_key)))
	return max(totals, key=totals.get) if totals else None

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
		# Sargable cutoff (PERF-01): TIMESTAMP(posting_date, posting_time)
		# wraps the indexed column and disables the posting_date index, so the
		# instant comparison is split into a date range plus a same-day time
		# comparison. Deliberate superset: the old TIMESTAMP(date, NULL) is
		# NULL, so NULL-time rows were dropped outright; the IS NULL arm now
		# lets them through (a NULL-time invoice before the cutoff belongs in
		# the window). Submitted invoices always carry a time, so this only
		# affects hand-mangled rows.
		params["cutoff_date"] = getdate(cutoff)
		params["cutoff_time"] = cutoff.time()
		where.append(
			f"({alias}.posting_date < %(cutoff_date)s"
			f" OR ({alias}.posting_date = %(cutoff_date)s"
			f" AND ({alias}.posting_time <= %(cutoff_time)s OR {alias}.posting_time IS NULL)))"
		)
	if profiles is not None:
		params["profiles"] = list(profiles)
		where.append(f"{alias}.pos_profile IN %(profiles)s")
	return " AND ".join(where), params


def _invoice_from(where=""):
	"""Invoice source for ``si`` aliases: report doctypes UNION ALL with the
	columns this module reads; legacy consolidated SIs are dropped inside the
	union (each one duplicates POS Invoices already present).

	PERF-01: the caller's window ``where`` is pushed into every branch, so
	each branch is index-sized on the invoice's posting_date/company instead
	of filtering after the derived-table join. The outer WHERE keeps the
	identical predicates, so results are unchanged."""
	base = "si.docstatus = 1 AND si.is_pos = 1"
	if where:
		base += f" AND ({where})"
	return sales_invoice_union(
		"si.name, si.docstatus, si.is_pos, si.is_return, si.company, si.posting_date,"
		" si.posting_time, si.pos_profile, si.base_grand_total, si.base_net_total,"
		" si.base_total_taxes_and_charges",
		where=base,
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
		FROM {_invoice_from(where)}
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

	# APC = tax-incl net / non-refund orders, per currency — the division
	# happens once per currency on summed totals; summing per-outlet averages
	# would inflate the ticket as outlets are added.
	net_by_ccy, orders_by_ccy = {}, {}
	for r in rows:
		ccy = currency_map.get(r.company)
		if ccy:
			net_by_ccy[ccy] = net_by_ccy.get(ccy, 0.0) + flt(r.net_tax_incl)
			orders_by_ccy[ccy] = orders_by_ccy.get(ccy, 0) + int(r.orders)
	by_ccy = {
		ccy: round(net_by_ccy[ccy] / n, 2) for ccy, n in orders_by_ccy.items() if n
	}
	out["apc"] = make_metric(by_ccy, default_ccy)
	return out


def _metrics_for_window(scope_companies, profiles, start, end, cutoff, currency_map, default_ccy):
	where, params = _si_window_where(scope_companies, profiles, start, end, cutoff)
	return _metrics_from_totals(_totals_rows(where, params), currency_map, default_ccy)


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------


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
		FROM {_invoice_from(where)}
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
	# PERF-01: the window predicates ride into BOTH union branches (see
	# _invoice_from), so each branch is pruned on the invoice's posting_date /
	# company index before the derived tables are joined — the outer WHERE
	# keeps the identical predicates, so results are unchanged. ``where``
	# always comes from _si_window_where, which already carries
	# docstatus/is_pos, so it is forwarded as-is: re-wrapping them here (and
	# feeding the wrapped form back into _invoice_from, which adds its own)
	# only nested the same predicates redundantly.
	# Packages count as their own line (qty sold + the instance's money) and
	# their components are skipped — see invoice_type.package_sold_amount.
	columns = (
		"sii.parent, sii.item_code, sii.item_name, sii.item_group, sii.qty,"
		f" {package_sold_amount()} AS base_net_amount, sii.pos_package_role, sii.pos_package_instance"
	)
	return f"""
	FROM {sales_invoice_item_union(columns, where=where)}
	INNER JOIN {_invoice_from(where)} ON si.name = sii.parent
	WHERE {where}
	  AND {package_sold_row_filter()}
"""


def _item_group_and_descendants(group):
	row = frappe.db.get_value("Item Group", group, ["lft", "rgt"], as_dict=True)
	if not row:
		frappe.throw(_("Item Group {0} does not exist").format(group), frappe.DoesNotExistError)
	return frappe.get_all(
		"Item Group", filters={"lft": [">=", row.lft], "rgt": ["<=", row.rgt]}, pluck="name"
	)


def _payments_section(where, params, default_ccy, currency_map, companies):
	"""Mode-of-payment spread over the range: child payment rows joined to
	the invoice union, default-currency companies only (same rule as the
	category datasets). Amounts are base_amount — the money that actually
	moved, tax included; return rows ride along as negatives so the shares
	stay net and honest. ponytail: cash is not netted for change here —
	drawer semantics, not distribution semantics."""
	ccy_companies = [c for c in companies if currency_map.get(c) == default_ccy]
	if not ccy_companies:
		return {"rows": [], "currency": default_ccy, "modes_with_sales": 0}
	rows = frappe.db.sql(
		f"""
		SELECT sip.mode_of_payment, SUM(sip.base_amount) AS amount
		FROM {sales_invoice_union("si.name", where)}
		JOIN `tabSales Invoice Payment` sip ON sip.parent = si.name
		GROUP BY sip.mode_of_payment
		ORDER BY amount DESC
		""",
		params,
		as_dict=True,
	)
	positive = [r for r in rows if flt(r.amount) > 0]
	total = sum(flt(r.amount) for r in positive)
	return {
		"rows": [
			{
				"mode_of_payment": r.mode_of_payment,
				"amount": flt(r.amount),
				"share_pct": ratio(r.amount, total),
			}
			for r in positive[:5]
		],
		"currency": default_ccy,
		"modes_with_sales": len(positive),
	}


def _recent_section(where, params, limit=HQ_RECENT_LIMIT):
	"""Latest invoices in the range (sale or return) for the Live Activity
	card; the full list is the invoice list view the card links to."""
	rows = frappe.db.sql(
		f"""
		SELECT name, posting_date, posting_time, company, customer,
			grand_total, is_return, currency
		FROM {sales_invoice_union(
			"si.name, si.posting_date, si.posting_time, si.company, si.customer,"
			" si.grand_total, si.is_return, si.currency",
			where,
		)}
		ORDER BY posting_date DESC, posting_time DESC, name DESC
		LIMIT {cint(limit)}
		""",
		params,
		as_dict=True,
	)
	# Dominant payment per invoice: the child row with the biggest amount.
	names = [r.name for r in rows]
	modes = {}
	if names:
		for m in frappe.db.sql(
			"""
			SELECT parent, mode_of_payment
			FROM `tabSales Invoice Payment`
			WHERE parent IN %(hq_names)s
			ORDER BY parent, ABS(base_amount) DESC
			""",
			{"hq_names": list(names)},
			as_dict=True,
		):
			modes.setdefault(m.parent, m.mode_of_payment)
	return {
		# the "View all" target: the doctype new POS sales are created in
		"list_doctype": get_pos_invoice_doctype(),
		"rows": [
			{
				"name": r.name,
				"time": str(r.posting_time or "")[:5],
				"company": r.company,
				"customer": r.customer,
				"mode_of_payment": modes.get(r.name),
				"grand_total": flt(r.grand_total),
				"is_return": bool(r.is_return),
				"currency": r.currency,
			}
			for r in rows
		]
	}


def _returns_section(where, params, default_ccy):
	"""Return summary over the range; the per-invoice list lives in the
	POS Return Report (the card links there with the same filters)."""
	counts = frappe.db.sql(
		f"""
		SELECT
			SUM(CASE WHEN is_return = 1 THEN 1 ELSE 0 END) AS returns_count,
			SUM(CASE WHEN is_return = 0 THEN 1 ELSE 0 END) AS sales_count,
			SUM(CASE WHEN is_return = 1 THEN ABS(base_grand_total) ELSE 0 END) AS returns_value
		FROM {sales_invoice_union("si.is_return, si.base_grand_total", where)}
		""",
		params,
		as_dict=True,
	)[0]
	returns_count = cint(counts.returns_count)
	invoice_count = returns_count + cint(counts.sales_count)
	return {
		"value": flt(counts.returns_value),
		"count": returns_count,
		"invoice_count": invoice_count,
		"rate": flt(returns_count) / invoice_count if invoice_count else None,
		"currency": default_ccy,
	}


def _shifts_section(scope, currency_map, limit=10):
	"""Open shifts in company scope (newest first) with the
	recap service's cash semantics: cash_expected already nets change and
	cash returns.

	Batched (no per-shift N+1): one grouped query per aggregate for ALL
	shifts at once, master data once per request. Query budget: shift list
	(1) + users (1) + explicit cash modes (1, only when the legacy column
	exists) + payment-method rows (1, same gate) + gross+change (1) +
	payment rows (1) + Payment Entry shares (1) + opening details (1) +
	Mode of Payment types over the used-mode union (1) = 9 with the legacy
	column, 7 without. has_column probes are metadata-cache reads, not DB
	queries (frappe/database/database.py:1346-1374).
	"""
	shifts = frappe.db.sql(
		"""
		SELECT os.name, os.user, os.company, os.pos_profile, os.status,
			os.period_start_date
		FROM `tabPOS Opening Shift` os
		WHERE os.docstatus = 1 AND os.company IN %(hq_companies)s
			AND os.status = 'Open'
		ORDER BY os.period_start_date DESC,
			os.creation DESC
		LIMIT %(hq_limit)s
		""",
		{
			"hq_companies": list(scope["companies"]),
			"hq_limit": cint(limit),
		},
		as_dict=True,
	)
	if not shifts:
		return []
	users = {
		u.name: u.full_name
		for u in frappe.get_all(
			"User",
			filters={"name": ["in", [s.user for s in shifts if s.user]]},
			fields=["name", "full_name"],
		)
	}
	shift_names = [s.name for s in shifts]

	# Cash-mode chain, batched (same chain as cash_mode.get_cash_mode…,
	# pos_next/services/cash_mode.py:17-55): explicit
	# posa_cash_mode_of_payment, then the profile's own default Cash-type
	# row, then its first Cash-type row, else generic "Cash". The payment
	# lookups stay gated on the legacy column — without it the original
	# never enters the chain at all (cash_mode.py:24 closes before the
	# get_all calls) and returns "Cash" for every profile.
	cash_modes = {s.pos_profile: "Cash" for s in shifts}
	profiles = [s.pos_profile for s in shifts if s.pos_profile]
	has_cash_field = bool(profiles) and frappe.db.has_column(
		"POS Profile", "posa_cash_mode_of_payment"
	)
	explicit, ordered = _shift_cash_masters(profiles, has_cash_field)

	# One grouped query each for every shift, no per-shift loop.
	pay_by_shift = _shift_payment_rows(shift_names)
	pe_by_shift = _shift_pe_rows(shift_names)

	# Gross sales + change for ALL shifts in one grouped query. The original
	# gross carried the no-drawer-return filter (sales_recap.py:369-372) and
	# change did not (sales_recap.py:503-509 sums base_change_amount over the
	# whole scope). That filter only drops is_return = 1 rows, which the gross
	# CASE maps to 0 either way — so one unfiltered query yields BOTH numbers
	# exactly.
	gross_change = {
		r.shift: r
		for r in frappe.db.sql(
			f"""
			SELECT si.posa_pos_opening_shift AS shift,
				SUM(CASE WHEN si.is_return = 0 THEN si.base_grand_total ELSE 0 END) AS gross_sales,
				SUM(si.base_change_amount) AS change_amount
			FROM {sales_invoice_union(_SHIFT_INVOICE_COLUMNS, where="si.docstatus = 1 AND si.posa_pos_opening_shift IN %(hq_shifts)s")}
			WHERE si.docstatus = 1 AND si.posa_pos_opening_shift IN %(hq_shifts)s
			GROUP BY si.posa_pos_opening_shift
			""",
			{"hq_shifts": shift_names},
			as_dict=True,
		)
	}

	# Opening floats + the Cash-type map: one read each for all shifts, and
	# NO profile gate / NO has_column gate (sales_recap.py:556-567 counts
	# every mode whose Mode of Payment.type == "Cash", on every site). The
	# map must answer is_cash for every mode the drawer math touches
	# (sales_recap.py:497-500 reads the whole table): payment rows, PE rows,
	# opening details, the profile methods behind the mode chain, the
	# explicit cash-mode values and generic "Cash".
	method_modes = {mode for methods in ordered.values() for mode, _default in methods}
	used_modes = set(v for v in (explicit or {}).values() if v) | method_modes | {"Cash"}
	for per_shift in (pay_by_shift or {}).values():
		used_modes.update(mode for mode in per_shift if mode)
	for per_shift in (pe_by_shift or {}).values():
		used_modes.update(mode for mode in per_shift if mode)
	opening_by_shift, mode_types = _shift_opening_rows(shift_names, used_modes)
	chain_cash = {mode for mode, typ in mode_types.items() if typ == "Cash"}
	for shift in shifts:
		if not shift.pos_profile or not has_cash_field:
			continue
		configured = explicit.get(shift.pos_profile)
		if configured:
			cash_modes[shift.pos_profile] = configured
			continue
		for mode, is_default in ordered.get(shift.pos_profile, []):
			if is_default and mode in chain_cash:
				cash_modes[shift.pos_profile] = mode
				break
		else:
			for mode, _is_default in ordered.get(shift.pos_profile, []):
				if mode in chain_cash:
					cash_modes[shift.pos_profile] = mode
					break

	def _is_cash(mode):
		return mode_types.get(mode) == "Cash"

	rows = []
	for shift in shifts:
		cash_mode = cash_modes[shift.pos_profile]
		collected = dict(pay_by_shift.get(shift.name, {}))
		for mode, amount in pe_by_shift.get(shift.name, {}).items():
			collected[mode] = collected.get(mode, 0.0) + flt(amount)
		gross_row = gross_change.get(shift.name)
		change = flt(gross_row.change_amount) if gross_row else 0.0
		if change or cash_mode in collected:
			collected[cash_mode] = collected.get(cash_mode, 0.0) - change
		cash_collected = flt(sum(amount for mode, amount in collected.items() if _is_cash(mode)))
		opening_cash = flt(opening_by_shift.get(shift.name, 0.0))
		rows.append(
			{
				"name": shift.name,
				"cashier": shift.user,
				"cashier_name": users.get(shift.user) or shift.user,
				"outlet": shift.company,
				"currency": currency_map.get(shift.company),
				"opening": opening_cash,
				"sales": flt(gross_row.gross_sales) if gross_row else 0.0,
				"cash": cash_collected,
				"expected_closing": flt(opening_cash + cash_collected),
				"status": shift.status,
			}
		)
	return rows


_SHIFT_INVOICE_COLUMNS = (
	"si.name, si.docstatus, si.is_return, si.base_grand_total,"
	" si.base_change_amount, si.posa_pos_opening_shift"
)


def _shift_cash_masters(profiles, has_cash_field):
	"""Batched reads for the cash-mode chain (mirrors cash_mode.py:17-55).

	Returns (explicit, ordered):

	- explicit: {profile: explicit cash mode or None} (one grouped query,
	  only when the legacy ``posa_cash_mode_of_payment`` column exists —
	  the whole chain sits inside that has_column arm there).
	- ordered: {profile: [(mode, is_default)] in idx order} (one grouped
	  query, same gate — the original never reads payment rows without the
	  column either, cash_mode.py:31-36).

	Cash-type membership for the chain is resolved by the CALLER over the
	shared mode_types map from _shift_opening_rows (the original checks
	THAT profile's methods only, cash_mode.py:38-47 — same per-profile
	lookup, one shared map). No DB when the gate is closed or there are
	no profiles.
	"""
	if not has_cash_field or not profiles:
		return {}, {}
	explicit = {}
	for row in frappe.db.sql(
		"""SELECT name, posa_cash_mode_of_payment FROM `tabPOS Profile`
		WHERE name IN %(hq_profiles)s""",
		{"hq_profiles": list(set(profiles))},
		as_dict=True,
	):
		explicit[row.name] = row.posa_cash_mode_of_payment or None
	ordered = {}
	for row in frappe.db.sql(
		"""SELECT parent, mode_of_payment, `default` FROM `tabPOS Payment Method`
		WHERE parent IN %(hq_profiles)s AND parenttype = 'POS Profile'
		ORDER BY idx asc""",
		{"hq_profiles": list(set(profiles))},
		as_dict=True,
	):
		ordered.setdefault(row.parent, []).append((row.mode_of_payment, bool(row.default)))
	return explicit, ordered


def _shift_payment_rows(shift_names):
	"""Payment rows grouped by shift (one query for all shifts)."""
	out = {}
	for row in frappe.db.sql(
		f"""
		SELECT si.posa_pos_opening_shift AS shift, sip.mode_of_payment,
			SUM(sip.base_amount) AS amount
		FROM `tabSales Invoice Payment` sip
		JOIN {sales_invoice_union("si.name, si.posa_pos_opening_shift", where="si.docstatus = 1 AND si.posa_pos_opening_shift IN %(hq_shifts)s")} ON si.name = sip.parent
		WHERE si.posa_pos_opening_shift IN %(hq_shifts)s
		GROUP BY si.posa_pos_opening_shift, sip.mode_of_payment
		""",
		{"hq_shifts": shift_names},
		as_dict=True,
	):
		out.setdefault(row.shift, {}).setdefault(row.mode_of_payment, 0.0)
		out[row.shift][row.mode_of_payment] += flt(row.amount)
	return out


def _shift_pe_rows(shift_names):
	"""Payment Entries grouped by shift (one query for all shifts).

	EXISTS-per-shift semantics, batched: a PE counts in full for every shift
	whose invoices it references (sales_recap.py:464-493 — EXISTS is
	boolean per scope, so a PE touching two shifts counts in full under
	BOTH, while a PE touching two invoices of ONE shift counts ONCE). The
	inner SELECT DISTINCT collapses the per-reference join rows down to one
	row per (shift, PE) — a multi-invoice PE is never multiplied — and the
	outer SUM sums the distinct PEs per shift and mode.
	"""
	out = {}
	branches = [
		f"SELECT name, posa_pos_opening_shift AS shift FROM `tab{dt}`"
		f" WHERE docstatus = 1 AND posa_pos_opening_shift IN %(hq_shifts)s"
		for dt in ("POS Invoice", "Sales Invoice")
		if frappe.db.has_column(dt, "posa_pos_opening_shift")
	]
	if not branches:
		return out
	per_pe = frappe.db.sql(
		f"""
		SELECT shift, mode_of_payment, SUM(base_paid_amount) AS amount
		FROM (
			SELECT DISTINCT link.shift AS shift, pe.name AS pe_name,
				pe.mode_of_payment AS mode_of_payment,
				pe.base_paid_amount AS base_paid_amount
			FROM `tabPayment Entry` pe
			JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
			JOIN ({" UNION ALL ".join(branches)}) link ON link.name = per.reference_name
				AND per.reference_doctype IN ('Sales Invoice', 'POS Invoice')
			WHERE pe.docstatus = 1 AND pe.payment_type = 'Receive'
		) per_pe
		GROUP BY shift, mode_of_payment
		""",
		{"hq_shifts": shift_names},
		as_dict=True,
	)
	for row in per_pe:
		shift_modes = out.setdefault(row.shift, {})
		shift_modes[row.mode_of_payment] = flt(row.amount)
	return out


def _shift_opening_rows(shift_names, used_modes):
	"""Opening floats grouped by shift + the shared Cash-type map (2 queries).

	Same rule as sales_recap.py:556-567 with NO profile gate and NO
	has_column gate: every mode whose Mode of Payment.type == "Cash" counts,
	including a Cash mode dropped from the profile or never on it. The type
	map covers every mode that can carry money here — opening details,
	payment rows, PE rows, the profile methods behind the mode chain, the
	explicit cash-mode values and generic "Cash" — so is_cash answers like
	the original's full-table map (sales_recap.py:497-500) on every mode
	the drawer math touches. Returns (per_shift_floats, mode_types).
	"""
	details = frappe.get_all(
		"POS Opening Shift Detail",
		filters={"parent": ["in", shift_names]},
		fields=["parent", "mode_of_payment", "amount"],
	)
	modes = {mode for mode in (used_modes or []) if mode}
	modes.update(row.mode_of_payment for row in details if row.mode_of_payment)
	type_rows = (
		frappe.get_all(
			"Mode of Payment",
			filters={"name": ["in", sorted(modes)]},
			fields=["name", "type"],
		)
		if modes
		else []
	)
	mode_types = {row.name: row.type for row in type_rows}
	out = {}
	for row in details:
		if mode_types.get(row.mode_of_payment) == "Cash":
			out[row.parent] = out.get(row.parent, 0.0) + flt(row.amount)
	return out, mode_types


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
	never a paginated list — and slots "a"/"b" stay independent:
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
		FROM {_invoice_from(where)}
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
		FROM {_invoice_from(where)}
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
				"apc": flt(flt(r.net_tax_incl) / r.orders) if r.orders else None,
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
# Targets are measured against a configurable basis (POS Settings): Net Sales
# (default, the behaviour before the switch existed), Gross Profit (HPP from
# the invoices' Stock Ledger Entries) or Net Profit (the outlet company's whole
# books). Formulas never change with the basis — only the numerator does.


def _actuals_from_totals(rows):
	"""_totals_rows-style rows -> basis-actuals shape (Net Sales basis)."""
	return {r.company: {"value": flt(r.net_tax_incl), "orders": int(r.orders)} for r in rows}


def _gross_profit_actuals(companies, profiles, start, end, cutoff):
	"""Gross Profit basis actuals: the POS invoices' own books (net sales,
	tax incl) minus the HPP their submit-time Stock Ledger Entries carry
	(voucher_type = the configured invoice doctype, see CustomPOSInvoice).

	ERPNext v16 persists OUTGOING SLEs with incoming_rate = 0 (wiped in
	StockLedgerEntry.process_sle) — the outgoing valuation lives only in
	stock_value_difference (negative for goods out). The obvious
	SUM(-actual_qty * incoming_rate) therefore always returns 0 on sales; do
	NOT reintroduce it. HPP = -SUM(stock_value_difference), so gross profit
	per company = SUM(base_grand_total) + SUM(stock_value_difference).

	SLEs are pre-aggregated per voucher before the grand total is summed, so
	an invoice's total is never multiplied by its SLE row count. Returns come
	out right by sign: their grand total is negative and their stock returns
	(stock_value_difference positive), reducing gross profit on both legs.

	zero_cost_rows counts OUTGOING SLE rows with no valuation
	(stock_value_difference = 0): sold item rows without HPP, whose sale
	price is counted in full — so the figure overstates gross profit by
	exactly those rows.
	"""
	dt = get_pos_invoice_doctype()
	# is_pos matches every other sales window: in Sales Invoice mode plain
	# credit invoices must not land in the outlet's POS payback figures.
	where = ["si.docstatus = 1", "si.is_pos = 1", "si.company IN %(companies)s"]
	params = {"voucher_type": dt, "companies": companies}
	# Same window semantics as _si_window_where: the SLE's posting_datetime is
	# copied from the voucher, so filtering si's date/time columns selects the
	# identical ledger rows (a cutoff cuts today at "now"; without one the end
	# date counts whole).
	if start:
		params["start"] = str(getdate(start))
		where.append("si.posting_date >= %(start)s")
	if end:
		params["end"] = str(getdate(end))
		where.append("si.posting_date <= %(end)s")
	if cutoff:
		# Same sargable split + deliberate NULL-time superset as
		# _si_window_where (PERF-01): old TIMESTAMP(date, NULL) was NULL and
		# dropped the row; the IS NULL arm now admits it.
		params["cutoff_date"] = getdate(cutoff)
		params["cutoff_time"] = cutoff.time()
		where.append(
			"(si.posting_date < %(cutoff_date)s"
			" OR (si.posting_date = %(cutoff_date)s"
			" AND (si.posting_time <= %(cutoff_time)s OR si.posting_time IS NULL)))"
		)
	if profiles is not None:
		params["profiles"] = list(profiles)
		where.append("si.pos_profile IN %(profiles)s")
	return {
		r.company: {
			"value": flt(r.value),
			"orders": int(r.orders),
			"zero_cost_rows": int(r.zero_cost_rows or 0),
		}
		for r in frappe.db.sql(
			f"""
			SELECT per_voucher.company,
				ROUND(SUM(per_voucher.net + per_voucher.hpp), 2) AS value,
				SUM(per_voucher.orders) AS orders,
				SUM(per_voucher.zero_cost_rows) AS zero_cost_rows
			FROM (
				SELECT si.name, si.company, si.base_grand_total AS net,
					IFNULL(SUM(sle.stock_value_difference), 0) AS hpp,
					IF(si.is_return = 0, 1, 0) AS orders,
					IFNULL(SUM(CASE WHEN sle.actual_qty < 0
						AND sle.stock_value_difference = 0 THEN 1 ELSE 0 END), 0) AS zero_cost_rows
				FROM `tab{dt}` si
				LEFT JOIN `tabStock Ledger Entry` sle
					ON sle.voucher_no = si.name
					AND sle.voucher_type = %(voucher_type)s
					AND sle.is_cancelled = 0
				WHERE {" AND ".join(where)}
				GROUP BY si.name, si.company, si.base_grand_total
			) per_voucher
			GROUP BY per_voucher.company
			""",
			params,
			as_dict=True,
		)
	}


def _net_profit_actuals(companies, start, end):
	"""Net Profit basis actuals: the outlet company's whole books from GL
	Entry (SUM(credit - debit) over Income+Expense accounts = income minus
	expense).

	Company level by nature — a POS Profile filter cannot narrow a company's
	P&L, so none is applied; orders is None (there is no order concept in the
	GL)."""
	where = [
		"gle.is_cancelled = 0",
		"gle.is_opening = 'No'",
		"acc.root_type IN ('Income', 'Expense')",
		"gle.voucher_type != 'Period Closing Voucher'",
		"gle.company IN %(companies)s",
	]
	params = {"companies": companies}
	if start:
		params["start"] = str(getdate(start))
		where.append("gle.posting_date >= %(start)s")
	if end:
		params["end"] = str(getdate(end))
		where.append("gle.posting_date <= %(end)s")
	return {
		r.company: {"value": flt(r.value), "orders": None}
		for r in frappe.db.sql(
			f"""
			SELECT gle.company, ROUND(SUM(gle.credit - gle.debit), 2) AS value
			FROM `tabGL Entry` gle
			INNER JOIN `tabAccount` acc ON acc.name = gle.account
			WHERE {" AND ".join(where)}
			GROUP BY gle.company
			""",
			params,
			as_dict=True,
		)
	}


def _basis_actuals(companies, profiles, basis, start, end, cutoff, totals_rows=None):
	"""Actuals per company on one target basis: {company: {value, orders,
	zero_cost_rows}} (zero_cost_rows only exists on the Gross Profit basis).

	- Net Sales: the shared POS totals window (net, tax incl). Pass the
	  caller's existing ``totals_rows`` when that window was already queried —
	  the dashboard and the outlet target sheet always have it.
	- Gross Profit / Net Profit: the two helpers above.

	``start``/``end`` are inclusive dates, ``cutoff`` (datetime) cuts the
	window at "now" exactly like _si_window_where; either bound may be None
	(open-ended) for the overall/payback window.
	"""
	if basis == GROSS_PROFIT:
		return _gross_profit_actuals(companies, profiles, start, end, cutoff)
	if basis == NET_PROFIT:
		return _net_profit_actuals(companies, start, end)
	if totals_rows is None:
		where, params = _si_window_where(
			companies, profiles, start or "1970-01-01", end or "9999-12-31", cutoff
		)
		totals_rows = _totals_rows(where, params)
	return _actuals_from_totals(totals_rows)


def _basis_label(basis):
	"""Server-side translated basis label (POS Settings enum -> UI label)."""
	return _(TARGET_BASIS_LABELS.get(basis) or basis)


def _target_basis_payload():
	"""The two configured bases plus their labels, for page headers/tooltips."""
	monthly = get_target_basis("monthly")
	overall = get_target_basis("overall")
	return {
		"monthly": monthly,
		"overall": overall,
		"monthly_label": _basis_label(monthly),
		"overall_label": _basis_label(overall),
	}


def _targets_section(companies, currency_map, default_ccy, window, monthly, mtd_rows, profiles=None):
	month_start = window["month_start"]
	basis = get_target_basis("monthly")
	actuals = _basis_actuals(
		companies,
		profiles,
		basis,
		month_start,
		window["day"],
		window["mtd_cutoff"],
		totals_rows=mtd_rows,
	)
	rows = frappe.get_all(
		"POS Monthly Target",
		filters={"company": ["in", companies], "month_start": month_start},
		fields=["company", "target_sales"],
	)
	missing = [c for c in companies if c not in {r.company for r in rows}]

	# Per-outlet rows: unlike the aggregate below, a missing target only blanks
	# that outlet's cells — the table shows whatever is configured. Sales
	# columns (mtd_net_tax_incl, mtd_apc) keep their sales meaning on every
	# basis; achievement and projection follow the basis numerator.
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
		basis_row = actuals.get(company) or {}
		value = flt(basis_row.get("value"))
		target_sales = flt(target.target_sales) if target else None
		projected = (
			round(value / days_elapsed * days_in_month, 2)
			if target and days_elapsed
			else None
		)
		by_company.append(
			{
				"company": company,
				"currency": currency_map.get(company),
				"missing": target is None,
				"target_sales": target_sales,
				"mtd_net_tax_incl": round(net, 2),
				"mtd_orders": orders,
				"mtd_apc": round(net / orders, 2) if orders else None,
				"achievement_sales_pct": ratio(value, target_sales) if target else None,
				"projected_sales": projected,
				"projected_achievement_pct": (
					ratio(projected, target_sales) if target and projected is not None else None
				),
				"mtd_value": round(value, 2),
				"target_value": target_sales,
				"projected_value": projected,
				"zero_cost_rows": basis_row.get("zero_cost_rows"),
			}
		)

	sales_target = {}
	for r in rows:
		ccy = currency_map.get(r.company)
		if ccy:
			sales_target[ccy] = sales_target.get(ccy, 0) + flt(r.target_sales)

	# Basis MTD per currency — same aggregation path as monthly["net_tax_incl"]
	# so the two reconcile bit-for-bit on the default basis.
	actual_rows = [{"company": c, "value": a.get("value") or 0} for c, a in actuals.items()]
	mtd_value_metric = metric_from_rows(actual_rows, "value", currency_map, default_ccy)

	if not rows or missing:
		return {
			"available": False,
			"month_start": month_start,
			"missing_companies": missing[:10],
			"missing_count": len(missing),
			"notice": _("Monthly target is not set for every scoped company, so achievement is not shown."),
			"by_company": by_company,
			"mtd_value_by_currency": mtd_value_metric,
		}

	target_metric = make_metric(sales_target, default_ccy)
	days_in_month = window["days_in_month"]

	achievement, surplus, projection = {}, {}, {}
	for ccy, target in sales_target.items():
		actual = mtd_value_metric["by_currency"].get(ccy, 0)
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
		"mtd_value_by_currency": mtd_value_metric,
		"achievement_sales_pct": achievement,
		"surplus_sales": surplus,
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
		"apc_projection_note": _(
			"APC is an average, so its projection equals the MTD figure (never day-extrapolated)."
		),
	}


def _overall_target_section(scope, currency_map):
	"""Per-outlet payback ("balik modal") target: cumulative actuals on the
	overall target basis (default: POS net sales, tax incl.) vs the one-time
	Overall Sales Target on the Company master.

	Each outlet counts from its own "Counted From" date (empty = all time).
	On the default basis one query pins every company's lower bound instead of
	one query per outlet; the profit bases resolve per outlet (few configure an
	overall target). Outlets without an overall target are simply absent from
	``by_company``.
	"""
	# Guard: before the app's first migrate the custom fields do not exist yet;
	# reading them would error, so report the section unavailable instead.
	if not frappe.get_meta("Company").has_field("pos_overall_sales_target"):
		return {"available": False, "by_company": {}}
	basis = get_target_basis("overall")
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

	if basis == NET_SALES:
		where = ["si.docstatus = 1", "si.is_pos = 1", "si.company IN %(companies)s"]
		params = {"companies": list(configured)}
		if scope["profiles"] is not None:
			params["profiles"] = list(scope["profiles"])
			where.append("si.pos_profile IN %(profiles)s")
		# Per-company start bound: a company WITHOUT a counted-from date stays
		# all-time. Every configured company needs an arm — filtering to only
		# the dated ones would silently zero out the undated outlets.
		bounds = []
		for i, (name, row) in enumerate(configured.items()):
			params[f"c{i}"] = name
			if row.pos_overall_target_from:
				params[f"d{i}"] = row.pos_overall_target_from
				bounds.append(f"(si.company = %(c{i})s AND si.posting_date >= %(d{i})s)")
			else:
				bounds.append(f"si.company = %(c{i})s")
		if bounds:
			where.append("(" + " OR ".join(bounds) + ")")

		cumulative = [
			r
			for r in frappe.db.sql(
				f"""
				SELECT si.company,
					SUM(si.base_grand_total) AS net_tax_incl,
					COUNT(CASE WHEN si.is_return = 0 THEN 1 END) AS orders
				FROM {_invoice_from(" AND ".join(where))}
				WHERE {" AND ".join(where)}
				GROUP BY si.company
				""",
				params,
				as_dict=True,
			)
		]
		actuals = _actuals_from_totals(cumulative)
	else:
		actuals = {}
		for company, row in configured.items():
			actuals.update(
				_basis_actuals(
					[company], scope["profiles"], basis, row.pos_overall_target_from, None, None
				)
			)

	by_company = {}
	for company, row in configured.items():
		actual = actuals.get(company) or {}
		value = flt(actual.get("value"))
		# Net Profit has no order concept; absent rows keep the old 0 figures.
		orders = None if basis == NET_PROFIT else (int(actual["orders"]) if actual else 0)
		target = flt(row.pos_overall_sales_target)
		by_company[company] = {
			"currency": currency_map.get(company),
			"overall_target": target,
			"from_date": str(row.pos_overall_target_from) if row.pos_overall_target_from else None,
			"cumulative_value": round(value, 2),
			"cumulative_orders": orders,
			"achievement_pct": ratio(value, target),
			"remaining": round(target - value, 2),
			"zero_cost_rows": actual.get("zero_cost_rows"),
		}
		if basis == NET_SALES:
			by_company[company]["cumulative_net_tax_incl"] = round(value, 2)
	note = None
	if basis == NET_PROFIT and scope.get("profile_restricted"):
		note = _(
			"Net Profit is the outlet's whole books (income minus expense); the POS Profile filter does not apply."
		)
	return {"available": True, "by_company": by_company, "profile_note": note}


def _empty_payload(scope, notice):
	return {
		"scope": scope,
		"notice": notice,
		"windows": {},
		"monthly": {},
		"range": {},
		"daily": {},
		"hours": {"rows": [], "peak": None, "top": [], "lowest": []},
		"outlet_ranking": [],
		"item_groups": [],
		"category_products": {"a": {}, "b": {}},
		"category_top": {"rows": [], "currency": None, "groups_with_sales": 0},
		"payments": {"rows": [], "currency": None, "modes_with_sales": 0},
		"recent": {"rows": []},
		"returns": {"value": 0, "count": 0, "rate": None, "rows": [], "currency": None},
		"shifts": [],
		"target_basis": _target_basis_payload(),
		"targets": {"available": False, "by_company": [], "overall": {"available": False, "by_company": {}}},
	}
