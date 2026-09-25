# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the POS Offer per-unit percentage discount cap,
plus the COR-BE-12b submit-path error propagation of the Min/Max pass."""

import unittest
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.overrides.pricing_rule import (
	_cap_percentage_discount,
	apply_min_max_price_discounts,
)


def rule(cap, rate_or_discount="Discount Percentage"):
	return frappe._dict(pos_offer_max_discount=cap, rate_or_discount=rate_or_discount)


class TestCapPercentageDiscount(unittest.TestCase):
	def test_cap_binds_converts_to_flat_amount(self):
		item = frappe._dict(price_list_rate=50000, discount_percentage=50, discount_amount=0)
		_cap_percentage_discount(rule(20000), item)
		self.assertEqual(0, item.discount_percentage)
		self.assertEqual(20000, item.discount_amount)

	def test_below_cap_untouched(self):
		item = frappe._dict(price_list_rate=30000, discount_percentage=50, discount_amount=0)
		_cap_percentage_discount(rule(20000), item)
		self.assertEqual(50, item.discount_percentage)
		self.assertEqual(0, item.discount_amount)

	def test_zero_cap_untouched(self):
		item = frappe._dict(price_list_rate=50000, discount_percentage=50, discount_amount=0)
		_cap_percentage_discount(rule(0), item)
		self.assertEqual(50, item.discount_percentage)

	def test_non_percentage_untouched(self):
		item = frappe._dict(price_list_rate=50000, discount_percentage=0, discount_amount=5000)
		_cap_percentage_discount(rule(20000), item)
		self.assertEqual(5000, item.discount_amount)

	def test_zero_base_untouched(self):
		item = frappe._dict(price_list_rate=0, discount_percentage=50, discount_amount=0)
		_cap_percentage_discount(rule(20000), item)
		self.assertEqual(50, item.discount_percentage)


class TestMinMaxErrorOnSubmit(FrappeTestCase):
	"""COR-BE-12b: a Min/Max application failure must not be swallowed on the
	submit path (double/inconsistent prices would post silently). Draft saves
	and the apply_offers preview (no docstatus) keep the soft warning."""

	COLLECTOR = "pos_next.overrides.pricing_rule._collect_min_max_rule_items"

	@staticmethod
	def _doc(docstatus):
		return frappe._dict(docstatus=docstatus, items=[])

	def test_submit_failure_raises(self):
		doc = self._doc(1)
		with mock.patch(self.COLLECTOR, side_effect=RuntimeError("boom")):
			with self.assertRaises(RuntimeError):
				apply_min_max_price_discounts(doc)

	def test_draft_failure_stays_soft(self):
		doc = self._doc(0)
		with mock.patch(self.COLLECTOR, side_effect=RuntimeError("boom")):
			apply_min_max_price_discounts(doc)  # must not raise

	def test_submit_clean_path_passes(self):
		doc = self._doc(1)
		with mock.patch(self.COLLECTOR, return_value=({}, {})):
			apply_min_max_price_discounts(doc)  # legitimate submit keeps working

