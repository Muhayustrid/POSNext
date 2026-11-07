# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


class POSMonitoringLabel(Document):
	def on_trash(self):
		# §3.1: snapshots reference labels via JSON, not link-check; never delete.
		frappe.throw(_("POS Monitoring Label cannot be deleted; disable it instead"), frappe.ValidationError)

	def validate(self):
		if self.is_new() or not cint(self.disabled):
			return
		stored = frappe.db.get_value("POS Monitoring Label", self.name, "disabled")
		if cint(stored):
			return
		enabled = cint(frappe.db.get_single_value("POS Monitoring Settings", "enabled"))
		if not enabled:
			return
		active = frappe.get_all(
			"POS Monitoring Label Item",
			filters={"parenttype": "POS Monitoring Settings", "parentfield": "default_labels"},
			fields=["label"],
		)
		active_names = [r.label for r in active]
		if self.name not in active_names:
			return
		for other in active_names:
			if other == self.name:
				continue
			if not cint(frappe.db.get_value("POS Monitoring Label", other, "disabled")):
				return
		frappe.throw(
			_("Cannot disable the last active default label while Outlet Monitoring is enabled"),
			frappe.ValidationError,
		)
