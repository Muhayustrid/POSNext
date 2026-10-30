"""Test helper: set what a POS Profile's code gate switch resolves to."""

import frappe

GLOBAL_DOCTYPE = "POS Next Global Settings"


def set_code_switch(pos_profile, field, value):
	"""Write `field` (require_discount_code / require_refund_code) where the
	resolver reads it for `pos_profile`: its enabled POS Settings row, else the
	global single. Returns a callable that restores the previous value."""
	row = frappe.db.get_value("POS Settings", {"pos_profile": pos_profile, "enabled": 1})
	if row:
		old = frappe.db.get_value("POS Settings", row, field)
		frappe.db.set_value("POS Settings", row, field, value)
		return lambda: frappe.db.set_value("POS Settings", row, field, old)
	old = frappe.db.get_single_value(GLOBAL_DOCTYPE, field)
	frappe.db.set_single_value(GLOBAL_DOCTYPE, field, value)
	return lambda: frappe.db.set_single_value(GLOBAL_DOCTYPE, field, old)
