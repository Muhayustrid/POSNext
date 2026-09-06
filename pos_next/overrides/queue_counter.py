# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Keep the queue counter ahead of numbers already printed offline.

Offline tills continue locally from their last known number; when those
invoices sync and submit, this hook raises the company counter so the
next online allocation never reuses a number that already left the
printer. It never lowers the counter.
"""

import frappe
from frappe.utils import nowdate


def bump_queue_counter(doc, method=None):
	number = doc.get("pos_queue_number")
	company = doc.get("company")
	if not number or not company:
		return
	date = doc.get("pos_queue_date") or nowdate()
	name = frappe.db.get_value("POS Queue Counter", {"company": company, "date": date}, "name")
	if name:
		# Raw SQL on purpose: GREATEST can never LOWER the counter, unlike
		# set_value which would blindly overwrite (e.g. a stale offline
		# receipt syncing after newer online allocations).
		frappe.db.sql(
			"UPDATE `tabPOS Queue Counter`"
			" SET current_number = GREATEST(current_number, %(number)s)"
			" WHERE name = %(name)s",
			{"number": number, "name": name},
		)
	else:
		frappe.get_doc(
			{
				"doctype": "POS Queue Counter",
				"company": company,
				"date": date,
				"current_number": number,
			}
		).insert(ignore_permissions=True)
