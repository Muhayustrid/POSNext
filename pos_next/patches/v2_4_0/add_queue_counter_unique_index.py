# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Add a UNIQUE index on POS Queue Counter (company, date).

Allocation uniqueness is the product guarantee: one counter row per
(company, date) backs every receipt number for the day. The doctype's
(company, date) validate alone has a race — two tills allocating at the same
instant on the day's first sale can both pass validate and both insert,
leaving two counter rows that duplicate every subsequent number until
midnight. The DB constraint makes the loser's insert fail; the API's retry
loop then reads and locks the winner's row.

Runs in [post_model_sync]: the table must exist.
"""

import frappe

INDEX_NAME = "pos_queue_counter_company_date_unique"


def execute():
	# Collapse any duplicate rows a past race created, keeping the highest
	# current_number per (company, date) so the day's count is not lost
	# (name breaks ties deterministically).
	frappe.db.sql(
		f"""
		DELETE t1 FROM `tabPOS Queue Counter` t1
		JOIN `tabPOS Queue Counter` t2
			ON t1.company = t2.company
			AND t1.`date` = t2.`date`
			AND (
				t1.current_number < t2.current_number
				OR (t1.current_number = t2.current_number AND t1.name < t2.name)
			)
		"""
	)
	# frappe.db.add_index cannot create a UNIQUE index, so DDL it directly;
	# the guard makes the patch safe to re-run.
	exists = frappe.db.sql(
		f"""
		SELECT 1 FROM information_schema.STATISTICS
		WHERE table_schema = DATABASE()
		AND table_name = 'tabPOS Queue Counter'
		AND index_name = '{INDEX_NAME}'
		"""
	)
	if not exists:
		frappe.db.sql_ddl(
			f"ALTER TABLE `tabPOS Queue Counter` ADD UNIQUE KEY {INDEX_NAME} (company, `date`)"
		)
