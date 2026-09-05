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


def execute():
	# One-time codes are meaningless without their rule; start clean. Codes
	# generated from now on are multi-use and carry no rule reference.
	frappe.db.delete("POS Discount Confirmation Code", {"name": ("is", "set")})

	frappe.db.delete("Custom Field", {"dt": "Sales Invoice", "fieldname": "pos_discount_restriction"})
	_drop_column("`tabSales Invoice`", "pos_discount_restriction")

	# Stale columns from the one-time-code era (model sync never drops columns).
	for column in ("restriction", "used_by", "used_in_invoice", "used_on"):
		_drop_column(CODE_TABLE, column)

	for doctype in LEGACY_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=1)
