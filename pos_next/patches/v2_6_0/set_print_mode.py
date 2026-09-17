# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Resolve print_mode on every POS Settings row from each profile's old flags.

Print behavior used to be split across two booleans: POS Settings.silent_print
and the print_receipt_on_order_complete custom field on POS Profile. Both now
collapse into POS Settings.print_mode (Off/Manual/Auto).

Per POS Profile: Auto if either old flag was set, else Manual. (Off is a new,
deliberate choice — never inferred from an old flag.) Existing rows are updated
in place; profiles without a row get an enabled one, so the mode the UI shows
matches what the profile actually did.

Runs in [post_model_sync]: POS Settings.print_mode must exist. Idempotent: on
re-run the old flags resolve to the same mode again.
"""

import frappe
from frappe.utils import cint


def execute():
	# print_receipt_on_order_complete is a custom field: it does not exist on
	# sites that never created it, so guard the column read with meta.
	has_custom_flag = bool(
		frappe.get_meta("POS Profile").get_field("print_receipt_on_order_complete")
	)

	for profile in frappe.get_all("POS Profile", pluck="name"):
		silent_print = frappe.db.get_value("POS Settings", {"pos_profile": profile}, "silent_print")
		auto_on_order = 0
		if has_custom_flag:
			auto_on_order = frappe.db.get_value(
				"POS Profile", profile, "print_receipt_on_order_complete"
			)

		print_mode = "Auto" if (cint(silent_print) or cint(auto_on_order)) else "Manual"

		name = frappe.db.get_value("POS Settings", {"pos_profile": profile}, "name")
		if name:
			frappe.db.set_value("POS Settings", name, "print_mode", print_mode)
		else:
			frappe.get_doc(
				{
					"doctype": "POS Settings",
					"pos_profile": profile,
					"enabled": 1,
					"print_mode": print_mode,
				}
			).insert(ignore_permissions=True)
