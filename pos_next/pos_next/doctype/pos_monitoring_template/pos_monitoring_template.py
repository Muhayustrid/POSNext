# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class POSMonitoringTemplate(Document):
	def validate(self):
		if not self.checkpoints:
			frappe.throw(_("At least one checkpoint is required"), frappe.ValidationError)

		seen = set()
		for row in self.checkpoints:
			if row.checkpoint in seen:
				frappe.throw(_("Duplicate checkpoint {0} found").format(row.checkpoint), frappe.ValidationError)
			seen.add(row.checkpoint)

		if self.cadence == "Per Shift":
			if not cint(self.due_minutes_after_open) or cint(self.due_minutes_after_open) < 1:
				frappe.throw(_("Due minutes after open is required for Per Shift cadence"), frappe.ValidationError)
		elif self.cadence == "Per Hari":
			if not self.due_time:
				frappe.throw(_("Due time is required for Per Hari cadence"), frappe.ValidationError)
