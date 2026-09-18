"""Global invoice-doctype resolution for POS Next (see spec 2026-09-10)."""

import frappe

SALES_INVOICE = "Sales Invoice"
POS_INVOICE = "POS Invoice"
_VALID_TYPES = (SALES_INVOICE, POS_INVOICE)


def get_pos_invoice_doctype():
	"""The doctype new POS transactions are created in (request-cached).

	Stored on the POS Settings rows — one global value shared by every row;
	the POS Settings controller keeps all rows in sync on save, so reading
	any row yields the site-wide choice.

	POS Invoice is the default: POS Next's POS Invoices post their own GL and
	stock ledger entries at submit (see CustomPOSInvoice), so they behave like
	Sales Invoices while keeping POS traffic out of the Sales Invoice list.
	"""
	cached = getattr(frappe.local, "_pos_next_invoice_doctype", None)
	if cached:
		return cached
	# NOTE: filters={} (any row) — filters=None would be a name lookup of None.
	value = frappe.db.get_value("POS Settings", {}, "invoice_type") or POS_INVOICE
	if value not in _VALID_TYPES:
		value = POS_INVOICE
	frappe.local._pos_next_invoice_doctype = value
	return value


def _legacy_deferred_pos_invoices():
	"""POS Next POS Invoices that still defer their books to consolidation.

	A POS Invoice created before accounting parity posts nothing until a POS
	Closing Entry consolidates it, so it still depends on the consolidation
	path this mode no longer runs. Newer parity invoices post their own GL and
	are therefore excluded — counting them would block the switch forever.
	"""
	return frappe.db.sql(
		"""
		SELECT COUNT(*) FROM `tabPOS Invoice` pi
		WHERE pi.docstatus = 1
		  AND IFNULL(pi.consolidated_invoice, '') = ''
		  AND IFNULL(pi.posa_pos_opening_shift, '') <> ''
		  AND NOT EXISTS (
			SELECT 1 FROM `tabGL Entry` gl
			WHERE gl.voucher_type = 'POS Invoice'
			  AND gl.voucher_no = pi.name
			  AND gl.is_cancelled = 0
		  )
		"""
	)[0][0]


def validate_invoice_type_change(doc):
	"""POS Settings validate hook: gate the global invoice-type switch."""
	from frappe import _

	before = doc.get_doc_before_save()
	if not before or before.get("invoice_type") == doc.invoice_type:
		return
	if doc.invoice_type not in _VALID_TYPES:
		frappe.throw(_("Invoice Type must be Sales Invoice or POS Invoice."))
	open_shifts = frappe.db.count("POS Opening Shift", {"docstatus": 1, "status": "Open"})
	if open_shifts:
		frappe.throw(
			_("Invoice Type cannot be changed while {0} POS Opening Shift(s) are open.").format(
				frappe.bold(open_shifts)
			)
		)
	pending = frappe.db.count("Offline Invoice Sync", {"status": "Pending"})
	if pending:
		frappe.throw(
			_("Invoice Type cannot be changed while {0} offline invoice(s) are pending sync.").format(
				frappe.bold(pending)
			)
		)
	deferred = _legacy_deferred_pos_invoices()
	if deferred:
		frappe.throw(
			_(
				"Invoice Type cannot be changed while {0} POS Invoice(s) still have no stock and accounting entries. Consolidate or cancel them first."
			).format(frappe.bold(deferred))
		)


def get_sales_report_doctypes():
	"""Doctypes sales reporting reads. In POS Invoice mode, legacy Sales
	Invoices are still included (consolidated ones excluded at query level)."""
	if get_pos_invoice_doctype() == POS_INVOICE:
		return [POS_INVOICE, SALES_INVOICE]
	return [SALES_INVOICE]


_SI_EXCLUSION = "IFNULL({prefix}.is_consolidated, 0) = 0"


def _branch_where(dt, where, exclude_consolidated):
	cond = where.format(dt=dt) if where else ""
	# The is_consolidated exclusion is an anti-double-count against the POS
	# Invoice branch — only meaningful (and only applied) when that branch is
	# in the union. In pure Sales Invoice mode the WHERE is untouched, so
	# ERPNext built-in-POS consolidated invoices stay visible.
	if exclude_consolidated and dt == SALES_INVOICE:
		cond += (" AND " if cond else "") + _SI_EXCLUSION.format(prefix="si")
	return f" WHERE {cond}" if cond else ""


def sales_invoice_union(columns, where=""):
	"""SQL source for sales reporting: UNION ALL of ``get_sales_report_doctypes()``
	projected to ``columns``, as derived table ``si``.

	``where`` (optional, ``si.``-prefixed SQL; may use ``{dt}`` for the branch
	doctype) is pushed into every branch so the union stays index-sized. In
	POS Invoice mode the Sales Invoice branch additionally drops
	``is_consolidated`` rows — each consolidated legacy SI represents POS
	Invoices that are already in the union (anti double-count)."""
	doctypes = get_sales_report_doctypes()
	exclude = POS_INVOICE in doctypes
	parts = []
	for dt in doctypes:
		cond = _branch_where(dt, where, exclude)
		parts.append(f"(SELECT {columns} FROM `tab{dt}` si{cond})")
	return f"({' UNION ALL '.join(parts)}) si"


def sales_invoice_item_union(columns, where=""):
	"""sales_invoice_union for the per-doctype item child tables: each branch
	is ``tab<dt> Item sii`` pre-joined to its invoice (``si``), so ``columns``
	and ``where`` may reference both ``sii.`` and ``si.`` columns. In POS
	Invoice mode consolidated legacy invoices are excluded together with their
	items."""
	doctypes = get_sales_report_doctypes()
	exclude = POS_INVOICE in doctypes
	parts = []
	for dt in doctypes:
		cond = _branch_where(dt, where, exclude)
		parts.append(
			f"(SELECT {columns} FROM `tab{dt} Item` sii "
			f"INNER JOIN `tab{dt}` si ON si.name = sii.parent{cond})"
		)
	return f"({' UNION ALL '.join(parts)}) sii"


def is_pos_next_owned(doc):
	"""True when a POS Invoice was created by POS Next (vs ERPNext built-in POS)."""
	return bool(doc.get("posa_pos_opening_shift"))


def guard_against_retroactive_consolidation(doc, method=None):
	"""POS Invoice Merge Log before_validate: refuse to consolidate invoices
	POS Next already accounted for.

	Parity POS Invoices post their own GL/stock entries at submit, so
	ERPNext's consolidation (usually reached through the built-in POS Closing
	Entry, which selects on an empty ``consolidated_invoice``) would post the
	same money and stock twice. Built-in ERPNext POS Invoices carry no
	``posa_pos_opening_shift`` and stay consolidatable.
	"""
	from frappe import _

	owned = [
		row.pos_invoice
		for row in (doc.get("pos_invoices") or [])
		if row.get("pos_invoice")
		and frappe.db.get_value("POS Invoice", row.pos_invoice, "posa_pos_opening_shift")
	]
	if owned:
		frappe.throw(
			_(
				"Cannot consolidate POS Invoice(s) {0}: they already posted their own stock and accounting entries."
			).format(frappe.bold(", ".join(owned)))
		)
