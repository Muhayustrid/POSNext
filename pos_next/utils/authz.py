# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe

# Role manajemen POS (SEC-NEW-10): management-level endpoints and profile
# settings are for these roles, or for the profile's own users.
_MANAGEMENT_ROLES = {"System Manager", "POSNext Manager"}


def is_management_user():
	if frappe.session.user == "Administrator":
		return True
	return bool(_MANAGEMENT_ROLES.intersection(frappe.get_roles()))
