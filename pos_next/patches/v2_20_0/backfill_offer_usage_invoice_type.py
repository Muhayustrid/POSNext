# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Backfill invoice_type on the offer usage ledgers.

sales_invoice became a Dynamic Link (so POS Invoice mode can record usage);
rows written before that all point at Sales Invoices.
"""

import frappe

def execute():
	for doctype in ("POS Offer Usage", "One Time Customer Offer Usage"):
		frappe.db.sql(
			f"update `tab{doctype}` set invoice_type = 'Sales Invoice' "
			"where ifnull(invoice_type, '') = '' and ifnull(sales_invoice, '') != ''"
		)
