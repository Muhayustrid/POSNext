import frappe


def execute():
	"""Wipe legacy POS Offer rows before the rebuilt schema installs.

	The old POS Offer fields (POS Awesome inheritance) do not map 1:1 to the
	2.0 semantics, and nothing read the doctype anyway. pre_model_sync so the
	rows are gone before doctype sync drops the legacy columns.
	"""
	frappe.db.delete("POS Offer Detail", {})
	frappe.db.delete("POS Offer", {})
