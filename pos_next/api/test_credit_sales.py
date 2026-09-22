# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api import credit_sales
from pos_next.api.credit_sales import check_credit_sale_enabled
from pos_next.api.settings_resolver import GLOBAL_DOCTYPE


def _builder_with_result(result):
	builder = Mock()
	builder.select.return_value = builder
	builder.where.return_value = builder
	builder.for_update.return_value = builder
	builder.run.return_value = result
	return builder


def _raise_runtime_error(message):
	raise RuntimeError(str(message))


class TestCreditSales(unittest.TestCase):
	@patch("pos_next.api.credit_sales.frappe.throw", side_effect=_raise_runtime_error)
	@patch("pos_next.api.credit_sales.frappe.qb.from_")
	def test_validate_invoice_credit_rejects_mismatched_customer(self, mock_from, _mock_throw):
		mock_from.return_value = _builder_with_result(
			[
				SimpleNamespace(
					name="SRC-INV",
					outstanding_amount=-100,
					customer="Other Customer",
					company="Sonex",
				)
			]
		)

		with self.assertRaisesRegex(RuntimeError, "does not belong to customer Guest"):
			credit_sales._validate_and_lock_invoice_credit("SRC-INV", 50, "Guest", "Sonex")

	@patch("pos_next.api.credit_sales.frappe.throw", side_effect=_raise_runtime_error)
	@patch("pos_next.api.credit_sales.frappe.qb.from_")
	def test_validate_advance_credit_rejects_mismatched_company(self, mock_from, _mock_throw):
		mock_from.return_value = _builder_with_result(
			[
				SimpleNamespace(
					name="PE-0001",
					unallocated_amount=100,
					party="Guest",
					company="Other Company",
					party_type="Customer",
					payment_type="Receive",
				)
			]
		)

		with self.assertRaisesRegex(RuntimeError, "does not belong to company Sonex"):
			credit_sales._validate_and_lock_advance_credit("PE-0001", 50, "Guest", "Sonex")

	@patch("pos_next.api.credit_sales._create_credit_allocation_journal_entry")
	@patch("pos_next.api.credit_sales._validate_and_lock_invoice_credit")
	@patch("pos_next.api.credit_sales.frappe.get_doc")
	def test_redeem_customer_credit_passes_invoice_context_to_validators(
		self,
		mock_get_doc,
		mock_validate_invoice,
		mock_create_je,
	):
		invoice_doc = Mock()
		invoice_doc.docstatus = 1
		invoice_doc.customer = "Guest"
		invoice_doc.company = "Sonex"
		mock_get_doc.return_value = invoice_doc
		mock_create_je.return_value = "ACC-JV-0001"

		result = credit_sales.redeem_customer_credit(
			"ACC-SINV-0001",
			[
				{
					"type": "Invoice",
					"credit_origin": "SRC-INV",
					"credit_to_redeem": 75,
				}
			],
		)

		self.assertEqual(result, ["ACC-JV-0001"])
		mock_validate_invoice.assert_called_once_with("SRC-INV", 75, "Guest", "Sonex")

	@patch("pos_next.api.credit_sales.frappe.throw", side_effect=_raise_runtime_error)
	def test_create_payment_entry_from_advance_rejects_non_customer_receive_entries(self, _mock_throw):
		invoice_doc = Mock()
		invoice_doc.customer = "Guest"
		invoice_doc.company = "Sonex"

		payment_entry = Mock()
		payment_entry.party_type = "Supplier"
		payment_entry.payment_type = "Pay"

		with patch("pos_next.api.credit_sales.frappe.get_doc", return_value=payment_entry):
			with self.assertRaisesRegex(RuntimeError, "is not a valid customer advance"):
				credit_sales._create_payment_entry_from_advance(invoice_doc, "PE-0001", 25)


class TestCheckCreditSaleEnabledGlobalTier(FrappeTestCase):
	"""A profile with no POS Settings row follows the global single's value."""

	PROFILE = "PN-NO-SUCH-PROFILE"

	def setUp(self):
		super().setUp()
		# Raw tabSingles row: None means "never persisted" and must be
		# restored as an absence, not as a value.
		row = frappe.db.sql(
			"select value from `tabSingles` where doctype = %s and field = 'allow_credit_sale'",
			GLOBAL_DOCTYPE,
		)
		self.orig = row[0][0] if row else None

	def tearDown(self):
		if self.orig is None:
			frappe.db.delete(
				"Singles", {"doctype": GLOBAL_DOCTYPE, "field": "allow_credit_sale"}
			)
		else:
			frappe.db.set_single_value("POS Next Global Settings", "allow_credit_sale", self.orig)
		frappe.db.commit()
		super().tearDown()

	def test_follows_the_global_value_both_ways(self):
		for value, expected in ((0, False), (1, True)):
			with self.subTest(allow_credit_sale=value):
				frappe.db.set_single_value("POS Next Global Settings", "allow_credit_sale", value)
				self.assertIs(check_credit_sale_enabled(self.PROFILE), expected)
