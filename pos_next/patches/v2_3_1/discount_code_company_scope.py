# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Migrate the removed single-company Link on POS Discount Confirmation Code
to the 3-mode outlet scope: each legacy row becomes "Selected Outlets" with one
child row.

Runs in [post_model_sync]: the child doctype already exists and the legacy
`company` column is still in the table (model sync never drops columns).
"""

import frappe

CODE_DOCTYPE = "POS Discount Confirmation Code"
CODE_COMPANY_DOCTYPE = "POS Discount Code Company"


def execute():
	# Sites installed after the Link was removed never had the legacy column,
	# so there is nothing to migrate — the SELECT below would 1054 on them.
	if not frappe.db.table_exists(CODE_DOCTYPE) or not frappe.db.has_column(
		CODE_DOCTYPE, "company"
	):
		return
	legacy = frappe.db.sql(
		"""
		select name, company
		from `tabPOS Discount Confirmation Code`
		where company is not null and company != ''
		""",
		as_dict=True,
	)
	for row in legacy:
		already_migrated = frappe.db.exists(
			CODE_COMPANY_DOCTYPE,
			{"parenttype": CODE_DOCTYPE, "parent": row.name, "parentfield": "companies"},
		)
		if already_migrated:
			continue
		frappe.db.set_value(CODE_DOCTYPE, row.name, "company_scope", "Selected Outlets")
		frappe.get_doc(
			{
				"doctype": CODE_COMPANY_DOCTYPE,
				"company": row.company,
				"parent": row.name,
				"parenttype": CODE_DOCTYPE,
				"parentfield": "companies",
			}
		).insert(ignore_permissions=True, ignore_links=True)
