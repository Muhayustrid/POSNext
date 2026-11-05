# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe

REVIEWER_ROLE = "POSNext Monitoring Reviewer"


def ensure_outlet_monitoring_setup(quiet=False):
	"""Ensure the reviewer role and outlet monitoring configuration exist."""
	if not frappe.db.exists("Role", REVIEWER_ROLE):
		frappe.get_doc({
			"doctype": "Role",
			"role_name": REVIEWER_ROLE,
			"desk_access": 1,
		}).insert(ignore_permissions=True)

	if frappe.db.exists("DocType", "POS Monitoring Settings"):
		s = frappe.get_single("POS Monitoring Settings")
		if not frappe.db.get_single_value("POS Monitoring Settings", "default_pass_label"):
			s.save(ignore_permissions=True)
