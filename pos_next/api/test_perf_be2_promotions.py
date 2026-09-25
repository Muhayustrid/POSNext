# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-16 acceptance tests: get_promotions must resolve pricing-rule counts,
campaign caps and scheme/policy child rows in bulk (one sweep + bulk child
fetches), never frappe.db.count + frappe.get_doc per scheme / per pricing rule.
Output must stay byte-identical to the per-scheme implementation.

Run inside the container (serial only):

    ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_perf_be2_promotions
"""

import unittest
from unittest import mock

import frappe
from frappe.utils import add_days, nowdate

from pos_next.api.promotions import get_promotions
from pos_next.api.test_packages import COMPANY, _ensure_item

SCHEME_A = "_PNXT_PERF16_SCHEME_A"
SCHEME_B = "_PNXT_PERF16_SCHEME_B"
RULE_C_TITLE = "_PNXT_PERF16_RULE_C"
# Dedicated item: schemes auto-create live pricing rules, and pointing them at
# a package fixture item would collide with invoice pricing in other tests.
ITEM = "_PNXT_PERF16_ITEM"
ITEM_NAME = "PNXT PERF16 Item"
# pos_offer campaign fixture: the sync engine (POS Offer.on_update) creates and
# manages the scheme named after the offer and stamps every rule of it with the
# offer's per-unit cap, so exactly one cap row exists -> old (get_value LIMIT 1)
# and new (first-row get_all) implementations cannot disagree on row order.
OFFER = "_PNXT_PERF16_OFFER"
CAP = 15000.0


def _ensure_pos_offer():
	if not frappe.db.exists("POS Offer", OFFER):
		frappe.get_doc(
			{
				"doctype": "POS Offer",
				"title": OFFER,
				"enabled": 1,
				"apply_on": "Item Code",
				"offer_type": "Discount Percentage",
				"discount_percentage": 10,
				"max_discount_amount": CAP,
				"valid_from": nowdate(),
				"valid_to": add_days(nowdate(), 30),
				"companies": [{"company": COMPANY, "enabled": 1}],
				"targets": [{"item_code": ITEM}],
			}
		).insert(ignore_permissions=True)
	else:
		# Offer insert is what creates/stamps the managed scheme; a leftover
		# offer from an earlier run only needs its cap re-asserted.
		frappe.db.set_value("Pricing Rule", {"promotional_scheme": OFFER}, "pos_offer_max_discount", CAP)


def _ensure_schemes():
	_ensure_item(ITEM, ITEM_NAME, is_stock_item=True)
	_ensure_pos_offer()

	if not frappe.db.exists("Promotional Scheme", SCHEME_A):
		frappe.get_doc(
			{
				"doctype": "Promotional Scheme",
				"name": SCHEME_A,
				"apply_on": "Item Code",
				"company": COMPANY,
				"selling": 1,
				"disable": 0,
				"items": [{"item_code": ITEM, "uom": "Nos"}],
				"price_discount_slabs": [
					{
						"rule_description": "perf16 a1",
						"min_qty": 1,
						"rate_or_discount": "Discount Percentage",
						"discount_percentage": 10,
					},
					{
						"rule_description": "perf16 a2",
						"min_qty": 5,
						"rate_or_discount": "Discount Percentage",
						"discount_percentage": 20,
					},
				],
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Promotional Scheme", SCHEME_B):
		frappe.get_doc(
			{
				"doctype": "Promotional Scheme",
				"name": SCHEME_B,
				"apply_on": "Item Code",
				"company": COMPANY,
				"selling": 1,
				"disable": 0,
				"items": [{"item_code": ITEM, "uom": "Nos"}],
				"product_discount_slabs": [
					{
						"rule_description": "perf16 b",
						"free_item": ITEM,
						"free_qty": 2,
					}
				],
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("Pricing Rule", {"title": RULE_C_TITLE}):
		frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": RULE_C_TITLE,
				"apply_on": "Item Code",
				"price_or_product_discount": "Price",
				"currency": frappe.db.get_value("Company", COMPANY, "default_currency"),
				"rate_or_discount": "Discount Percentage",
				"discount_percentage": 5,
				"selling": 1,
				"items": [{"item_code": ITEM, "uom": "Nos"}],
			}
		).insert(ignore_permissions=True)


class TestGetPromotionsIsBulk(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		_ensure_schemes()
		frappe.db.commit()

	def _find(self, promotions, name):
		for promotion in promotions:
			if promotion["name"] == name or promotion.get("title") == name:
				return promotion
		self.fail(f"{name} not returned by get_promotions")

	def test_output_matches_per_scheme_semantics(self):
		promotions = get_promotions()

		scheme_a = self._find(promotions, SCHEME_A)
		self.assertEqual(scheme_a["source"], "Promotional Scheme")
		# erpnext auto-creates pricing rules on scheme insert; the endpoint must
		# report exactly what the database holds
		self.assertEqual(
			scheme_a["pricing_rules_count"],
			frappe.db.count("Pricing Rule", {"promotional_scheme": SCHEME_A}),
		)
		self.assertEqual(scheme_a["price_slabs"], 2)
		self.assertEqual(scheme_a["product_slabs"], 0)
		self.assertEqual(scheme_a["items_count"], 1)
		self.assertEqual(
			scheme_a["discount"],
			{"kind": "Discount Percentage", "discount_percentage": 10.0, "discount_amount": 0.0},
		)
		self.assertEqual(
			scheme_a["targets"],
			{
				"apply_on": "Item Code",
				"items": [{"item_code": ITEM, "item_name": ITEM_NAME}],
				"item_groups": [],
				"brands": [],
			},
		)

		scheme_b = self._find(promotions, SCHEME_B)
		self.assertEqual(
			scheme_b["pricing_rules_count"],
			frappe.db.count("Pricing Rule", {"promotional_scheme": SCHEME_B}),
		)
		self.assertEqual(scheme_b["price_slabs"], 0)
		self.assertEqual(scheme_b["product_slabs"], 1)
		self.assertEqual(
			scheme_b["discount"], {"kind": "Free Item", "free_item": ITEM, "free_qty": 2.0}
		)

		rule_c = self._find(promotions, RULE_C_TITLE)
		self.assertEqual(rule_c["source"], "Pricing Rule")
		self.assertEqual(rule_c["pricing_rules_count"], 1)
		self.assertEqual(rule_c["price_slabs"], 1)
		self.assertEqual(rule_c["product_slabs"], 0)
		self.assertEqual(rule_c["items_count"], 1)
		self.assertEqual(
			rule_c["discount"],
			{"kind": "Discount Percentage", "discount_percentage": 5.0, "discount_amount": 0.0},
		)

	def test_pos_offer_max_discount_cap_is_reported(self):
		promotions = get_promotions()

		managed = self._find(promotions, OFFER)
		self.assertEqual(managed["source"], "Promotional Scheme")
		self.assertTrue(managed["pos_offer"])
		# same value the old per-scheme frappe.db.get_value path reported
		self.assertEqual(managed["max_discount"], CAP)
		self.assertEqual(
			managed["pricing_rules_count"],
			frappe.db.count("Pricing Rule", {"promotional_scheme": OFFER}),
		)

		# schemes without a campaign keep the old shape: no max_discount key
		scheme_a = self._find(promotions, SCHEME_A)
		self.assertNotIn("max_discount", scheme_a)

	def test_no_per_scheme_count_or_doc_calls(self):
		count_calls = []
		doc_calls = []
		original_count = frappe.db.count
		original_get_doc = frappe.get_doc

		def counting_count(*args, **kwargs):
			count_calls.append(args)
			return original_count(*args, **kwargs)

		def counting_get_doc(*args, **kwargs):
			doc_calls.append(args)
			return original_get_doc(*args, **kwargs)

		with (
			mock.patch("frappe.db.count", side_effect=counting_count),
			mock.patch("frappe.get_doc", side_effect=counting_get_doc),
		):
			promotions = get_promotions()

		self.assertEqual(count_calls, [])
		self.assertEqual(doc_calls, [])

		scheme_a = self._find(promotions, SCHEME_A)
		self.assertEqual(
			scheme_a["pricing_rules_count"],
			frappe.db.count("Pricing Rule", {"promotional_scheme": SCHEME_A}),
		)
		self.assertEqual(scheme_a["price_slabs"], 2)
		rule_c = self._find(promotions, RULE_C_TITLE)
		self.assertEqual(rule_c["items_count"], 1)
