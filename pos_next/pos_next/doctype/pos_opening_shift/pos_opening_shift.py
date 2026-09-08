# Copyright (c) 2020, Youssef Restom and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class POSOpeningShift(Document):
	def before_insert(self):
		# Freeze the schedule + deadline from the POS Profile at open time;
		# later profile edits must not move an open shift's deadline.
		from pos_next.shift_schedule import apply_schedule_snapshot

		apply_schedule_snapshot(self)

	def validate(self):
		# Any save of an existing shift (incl. the closing link) discards
		# client edits to the snapshot — the deadline can only be changed by
		# an admin via frappe.db.set_value.
		from pos_next.shift_schedule import freeze_schedule_snapshot

		freeze_schedule_snapshot(self)
		self.validate_pos_profile_and_cashier()
		self.set_status()

	def before_submit(self):
		# Re-resolve authoritatively at the moment the shift goes live: a
		# draft held past the enforced hours cannot be submitted, and any
		# tampered schedule fields are overwritten from the profile.
		from pos_next.shift_schedule import apply_schedule_snapshot

		apply_schedule_snapshot(self)

	def validate_pos_profile_and_cashier(self):
		if self.company != frappe.db.get_value("POS Profile", self.pos_profile, "company"):
			frappe.throw(_(f"POS Profile {self.pos_profile} does not belongs to company {self.company}"))

		if not cint(frappe.db.get_value("User", self.user, "enabled")):
			frappe.throw(_(f"User {self.user} has been disabled. Please select valid user/cashier"))

	def on_submit(self):
		self.set_status(update=True)

	def set_status(self, update=False):
		"""Set the status of the opening shift"""
		if self.docstatus == 0:
			status = "Draft"
		elif self.docstatus == 1:
			if self.pos_closing_shift:
				status = "Closed"
			else:
				status = "Open"
		else:
			status = "Cancelled"

		if update:
			frappe.db.set_value("POS Opening Shift", self.name, "status", status)
		else:
			self.status = status
