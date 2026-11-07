# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_time, to_timedelta


def apply_scope_defaults(doc):
	"""Fill only an empty scope (spec S3.3); never rewrite an explicit value.

	JSON carries no Select default so old rows stay distinguishable, but a
	request-built doc (`frappe.get_doc(dict)`) then reads None while the
	Desk form applies new_doc All before save. Tested callers apply this
	before save to mirror the Desk onload rule.
	"""
	if not doc.get("company_scope"):
		doc.set("company_scope", "Selected")
	if not doc.get("profile_scope"):
		doc.set("profile_scope", "Selected" if doc.get("pos_profiles") else "All")
	if not doc.get("shift_type_scope"):
		doc.set("shift_type_scope", "Selected" if doc.get("shift_types") else "All")


class POSMonitoringTemplate(Document):
	def before_insert(self):
		# Same rule via the shared helper; runs before _set_defaults
		# fills new_doc All into None fields.
		apply_scope_defaults(self)

	def before_validate(self):
		# Update path (save on an existing row): same rule, same results.
		apply_scope_defaults(self)

	def validate(self):
		if not self.checkpoints:
			frappe.throw(_("At least one checkpoint is required"), frappe.ValidationError)

		seen = set()
		for row in self.checkpoints:
			if row.checkpoint in seen:
				frappe.throw(
					_("Duplicate checkpoint {0} found").format(row.checkpoint), frappe.ValidationError
				)
			seen.add(row.checkpoint)

		# Negative open_after_minutes is never valid, for any cadence.
		if self.open_after_minutes is not None and cint(self.open_after_minutes) < 0:
			frappe.throw(_("Open after minutes cannot be negative"), frappe.ValidationError)

		# Company scope applies to every cadence; only the profile/shift
		# table checks are skipped for Per Hari.
		if self.company_scope == "Selected" and not self.companies:
			frappe.throw(
				_("Companies table is required when company scope is Selected"), frappe.ValidationError
			)

		if self.cadence == "Per Shift":
			if not cint(self.due_minutes_after_open) or cint(self.due_minutes_after_open) < 1:
				frappe.throw(
					_("Due minutes after open is required for Per Shift cadence"), frappe.ValidationError
				)
			for field, table in (("profile_scope", "pos_profiles"), ("shift_type_scope", "shift_types")):
				if getattr(self, field, None) == "Selected" and not getattr(self, table):
					frappe.throw(
						_("{0} requires at least one row when scope is Selected").format(field),
						frappe.ValidationError,
					)
			if cint(self.open_after_minutes) and cint(self.open_after_minutes) >= cint(
				self.due_minutes_after_open
			):
				frappe.throw(_("Open after minutes must be before due minutes"), frappe.ValidationError)
		elif self.cadence == "Per Hari":
			if not self.due_time:
				frappe.throw(_("Due time is required for Per Hari cadence"), frappe.ValidationError)
			if self.open_time and to_timedelta(get_time(self.open_time)) >= to_timedelta(
				get_time(self.due_time)
			):
				frappe.throw(_("Open time must be before due time"), frappe.ValidationError)
		elif self.cadence == "Manual":
			if not cint(self.submit_within_hours) or cint(self.submit_within_hours) < 1:
				frappe.throw(_("Submit within hours is required for Manual cadence"), frappe.ValidationError)
			for field, table in (("profile_scope", "pos_profiles"), ("shift_type_scope", "shift_types")):
				if getattr(self, field, None) == "Selected" and not getattr(self, table):
					frappe.throw(
						_("{0} requires at least one row when scope is Selected").format(field),
						frappe.ValidationError,
					)
		else:
			frappe.throw(_("Unknown cadence {0}").format(self.cadence), frappe.ValidationError)
