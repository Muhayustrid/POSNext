# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Move the code gate switches from Company to POS Settings.

The global single gets both switches ON. Every POS Settings row copies the
switches of its profile's company, read with raw SQL because the Company
custom fields are deleted right after. Without the Company columns (a site
that never had them) a row keeps what it holds. Then the Company fields and
the POS Code Gate Group doctypes are removed.
"""

import frappe

GLOBAL_DOCTYPE = "POS Next Global Settings"
SWITCHES = {
	"require_discount_code": "pos_require_discount_code",
	"require_refund_code": "pos_require_refund_code",
}
COMPANY_FIELDS = ("pos_require_discount_code", "pos_require_refund_code", "pos_code_gate_group")


def execute():
	for field in SWITCHES:
		frappe.db.set_single_value(GLOBAL_DOCTYPE, field, 1)

	company_columns = frappe.db.get_table_columns("Company")
	for new, old in SWITCHES.items():
		if old not in company_columns:
			continue
		# NULL on the company means required (the old gate failed closed)
		frappe.db.sql(
			f"""update `tabPOS Settings` ps
			join `tabPOS Profile` p on p.name = ps.pos_profile
			join `tabCompany` c on c.name = p.company
			set ps.`{new}` = coalesce(c.`{old}`, 1)"""
		)

	for field in COMPANY_FIELDS:
		name = f"Company-{field}"
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)

	for doctype in ("POS Code Gate Group", "POS Code Gate Group Member"):
		frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True, ignore_permissions=True)
	frappe.clear_cache(doctype="Company")
