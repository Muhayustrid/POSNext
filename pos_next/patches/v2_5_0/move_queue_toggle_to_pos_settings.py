# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Move the POS queue toggle from Company to POS Settings.

"Enable POS Queue Number" lived as a custom field on Company, so operators
had to hunt through the Company master to turn receipts' queue numbers on.
The toggle now lives on POS Settings (per POS Profile), where every other
outlet-facing POS knob already lives. The counter itself stays per
(company, date); only the gate moves.

This patch carries the old Company value over to every POS Settings row of
that company's profiles, then drops the Company custom fields. Idempotent:
safe on fresh installs (no Company column → copy step skips) and on re-run.

Runs in [post_model_sync]: POS Settings.enable_pos_queue must exist.
"""

import frappe

COMPANY_CUSTOM_FIELDS = [
	"Company-pos_settings_section",
	"Company-enable_pos_queue",
]


def execute():
	if frappe.db.has_column("Company", "enable_pos_queue"):
		for company in frappe.get_all("Company", filters={"enable_pos_queue": 1}, pluck="name"):
			for profile in frappe.get_all("POS Profile", filters={"company": company}, pluck="name"):
				name = frappe.db.get_value("POS Settings", {"pos_profile": profile}, "name")
				if name:
					frappe.db.set_value("POS Settings", name, "enable_pos_queue", 1)
				else:
					frappe.get_doc(
						{
							"doctype": "POS Settings",
							"pos_profile": profile,
							"enabled": 1,
							"enable_pos_queue": 1,
						}
					).insert(ignore_permissions=True)

	for field_name in COMPANY_CUSTOM_FIELDS:
		if frappe.db.exists("Custom Field", field_name):
			frappe.delete_doc("Custom Field", field_name, force=True, ignore_permissions=True)
