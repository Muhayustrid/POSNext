# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-17: get_payment_reconciliation_details must fetch invoice / Payment
Entry docs straight with get_cached_doc (DoesNotExistError = skip the row)
instead of probing frappe.db.exists for the same name first — one query per
row, not two. Both paths keep their behavior: an existing doc is processed, a
missing one is skipped silently.

Run via pos_next/_pn_run_tests.py
pos_next.pos_next.doctype.pos_closing_shift.test_perf_be1_closing
"""

import unittest
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.tests.price_group_helpers import (
	get_default_company,
	get_default_customer,
	make_test_item,
)

MISSING_POS_INVOICE = "POS-PERF17-MISSING"
MISSING_PAYMENT_ENTRY = "ACC-PE-PERF17-MISSING"
_PROBED_DOCTYPES = ("Sales Invoice", "POS Invoice", "Payment Entry")


class TestReconciliationDocFetch(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		cls.currency = frappe.db.get_value("Company", cls.company, "default_currency")
		cls.customer = get_default_customer(cls.company)
		item = make_test_item("PERF17", "Nos", is_stock_item=0)
		inv = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": cls.company,
				"customer": cls.customer,
				"is_pos": 1,
				"items": [{"item_code": item, "qty": 1, "rate": 10}],
				"payments": [{"mode_of_payment": "Cash", "amount": 10}],
			}
		)
		inv.insert(ignore_permissions=True)
		cls.invoice = inv.name

	def _closing_doc(self, transactions, payments=None):
		return frappe.get_doc(
			{
				"doctype": "POS Closing Shift",
				"company": self.company,
				"pos_transactions": transactions,
				"pos_payments": payments or [],
			}
		)

	def _probe_exists(self):
		real = frappe.db.exists
		probes = []

		def spy(doctype, *args, **kwargs):
			if doctype in _PROBED_DOCTYPES:
				probes.append(doctype)
			return real(doctype, *args, **kwargs)

		return probes, mock.patch("frappe.db.exists", side_effect=spy)

	def test_existing_invoice_processed_without_exists_probe(self):
		doc = self._closing_doc([{"sales_invoice": self.invoice}])
		probes, patcher = self._probe_exists()
		with patcher:
			html = doc.get_payment_reconciliation_details()

		# the existing doc path still processes the invoice's money
		self.assertIsInstance(html, str)
		self.assertIn(self.currency, html)
		# and it was fetched without an exists probe (PERF-17)
		self.assertEqual(probes, [])

	def test_missing_docs_skipped_without_error(self):
		doc = self._closing_doc(
			[{"pos_invoice": MISSING_POS_INVOICE}, {"sales_invoice": MISSING_POS_INVOICE}],
			payments=[{"payment_entry": MISSING_PAYMENT_ENTRY}],
		)
		probes, patcher = self._probe_exists()
		with patcher:
			html = doc.get_payment_reconciliation_details()

		# missing docs keep the old behavior: the row is skipped, no raise
		self.assertIsInstance(html, str)
		self.assertEqual(probes, [])

	def test_mixed_rows_process_existing_and_skip_missing(self):
		doc = self._closing_doc(
			[{"sales_invoice": self.invoice}, {"pos_invoice": MISSING_POS_INVOICE}],
			payments=[{"payment_entry": MISSING_PAYMENT_ENTRY}],
		)
		html = doc.get_payment_reconciliation_details()

		self.assertIsInstance(html, str)
		self.assertIn(self.currency, html)
