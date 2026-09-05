# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for offers/promotions API extensions (quota summary, cap, read-only)."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pos_next.api.offers import enrich_offers_with_quota
from pos_next.api.promotions import _managed_schemes_for_company


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
