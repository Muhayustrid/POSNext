"""
Runtime patch for ERPNext POS reserved-qty math.

Why this patch exists:
- ERPNext treats every submitted, unconsolidated POS Invoice as "reserved"
  stock, because a built-in POS Invoice posts no Stock Ledger until closing.
- POS Next POS Invoices post their own Stock Ledger at submit (see
  CustomPOSInvoice) and are never consolidated. ERPNext then subtracts them
  twice — once in Bin.actual_qty and again as reserved — so availability goes
  negative while Stock Balance is still positive (ProductBundleStockValidationError).
- Fix: exclude POS Next invoices (posa_pos_opening_shift set) from the
  reserved sum. Built-in POS Invoices keep the original behaviour.
"""

from __future__ import annotations

import frappe
from frappe.query_builder.functions import IfNull, Sum
from frappe.utils import flt


def patch_pos_reserved_qty(pos_invoice_module):
	if getattr(pos_invoice_module, "_pos_next_reserved_qty_patched", False):
		return

	original = pos_invoice_module.get_pos_reserved_qty_from_table

	def get_pos_reserved_qty_from_table(child_table, item_code, warehouse):
		if not frappe.get_meta("POS Invoice").has_field("posa_pos_opening_shift"):
			return original(child_table, item_code, warehouse)

		p_inv = frappe.qb.DocType("POS Invoice")
		p_item = frappe.qb.DocType(child_table)
		qty_column = "qty" if child_table == "Packed Item" else "stock_qty"

		reserved_qty = (
			frappe.qb.from_(p_inv)
			.from_(p_item)
			.select(Sum(p_item[qty_column]).as_("stock_qty"))
			.where(
				(p_inv.name == p_item.parent)
				& (IfNull(p_inv.consolidated_invoice, "") == "")
				& (IfNull(p_inv.posa_pos_opening_shift, "") == "")
				& (p_item.docstatus == 1)
				& (p_item.item_code == item_code)
				& (p_item.warehouse == warehouse)
			)
		).run(as_dict=True)

		return flt(reserved_qty[0].stock_qty) if reserved_qty else 0

	pos_invoice_module.get_pos_reserved_qty_from_table = get_pos_reserved_qty_from_table
	pos_invoice_module._pos_next_reserved_qty_patched = True
