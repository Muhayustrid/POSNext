# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Keep the queue counter ahead of numbers already printed offline.

Offline tills continue locally from their last known number; when those
invoices sync and submit, this hook raises the company counter so the
next online allocation never reuses a number that already left the
printer. It never lowers the counter.
"""

import frappe
from frappe.utils import cint, nowdate

# ponytail: flat daily clamp instead of per-number allocation records — a real
# allocation ledger would need a new table; raise this only if a real outlet
# ever legitimately prints past it in one day.
MAX_QUEUE_NUMBER_PER_DAY = 10000


def bump_queue_counter(doc, method=None):
	if doc.get("is_consolidated"):
		return
	number = doc.get("pos_queue_number")
	company = doc.get("company")
	if not number or not company:
		return
	number = cint(number)
	# SEC-20: pos_queue_number rides on the client payload, so a forged huge
	# number must not poison the shared counter. Real offline tills only ever
	# advance a little past the counter; anything beyond the daily clamp is
	# treated as tampering and ignored.
	if number < 1 or number > MAX_QUEUE_NUMBER_PER_DAY:
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
