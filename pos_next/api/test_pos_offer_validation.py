# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the rebuilt POS Offer form validation.

Mocked-frappe style (same as test_discount_restriction.py) — run via
pos_next/_pn_run_tests.py pos_next.api.test_pos_offer_validation
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from pos_next.pos_next.doctype.pos_offer.pos_offer import validate_offer

DB_PATCH = patch(
	"pos_next.pos_next.doctype.pos_offer.pos_offer.frappe.db", new_callable=MagicMock
)


def make_offer(**kwargs):
	values = dict(
		name="Promo Gula",
		title="Promo Gula",
		valid_from="2026-09-01",
		valid_to="2026-09-30",
		apply_on="Item Code",
		offer_type="Discount Percentage",
		discount_percentage=10,
		max_discount_amount=0,
		discount_amount=0,
		free_item=None,
		free_qty=1,
		min_qty=0,
		min_amt=0,
		enabled=1,
		enforce_usage_quota=0,
		quota_scope="Global",
		quota_period="Campaign Total",
		global_max_usage=0,
	)
	values.update(kwargs)
	offer = frappe._dict(values)
	offer.targets = kwargs.get("targets", [frappe._dict(item_code="GULA-1")])
	offer.companies = kwargs.get(
		"companies", [frappe._dict(company="Company A", enabled=1, max_usage=0)]
	)
	return offer


class TestValidOfferPasses(unittest.TestCase):
	def test_minimal_offer_passes(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			validate_offer(make_offer())  # must not raise

	def test_transaction_offer_needs_no_targets_and_clears_cap(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			offer = make_offer(apply_on="Transaction", targets=[], max_discount_amount=5000)
			validate_offer(offer)
			self.assertEqual(offer.max_discount_amount, 0)

	def test_scheme_owned_by_self_passes(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = True
			mock_db.get_value.return_value = "Promo Gula"  # scheme's owner == this offer
			validate_offer(make_offer())


class TestWindowAndCompanies(unittest.TestCase):
	def test_inverted_dates_throw(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(valid_from="2026-10-01", valid_to="2026-09-01"))

	def test_no_company_rows_throw(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(companies=[]))

	def test_duplicate_company_rows_throw(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			offer = make_offer(
				companies=[
					frappe._dict(company="Company A", enabled=1, max_usage=0),
					frappe._dict(company="Company A", enabled=1, max_usage=5),
				]
			)
			with self.assertRaises(frappe.ValidationError):
				validate_offer(offer)

	def test_negative_per_company_usage_throw(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			offer = make_offer(
				companies=[frappe._dict(company="Company A", enabled=1, max_usage=-1)]
			)
			with self.assertRaises(frappe.ValidationError):
				validate_offer(offer)

	def test_negative_global_usage_throw(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(enforce_usage_quota=1, global_max_usage=-3))


class TestRewardValidation(unittest.TestCase):
	def test_percentage_out_of_range_throws(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			for bad in (0, 101, -5):
				with self.assertRaises(frappe.ValidationError):
					validate_offer(make_offer(discount_percentage=bad))

	def test_discount_amount_must_be_positive(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(offer_type="Discount Amount", discount_amount=0))

	def test_free_item_requires_item(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(offer_type="Free Item", free_item=None))

	def test_free_item_requires_qty(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(offer_type="Free Item", free_item="ITEM-1", free_qty=0))

	def test_negative_cap_throws(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(max_discount_amount=-1000))

	def test_negative_min_qty_or_amt_throws(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(min_qty=-1))


class TestTargetsAndSchemeCollision(unittest.TestCase):
	def test_missing_targets_throw_for_item_code(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(targets=[]))

	def test_target_row_missing_column_throws(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			offer = make_offer(targets=[frappe._dict(item_code=None)])
			with self.assertRaises(frappe.ValidationError):
				validate_offer(offer)

	def test_scheme_title_collision_with_other_owner_throws(self):
		with DB_PATCH as mock_db:
			mock_db.exists.return_value = True  # a scheme named like the title exists
			mock_db.get_value.return_value = "Some Other Offer"
			with self.assertRaises(frappe.ValidationError):
				validate_offer(make_offer(title="Existing Scheme"))
