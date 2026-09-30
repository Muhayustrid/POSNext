# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Re-seat the outlet target fields into the Buying and Selling tab.

The pair was first shipped with insert_after default_currency, which lands it
in whatever section that field lives in — far from where a user sets sales
goals. ERPNext already has the Monthly Sales Target / Total Monthly Sales pair
in the Buying and Selling tab; park our fields right after it.

Custom Field position is resolved from insert_after, so updating the value is
the whole move — no delete/recreate, the target values on existing companies
survive. Idempotent: the second run sees the new position and changes nothing.
"""

import frappe

MOVES = (
	(("Company", "pos_overall_sales_target"), "total_monthly_sales"),
	(("Company", "pos_overall_target_from"), "pos_overall_sales_target"),
)


def execute():
	for (dt, fieldname), insert_after in MOVES:
		name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname}, "name")
		if not name:
			continue
		if frappe.db.get_value("Custom Field", name, "insert_after") != insert_after:
			frappe.db.set_value("Custom Field", name, "insert_after", insert_after, update_modified=False)
	if frappe.db.exists("Custom Field", {"dt": "Company", "fieldname": "pos_overall_sales_target"}):
		frappe.clear_cache(doctype="Company")
