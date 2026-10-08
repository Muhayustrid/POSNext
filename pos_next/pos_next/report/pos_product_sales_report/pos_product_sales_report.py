# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""POS Product Sales Report — the full product ranking behind the HQ Sales
Monitoring page (which only shows category summaries). Same access rule,
company/profile scope and invoice source as that page.

One row per item and company currency, net sales descending. Qty is net of
returns; amounts are pre-tax net (base_net_amount, company currency). A
package is one row carrying its instance's money; its components are not
listed. Share % is within the same currency — currencies never merge."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from pos_next.api.hq_monitoring import (
	MAX_RANGE_DAYS,
	_check_hq_access,
	_currency_map,
	_item_from,
	_item_group_and_descendants,
	_main_currency,
	_resolve_scope,
	_si_window_where,
	ratio,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(), data, None, get_chart(data), get_summary(data)

def _main_rows(data):
	ccy = _main_currency(data, "net_amount")
	return ccy, [r for r in data if r.currency == ccy]

def get_chart(data):
	"""Top 10 products by net sales (rows are already net-sales descending)."""
	ccy, rows = _main_rows(data)
	top = rows[:10]
	if not top:
		return None
	return {
		"data": {
			"labels": [r.item_name or r.item_code for r in top],
			"datasets": [{"name": _("Net Sales"), "values": [flt(r.net_amount) for r in top]}],
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
	groups = {}
	for r in rows:
		groups[r.item_group] = groups.get(r.item_group, 0) + flt(r.net_amount)
	top_group = max(groups, key=groups.get)
	return [
		{"value": sum(flt(r.net_amount) for r in rows), "label": _("Net Sales"), "datatype": "Currency", "currency": ccy, "indicator": "Green"},
		{"value": sum(flt(r.qty) for r in rows), "label": _("Qty Sold"), "datatype": "Float", "indicator": "Blue"},
		{"value": len(rows), "label": _("Products Sold"), "datatype": "Int", "indicator": "Blue"},
		{"value": rows[0].item_name or rows[0].item_code, "label": _("Best Seller"), "datatype": "Data", "indicator": "Purple"},
		{"value": top_group or "-", "label": _("Top Item Group"), "datatype": "Data", "indicator": "Orange"},
	]


def get_columns():
	return [
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 220},
		{"fieldname": "item_group", "label": _("Item Group"), "fieldtype": "Link", "options": "Item Group", "width": 150},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
		{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 90},
		{"fieldname": "net_amount", "label": _("Net Sales"), "fieldtype": "Currency", "options": "currency", "width": 150},
		{"fieldname": "share_pct", "label": _("Share %"), "fieldtype": "Float", "precision": 2, "width": 90},
	]


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
	item_where = ""
	if filters.get("item_group"):
		params["item_groups"] = _item_group_and_descendants(filters.item_group)
		item_where = " AND sii.item_group IN %(item_groups)s"

	rows = frappe.db.sql(
		f"""
		SELECT sii.item_code, MAX(sii.item_name) AS item_name, MAX(sii.item_group) AS item_group,
			si.company, SUM(sii.qty) AS qty, SUM(sii.base_net_amount) AS net_amount
		{_item_from(where)}{item_where}
		GROUP BY sii.item_code, si.company
		""",
		params,
		as_dict=True,
	)

	# Fold companies into one row per (item, currency): same-currency outlets
	# sum, different currencies stay separate rows.
	currency_map = _currency_map(scope["companies"])
	merged, totals = {}, {}
	for r in rows:
		ccy = currency_map.get(r.company)
		row = merged.setdefault(
			(r.item_code, ccy),
			frappe._dict(item_code=r.item_code, item_name=r.item_name, item_group=r.item_group, currency=ccy, qty=0.0, net_amount=0.0),
		)
		row.qty += flt(r.qty)
		row.net_amount += flt(r.net_amount)
		totals[ccy] = totals.get(ccy, 0.0) + flt(r.net_amount)

	data = sorted(merged.values(), key=lambda r: (r.currency or "", -r.net_amount, -r.qty, r.item_code))
	for r in data:
		r.share_pct = ratio(r.net_amount, totals.get(r.currency))
	return data
