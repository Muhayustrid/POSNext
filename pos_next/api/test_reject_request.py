# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

SCHEMA = {
	"POS Reject Settings": (
		"deadline_days", "production_loss_deadline_days", "late_mode",
		"monthly_limit_amount", "notify_emails",
	),
	"POS Reject Request": (
		"purpose", "pos_profile", "company", "incident_date", "deadline_date", "reason",
		"video_link", "work_order", "is_system_generated", "items", "total_amount",
		"is_late", "over_limit", "status", "decision", "decision_notes", "decided_by",
		"decided_on", "transfer_entry", "issue_entry", "closed_on",
	),
	"POS Reject Request Item": (
		"item_code", "item_name", "qty", "uom", "valuation_rate", "amount",
	),
	"POS Settings": ("reject_warehouse",),
}


class TestRejectSchema(FrappeTestCase):
	def test_doctypes_and_fields_exist(self):
		for doctype, fields in SCHEMA.items():
			meta = frappe.get_meta(doctype)
			for fieldname in fields:
				self.assertTrue(meta.has_field(fieldname), f"{doctype}.{fieldname}")
		self.assertTrue(frappe.db.exists("Role", "POSNext Reject Approver"))
