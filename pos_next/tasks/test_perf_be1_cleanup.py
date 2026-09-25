# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-18: expired-promotion cleanup must flip the matched rows with one
bulk ``UPDATE ... IN (names)`` instead of one set_value (and one round trip)
per rule — and never execute the UPDATE for an empty result set.

Run via pos_next/_pn_run_tests.py pos_next.tasks.test_perf_be1_cleanup
"""

import unittest
from unittest import mock

import frappe

from pos_next.tasks.cleanup_expired_promotions import (
	disable_expired_pricing_rules,
	disable_expired_promotional_schemes,
)

EXPIRED_TITLE = "_PERF18 expired rule"
ACTIVE_TITLE = "_PERF18 active rule"


def _norm(query):
	return " ".join(str(query).split())


class TestDisableExpiredPricingRules(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.names = []
		company = frappe.db.get_value("Company", {}, "name")
		currency = frappe.db.get_value("Company", company, "default_currency")
		item = frappe.db.get_value("Item", {}, "name") or "X"
		for title, valid_upto in ((EXPIRED_TITLE, "2020-01-01"), (ACTIVE_TITLE, "2999-01-01")):
			doc = frappe.get_doc(
				{
					"doctype": "Pricing Rule",
					"title": title,
					"apply_on": "Item Code",
					"price_or_product_discount": "Price",
					"currency": currency,
					"selling": 1,
					"disable": 0,
					"valid_from": "2019-01-01",
					"valid_upto": valid_upto,
					"rate_or_discount": "Rate",
					"rate": 0,
					"items": [{"item_code": item}],
				}
			)
			doc.insert(ignore_permissions=True)
			cls.names.append(doc.name)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for name in cls.names:
			try:
				frappe.delete_doc("Pricing Rule", name, force=1)
			except Exception:
				pass
		frappe.db.commit()

	def test_bulk_update_disables_expired_only_and_runs_once(self):
		# any other expired rule that may already sit on this shared dev site
		# is flipped by the same bulk UPDATE — count it into the expectation
		expected_expired = frappe.db.count(
			"Pricing Rule", {"disable": 0, "valid_upto": ["<", frappe.utils.nowdate()]}
		)
		set_value_calls = []
		updates = []
		real_sql = frappe.db.sql
		real_set_value = frappe.db.set_value

		def spy_set_value(*args, **kwargs):
			set_value_calls.append(args)
			return real_set_value(*args, **kwargs)

		def spy_sql(query, *args, **kwargs):
			text = _norm(query)
			if text.upper().startswith("UPDATE"):
				updates.append(text)
			return real_sql(query, *args, **kwargs)

		with (
			mock.patch("frappe.db.set_value", side_effect=spy_set_value),
			mock.patch("frappe.db.sql", side_effect=spy_sql),
		):
			result = disable_expired_pricing_rules()

		self.assertEqual(result["success"], True)
		self.assertEqual(result["disabled_count"], expected_expired)
		# the expired fixture is flipped, the active fixture untouched
		self.assertEqual(frappe.db.get_value("Pricing Rule", self.names[0], "disable"), 1)
		self.assertEqual(frappe.db.get_value("Pricing Rule", self.names[1], "disable"), 0)
		# one bulk UPDATE carrying the name list, no per-rule set_value
		self.assertEqual(len(updates), 1)
		self.assertIn("IN", updates[0])
		self.assertEqual(set_value_calls, [])

	def test_no_expired_rules_means_no_update(self):
		updates = []
		real_sql = frappe.db.sql

		def fake_sql(query, *args, **kwargs):
			text = _norm(query)
			if text.upper().startswith("SELECT") and "tabPricing Rule" in text:
				return []
			updates.append(text)
			return real_sql(query, *args, **kwargs)

		with mock.patch("frappe.db.sql", side_effect=fake_sql):
			result = disable_expired_pricing_rules()

		self.assertEqual(result["disabled_count"], 0)
		# empty result set: no UPDATE (an `IN ()` would not even parse)
		self.assertEqual(updates, [])


class TestDisableExpiredPromotionalSchemes(unittest.TestCase):
	def test_single_bulk_update_without_set_value(self):
		fake_schemes = [
			frappe._dict(
				name="_PERF18 scheme", selling_or_buying="Selling", valid_upto="2020-01-01"
			)
		]
		updates = []
		real_sql = frappe.db.sql

		def spy_sql(query, *args, **kwargs):
			text = _norm(query)
			if text.upper().startswith("UPDATE"):
				updates.append(text)
				return len(fake_schemes)
			if "tabPromotional Scheme" in text:
				return fake_schemes
			return real_sql(query, *args, **kwargs)

		with (
			mock.patch("frappe.db.sql", side_effect=spy_sql),
			mock.patch("frappe.db.set_value") as set_value,
		):
			result = disable_expired_promotional_schemes()

		self.assertEqual(result["success"], True)
		self.assertEqual(result["disabled_count"], 1)
		# one bulk UPDATE carrying the name list, no per-rule set_value
		self.assertEqual(set_value.call_count, 0)
		self.assertEqual(len(updates), 1)
		self.assertIn("IN", updates[0])
