# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Scope existing POS Coupons to their issuing outlet.

Coupons gained an outlet scope (mirroring the head-office code gate). Every
coupon that predates the scope is bound to its issuing company only, so
behaviour stays the same until head office widens the scope.
"""

import frappe


def execute():
	for coupon in frappe.get_all("POS Coupon", fields=["name", "company", "company_scope"]):
		if not coupon.company:
			continue
		if not coupon.company_scope:
			frappe.db.set_value(
				"POS Coupon", coupon.name, "company_scope", "Selected Outlets", update_modified=False
			)
		if frappe.db.exists(
			"POS Discount Code Company",
			{"parenttype": "POS Coupon", "parent": coupon.name},
		):
			continue
		frappe.get_doc(
			{
				"doctype": "POS Discount Code Company",
				"parenttype": "POS Coupon",
				"parent": coupon.name,
				"parentfield": "companies",
				"company": coupon.company,
			}
		).insert(ignore_permissions=True, ignore_links=True)
