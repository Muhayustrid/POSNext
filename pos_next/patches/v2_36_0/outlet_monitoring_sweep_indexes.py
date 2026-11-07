# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Indexes for the bounded mark_missed sweep.

`mark_missed` locks its candidate rows with `FOR UPDATE`, and MariaDB locks
every row such a statement scans, so each sweep branch must be able to reach
its rows by an index prefix instead of scanning the table:

- `om_sweep_due_idx (docstatus, status, cadence, due_at)` serves the
  due+grace branches. The three leading equalities plus the due_at range also
  satisfy their `order by due_at`, so the sort happens in the index and no
  extra rows are read (and therefore locked).
- `om_sweep_shift_idx (opening_shift, docstatus, status)` serves the
  closed-shift branch: it reaches its rows through the report's own
  `opening_shift in (...)`, locking only the linked draft reports.
- `om_sweep_day_idx (docstatus, status, cadence, operational_date)` serves the
  plain Per Hari branch, whose rule is the next-day cutoff on
  `operational_date` rather than `due_at`.

Composite indexes are not expressible as DocType field flags, so this is a
site-level DDL patch. Idempotent; runs in [post_model_sync] after the table
exists.
"""

import frappe

INDEXES = {
	"om_sweep_due_idx": ("docstatus", "status", "cadence", "due_at"),
	"om_sweep_shift_idx": ("opening_shift", "docstatus", "status"),
	"om_sweep_day_idx": ("docstatus", "status", "cadence", "operational_date"),
}


def execute():
	for index_name, columns in INDEXES.items():
		exists = frappe.db.sql(
			"""
			SELECT 1 FROM information_schema.STATISTICS
			WHERE table_schema = DATABASE()
			AND table_name = 'tabPOS Monitoring Report'
			AND index_name = %s
			""",
			(index_name,),
		)
		if not exists:
			frappe.db.sql_ddl(
				f"ALTER TABLE `tabPOS Monitoring Report` ADD INDEX {index_name} ({', '.join(columns)})"
			)
