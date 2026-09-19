# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Re-seat POS Settings DocField order to match the doctype JSON.

sync_for appends NEW fields with the next free idx instead of the JSON's
order, so fields added to an existing site (print_mode in v2.6, the Purchase
Order section in v2.7) drift to the end of the form — past barcode_tab into
the Barcode tab, where nobody looks for them. Reindex every DocField row to
the JSON definition order. Idempotent by construction: assigning the JSON
order a second time changes nothing.
"""

import json
import os

import frappe


def execute():
	meta_path = os.path.join(
		frappe.get_app_path("pos_next"), "pos_next", "doctype", "pos_settings", "pos_settings.json"
	)
	with open(meta_path) as f:
		order = [fld["fieldname"] for fld in json.load(f)["fields"]]
	rows = frappe.get_all(
		"DocField", filters={"parent": "POS Settings"}, fields=["name", "fieldname"]
	)
	by_fieldname = {r.fieldname: r.name for r in rows}
	for pos, fieldname in enumerate(order, start=1):
		row = by_fieldname.get(fieldname)
		if row:
			frappe.db.set_value("DocField", row, "idx", pos, update_modified=False)
