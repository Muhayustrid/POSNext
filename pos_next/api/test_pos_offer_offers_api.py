# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for offers/promotions API extensions (quota summary, cap, read-only)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pos_next.api.offers import enrich_offers_with_quota
from pos_next.api.promotions import (
	_items_with_names,
	_managed_schemes_for_company,
	_rule_discount_summary,
	_scheme_discount_summary,
	_target_summary,
)


class FakeOffer:
	def __init__(self, name, promotional_scheme=None):
		self.name = name
		self.promotional_scheme = promotional_scheme
		self.max_discount_amount = 0
		self.quota_scope = None
		self.quota_period = None
		self.quota_limit = 0
		self.quota_used = 0
		self.quota_remaining = None


GET_ALL_PATCH = patch("pos_next.api.offers.frappe.get_all")
DB_PATCH = patch("pos_next.api.offers.frappe.db", new_callable=MagicMock)


def offers_get_all_side_effect():
	def get_all(doctype, filters=None, fields=None, **kwargs):
		if doctype == "Promotional Scheme":
			# scheme → offer ownership
			return [SimpleNamespace(name="S-Promo Gula", pos_offer="Promo Gula")]
		if doctype == "POS Offer":
			return [
				SimpleNamespace(
					name="Promo Gula",
					enforce_usage_quota=1,
					quota_scope="Per Company",
					quota_period="Daily",
					global_max_usage=0,
				)
			]
		if doctype == "POS Offer Company":
			return [
				SimpleNamespace(parent="Promo Gula", company="Company A", enabled=1, max_usage=5),
				SimpleNamespace(parent="Promo Gula", company="Company B", enabled=1, max_usage=0),
			]
		raise AssertionError(f"unexpected get_all on {doctype}")

	return get_all


class TestEnrichOffersWithQuota(unittest.TestCase):
	def test_quota_summary_attached(self):
		offer = FakeOffer("PR-A", promotional_scheme="S-Promo Gula")
		with GET_ALL_PATCH as mock_get_all, DB_PATCH as mock_db:
			mock_get_all.side_effect = offers_get_all_side_effect()
			mock_db.count.return_value = 2

			enrich_offers_with_quota([offer], "Company A", date="2026-09-05")

			self.assertEqual("Per Company", offer.quota_scope)
			self.assertEqual("Daily", offer.quota_period)
			self.assertEqual(5, offer.quota_limit)
			self.assertEqual(2, offer.quota_used)
			self.assertEqual(3, offer.quota_remaining)
			self.assertEqual(
				{"pos_offer": "Promo Gula", "company": "Company A", "posting_date": "2026-09-05"},
				mock_db.count.call_args.args[1],
			)

	def test_unmanaged_offer_untouched(self):
		offer = FakeOffer("PR-PLAIN", promotional_scheme="S-Plain")
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.side_effect = offers_get_all_side_effect()
			enrich_offers_with_quota([offer], "Company A")
			self.assertEqual(0, offer.quota_limit)
			self.assertIsNone(offer.quota_remaining)

	def test_get_all_called_with_keyword_filters(self):
		"""Regression: `get_all(dt, {filters}, fields=[...])` (positional filters
		+ keyword fields) crashes on real frappe with "DatabaseQuery.execute()
		got multiple values for argument 'fields'", and get_offers swallowed the
		error into an empty list — no offers ever reached the POS.
		Filters must always be passed as the `filters` keyword."""

		def strict_get_all(doctype, *args, **kwargs):
			# trip on BOTH real-frappe crash shapes: positional filters combined
			# with kwarg fields, and fields passed positionally alongside filters
			if (args and "fields" in kwargs) or len(args) > 1:
				raise TypeError("DatabaseQuery.execute() got multiple values for argument 'fields'")
			return offers_get_all_side_effect()(doctype, *args, **kwargs)

		offer = FakeOffer("PR-A", promotional_scheme="S-Promo Gula")
		with GET_ALL_PATCH as mock_get_all, DB_PATCH as mock_db:
			mock_get_all.side_effect = strict_get_all
			mock_db.count.return_value = 1

			enrich_offers_with_quota([offer], "Company A", date="2026-09-05")

			self.assertEqual("Per Company", offer.quota_scope)
			self.assertEqual(1, offer.quota_used)

	def test_quota_not_enforced_untouched(self):
		offer = FakeOffer("PR-A", promotional_scheme="S-Promo Gula")

		def get_all(doctype, filters=None, **kwargs):
			if doctype == "Promotional Scheme":
				return [SimpleNamespace(name="S-Promo Gula", pos_offer="Promo Gula")]
			if doctype == "POS Offer":
				return [
					SimpleNamespace(
						name="Promo Gula",
						enforce_usage_quota=0,
						quota_scope=None,
						quota_period=None,
						global_max_usage=0,
					)
				]
			return []

		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.side_effect = get_all
			enrich_offers_with_quota([offer], "Company A")
			self.assertEqual(0, offer.quota_limit)


PROMO_GET_ALL_PATCH = patch("pos_next.api.promotions.frappe.get_all")


