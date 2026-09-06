import frappe


def execute():
	"""Wipe legacy POS Offer rows before the rebuilt schema installs.

	The old POS Offer fields (POS Awesome inheritance) do not map 1:1 to the
	2.0 semantics, and nothing read the doctype anyway. pre_model_sync so the
	rows are gone before doctype sync drops the legacy columns.
	"""
	# pre_model_sync runs before doctype sync creates the tables on a fresh
	# install — nothing to wipe there.
	for doctype in ("POS Offer Detail", "POS Offer"):
		if frappe.db.table_exists(doctype):
			frappe.db.delete(doctype, {})
