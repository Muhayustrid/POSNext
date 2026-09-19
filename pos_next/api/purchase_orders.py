# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json

import frappe
from erpnext.accounts.party import get_party_details
from erpnext.stock.get_item_details import get_item_details
from frappe import _
from frappe.utils import add_days, cint, flt, nowdate


def _check_guest():
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)


def _check_permission(perm, doc=None):
	# frappe v16 has_permission only returns a bool, never raises — the explicit
	# check here gives a clear error and closes read paths get_doc doesn't gate
	if not frappe.has_permission("Purchase Order", perm, doc=doc):
		frappe.throw(_("No {0} permission on Purchase Order").format(_(perm)), frappe.PermissionError)


def _parse(value):
	"""Whitelisted list/dict args arrive as JSON strings."""
	if not isinstance(value, str):
		return value
	try:
		return json.loads(value)
	except json.JSONDecodeError:
		frappe.throw(_("Invalid request data"), frappe.ValidationError)


def _profile_value(pos_profile, fieldname):
	return frappe.db.get_value("POS Profile", pos_profile, fieldname) if pos_profile else None


def _resolve_company(pos_profile=None):
	"""POS Profile's company when given, else the user/site default company."""
	return (
		_profile_value(pos_profile, "company")
		or frappe.db.get_default("company")
		or frappe.db.get_value("Company", {}, "name")
	)


@frappe.whitelist()
def search_suppliers(search_term=None, limit=20):
	_check_guest()
	term = f"%{(search_term or '').strip()}%"
	return {
		"suppliers": frappe.get_list(
			"Supplier",
			filters={"disabled": 0},
			or_filters=[["name", "like", term], ["supplier_name", "like", term]],
			fields=["name", "supplier_name", "supplier_group"],
			order_by="supplier_name asc",
			limit_page_length=cint(limit) or 20,
		)
	}


@frappe.whitelist()
def search_purchase_items(search_term=None, limit=20):
	_check_guest()
	term = f"%{(search_term or '').strip()}%"
	return {
		"items": [
			{"item_code": d.name, "item_name": d.item_name, "stock_uom": d.stock_uom}
			for d in frappe.get_list(
				"Item",
				filters={"disabled": 0, "is_purchase_item": 1},
				or_filters=[["name", "like", term], ["item_name", "like", term]],
				fields=["name", "item_name", "stock_uom"],
				order_by="item_name asc",
				limit_page_length=cint(limit) or 20,
			)
		]
	}


@frappe.whitelist()
def get_supplier_details(supplier, pos_profile=None):
	_check_guest()
	details = get_party_details(
		party=supplier,
		party_type="Supplier",
		company=_resolve_company(pos_profile),
		doctype="Purchase Order",
	)
	return {
		"supplier_name": details.get("supplier_name"),
		"currency": details.get("currency"),
		"buying_price_list": details.get("buying_price_list"),
		"taxes_and_charges": details.get("taxes_and_charges"),
	}


@frappe.whitelist()
def get_purchase_item_details(
	item_code, supplier=None, pos_profile=None, qty=1, transaction_date=None, warehouse=None
):
	_check_guest()
	company = _resolve_company(pos_profile)
	ctx = {
		"item_code": item_code,
		"company": company,
		"supplier": supplier,
		"doctype": "Purchase Order",
		# ERPNext v16 throws on a null currency once a price list resolves, so
		# seed the company default instead of leaving currency unset
		"currency": frappe.get_cached_value("Company", company, "default_currency") if company else None,
		"transaction_date": transaction_date or nowdate(),
		"qty": flt(qty) or 1,
		"set_warehouse": warehouse,
	}
	# get_item_details never resolves the party price list itself — that is the
	# caller's job (the PO does it via set_missing_values) — so seed it here
	if supplier:
		ctx["buying_price_list"] = frappe.db.get_value("Supplier", supplier, "default_price_list")
	details = get_item_details(ctx)
	return {
		"item_code": details.get("item_code"),
		"item_name": details.get("item_name"),
		"uom": details.get("uom"),
		"stock_uom": details.get("stock_uom"),
		"conversion_factor": details.get("conversion_factor"),
		"price_list_rate": details.get("price_list_rate"),
		"rate": details.get("rate"),
		"warehouse": details.get("warehouse"),
	}


def _po_summary(doc):
	"""Shape shared by every mutating/reading method."""
	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"status": doc.status,
		"supplier": doc.supplier,
		"supplier_name": doc.supplier_name,
		"transaction_date": doc.transaction_date,
		"schedule_date": doc.schedule_date,
		"company": doc.company,
		"currency": doc.currency,
		"net_total": doc.net_total,
		"total_taxes_and_charges": doc.total_taxes_and_charges,
		"taxes_and_charges": doc.taxes_and_charges,
		"grand_total": doc.grand_total,
		# PO v16 has no remarks field — the payload remarks ride the native terms field
		"remarks": doc.terms,
		"items": [
			{
				"name": row.name,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": row.qty,
				"uom": row.uom,
				"rate": row.rate,
				"amount": row.amount,
				"warehouse": row.warehouse,
				"schedule_date": row.schedule_date,
			}
			for row in doc.items
		],
	}


