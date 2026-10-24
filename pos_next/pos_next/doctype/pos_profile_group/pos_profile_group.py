# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from pos_next.shift_schedule import GATE_TITLE, _as_time_delta


class POSProfileGroup(Document):
	"""Shift Group: shift slots shared by member POS Profiles.

	Company-neutral: a group may hold profiles from any company (HQ groups a
	parent and its outlet companies in one schedule template). The slots and
	the group-wide warning/enforce settings are read at shift open; nothing is
	pushed onto the member profiles. Membership is
	authoritative in the Members table; the profile's `pos_profile_group`
	link is a read-only mirror kept in step by sync_members (a profile save
	rejects a link without a matching table row).
	"""

	def validate(self):
		self.validate_slots()
		if cint(self.pos_schedule_warning_minutes) < 0:
			frappe.throw(_("Schedule Warning Minutes must be zero or greater."), title=GATE_TITLE)
		self.validate_members()

	def on_update(self):
		self.sync_members()

	def validate_slots(self):
		seen = set()
		for row in self.shifts or []:
			name = (row.shift_name or "").strip()
			if not name:
				frappe.throw(_("Shift Name is required for all slots"), title=GATE_TITLE)
			if name.lower() in seen:
				frappe.throw(
					_("Shift slot {0} is listed more than once in Shift Group {1}").format(
						name, self.group_name
					),
					title=GATE_TITLE,
				)
			seen.add(name.lower())

			start = _as_time_delta(row.start_time)
			end = _as_time_delta(row.end_time)
			if start is None or end is None:
				frappe.throw(
					_("Shift slot {0}: start time and end time must be valid times.").format(name),
					title=GATE_TITLE,
				)
			if start == end:
				frappe.throw(
					_("Shift slot {0}: start time and end time cannot be equal.").format(name),
					title=GATE_TITLE,
				)

	def validate_members(self):
		seen = set()
		for row in self.profiles or []:
			if row.pos_profile in seen:
				frappe.throw(
					_("POS Profile {0} is listed more than once in Shift Group {1}").format(
						row.pos_profile, self.group_name
					),
					title=GATE_TITLE,
				)
			seen.add(row.pos_profile)

			company, current_group = frappe.db.get_value(
				"POS Profile", row.pos_profile, ("company", "pos_profile_group")
			)
			if company is None:
				frappe.throw(
					_("POS Profile {0} does not exist").format(row.pos_profile),
					title=GATE_TITLE,
				)
			if current_group and current_group != self.name:
				frappe.throw(
					_("POS Profile {0} already belongs to Shift Group {1}. Remove it there first.").format(
						row.pos_profile, current_group
					),
					title=GATE_TITLE,
				)

	def sync_members(self):
		"""Mirror membership onto the profiles' `pos_profile_group` link.

		Only the link is written: slots are read from the group at shift open.
		Company User Permissions are honoured: the whole save is aborted when
		the user cannot write one of the removed profiles, before any write.
		"""
		members = {row.pos_profile for row in self.profiles or []}
		linked = set(frappe.get_all("POS Profile", filters={"pos_profile_group": self.name}, pluck="name"))

		removed = linked - members
		# db.set_value below skips permissions, so verify write access up
		# front — before ANY write — or a restricted user could silently
		# unlink profiles of companies they cannot touch.
		for name in sorted(removed):
			if not frappe.has_permission("POS Profile", "write", doc=name):
				frappe.throw(
					_("Write access to POS Profile {0} is required to save Shift Group {1}").format(
						name, self.group_name
					),
					title=GATE_TITLE,
				)

		for name in removed:
			frappe.db.set_value("POS Profile", name, "pos_profile_group", "")

		for name in sorted(members - linked):
			profile = frappe.get_doc("POS Profile", name)
			profile.pos_profile_group = self.name
			# full save so profile validation and on_update events still run;
			# a permission failure here aborts the group save (clear error)
			profile.save()
