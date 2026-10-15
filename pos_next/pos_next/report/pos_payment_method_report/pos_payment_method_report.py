# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""POS Payment Method Report — the full mode-of-payment detail behind the
"Payment Methods" donut on the HQ Sales Monitoring page (which shows the top
five modes of the default currency only). Same access rule, company/profile
scope and invoice source as that page.

One row per mode of payment and currency, amount descending. Amount is the
money that actually moved (base_amount, tax included); return payment rows ride
along as negatives so the totals stay net. Share % is within the same currency
over positive amounts — currencies never merge, and a net-negative/zero mode
keeps its row but carries no share. With "Group by Outlet" the rows split per
company instead of folding same-currency outlets."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from pos_next.api.hq_monitoring import (
	MAX_RANGE_DAYS,
	_check_hq_access,
	_currency_map,
	_main_currency,
	_resolve_scope,
	_si_window_where,
	ratio,
)
from pos_next.invoice_type import sales_invoice_union


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(filters), data, None, get_chart(data), get_summary(data)


def _main_rows(data):
	ccy = _main_currency(data, "amount")
	return ccy, [r for r in data if r.currency == ccy]


def get_chart(data):
	"""Top 10 payment methods by amount in the main currency (rows are already
	amount-descending within their currency)."""
	ccy, rows = _main_rows(data)
	top = rows[:10]
	if not top:
		return None
	return {
		"data": {
			"labels": [
				f"{r.mode_of_payment} ({r.company})" if r.get("company") else r.mode_of_payment for r in top
			],
			"datasets": [{"name": _("Amount"), "values": [flt(r.amount) for r in top]}],
		},
		"type": "bar",
		"colors": ["#6366f1"],
		"fieldtype": "Currency",
		"options": "currency",
		"currency": ccy,
		"height": 300,
	}


def get_summary(data):
	ccy, rows = _main_rows(data)
	if not rows:
		return None
	positive = [r for r in rows if flt(r.amount) > 0]
	return [
		{"value": sum(flt(r.amount) for r in positive), "label": _("Total Amount"), "datatype": "Currency", "currency": ccy, "indicator": "Green"},
		{"value": sum(int(r.transactions) for r in rows), "label": _("Transactions"), "datatype": "Int", "indicator": "Blue"},
		{"value": len(positive), "label": _("Payment Methods"), "datatype": "Int", "indicator": "Blue"},
		{"value": rows[0].mode_of_payment, "label": _("Top Method"), "datatype": "Data", "indicator": "Purple"},
	]


def get_columns(filters=None):
	columns = [
		{"fieldname": "mode_of_payment", "label": _("Mode of Payment"), "fieldtype": "Link", "options": "Mode of Payment", "width": 180},
	]
	if filters and filters.get("group_by_outlet"):
		columns.append({"fieldname": "company", "label": _("Outlet"), "fieldtype": "Link", "options": "Company", "width": 180})
	columns += [
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
		{"fieldname": "transactions", "label": _("Transactions"), "fieldtype": "Int", "width": 110},
		{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "options": "currency", "width": 150},
		{"fieldname": "share_pct", "label": _("Share %"), "fieldtype": "Float", "precision": 2, "disable_total": 1, "width": 90},
	]
	return columns


def get_data(filters):
	_check_hq_access()
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or to_date)
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date"))
	if (to_date - from_date).days > MAX_RANGE_DAYS:
		frappe.throw(_("Date range is limited to {0} days").format(MAX_RANGE_DAYS))

	scope = _resolve_scope(filters.get("company"), filters.get("include_descendants"))
	if not scope["companies"] or scope["profiles"] == []:
		return []
	where, params = _si_window_where(scope["companies"], scope["profiles"], from_date, to_date)
	if filters.get("pos_profile"):
		params["f_pos_profile"] = filters.pos_profile
		where += " AND si.pos_profile = %(f_pos_profile)s"
	mode_where = ""
	if filters.get("mode_of_payment"):
		params["f_mode_of_payment"] = filters.mode_of_payment
		mode_where = "WHERE sip.mode_of_payment = %(f_mode_of_payment)s"

	rows = frappe.db.sql(
		f"""
		SELECT sip.mode_of_payment, si.company,
			SUM(sip.base_amount) AS amount, COUNT(DISTINCT si.name) AS transactions
		FROM {sales_invoice_union("si.name, si.company", where)}
		JOIN `tabSales Invoice Payment` sip ON sip.parent = si.name
		{mode_where}
		GROUP BY sip.mode_of_payment, si.company
		""",
		params,
		as_dict=True,
	)

	# Fold companies into one row per (mode, currency) unless the user asked to
	# keep outlets apart: same-currency outlets sum, different currencies stay
	# separate rows.
	currency_map = _currency_map(scope["companies"])
	by_outlet = bool(filters.get("group_by_outlet"))
	merged = {}
	for r in rows:
		ccy = currency_map.get(r.company)
		key = (r.mode_of_payment, r.company) if by_outlet else (r.mode_of_payment, ccy)
		row = merged.setdefault(
			key,
			frappe._dict(
				mode_of_payment=r.mode_of_payment,
				company=r.company if by_outlet else None,
				currency=ccy,
				amount=0.0,
				transactions=0,
			),
		)
		row.amount += flt(r.amount)
		row.transactions += int(r.transactions or 0)

	data = sorted(merged.values(), key=lambda r: (r.currency or "", -r.amount, r.mode_of_payment))
	# Share within the same currency, over positive amounts only — a net-negative
	# or zero mode keeps its row but is not part of the denominator.
	totals = {}
	for r in data:
		if flt(r.amount) > 0:
			totals[r.currency] = totals.get(r.currency, 0.0) + flt(r.amount)
	for r in data:
		r.share_pct = ratio(r.amount, totals.get(r.currency)) if flt(r.amount) > 0 else None
	return data
