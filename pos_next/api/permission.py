import frappe


def has_app_permission() -> bool:
	"""Gate the POSNext tile on the apps screen: only users who can read the
	app's global settings (both POSNext personas can; unrelated users can't)."""
	return bool(frappe.has_permission("POS Next Global Settings", "read"))