class TestManagedSchemesForCompany(unittest.TestCase):
	def test_lists_managed_schemes_for_enabled_companies(self):
		with PROMO_GET_ALL_PATCH as mock_get_all:
			def get_all(doctype, filters=None, fields=None, **kwargs):
				if doctype == "POS Offer Company":
					return ["Promo Gula"]
				if doctype == "POS Offer":
					return ["Promo Gula"]
				if doctype == "Promotional Scheme":
					self.assertEqual(["in", ["Promo Gula"]], filters.get("pos_offer"))
					return [SimpleNamespace(name="Promo Gula", disable=0)]
				raise AssertionError(doctype)

			mock_get_all.side_effect = get_all
			result = _managed_schemes_for_company("Company A")
			self.assertEqual(["Promo Gula"], [r.name for r in result])

	def test_no_company_returns_empty(self):
		with PROMO_GET_ALL_PATCH as mock_get_all:
			self.assertEqual([], _managed_schemes_for_company(None))
			mock_get_all.assert_not_called()


class TestPromotionMutationGuards(unittest.TestCase):
	def test_mutating_endpoints_throw(self):
		from pos_next.api import promotions

		for fn, args in [
			(promotions.create_promotion, ({},)),
			(promotions.update_promotion, ("S-1", {})),
			(promotions.toggle_promotion, ("S-1",)),
			(promotions.delete_promotion, ("S-1",)),
		]:
			with self.assertRaises(frappe.PermissionError):
				fn(*args)


class TestPromotionCardHelpers(unittest.TestCase):
	"""_target_summary / _scheme_discount_summary / _rule_discount_summary
	feed the informational promotion cards (what is discounted, by how much)."""

	def test_target_summary_lists_matching_column_only(self):
		doc = frappe._dict(
			apply_on="Item Code",
			items=[frappe._dict(item_code="SKU001"), frappe._dict(item_code="SKU002")],
			item_groups=[frappe._dict(item_group="Minuman")],
			brands=[],
		)
		summary = _target_summary(doc)
		self.assertEqual(["SKU001", "SKU002"], summary["items"])
		self.assertEqual([], summary["item_groups"])
		self.assertEqual([], summary["brands"])
		self.assertEqual("Item Code", summary["apply_on"])

	def test_target_summary_transaction_is_empty(self):
		summary = _target_summary(frappe._dict(apply_on="Transaction", items=[frappe._dict(item_code="X")]))
		self.assertEqual("Transaction", summary["apply_on"])
		self.assertEqual([], summary["items"])

	def test_scheme_discount_summary_percentage(self):
		doc = frappe._dict(
			product_discount_slabs=[],
			price_discount_slabs=[
				frappe._dict(rate_or_discount="Discount Percentage", discount_percentage=50, discount_amount=0)
			],
		)
		self.assertEqual(
			{"kind": "Discount Percentage", "discount_percentage": 50.0, "discount_amount": 0.0},
			_scheme_discount_summary(doc),
		)

	def test_scheme_discount_summary_free_item(self):
		doc = frappe._dict(
			product_discount_slabs=[frappe._dict(free_item="BONUS-1", free_qty=2)],
			price_discount_slabs=[],
		)
		self.assertEqual(
			{"kind": "Free Item", "free_item": "BONUS-1", "free_qty": 2.0},
			_scheme_discount_summary(doc),
		)

	def test_scheme_discount_summary_no_slabs(self):
		doc = frappe._dict(product_discount_slabs=[], price_discount_slabs=[])
		self.assertEqual({"kind": "", "discount_percentage": 0.0, "discount_amount": 0.0}, _scheme_discount_summary(doc))

	def test_rule_discount_summary_amount(self):
		doc = frappe._dict(
			price_or_product_discount="Price",
			rate_or_discount="Discount Amount",
			discount_percentage=0,
			discount_amount=5000,
		)
		self.assertEqual(
			{"kind": "Discount Amount", "discount_percentage": 0.0, "discount_amount": 5000.0},
			_rule_discount_summary(doc),
		)


class TestItemsWithNames(unittest.TestCase):
	"""_items_with_names maps codes to {item_code, item_name} for card display."""

	GET_ALL_PROMO_PATCH = patch("pos_next.api.promotions.frappe.get_all")

	def test_maps_codes_to_names(self):
		with self.GET_ALL_PROMO_PATCH as mock_get_all:
			mock_get_all.return_value = [
				SimpleNamespace(item_code="SKU001", item_name="T-shirt"),
				SimpleNamespace(item_code="SKU002", item_name="Laptop"),
			]
			self.assertEqual(
				[
					{"item_code": "SKU002", "item_name": "Laptop"},
					{"item_code": "SKU001", "item_name": "T-shirt"},
				],
				_items_with_names(["SKU002", "SKU001"]),
			)

	def test_falls_back_to_code_when_name_missing(self):
		with self.GET_ALL_PROMO_PATCH as mock_get_all:
			mock_get_all.return_value = []
			self.assertEqual(
				[{"item_code": "SKU001", "item_name": "SKU001"}],
				_items_with_names(["SKU001"]),
			)

	def test_empty_codes_skip_query(self):
		with self.GET_ALL_PROMO_PATCH as mock_get_all:
			self.assertEqual([], _items_with_names([]))
			mock_get_all.assert_not_called()
