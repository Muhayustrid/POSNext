# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Daily per-outlet queue numbers for receipts.

One counter row per (company, date). Allocation locks the row so two tills
in one outlet serialize; the increment commits in its own transaction so
the number exists the moment it is answered, independent of when the
invoice is saved.
"""

import frappe
from frappe import _
from frappe.utils import nowdate


@frappe.whitelist()
def get_next_queue_number(pos_profile: str) -> dict:
	company = frappe.db.get_value("POS Profile", pos_profile, "company")
	if not company:
		frappe.throw(_("POS Profile {0} not found").format(pos_profile))
	if not frappe.db.get_value("POS Settings", {"pos_profile": pos_profile}, "enable_pos_queue"):
		return {"enabled": False}

	date = nowdate()
	for _attempt in range(3):
		# Lock the row (or confirm its absence) inside this transaction so
		# concurrent tills for the same company serialize here.
		row = frappe.db.get_value(
			"POS Queue Counter",
			{"company": company, "date": date},
			["name", "current_number"],
			as_dict=True,
			for_update=True,
		)
		# next_number is always derived from the fresh locked read, so a
		# retry can never double-increment.
		next_number = int(row.current_number or 0) + 1 if row else 1
		try:
			if row:
				frappe.db.set_value("POS Queue Counter", row.name, "current_number", next_number)
			else:
				frappe.get_doc(
					{
						"doctype": "POS Queue Counter",
						"company": company,
						"date": date,
						"current_number": next_number,
					}
				).insert(ignore_permissions=True)
		except (frappe.exceptions.ValidationError, frappe.exceptions.DuplicateEntryError):
			# Lost the insert race for the day's first number: the doctype's
			# (company, date) validate fired, or the DB's unique index
			# rejected the row (frappe surfaces that from the insert path as
			# DuplicateEntryError / UniqueValidationError). The winner's row
			# is committed by now, so retry reads and locks it instead of
			# re-inserting.
			frappe.db.rollback()
			continue
		# frappe.db.commit() is deliberate (spec design): the queue number is
		# allocated in its own transaction so it survives independently of the
		# invoice save, and the commit releases the row lock.
		frappe.db.commit()
		return {
			"enabled": True,
			"company": company,
			"date": date,
			"queue_number": next_number,
		}

	frappe.throw(_("Could not allocate a queue number for {0} on {1}").format(company, date))
