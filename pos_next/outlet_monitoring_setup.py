# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe

REVIEWER_ROLE = "POSNext Monitoring Reviewer"

DEFAULT_LABELS = (
	("Sesuai SOP", "Green"),
	("Tidak Sesuai SOP", "Red"),
	("Tidak Berlaku", "Gray"),
)


def _find_label_case_insensitive(label_name):
	for name in frappe.get_all("POS Monitoring Label", pluck="name"):
		if name.strip().lower() == label_name.strip().lower():
			return name
	return None


def ensure_default_labels() -> list:
	"""Create missing default labels (case-insensitive) and fill empty default_labels."""
	existing = {}
	for row in frappe.get_all("POS Monitoring Label", fields=["name", "label_name"]):
		key = (row.label_name or row.name).strip().lower()
		if key and key not in existing:
			existing[key] = row.name
	names = []
	for label_name, color in DEFAULT_LABELS:
		found = existing.get(label_name.strip().lower())
		if not found:
			doc = frappe.get_doc({
				"doctype": "POS Monitoring Label",
				"label_name": label_name,
				"color": color,
			})
			doc.insert(ignore_permissions=True)
			found = doc.name
			existing[label_name.strip().lower()] = found
		names.append(found)

	s = frappe.get_single("POS Monitoring Settings")
	if not s.get("default_labels"):
		s.set("default_labels", [{"label": name} for name in names])
		s.save(ignore_permissions=True)
	return names


def ensure_outlet_monitoring_setup(quiet=False):
	"""Ensure the reviewer role and outlet monitoring configuration exist."""
	if not frappe.db.exists("Role", REVIEWER_ROLE):
		frappe.get_doc({
			"doctype": "Role",
			"role_name": REVIEWER_ROLE,
			"desk_access": 1,
		}).insert(ignore_permissions=True)

	if frappe.db.exists("DocType", "POS Monitoring Settings"):
		ensure_default_labels()
