# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Add a non-unique index on Payment Entry Reference (reference_name, reference_doctype).

Serves the Payment Entry <-> shift-invoice matching in the closing drawer
(pos_closing_shift.get_payments_entries) and the sales recap
(_aggregate_payments): production PEs carry "POS-<invoice>"-style
reference_no, so the match goes through the reference child rows, and ERPNext
ships no index leading on reference_name. At small volumes MariaDB probes
per.parent (the stock child-table index); this index is what lets the planner
flip to a reference_name-driven semijoin once tabPayment Entry grows. Core
ERPNext table, hence a site-level DDL rather than an app schema change.

Runs in [post_model_sync]: the table must exist.
"""

import frappe

INDEX_NAME = "payment_entry_reference_name_doctype_idx"


def execute():
	# DDL directly (frappe.db.add_index is fine too, but the guard keeps the
	# patch safe to re-run either way)
	exists = frappe.db.sql(
		f"""
		SELECT 1 FROM information_schema.STATISTICS
		WHERE table_schema = DATABASE()
		AND table_name = 'tabPayment Entry Reference'
		AND index_name = '{INDEX_NAME}'
		"""
	)
	if not exists:
		frappe.db.sql_ddl(
			f"ALTER TABLE `tabPayment Entry Reference` ADD INDEX {INDEX_NAME}"
			" (reference_name, reference_doctype)"
		)
