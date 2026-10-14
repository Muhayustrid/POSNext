# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from pos_next.overrides.company_code_switch import _SWITCH_FIELDS

GATE_TITLE = _("Code Gate Group")


class POSCodeGateGroup(Document):
	"""Code Gate Group: one pair of gate switches shared by member Companies.

	Saving the group pushes both switches onto every member Company. Membership
	is authoritative in the Members table; the Company's `pos_code_gate_group`
	link is a read-only mirror kept in step by sync_members (a Company save
	rejects a link without a matching table row).
	"""

	def validate(self):
		self.validate_members()

	def on_update(self):
		self.sync_members()

	def on_trash(self):
		# members keep their last synced switches — only the mirror is
		# cleared, so no outlet silently loses its code protection
		linked = frappe.get_all("Company", filters={"pos_code_gate_group": self.name}, pluck="name")
		for name in linked:
			frappe.db.set_value("Company", name, "pos_code_gate_group", "")

	def validate_members(self):
		seen = set()
		for row in self.companies or []:
			if row.company in seen:
				frappe.throw(
					_("Company {0} is listed more than once in Code Gate Group {1}").format(
						row.company, self.group_name
					),
					title=GATE_TITLE,
				)
			seen.add(row.company)

			exists, current_group = frappe.db.get_value(
				"Company", row.company, ("name", "pos_code_gate_group")
			) or (None, None)
			if exists is None:
				frappe.throw(
					_("Company {0} does not exist").format(row.company),
					title=GATE_TITLE,
				)
			if current_group and current_group != self.name:
				frappe.throw(
					_("Company {0} already belongs to Code Gate Group {1}. Remove it there first.").format(
						row.company, current_group
					),
					title=GATE_TITLE,
				)

	def sync_members(self):
		"""Push the group switches onto member Companies; unlink removed ones.

		Removed members keep their last synced switches — only the group link
		is cleared, so an outlet never silently loses its code protection.
		Everything runs in the group save's transaction: any failure aborts
		the whole save, leaving no half-synced members.

		Unlike the Shift Group (which full-saves each POS Profile so profile
		validation runs), members are written with db.set_value: a full Company
		save would run every ERPNext Company validation, and the switches stand
		alone. db.set_value also bypasses the System-Manager guard — safe here
		because the group doctype itself is System-Manager-only, and write
		access to every affected Company is verified up front, before ANY
		write happens.
		"""
		members = {row.company for row in self.companies or []}
		linked = set(frappe.get_all("Company", filters={"pos_code_gate_group": self.name}, pluck="name"))

		affected = sorted(members | linked)
		for name in affected:
			if not frappe.has_permission("Company", "write", doc=name):
				frappe.throw(
					_("Write access to Company {0} is required to save Code Gate Group {1}").format(
						name, self.group_name
					),
					title=GATE_TITLE,
				)

		for name in sorted(linked - members):
			frappe.db.set_value("Company", name, "pos_code_gate_group", "")

		values = {field: self.get(field) for field in _SWITCH_FIELDS}
		for name in sorted(members):
			stored = frappe.db.get_value(
				"Company", name, ("pos_code_gate_group", *_SWITCH_FIELDS), as_dict=True
			)
			if stored.pos_code_gate_group != self.name:
				frappe.db.set_value("Company", name, "pos_code_gate_group", self.name)
			for field in _SWITCH_FIELDS:
				if stored[field] != values[field]:
					frappe.db.set_value("Company", name, field, values[field])
