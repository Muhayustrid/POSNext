# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Re-seat DocField order after the naming-series fields (v2.14).

Same drift as v2_7_0..v2_11_0: sync_for appends the three new naming-series
fields with the next free idx instead of the JSON's order, so they land past
tab boundaries where nobody looks for them. Reindex every DocField row of
both settings doctypes to the JSON field_order. Idempotent by construction;
the JSON is read from disk at execute() time.
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
		rows = frappe.get_all("DocField", filters={"parent": doctype}, fields=["name", "fieldname"])
		by_fieldname = {r.fieldname: r.name for r in rows}
		for pos, fieldname in enumerate(order, start=1):
			row = by_fieldname.get(fieldname)
			if row:
				frappe.db.set_value("DocField", row, "idx", pos, update_modified=False)
