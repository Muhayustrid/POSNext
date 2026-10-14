# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Zero-total (100% discount) sales: the helpers update_invoice relies on.

- _is_full_discount_row: a rate-0 row is exempt from the SEC-23 offer replay
  only when its explicit discount covers the whole list price (the discount
  code gate authorizes it); a small discount on a rate-0 row stays gated.
- _ensure_zero_total_payment_row: POS Invoice needs a Mode of Payment row, so a
  grand-total-0 sale gets the profile's default mode with amount 0.

Run via pos_next/_pn_run_tests.py pos_next.api.test_zero_total_invoice
"""

import unittest

import frappe

from pos_next.api.invoices import _ensure_zero_total_payment_row, _is_full_discount_row

PRICES = {"AIR600": {"": 5000.0}}


def _row(**kw):
	return frappe._dict({"item_code": "AIR600", "uom": "Nos", "rate": 0, **kw})


class TestFullDiscountRow(unittest.TestCase):
	def test_percentage_100_is_full_discount(self):
		self.assertTrue(_is_full_discount_row(_row(discount_percentage=100), PRICES, 2))

	def test_amount_covering_list_price_is_full_discount(self):
		self.assertTrue(_is_full_discount_row(_row(discount_amount=5000), PRICES, 2))

	def test_small_discount_on_rate_zero_is_not_exempt(self):
		self.assertFalse(_is_full_discount_row(_row(discount_percentage=10), PRICES, 2))
		self.assertFalse(_is_full_discount_row(_row(discount_amount=100), PRICES, 2))

	def test_no_discount_fields_is_not_exempt(self):
		self.assertFalse(_is_full_discount_row(_row(), PRICES, 2))

	def test_manual_rate_edit_is_not_exempt(self):
		row = _row(discount_percentage=100, is_rate_manually_edited=1)
		self.assertFalse(_is_full_discount_row(row, PRICES, 2))

	def test_amount_without_server_price_is_not_exempt(self):
		self.assertFalse(_is_full_discount_row(_row(discount_amount=5000), {}, 2))


class _Doc(frappe._dict):
	def append(self, key, value):
		self.setdefault(key, []).append(frappe._dict(value))


def _profile(*modes):
	return frappe._dict({"payments": [frappe._dict(m) for m in modes]})


class TestZeroTotalPaymentRow(unittest.TestCase):
	def test_adds_default_mode_with_zero_amount(self):
		doc = _Doc(grand_total=0, rounded_total=0, payments=[])
		profile = _profile({"mode_of_payment": "Card"}, {"mode_of_payment": "Cash", "default": 1})
		_ensure_zero_total_payment_row(doc, profile)
		self.assertEqual([(p.mode_of_payment, p.amount) for p in doc.payments], [("Cash", 0)])

	def test_falls_back_to_first_mode(self):
		doc = _Doc(grand_total=0, rounded_total=0, payments=[])
		_ensure_zero_total_payment_row(doc, _profile({"mode_of_payment": "Card"}))
		self.assertEqual(doc.payments[0].mode_of_payment, "Card")

	def test_skips_non_zero_total(self):
		doc = _Doc(grand_total=5000, rounded_total=5000, payments=[])
		_ensure_zero_total_payment_row(doc, _profile({"mode_of_payment": "Cash", "default": 1}))
		self.assertEqual(doc.payments, [])

	def test_keeps_existing_payments(self):
		existing = [frappe._dict(mode_of_payment="Card", amount=0)]
		doc = _Doc(grand_total=0, rounded_total=0, payments=existing)
		_ensure_zero_total_payment_row(doc, _profile({"mode_of_payment": "Cash", "default": 1}))
		self.assertEqual(doc.payments, existing)

	def test_skips_returns(self):
		doc = _Doc(grand_total=0, rounded_total=0, payments=[], is_return=1)
		_ensure_zero_total_payment_row(doc, _profile({"mode_of_payment": "Cash", "default": 1}))
		self.assertEqual(doc.payments, [])
