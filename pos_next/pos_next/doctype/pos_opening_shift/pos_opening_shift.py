# Copyright (c) 2020, Youssef Restom and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, format_datetime


def get_active_profile_shift(pos_profile, exclude=None):
	"""The open shift currently holding ``pos_profile`` (locking read), or None."""
	filters = {
		"pos_profile": pos_profile,
		"docstatus": 1,
		"status": "Open",
		"pos_closing_shift": ["is", "not set"],
	}
	if exclude:
		filters["name"] = ["!=", exclude]
	return frappe.db.get_value(
		"POS Opening Shift",
		filters,
		["name", "user", "period_start_date"],
		as_dict=True,
		order_by="period_start_date asc",
		for_update=True,
	)


def throw_profile_in_use(pos_profile, shift):
	cashier = frappe.db.get_value("User", shift.user, "full_name") or shift.user
	frappe.throw(
		_(
			"POS Profile {0} is still active on shift {1}, opened by {2} since {3}. Only one shift can be open per POS Profile. Ask {2} or a POS Manager to close that shift first."
		).format(
			frappe.bold(pos_profile),
			frappe.bold(shift.name),
			frappe.bold(cashier),
			format_datetime(shift.period_start_date, "dd-MM-yyyy HH:mm"),
		),
		title=_("POS Profile Is Active"),
	)


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
		# may hold it at a time — managers included (they resume the active
		# shift through create_opening_shift instead of opening a second one).
		# create_opening_shift already holds the profile row lock before
		# insert+submit, so this locking read is serialized against concurrent
		# SPA opens; being on the doctype it also covers Desk submits and imports.
		other = get_active_profile_shift(self.pos_profile, exclude=self.name)
		if other:
			throw_profile_in_use(self.pos_profile, other)

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
