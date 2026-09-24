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
	row_values = {}
	for fieldname in _ROW_FIELDS:
		if frappe.db.has_column("POS Settings", fieldname):
			row_values[fieldname] = frappe.db.get_value("POS Settings", {}, fieldname)
		else:
			# Sites that never synced while c7a48a9..e2fb015 briefly put the
			# target-basis columns on tabPOS Settings have no such column;
			# there is no stored value to copy.
			row_values[fieldname] = None
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

	if _migrate_allowed_locales(single):
		changed = True

	if changed:
		# Copying the site's current values must not trip the invoice-type
		# switch gate (open shifts/offline pending) — the value is not
		# semantically changing, it is moving house.
		single.flags.ignore_validate = True
		single.save(ignore_permissions=True)


def _migrate_allowed_locales(single):
	"""Copy allowed_locales from the first enabled legacy POS Settings row.

	The allowed_locales field no longer exists on the POS Settings meta (this
	patch runs in [post_model_sync]), so the old child rows are read straight
	from the table instead of through the doctype — get_doc would always
	return an empty list.

	Appends to `single` in place; returns True when rows were appended.
	"""
	if single.get("allowed_locales"):
		return False
	name = frappe.db.get_value("POS Settings", {"enabled": 1}, "name")
	if not name:
		return False
	languages = frappe.db.get_all(
		"POS Allowed Locale",
		filters={"parent": name, "parentfield": "allowed_locales", "parenttype": "POS Settings"},
		pluck="language",
		order_by="idx asc",
	)
	for language in languages:
		single.append("allowed_locales", {"language": language})
	return bool(languages)
