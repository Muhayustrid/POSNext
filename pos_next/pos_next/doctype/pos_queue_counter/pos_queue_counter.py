# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

INDEX_NAME = "pos_queue_counter_company_date_unique"


class POSQueueCounter(Document):
	# begin: auto-generated types
	from frappe.types import DF

	if frappe.__version__ >= "15":
		company: DF.Link
		date: DF.Date | None
		current_number: DF.Int
	# end: auto-generated types

	def validate(self):
		duplicate = frappe.db.exists(
			"POS Queue Counter",
			{"company": self.company, "date": self.date, "name": ("!=", self.name)},
		)
		if duplicate:
			frappe.throw(
				_("A queue counter for {0} on {1} already exists").format(
					frappe.bold(self.company), frappe.bold(self.date)
				)
			)


def on_doctype_update():
	# Fresh installs never run patches (set_all_patches_as_completed), so the
	# v2_4_0 patch alone cannot guarantee the constraint. Enforce it on every
	# doctype sync: collapse any race-created duplicates first, then add the
	# unique key if missing. Both steps no-op when it is in place.
	frappe.db.sql(
		"""
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
	exists = frappe.db.sql(
		"""
		SELECT 1 FROM information_schema.STATISTICS
		WHERE table_schema = DATABASE()
		AND table_name = 'tabPOS Queue Counter'
		AND index_name = %s
		""",
		INDEX_NAME,
	)
	if not exists:
		frappe.db.sql_ddl(
			f"ALTER TABLE `tabPOS Queue Counter` ADD UNIQUE KEY {INDEX_NAME} (company, `date`)"
		)
