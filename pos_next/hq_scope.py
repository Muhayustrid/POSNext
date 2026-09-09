# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Row-level data scope shared by HQ reporting surfaces (Sales vs Shifts Report,
HQ Sales Monitoring page/API).

Scope rules:
- Companies come from the user's User Permissions on "Company". A user without
  any Company rule is unrestricted (sees every company).
- An explicit company filter must lie inside that scope; a forged or foreign
  company raises PermissionError instead of being silently ignored or summed.
- POS Profiles follow the same pattern via User Permissions on "POS Profile".
"""

import frappe
from frappe import _


def get_permitted_companies(user=None):
	"""Allowed company names from User Permissions, or None if unrestricted."""
	return _user_perm_docs("Company", user) or None


def get_permitted_pos_profiles(user=None):
	"""Allowed POS Profile names from User Permissions, or None if unrestricted."""
	return _user_perm_docs("POS Profile", user) or None


def _user_perm_docs(doctype, user=None):
	perms = frappe.permissions.get_user_permissions(user or frappe.session.user)
	return [entry.get("doc") for entry in (perms.get(doctype) or []) if entry.get("doc")]


def resolve_company_scope(filters):
	"""Combine the explicit company filter with the permission scope.

	Returns ``(companies, restricted)`` where ``companies`` is None when the user
	is unrestricted and gave no explicit filter. Raises for forged/unknown
	company values so a non-owner can never widen their window.
	"""
	filters = filters or {}
	permitted = get_permitted_companies()
	company = filters.get("company")

	if company:
		if not frappe.db.exists("Company", company):
			frappe.throw(_("Company {0} does not exist").format(company), frappe.DoesNotExistError)
		if permitted and company not in permitted:
			frappe.throw(_("Not permitted to access Company {0}").format(company), frappe.PermissionError)
		return [company], permitted is not None

	return permitted, permitted is not None


def apply_company_scope(filters, alias):
	"""Return SQL WHERE fragments scoping ``alias.company`` for these filters.

	Mutates ``filters`` in place to add the ``company_scope`` bind parameter.
	"""
	filters = filters or {}
	companies, restricted = resolve_company_scope(filters)

	parts = []
	if filters.get("company"):
		parts.append(f"{alias}.company = %(company)s")
	if restricted and companies:
		filters["company_scope"] = companies
		parts.append(f"{alias}.company IN %(company_scope)s")
	return parts


def expand_company_descendants(company, scope=None):
	"""Return ``[company] + its descendants`` (tree), optionally intersected with
	a permitted scope so subsidiaries outside the user's window stay excluded."""
	names = [company] + (frappe.db.get_descendants("Company", company) or [])
	if scope is not None:
		names = [name for name in names if name in scope]
	return sorted(set(names))
