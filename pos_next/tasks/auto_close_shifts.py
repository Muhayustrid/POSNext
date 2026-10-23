# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Automatic closing of shifts when the shift schedule deadline + grace period passes."""

import datetime

import frappe
from frappe.utils import now_datetime

from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import _submit_closing

GRACE_MINUTES = 30


def _get_expired_shifts():
	"""Find open shifts past schedule deadline + grace period with enforced closing."""
	cutoff = now_datetime() - datetime.timedelta(minutes=GRACE_MINUTES)
	return frappe.db.sql(
		"""
		SELECT os.name
		FROM `tabPOS Opening Shift` os
		WHERE os.docstatus = 1
		  AND os.status = 'Open'
		  AND ifnull(os.pos_closing_shift, '') = ''
		  AND os.pos_schedule_enabled = 1
		  AND os.pos_schedule_enforce_closing = 1
		  AND os.pos_schedule_deadline IS NOT NULL
		  AND os.pos_schedule_deadline < %s
		  AND NOT EXISTS (
		      SELECT 1 FROM `tabPOS Closing Shift` cs
		      WHERE cs.pos_opening_shift = os.name
		        AND cs.docstatus = 1
		  )
		ORDER BY os.pos_schedule_deadline ASC
		""",
		(cutoff,),
		as_dict=True,
	)


def auto_close_expired_shifts():
	"""Close expired shifts with enforced schedule past the grace period."""
	expired_shifts = _get_expired_shifts()
	if not expired_shifts:
		return

	original_user = frappe.session.user
	try:
		frappe.set_user("Administrator")
		for row in expired_shifts:
			try:
				_submit_closing(row.name, auto_balance=True, auto_closed=True)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Auto Close Shift Failed: {row.name}")
	finally:
		frappe.set_user(original_user)
