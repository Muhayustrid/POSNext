# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Seed the new global Automatic Stock Sync switch ON.

A Check added to an existing Single gets no tabSingles row, so it would read
as 0 despite default "1". Only seeds when the row is missing, so a site that
already turned it off keeps it off.
"""

import frappe

def execute():
	if frappe.db.sql(
		"select 1 from tabSingles where doctype=%s and field=%s",
		("POS Next Global Settings", "enable_automatic_stock_sync"),
	):
		return
	frappe.db.set_single_value("POS Next Global Settings", "enable_automatic_stock_sync", 1)
