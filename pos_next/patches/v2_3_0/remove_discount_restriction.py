# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Replace the POS Discount Restriction rule system with the discount code gate.

Drops the rule doctypes (window/company/quota machinery), the rule link field
on Sales Invoice, and the one-time code rows generated under those rules. The
code doctype itself survives, repurposed as a multi-use access code that stays
valid until head office disables it.
"""

import frappe

LEGACY_DOCTYPES = (
	"POS Discount Restriction",
	"POS Discount Restriction Company",
	"POS Discount Restriction Item",
	"POS Discount Restriction Usage",
)

CODE_TABLE = "`tabPOS Discount Confirmation Code`"


def _drop_column(table, column):
	# sql_ddl (not sql): DDL autocommits in MariaDB, and frappe raises
	# ImplicitCommitError on DDL inside an open transaction.
	try:
		frappe.db.sql_ddl(f"ALTER TABLE {table} DROP COLUMN `{column}`")
	except Exception:
		# Column never existed on this site (fresh install) — nothing to do.
		pass


def _fix_custom_field_anchors():
	"""Re-anchor the surviving Custom Field rows on upgraded sites.

	Model sync never moves an existing field, and the old anchor
	(`pos_discount_restriction`) is dropped above — re-point the fields at
	their intended neighbours so the form layout matches install.py.
	Guarded: a missing row (fresh install path) is skipped silently.
	"""
	fixups = (
		{
			"fieldname": "discount_confirmation_code",
			"insert_after": "buyer_name",
			"description": "HQ discount code entered for this invoice's manual discounts.",
		},
		{"fieldname": "pos_applied_offer_rules", "insert_after": "discount_confirmation_code"},
	)
	for fixup in fixups:
		fieldname = fixup["fieldname"]
		filters = {"dt": "Sales Invoice", "fieldname": fieldname}
		if not frappe.db.exists("Custom Field", filters):
			continue
		values = {key: value for key, value in fixup.items() if key != "fieldname"}
		frappe.db.set_value("Custom Field", filters, values)


def execute():
	# One-time codes are meaningless without their rule; start clean. Codes
	# generated from now on are multi-use and carry no rule reference.
	# pre_model_sync runs before the table exists on sites that never had
	# the code doctype (fresh installs, upgrades from pre-2.3 versions).
	if frappe.db.table_exists("POS Discount Confirmation Code"):
		frappe.db.delete("POS Discount Confirmation Code")

	frappe.db.delete("Custom Field", {"dt": "Sales Invoice", "fieldname": "pos_discount_restriction"})
	_drop_column("`tabSales Invoice`", "pos_discount_restriction")

	# Stale columns from the one-time-code era (model sync never drops columns).
	for column in ("restriction", "used_by", "used_in_invoice", "used_on"):
		_drop_column(CODE_TABLE, column)

	for doctype in LEGACY_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=1)

	_fix_custom_field_anchors()