@frappe.whitelist()
def save_purchase_order(data, pos_profile=None, submit=0):
	"""Create or update a Purchase Order and optionally submit it.

	The update path (payload carries `name`) expects the FULL payload: omitted
	optional fields (transaction_date, schedule_date, company, set_warehouse,
	currency, remarks...) are reset to their defaults, and the items table is
	replaced wholesale. `taxes_and_charges` is honored whenever present —
	passing "" / null explicitly clears the template and its rows — and is left
	untouched when the key is absent. `remarks` is stored in the native `terms`
	field (PO has no remarks field in ERPNext v16).
	"""
	_check_guest()
	data = _parse(data) or {}
	if not data.get("supplier"):
		frappe.throw(_("Supplier is required"))
	items = _parse(data.get("items")) or []
	if not items:
		frappe.throw(_("At least one item is required"))

	name = data.get("name")
	if name:
		doc = frappe.get_doc("Purchase Order", name)
		_check_permission("write", doc=doc)
		if doc.docstatus != 0:
			frappe.throw(_("Only a Draft Purchase Order can be edited"))
	else:
		_check_permission("create")
		doc = frappe.new_doc("Purchase Order")

	set_warehouse = data.get("set_warehouse") or _profile_value(pos_profile, "warehouse")
	schedule_date = data.get("schedule_date") or add_days(nowdate(), 1)

	doc.supplier = data.get("supplier")
	doc.transaction_date = data.get("transaction_date") or nowdate()
	doc.schedule_date = schedule_date
	doc.company = data.get("company") or _resolve_company(pos_profile)
	if set_warehouse:
		doc.set_warehouse = set_warehouse
	if data.get("currency"):
		doc.currency = data.get("currency")
	if data.get("conversion_rate"):
		doc.conversion_rate = flt(data.get("conversion_rate"))
	if "taxes_and_charges" in data:
		# full payload: present-but-empty explicitly clears the template, so the
		# dialog's remove-tax action works — set_missing_values only expands rows
		# for a truthy template, so a cleared template stays clear through save
		doc.taxes_and_charges = data.get("taxes_and_charges") or None
		if not doc.taxes_and_charges:
			doc.set("taxes", [])
	doc.terms = data.get("remarks")

	doc.set("items", [])
	for row in items:
		doc.append(
			"items",
			{
				"item_code": row.get("item_code"),
				"qty": flt(row.get("qty")),
				"rate": row.get("rate"),
				"uom": row.get("uom"),
				"warehouse": row.get("warehouse") or set_warehouse,
				"schedule_date": row.get("schedule_date") or schedule_date,
			},
		)

	# expand tax template + item details before validate() (validate's
	# set_missing_values(for_validate=True) never expands taxes)
	doc.set_missing_values()
	if name:
		doc.save()
	else:
		doc.insert()
	if cint(submit):
		doc.submit()
	return _po_summary(doc)


@frappe.whitelist()
def get_purchase_order(name):
	_check_guest()
	_check_permission("read", doc=name)
	return _po_summary(frappe.get_doc("Purchase Order", name))


@frappe.whitelist()
def get_purchase_orders(pos_profile=None, status=None, search_term=None, limit=50):
	_check_guest()
	filters = []
	company = _profile_value(pos_profile, "company")
	if company:
		filters.append(["company", "=", company])
	if status:
		filters.append(["status", "=", status])
	or_filters = []
	if search_term:
		term = f"%{search_term.strip()}%"
		or_filters = [["name", "like", term], ["supplier", "like", term], ["supplier_name", "like", term]]
	return {
		"orders": frappe.get_list(
			"Purchase Order",
			filters=filters,
			or_filters=or_filters,
			fields=[
				"name",
				"supplier",
				"supplier_name",
				"transaction_date",
				"schedule_date",
				"grand_total",
				"currency",
				"status",
				"docstatus",
				"per_received",
				"per_billed",
				"company",
				"modified",
			],
			order_by="modified desc",
			limit_page_length=cint(limit) or 50,
		)
	}


@frappe.whitelist()
def submit_purchase_order(name):
	_check_guest()
	doc = frappe.get_doc("Purchase Order", name)
	_check_permission("submit", doc=doc)
	if doc.docstatus != 0:
		frappe.throw(_("Only a Draft Purchase Order can be submitted"))
	doc.submit()
	return _po_summary(doc)


@frappe.whitelist()
def cancel_purchase_order(name):
	_check_guest()
	doc = frappe.get_doc("Purchase Order", name)
	_check_permission("cancel", doc=doc)
	if doc.docstatus != 1:
		frappe.throw(_("Only a submitted Purchase Order can be cancelled"))
	doc.cancel()
	return _po_summary(doc)
