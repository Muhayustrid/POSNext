# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _


class POSRejectPurpose(frappe.model.document.Document):
	def validate(self):
		if frappe.db.get_value("POS Reject Purpose", self.name, "is_system"):
			# read_only isn't enforced server-side; the stored value is the truth
			if not self.is_system:
				frappe.throw(_("Is System cannot be cleared on a system purpose"))
			if self.has_value_changed("purpose_name"):
				frappe.throw(_("A system purpose cannot be renamed"))

	def on_trash(self):
		if self.is_system:
			frappe.throw(_("A system purpose cannot be deleted"))
