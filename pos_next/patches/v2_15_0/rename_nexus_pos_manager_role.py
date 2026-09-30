# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Rename the custom role "Nexus POS Manager" to "POSNext Manager".

Branding consistency with "POSNext Cashier". References are updated first —
``tabHas Role`` child rows (shared by User, Role Profile and Report) and
``tabCustom DocPerm`` — then the Role doc itself is renamed. Idempotent:
fresh installs already get "POSNext Manager" from fixtures, and a site that
was renamed before hits the early return.
"""

import frappe

OLD_ROLE = "Nexus POS Manager"
NEW_ROLE = "POSNext Manager"

# Desk pages whose role lists changed in this overhaul. `migrate` does not
# refresh roles on existing Page docs (only on first insert), so the HQ-native
# roles would never reach sites that already had these pages. Names are the
# Page docs (hyphenated); on-disk folders/files use underscores.
_PAGES_WITH_ROLE_CHANGES = ("backdate-entry", "hq-sales-monitoring", "outlet-targets")


def execute():
	if not frappe.db.exists("Role", OLD_ROLE):
		_sync_page_roles()
		frappe.db.commit()
		return

	_dedupe_and_update_has_role()
	frappe.db.sql("UPDATE `tabCustom DocPerm` SET `role` = %s WHERE `role` = %s", (NEW_ROLE, OLD_ROLE))

	if frappe.db.exists("Role", NEW_ROLE):
		# Migrate imports fixtures before running patches, so a site updating
		# from an older app version may already hold BOTH roles — the fixture
		# created the new one. References now point at the new name; the old
		# shell is empty and can be dropped.
		frappe.delete_doc("Role", OLD_ROLE, force=True, ignore_permissions=True)
	else:
		try:
			frappe.rename_doc("Role", OLD_ROLE, NEW_ROLE, force=True)
		except frappe.DoesNotExistError:
			pass

	# rename_doc moves the row name; keep role_name (the autoname source) in
	# step so role pickers and the role.json fixture show the new branding.
	if frappe.db.exists("Role", NEW_ROLE):
		frappe.db.set_value("Role", NEW_ROLE, "role_name", NEW_ROLE)
	frappe.clear_cache()

	_sync_page_roles()

	frappe.db.commit()  # explicit commit: project patch convention, don't rely on migrate


def _sync_page_roles():
	"""Re-apply each page's role list from its shipped JSON.

	Idempotent: wipes the Page's ``Has Role`` children and re-inserts the
	roles declared in the app's page .json. Covers both the rename and the
	HQ-native additions (Sales Manager / Accounts Manager).
	"""
	for page in _PAGES_WITH_ROLE_CHANGES:
		if not frappe.db.exists("Page", page):
			continue
		meta = frappe.get_doc("Page", page)
		meta.set("roles", [])
		for row in _page_roles_from_file(page):
			meta.append("roles", {"role": row})
		meta.flags.ignore_permissions = True
		meta.save()
	frappe.db.commit()


def _page_roles_from_file(page):
	import json
	import os

	folder = page.replace("-", "_")
	path = os.path.join(
		frappe.get_app_path("pos_next"),
		"pos_next",
		"page",
		folder,
		f"{folder}.json",
	)
	if not os.path.exists(path):
		frappe.logger().warning(f"pos_next patch v2_15_0: page file not found for {page} at {path}")
		return []
	with open(path) as f:
		return [r.get("role") for r in json.load(f).get("roles", []) if r.get("role")]


def _dedupe_and_update_has_role():
	"""Point ``Has Role`` rows at the new name without creating duplicates.

	A parent (User or Role Profile) may already hold a "POSNext Manager" row
	assigned alongside the old one — in that case the old row is deleted
	instead of updated, otherwise the parent would carry the role twice.
	"""
	for row in frappe.get_all(
		"Has Role",
		filters={"role": OLD_ROLE},
		fields=["name", "parent", "parenttype"],
	):
		duplicate = frappe.db.exists(
			"Has Role",
			{"parent": row.parent, "parenttype": row.parenttype, "role": NEW_ROLE},
		)
		if duplicate:
			frappe.db.delete("Has Role", row.name)
		else:
			frappe.db.set_value("Has Role", row.name, "role", NEW_ROLE)
