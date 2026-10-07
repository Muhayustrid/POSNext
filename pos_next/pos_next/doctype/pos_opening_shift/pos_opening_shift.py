# Copyright (c) 2020, Youssef Restom and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from pos_next.utils.authz import is_management_user


class POSOpeningShift(Document):
	def before_insert(self):
		# Freeze the schedule + deadline from the POS Profile at open time;
		# later profile edits must not move an open shift's deadline.
		from pos_next.shift_schedule import apply_schedule_snapshot

		apply_schedule_snapshot(self)

	def autoname(self):
		# set_new_name runs this before meta autoname and only while the name
		# is unset: a configured series (per-profile row, then global) takes
		# over; empty keeps the POSA-OS meta pattern.
		from pos_next.naming_series import apply_naming_series_setting

		apply_naming_series_setting(self)

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
		self.validate_single_open_shift_per_profile()

	def validate_single_open_shift_per_profile(self):
		# One POS Profile is one physical cash drawer: at most one open shift
		# may hold it at a time. create_opening_shift already holds the profile
		# row lock before insert+submit, so this locking read is serialized
		# against concurrent SPA opens; being on the doctype it also covers
		# Desk submits and imports.
		# Managers are exempt by design: "is a manager" is the shared
		# is_management_user() test, and a manager may deliberately open over
		# an in-use profile — e.g. to take the register while the previous
		# shift waits for its close.
		if is_management_user():
			return
		other = frappe.db.get_value(
			"POS Opening Shift",
			{
				"pos_profile": self.pos_profile,
				"docstatus": 1,
				"status": "Open",
				"pos_closing_shift": ["is", "not set"],
				"name": ["!=", self.name],
			},
			["name", "user", "period_start_date"],
			as_dict=True,
			for_update=True,
		)
		if other:
			frappe.throw(
				_("POS Profile {0} is in use by shift {1} ({2}) since {3}. Ask that cashier or a manager to close it first.").format(
					frappe.bold(self.pos_profile),
					frappe.bold(other.name),
					other.user,
					other.period_start_date,
				),
				title=_("Another Shift Is Open"),
			)

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
