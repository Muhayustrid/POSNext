# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Re-seat DocField order to match the doctype JSON, for both settings doctypes.

sync_for appends NEW fields with the next free idx instead of the JSON's
order, so fields added to an existing site drift to the end of the form, past
barcode_tab into the Barcode tab, where nobody looks for them. v2.11 also
extends the "POS Next Global Settings" single with new fields, which can
drift the same way. Reindex every DocField row of both doctypes to the JSON
field_order. Idempotent by construction: assigning the JSON order a second
time changes nothing.

The JSON is read from disk at execute() time, so fields a later stage adds
to pos_settings.json (or the global settings single) are picked up when
migrate actually runs.
"""

import json
import os

import frappe

DOCTYPES = ("POS Settings", "POS Next Global Settings")


def execute():
	for doctype in DOCTYPES:
		snake = frappe.scrub(doctype)
		meta_path = os.path.join(
			frappe.get_app_path("pos_next"), "pos_next", "doctype", snake, snake + ".json"
		)
		with open(meta_path) as f:
			order = json.load(f)["field_order"]
		rows = frappe.get_all(
			"DocField", filters={"parent": doctype}, fields=["name", "fieldname"]
		)
		by_fieldname = {r.fieldname: r.name for r in rows}
		for pos, fieldname in enumerate(order, start=1):
			row = by_fieldname.get(fieldname)
			if row:
				frappe.db.set_value("DocField", row, "idx", pos, update_modified=False)
