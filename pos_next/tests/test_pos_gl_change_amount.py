# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""make_pos_gl_entries must net change in company currency.

With post_change_gl_entries off, change is netted out of the change-account
payment row. The old code subtracted change_amount (transaction currency) from
base_amount (company currency): an INR invoice in a USD company paid 11000 INR
with 1000 INR change posted Cash CREDIT 884.39 instead of DEBIT 105.10.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_pos_gl_change_amount
"""

import unittest
from unittest import mock

import frappe

from pos_next.overrides.sales_invoice import CustomSalesInvoice


def _invoice(**overrides):
	values = {
		"doctype": "Sales Invoice",
		"is_pos": 1,
		"customer": "_PNXT Customer",
		"debit_to": "Debtors - X",
		"company": "_PNXT Co",
		"currency": "INR",
		"conversion_rate": 0.01051,
		"party_account_currency": "USD",
		"account_for_change_amount": "Cash - X",
		"change_amount": 1000,
		"base_change_amount": 10.51,
		"payments": [
			{"mode_of_payment": "Cash", "account": "Cash - X", "amount": 11000, "base_amount": 115.61},
		],
	}
	values.update(overrides)
	return CustomSalesInvoice(values)


def _gl_entries(doc, account_currency="USD"):
	gl = []
	with (
		mock.patch.object(
			CustomSalesInvoice, "company_currency", new_callable=mock.PropertyMock, return_value="USD"
		),
		mock.patch("pos_next.overrides.sales_invoice._get_post_change_gl_entries_setting", return_value=0),
		mock.patch("pos_next.overrides.sales_invoice.get_account_currency", return_value=account_currency),
		mock.patch.object(CustomSalesInvoice, "get_gl_dict", side_effect=lambda d, *a, **k: frappe._dict(d)),
		mock.patch.object(
			CustomSalesInvoice, "get_party_and_party_type_for_pos_gl_entry", return_value=(None, None)
		),
	):
		doc.make_pos_gl_entries(gl)
	return {e.account: e for e in gl}


class TestPosGlChangeAmount(unittest.TestCase):
	def test_foreign_currency_change_nets_in_base_currency(self):
		"""RED: change_amount (INR) was subtracted from base_amount (USD)."""
		gl = _gl_entries(_invoice())
		self.assertAlmostEqual(gl["Cash - X"].debit, 105.10, places=2)
		self.assertAlmostEqual(gl["Debtors - X"].credit, 105.10, places=2)

	def test_account_currency_amount_also_nets_change(self):
		gl = _gl_entries(_invoice(), account_currency="INR")
		self.assertAlmostEqual(gl["Cash - X"].debit_in_account_currency, 10000)

	def test_same_currency_unchanged(self):
		doc = _invoice(
			currency="USD",
			conversion_rate=1,
			change_amount=300,
			base_change_amount=300,
			payments=[{"mode_of_payment": "Cash", "account": "Cash - X", "amount": 400, "base_amount": 400}],
		)
		gl = _gl_entries(doc)
		self.assertAlmostEqual(gl["Cash - X"].debit, 100)
		self.assertAlmostEqual(gl["Debtors - X"].credit, 100)

	def test_stored_payment_row_not_mutated(self):
		doc = _invoice()
		_gl_entries(doc)
		self.assertAlmostEqual(doc.payments[0].base_amount, 115.61)
		self.assertAlmostEqual(doc.payments[0].amount, 11000)
