# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""POS Outlet Performance Report — the full table behind the HQ Sales
Monitoring Outlet Performance card. Same access rule, scope and helpers as
that page, so every figure reconciles with the card.

Range columns follow From/To Date; Monthly Target columns are the month of
To Date (month to date); Balik Modal is the all-time payback target."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from pos_next.api.hq_monitoring import (
	MAX_RANGE_DAYS,
	_build_windows,
	_check_hq_access,
	_currency_map,
	_default_company,
	_main_currency,
	_metrics_from_totals,
	_overall_target_section,
	_resolve_scope,
	_si_window_where,
	_targets_section,
	_totals_rows,
	ratio,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(), data, None, get_chart(data), get_summary(data)

def _main_rows(data):
	ccy = _main_currency(data, "net_sales")
	return ccy, [r for r in data if r["currency"] == ccy]

def get_chart(data):
	"""Top 10 outlets: net sales next to returns for the selected range."""
	ccy, rows = _main_rows(data)
	top = [r for r in rows if r["net_sales"] or r["refunds"]][:10]
	if not top:
		return None
	return {
		"data": {
			"labels": [r["company"] for r in top],
			"datasets": [
				{"name": _("Net Sales"), "values": [r["net_sales"] for r in top]},
				{"name": _("Returns"), "values": [r["refunds"] for r in top]},
			],
		},
		"type": "bar",
		"colors": ["#10b981", "#ef4444"],
		"barOptions": {"spaceRatio": 0.4},
		"fieldtype": "Currency",
		"options": "currency",
		"currency": ccy,
		"height": 300,
	}

def get_summary(data):
	ccy, rows = _main_rows(data)
	if not rows:
		return None
	net = sum(r["net_sales"] for r in rows)
	orders = sum(r["orders"] for r in rows)
	targeted = [r for r in rows if r["target_sales"]]
	target = sum(flt(r["target_sales"]) for r in targeted)
	mtd = sum(flt(r["mtd_value"]) for r in targeted)
	pct = ratio(mtd, target) if target else None
	summary = [
		{"value": net, "label": _("Net Sales"), "datatype": "Currency", "currency": ccy, "indicator": "Green"},
		{"value": orders, "label": _("TC"), "datatype": "Int", "indicator": "Blue"},
		{"value": net / orders if orders else 0, "label": _("Avg Ticket"), "datatype": "Currency", "currency": ccy, "indicator": "Blue"},
		{"value": sum(r["refunds"] for r in rows), "label": _("Returns"), "datatype": "Currency", "currency": ccy, "indicator": "Red"},
		{"value": sum(1 for r in rows if r["orders"]), "label": _("Active Outlets"), "datatype": "Int", "indicator": "Purple"},
	]
	if pct is not None:
		summary.append({
			"value": pct,
			"label": _("MTD Target Achievement"),
			"datatype": "Percent",
			"indicator": "Green" if pct >= 100 else "Orange" if pct >= 70 else "Red",
		})
	return summary


def get_columns():
	def money(fieldname, label, width=130):
		return {"fieldname": fieldname, "label": label, "fieldtype": "Currency", "options": "currency", "width": width}

	# A summed or averaged percentage means nothing across outlets, so the
	# total row leaves these blank.
	def pct(fieldname, label):
		return {"fieldname": fieldname, "label": label, "fieldtype": "Percent", "width": 110, "disable_total": 1}

	return [
		{"fieldname": "company", "label": _("Outlet"), "fieldtype": "Link", "options": "Company", "width": 180},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
		money("gross", _("Gross Sales")),
		money("refunds", _("Returns")),
		money("net_sales", _("Net Sales")),
		{"fieldname": "orders", "label": _("TC"), "fieldtype": "Int", "width": 80},
		{**money("apc", _("Avg Ticket"), 120), "disable_total": 1},
		pct("share_pct", _("Share")),
		money("target_sales", _("Monthly Target")),
		money("mtd_value", _("MTD Actual")),
		pct("achievement_pct", _("MTD Achievement")),
		money("projected_sales", _("Projected")),
		money("overall_target", _("Balik Modal Target")),
		money("cumulative_value", _("Balik Modal Actual")),
		pct("overall_pct", _("Balik Modal %")),
		money("overall_remaining", _("Balik Modal Remaining")),
	]


def get_data(filters):
	_check_hq_access()
	today = getdate(nowdate())
	to_date = min(getdate(filters.get("to_date") or today), today)
	from_date = getdate(filters.get("from_date") or to_date)
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date"))
	if (to_date - from_date).days > MAX_RANGE_DAYS:
		frappe.throw(_("Date range is limited to {0} days").format(MAX_RANGE_DAYS))

	scope = _resolve_scope(filters.get("company"), filters.get("include_descendants"))
	if not scope["companies"] or scope["profiles"] == []:
		return []
	companies, profiles = scope["companies"], scope["profiles"]
	currency_map = _currency_map(companies)
	default_ccy = currency_map.get(_default_company(companies))
	window = _build_windows(to_date)

	range_rows = {
		r.company: r
		for r in _totals_rows(*_si_window_where(companies, profiles, from_date, to_date, window["mtd_cutoff"]))
	}
	mtd_rows = _totals_rows(
		*_si_window_where(companies, profiles, window["month_start"], to_date, window["mtd_cutoff"])
	)
	monthly = _metrics_from_totals(mtd_rows, currency_map, default_ccy)
	targets = {
		t["company"]: t
		for t in _targets_section(
			companies, currency_map, default_ccy, window, monthly, mtd_rows, profiles=profiles
		)["by_company"]
	}
	overall = _overall_target_section(scope, currency_map)["by_company"]

	# Share % stays inside one currency, like the HQ card.
	currency_totals = {}
	for company, r in range_rows.items():
		ccy = currency_map.get(company)
		currency_totals[ccy] = currency_totals.get(ccy, 0) + flt(r.net_tax_incl)

	data = []
	for company in companies:
		r = range_rows.get(company) or frappe._dict()
		t = targets.get(company) or {}
		o = overall.get(company) or {}
		orders = int(r.orders or 0)
		net = flt(r.net_tax_incl)
		row = {
			"company": company,
			"currency": currency_map.get(company),
			"gross": flt(r.gross),
			"refunds": flt(r.refunds),
			"net_sales": net,
			"orders": orders,
			"apc": net / orders if orders else None,
			"share_pct": ratio(net, currency_totals.get(currency_map.get(company))),
			"target_sales": t.get("target_sales"),
			"mtd_value": t.get("mtd_value"),
			"achievement_pct": t.get("achievement_sales_pct"),
			"projected_sales": t.get("projected_value"),
			"overall_target": o.get("overall_target"),
			"cumulative_value": o.get("cumulative_value"),
			"overall_pct": o.get("achievement_pct"),
			"overall_remaining": o.get("remaining"),
		}
		if not filters.get("show_empty") and not orders and not row["refunds"] and not t.get("mtd_value"):
			continue
		data.append(row)

	data.sort(key=lambda d: (d["currency"] != default_ccy, -d["net_sales"]))
	return data
