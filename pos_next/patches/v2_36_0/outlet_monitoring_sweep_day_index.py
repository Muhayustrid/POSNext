# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Add om_sweep_day_idx on tabPOS Monitoring Report for mark_missed Per Hari branch.

Serves the plain Per Hari branch of mark_missed:
`force index (om_sweep_day_idx)` on (docstatus, status, cadence, operational_date).

Idempotent site-level DDL patch; runs in [post_model_sync] after the table exists.
"""

import frappe

INDEX_NAME = "om_sweep_day_idx"
INDEX_COLUMNS = ("docstatus", "status", "cadence", "operational_date")


def execute():
	if not frappe.db.table_exists("POS Monitoring Report"):
		return

	exists = frappe.db.sql(
		"""
		SELECT 1 FROM information_schema.STATISTICS
		WHERE table_schema = DATABASE()
		AND table_name = 'tabPOS Monitoring Report'
		AND index_name = %s
		""",
		(INDEX_NAME,),
	)
	if not exists:
		frappe.db.sql_ddl(
			f"ALTER TABLE `tabPOS Monitoring Report` ADD INDEX {INDEX_NAME} ({', '.join(INDEX_COLUMNS)})"
		)
