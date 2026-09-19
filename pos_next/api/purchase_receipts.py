# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from frappe import _
from frappe.utils import cint, flt, nowdate

from pos_next.api.purchase_orders import (
	_check_guest,
	_parse,
	_profile_value,
	_resolve_company,
)
from pos_next.api.purchase_orders import (
	_check_permission as _check_po_permission,
)


def _check_permission(perm, doc=None):
	# frappe v16 has_permission only returns a bool, never raises — the explicit
	# check here gives a clear error and closes read paths get_doc doesn't gate
	if not frappe.has_permission("Purchase Receipt", perm, doc=doc):
		frappe.throw(_("No {0} permission on Purchase Receipt").format(_(perm)), frappe.PermissionError)


def _refuse_internal_suppliers(suppliers):
	# An intercompany receipt is born from the selling company's Delivery Note
	# (make_inter_company_purchase_receipt); a POS receipt against the same PO
	# would never update the DN and opens a double-receipt window.
	internal = frappe.get_all(
		"Supplier",
		filters={"name": ("in", sorted({s for s in suppliers if s})), "is_internal_supplier": 1},
		fields=["name"],
	)
	if internal:
		frappe.throw(
			_("Purchase Receipts for internal supplier {0} are created from the Delivery Note").format(
				", ".join(d.name for d in internal)
			)
		)


@frappe.whitelist()
def get_purchase_receipt_draft(po_name):
	"""Unsaved mapper output shaped for the POS receive form.

	The official ERPNext mapper fills rate, UOM, conversion links and the
	remaining (pending) qty per row; rows already fully received — or
	drop-shipped — are absent. Nothing is inserted.
	"""
	_check_guest()
	po = frappe.get_doc("Purchase Order", po_name)
	_check_po_permission("read", doc=po)
	_check_permission("create")
	_refuse_internal_suppliers({po.supplier})
	po_items = {d.name: d for d in po.items}
	pr = make_purchase_receipt(po_name)
	return {
		"supplier": pr.supplier,
		"supplier_name": pr.supplier_name,
		"posting_date": pr.posting_date or nowdate(),
		"company": pr.company or po.company,
		"currency": pr.currency or po.currency,
		"set_warehouse": pr.set_warehouse or po.set_warehouse,
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"purchase_order": row.purchase_order,
				"purchase_order_item": row.purchase_order_item,
				"ordered_qty": flt(po_items[row.purchase_order_item].qty)
				if row.purchase_order_item in po_items
				else flt(row.qty),
				"received_qty": flt(po_items[row.purchase_order_item].received_qty)
				if row.purchase_order_item in po_items
				else 0,
				"pending_qty": flt(row.qty),
				"uom": row.uom,
				"rate": row.rate,
				"warehouse": row.warehouse,
			}
			for row in pr.items
		],
	}


def _pr_summary(doc):
	"""Shape shared by every mutating/reading method."""
	return {
		"name": doc.name,
		"docstatus": doc.docstatus,
		"status": doc.status,
		"supplier": doc.supplier,
		"supplier_name": doc.supplier_name,
		"posting_date": doc.posting_date,
		"company": doc.company,
		"set_warehouse": doc.set_warehouse,
		"currency": doc.currency,
		"net_total": doc.net_total,
		"grand_total": doc.grand_total,
		"per_billed": doc.per_billed,
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
				"purchase_order": row.purchase_order,
				"purchase_order_item": row.purchase_order_item,
			}
			for row in doc.items
		],
	}


@frappe.whitelist()
def save_purchase_receipt(data, submit=0):
	"""Create or update a Purchase Receipt and optionally submit it.

	The update path (payload carries `name`) expects the FULL payload: omitted
	optional fields (posting_date, company, set_warehouse, currency...) are
	reset to their defaults, and the items table is replaced wholesale.
	"""
	_check_guest()
	data = _parse(data) or {}
	items = _parse(data.get("items")) or []
	if not items:
		frappe.throw(_("At least one item is required"))

	name = data.get("name")
	if name:
		doc = frappe.get_doc("Purchase Receipt", name)
		_check_permission("write", doc=doc)
		if doc.docstatus != 0:
			frappe.throw(_("Only a Draft Purchase Receipt can be edited"))
	else:
		_check_permission("create")
		# guard the create path even for hand-crafted payloads: any item linked
		# to an internal supplier's PO belongs to the Delivery Note flow
		po_names = sorted({row.get("purchase_order") for row in items if row.get("purchase_order")})
		suppliers = {data.get("supplier")}
		if po_names:
			suppliers |= {
				d.supplier
				for d in frappe.get_all(
					"Purchase Order", filters={"name": ("in", po_names)}, fields=["supplier"]
				)
			}
		_refuse_internal_suppliers(suppliers)
		doc = frappe.new_doc("Purchase Receipt")

	doc.supplier = data.get("supplier")
	doc.posting_date = data.get("posting_date") or nowdate()
	doc.company = data.get("company") or _resolve_company()
	if data.get("set_warehouse"):
		doc.set_warehouse = data.get("set_warehouse")
	if data.get("currency"):
		doc.currency = data.get("currency")
	if data.get("conversion_rate"):
		doc.conversion_rate = flt(data.get("conversion_rate"))

	doc.set("items", [])
	for row in items:
		doc.append(
			"items",
			{
				"item_code": row.get("item_code"),
				"qty": flt(row.get("qty")),
				"rate": row.get("rate"),
				"uom": row.get("uom"),
				"warehouse": row.get("warehouse") or data.get("set_warehouse"),
				"purchase_order": row.get("purchase_order"),
				"purchase_order_item": row.get("purchase_order_item"),
			},
		)

	# expand item details (uom/conversion factor/rate/accounts) + currency
	# before validate() — a missing supplier lets PR validate throw natively
	doc.set_missing_values()
	if name:
		doc.save()
	else:
		doc.insert()
	if cint(submit):
		doc.submit()
	return _pr_summary(doc)


@frappe.whitelist()
def get_purchase_receipts(pos_profile=None, status=None, search_term=None, limit=50):
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
		"receipts": frappe.get_list(
			"Purchase Receipt",
			filters=filters,
			or_filters=or_filters,
			fields=[
				"name",
				"supplier",
				"supplier_name",
				"posting_date",
				"status",
				"docstatus",
				"grand_total",
				"currency",
				"per_billed",
				"company",
				"modified",
			],
			order_by="modified desc",
			limit_page_length=cint(limit) or 50,
		)
	}


@frappe.whitelist()
def cancel_purchase_receipt(name):
	_check_guest()
	doc = frappe.get_doc("Purchase Receipt", name)
	_check_permission("cancel", doc=doc)
	if doc.docstatus != 1:
		frappe.throw(_("Only a submitted Purchase Receipt can be cancelled"))
	doc.cancel()
	return _pr_summary(doc)
