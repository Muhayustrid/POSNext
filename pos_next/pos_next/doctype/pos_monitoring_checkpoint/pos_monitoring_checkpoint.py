# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class POSMonitoringCheckpoint(Document):
	def validate(self):
		seen = set()
		for row in self.get("labels") or []:
			if row.label in seen:
				frappe.throw(_("Duplicate label {0} found").format(row.label), frappe.ValidationError)
			seen.add(row.label)
		if cint(self.min_count) < 1:
			frappe.throw(_("Min count must be at least 1"), frappe.ValidationError)
		if cint(self.max_count) < cint(self.min_count):
			frappe.throw(_("Max count cannot be less than min count"), frappe.ValidationError)
		if cint(self.video_max_seconds) < 0:
			frappe.throw(_("Video max seconds cannot be negative"), frappe.ValidationError)
