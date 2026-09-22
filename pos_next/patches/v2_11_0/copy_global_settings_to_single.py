# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Move the global settings from POS Settings rows to the new single.

POS Settings used to force-sync invoice_type, the two target bases and
allow_negative_stock across every row, and allowed_locales was read from the
first enabled row. Those five values now live on the "POS Next Global
Settings" single; this patch copies the site-wide values over once.

Idempotent: values already persisted on the single are never overwritten, so
re-runs and second migrate passes keep any newer manual choice.

Runs in [post_model_sync]: the single doctype must exist.
"""

import frappe

GLOBAL_DOCTYPE = "POS Next Global Settings"

_ROW_FIELDS = ("invoice_type", "monthly_target_basis", "overall_target_basis", "allow_negative_stock")


def execute():
	# filters={} reads any row — the old controller kept every row in sync,
	# so all rows agreed.
	row_values = {f: frappe.db.get_value("POS Settings", {}, f) for f in _ROW_FIELDS}
	if row_values["allow_negative_stock"] is None:
		# no POS Settings row at all: fall back to the core toggle the old
		# per-profile bridge used to keep in sync
		row_values["allow_negative_stock"] = frappe.db.get_single_value(
			"Stock Settings", "allow_negative_stock"
		)

	single = frappe.get_doc(GLOBAL_DOCTYPE)
	# get_single_value returns '' / 0 (never None) for fields that were never
	# saved, so "already persisted" must come from tabSingles itself.
	persisted_fields = {
		row.field
		for row in frappe.db.sql(
			"SELECT field FROM tabSingles WHERE doctype = %s", GLOBAL_DOCTYPE, as_dict=True
		)
	}

	changed = False
	for fieldname, value in row_values.items():
		if fieldname not in persisted_fields and value is not None:
			single.set(fieldname, value)
			changed = True

	if not single.get("allowed_locales"):
		name = frappe.db.get_value("POS Settings", {"enabled": 1}, "name")
		if name:
			doc = frappe.get_doc("POS Settings", name)
			for row in doc.get("allowed_locales") or []:
				single.append("allowed_locales", {"language": row.language})
				changed = True

	if changed:
		# Copying the site's current values must not trip the invoice-type
		# switch gate (open shifts/offline pending) — the value is not
		# semantically changing, it is moving house.
		single.flags.ignore_validate = True
		single.save(ignore_permissions=True)
