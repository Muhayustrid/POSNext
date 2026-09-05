# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the POS Offer per-unit percentage discount cap."""

import unittest

import frappe

from pos_next.overrides.pricing_rule import _cap_percentage_discount


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
