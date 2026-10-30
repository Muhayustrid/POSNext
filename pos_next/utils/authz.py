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


def is_sales_blocked(user=None):
	"""True for a POSNext Manager: they purchase, count, produce and review,
	but never sell or open a shift (even holding POSNext Cashier too).
	Administrator and System Manager keep selling for support and tests."""
	user = user or frappe.session.user
	if user == "Administrator":
		return False
	roles = set(frappe.get_roles(user))
	return "POSNext Manager" in roles and "System Manager" not in roles


def assert_can_sell():
	if is_sales_blocked():
		frappe.throw(frappe._("POS Managers cannot make sales or open a shift"), frappe.PermissionError)


# Roles that legitimately work in Desk (management, HQ monitoring, opname
# approval). A POSNext Cashier holding none of them is kept in the POS SPA.
_DESK_ROLES = _MANAGEMENT_ROLES | {
	"Accounts Manager",
	"Sales Manager",
	"Stock Manager",
}


def is_desk_blocked(user=None):
	"""True for a POS cashier who may not open Desk (see utils/desk_guard)."""
	user = user or frappe.session.user
	if user in ("Administrator", "Guest"):
		return False
	roles = set(frappe.get_roles(user))
	return "POSNext Cashier" in roles and not (roles & _DESK_ROLES)
