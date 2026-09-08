# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from pos_next.shift_schedule import GATE_TITLE, SCHEDULE_FIELDS, validate_schedule_values


class POSProfileGroup(Document):
	"""Shift Group: one set of shift hours shared by member POS Profiles.

	Company-neutral: a group may hold profiles from any company (HQ groups a
	parent and its outlet companies in one schedule template). Saving the
	group syncs the hours onto every member profile. Membership is
	authoritative in the Members table; the profile's `pos_profile_group`
	link is a read-only mirror kept in step by sync_members (a profile save
	rejects a link without a matching table row).
	"""

	def validate(self):
		validate_schedule_values(self, label=_("Shift Group {0}").format(self.group_name))
		self.validate_members()

	def on_update(self):
		self.sync_members()

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
		"""Push the group schedule onto member profiles; unlink removed ones.

		Removed members keep their last synced schedule — only the group link
		is cleared, so an outlet never silently loses its hours. Everything
		runs in the group save's transaction: any failure aborts the whole
		save, leaving no half-synced members. Open shifts are unaffected —
		their schedule was snapshotted at open time and only applies to the
		next session.

		Company User Permissions are honoured: the whole save is aborted when
		the user cannot write one of the affected profiles (including a
		removed one), and only before any write happens.
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

		values = {field: self.get(field) for field in SCHEDULE_FIELDS}
		for name in sorted(members):
			stored = frappe.db.get_value(
				"POS Profile", name, ("pos_profile_group", *SCHEDULE_FIELDS), as_dict=True
			)
			if stored.pos_profile_group == self.name and all(
				stored[field] == values[field] for field in SCHEDULE_FIELDS
			):
				continue
			profile = frappe.get_doc("POS Profile", name)
			profile.update(values)
			profile.pos_profile_group = self.name
			# full save so profile validation and on_update events still run;
			# a permission failure here aborts the group save (clear error)
			profile.save()
