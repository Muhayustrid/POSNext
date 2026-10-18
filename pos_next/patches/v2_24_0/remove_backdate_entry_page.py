# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import frappe


def execute():
	"""Delete legacy Desk Page backdate-entry superseded by POS Backdate Entry DocType."""
	frappe.delete_doc("Page", "backdate-entry", force=True, ignore_missing=True)
