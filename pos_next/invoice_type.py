"""Global invoice-doctype resolution for POS Next (see spec 2026-09-10)."""

import frappe
from frappe.utils import flt

SALES_INVOICE = "Sales Invoice"
POS_INVOICE = "POS Invoice"
_SINGLE = "POS Next Invoice Settings"


def get_pos_invoice_doctype():
	"""The doctype new POS transactions are created in (request-cached)."""
	cached = getattr(frappe.local, "_pos_next_invoice_doctype", None)
	if cached:
		return cached
	value = frappe.db.get_single_value(_SINGLE, "invoice_type") or SALES_INVOICE
	if value not in (SALES_INVOICE, POS_INVOICE):
		value = SALES_INVOICE
	frappe.local._pos_next_invoice_doctype = value
	return value


def get_sales_report_doctypes():
	"""Doctypes sales reporting reads. In POS Invoice mode, legacy Sales
	Invoices are still included (consolidated ones excluded at query level)."""
	if get_pos_invoice_doctype() == POS_INVOICE:
		return [POS_INVOICE, SALES_INVOICE]
	return [SALES_INVOICE]


_SI_EXCLUSION = "IFNULL({prefix}.is_consolidated, 0) = 0"


def sales_invoice_union(columns, where=""):
	"""SQL source for sales reporting: UNION ALL of ``get_sales_report_doctypes()``
	projected to ``columns``, as derived table ``si``.

	``where`` (optional, ``si.``-prefixed SQL; may use ``{dt}`` for the branch
	doctype) is pushed into every branch so the union stays index-sized. The
	Sales Invoice branch additionally drops ``is_consolidated`` rows — each
	consolidated legacy SI represents POS Invoices that are already in the
	union (anti double-count)."""
	parts = []
	for dt in get_sales_report_doctypes():
		cond = where.format(dt=dt) if where else ""
		if dt == SALES_INVOICE:
			cond += (" AND " if cond else "") + _SI_EXCLUSION.format(prefix="si")
		cond = f" WHERE {cond}" if cond else ""
		parts.append(f"(SELECT {columns} FROM `tab{dt}` si{cond})")
	return f"({' UNION ALL '.join(parts)}) si"


def sales_invoice_item_union(columns, where=""):
	"""sales_invoice_union for the per-doctype item child tables: each branch
	is ``tab<dt> Item sii`` pre-joined to its invoice (``si``), so ``columns``
	and ``where`` may reference both ``sii.`` and ``si.`` columns. Consolidated
	legacy invoices are excluded together with their items."""
	parts = []
	for dt in get_sales_report_doctypes():
		cond = where.format(dt=dt) if where else ""
		if dt == SALES_INVOICE:
			cond += (" AND " if cond else "") + _SI_EXCLUSION.format(prefix="si")
		cond = f" WHERE {cond}" if cond else ""
		parts.append(
			f"(SELECT {columns} FROM `tab{dt} Item` sii "
			f"INNER JOIN `tab{dt}` si ON si.name = sii.parent{cond})"
		)
	return f"({' UNION ALL '.join(parts)}) sii"


def is_pos_next_owned(doc):
	"""True when a POS Invoice was created by POS Next (vs ERPNext built-in POS)."""
	return bool(doc.get("posa_pos_opening_shift"))


def get_unconsolidated_posi_qty(item_codes, warehouse):
	"""Sold-but-unconsolidated POS Invoice qty per item for a warehouse.
	SLEs only appear at consolidation, so this is the intraday reservation."""
	# ponytail: exact-warehouse match only — group-warehouse callers need the
	# per-child sum expanded at the call site if that case ever matters.
	if get_pos_invoice_doctype() != POS_INVOICE or not item_codes or not warehouse:
		return {}
	data = frappe.db.sql(
		"""
		select item.item_code, sum(item.stock_qty)
		from `tabPOS Invoice` inv, `tabPOS Invoice Item` item
		where item.parent = inv.name
		  and inv.docstatus = 1 and ifnull(inv.consolidated_invoice,'') = ''
		  and ifnull(inv.is_return, 0) = 0
		  and item.warehouse = %(warehouse)s
		  and item.item_code in %(items)s
		group by item.item_code
		""",
		{"warehouse": warehouse, "items": list(item_codes)},
	)
	return {item_code: flt(qty) for item_code, qty in data}
