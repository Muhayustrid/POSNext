# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""POS Return Report — the detail list behind the HQ Sales Monitoring
Returns card. Same access rule, company/profile scope and invoice source as
that page, so the totals here always reconcile with the card.

Group By "Invoice": one row per return invoice. "Item": one row per returned
item line. Quantities and amounts are shown as positive values (company
currency)."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate

from pos_next.api.hq_monitoring import (
	MAX_RANGE_DAYS,
	_check_hq_access,
	_currency_map,
	_resolve_scope,
	_si_window_where,
)
from pos_next.invoice_type import (
	package_sold_amount,
	package_sold_row_filter,
	sales_invoice_item_union,
	sales_invoice_union,
)

_INVOICE_COLS = (
	"si.name, si.posting_date, si.posting_time, si.return_against,"
	" si.company, si.pos_profile, si.owner, si.customer"
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	by_item = filters.get("group_by") == "Item"
	return get_columns(by_item), get_data(filters, by_item)


def get_columns(by_item):
	columns = [
		{"fieldname": "posting_date", "label": _("Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "posting_time", "label": _("Time"), "fieldtype": "Data", "width": 70},
		{"fieldname": "doctype", "label": _("Type"), "fieldtype": "Data", "hidden": 1},
		{"fieldname": "name", "label": _("Return Invoice"), "fieldtype": "Dynamic Link", "options": "doctype", "width": 170},
		{"fieldname": "return_against", "label": _("Original Invoice"), "fieldtype": "Dynamic Link", "options": "doctype", "width": 170},
		{"fieldname": "company", "label": _("Outlet"), "fieldtype": "Link", "options": "Company", "width": 160},
		{"fieldname": "pos_profile", "label": _("POS Profile"), "fieldtype": "Link", "options": "POS Profile", "width": 140},
		{"fieldname": "owner", "label": _("Cashier"), "fieldtype": "Link", "options": "User", "width": 160},
		{"fieldname": "customer", "label": _("Customer"), "fieldtype": "Link", "options": "Customer", "width": 150},
		{"fieldname": "currency", "label": _("Currency"), "fieldtype": "Link", "options": "Currency", "hidden": 1},
	]
	if by_item:
		columns += [
			{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 140},
			{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 180},
			{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 80},
			{"fieldname": "rate", "label": _("Rate"), "fieldtype": "Currency", "options": "currency", "width": 120},
			{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "options": "currency", "width": 130},
		]
	else:
		columns += [
			{"fieldname": "qty", "label": _("Qty"), "fieldtype": "Float", "width": 80},
			{"fieldname": "amount", "label": _("Return Value"), "fieldtype": "Currency", "options": "currency", "width": 140},
		]
	return columns


def get_data(filters, by_item=False):
	_check_hq_access()
	to_date = getdate(filters.get("to_date") or nowdate())
	from_date = getdate(filters.get("from_date") or to_date)
	if from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date"))
	if (to_date - from_date).days > MAX_RANGE_DAYS:
		frappe.throw(_("Date range is limited to {0} days").format(MAX_RANGE_DAYS))

	# Same scope as the HQ page: forged companies raise, profile User
	# Permissions narrow the rows.
	scope = _resolve_scope(filters.get("company"), filters.get("include_descendants"))
	if not scope["companies"] or scope["profiles"] == []:
		return []
	where, params = _si_window_where(scope["companies"], scope["profiles"], from_date, to_date)
	where += " AND si.is_return = 1"
	for key, column in (("pos_profile", "pos_profile"), ("cashier", "owner"), ("customer", "customer")):
		if filters.get(key):
			params[f"f_{key}"] = filters.get(key)
			where += f" AND si.{column} = %(f_{key})s"

	if by_item:
		# A package is one row carrying its instance's money; its components
		# are not listed (same rule as the HQ ranking).
		source = sales_invoice_item_union(
			_INVOICE_COLS + ", '{dt}' AS doctype, sii.idx, sii.item_code, sii.item_name, sii.qty,"
			f" {package_sold_amount('base_amount')} AS base_amount, sii.pos_package_role, sii.pos_package_instance",
			where=where,
		)
		rows = frappe.db.sql(
			f"""
			SELECT sii.doctype, sii.name, sii.posting_date, sii.posting_time, sii.return_against,
				sii.company, sii.pos_profile, sii.owner, sii.customer, sii.item_code, sii.item_name,
				ABS(sii.qty) AS qty, ABS(sii.base_amount) AS amount
			FROM {source}
			WHERE {package_sold_row_filter('base_amount')}
			ORDER BY sii.posting_date DESC, sii.posting_time DESC, sii.name DESC, sii.idx
			""",
			params,
			as_dict=True,
		)
	else:
		rows = frappe.db.sql(
			f"""
			SELECT doctype, name, posting_date, posting_time, return_against, company,
				pos_profile, owner, customer, ABS(total_qty) AS qty, ABS(base_grand_total) AS amount
			FROM {sales_invoice_union("'{dt}' AS doctype, " + _INVOICE_COLS + ", si.total_qty, si.base_grand_total", where)}
			ORDER BY posting_date DESC, posting_time DESC, name DESC
			""",
			params,
			as_dict=True,
		)

	currency_map = _currency_map(scope["companies"])
	for r in rows:
		r.posting_time = str(r.posting_time).split(".")[0].zfill(8)[:5] if r.posting_time is not None else ""
		r.currency = currency_map.get(r.company)
		r.qty = flt(r.qty)
		r.amount = flt(r.amount)
		if by_item:
			r.rate = r.amount / r.qty if r.qty else 0.0
	return rows
