# Copyright (c) 2021, Youssef Restom and Contributors
# See license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from pos_next.pos_next.doctype.pos_coupon.pos_coupon import (
	_get_customer_coupon_usage_count,
	coupon_usable_at,
)

GET_ALL_PATCH = patch("pos_next.pos_next.doctype.pos_coupon.pos_coupon.frappe.get_all")
CACHED_PATCH = patch("pos_next.pos_next.doctype.pos_coupon.pos_coupon.frappe.get_cached_value")


def make_coupon(**overrides):
	values = {
		"name": "CPN-1",
		"coupon_code": "SAVE10",
		"company": "Company A",
		"company_scope": "Selected Outlets",
		"discount_type": "Percentage",
	}
	values.update(overrides)
	return values


class TestPOSCoupon(unittest.TestCase):
	@patch("pos_next.pos_next.doctype.pos_coupon.pos_coupon.frappe.get_meta")
	@patch("pos_next.pos_next.doctype.pos_coupon.pos_coupon.frappe.db")
	def test_one_use_coupon_counts_sales_invoice_and_pos_invoice(self, mock_db, mock_get_meta):
		def table_exists(doctype):
			return doctype in {"Sales Invoice", "POS Invoice"}

		def count(doctype, filters=None):
			counts = {"Sales Invoice": 1, "POS Invoice": 2}
			return counts[doctype]

		# Explicit MagicMock: patch auto-derives the child mock class from the
		# target attribute, and an AsyncMock here returns a coroutine instead
		# of the value on Python 3.14.
		mock_db.table_exists = MagicMock(side_effect=table_exists)
		mock_db.count = MagicMock(side_effect=count)
		mock_get_meta.return_value = Mock(has_field=Mock(return_value=True))

		used_count = _get_customer_coupon_usage_count("Customer A", "SAVE10")

		self.assertEqual(used_count, 3)
		mock_db.count.assert_any_call(
			"Sales Invoice",
			filters={"customer": "Customer A", "pos_coupon_code": "SAVE10", "docstatus": 1},
		)
		mock_db.count.assert_any_call(
			"POS Invoice",
			filters={"customer": "Customer A", "pos_coupon_code": "SAVE10", "docstatus": 1},
		)

	@patch("pos_next.pos_next.doctype.pos_coupon.pos_coupon.frappe.get_meta")
	@patch("pos_next.pos_next.doctype.pos_coupon.pos_coupon.frappe.db")
	def test_one_use_coupon_skips_doctypes_without_coupon_field(self, mock_db, mock_get_meta):
		mock_db.table_exists = MagicMock(return_value=True)
		mock_db.count = MagicMock(return_value=4)
		mock_get_meta.side_effect = [
			Mock(has_field=Mock(return_value=True)),
			Mock(has_field=Mock(return_value=False)),
		]

		used_count = _get_customer_coupon_usage_count("Customer A", "SAVE10")

		self.assertEqual(used_count, 4)
		mock_db.count.assert_called_once_with(
			"Sales Invoice",
			filters={"customer": "Customer A", "pos_coupon_code": "SAVE10", "docstatus": 1},
		)


class TestCouponUsableAt(unittest.TestCase):
	def test_all_outlets_usable_anywhere(self):
		with GET_ALL_PATCH as mock_get_all:
			self.assertIsNone(coupon_usable_at(make_coupon(company_scope="All Outlets"), "Company B"))
			mock_get_all.assert_not_called()

	def test_selected_outlets_listed_company_usable(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = ["Company B"]
			self.assertIsNone(coupon_usable_at(make_coupon(), "Company B"))

	def test_selected_outlets_unlisted_company_rejected(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = ["Company C"]
			self.assertEqual(
				"Sorry, this coupon is not valid for this company",
				coupon_usable_at(make_coupon(), "Company B"),
			)

	def test_selected_outlets_empty_table_means_issuing_company(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = []
			# empty scope list falls back to the issuing company only
			self.assertIsNone(coupon_usable_at(make_coupon(), "Company A"))
			self.assertEqual(
				"Sorry, this coupon is not valid for this company",
				coupon_usable_at(make_coupon(), "Company B"),
			)

	def test_empty_scope_means_issuing_company_only(self):
		# A row the patch/default missed must fail closed, not become "All Outlets".
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = []
			self.assertIsNone(coupon_usable_at(make_coupon(company_scope=None), "Company A"))
			self.assertEqual(
				"Sorry, this coupon is not valid for this company",
				coupon_usable_at(make_coupon(company_scope=None), "Company B"),
			)

	def test_all_outlets_except_excludes_listed(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = ["Company B"]
			other = make_coupon(company_scope="All Outlets Except")
			self.assertEqual(
				"Sorry, this coupon is not valid for this company",
				coupon_usable_at(other, "Company B"),
			)
			self.assertIsNone(coupon_usable_at(other, "Company C"))

	def test_amount_coupon_currency_mismatch_rejected(self):
		currencies = {"Company A": "USD", "Company B": "IDR"}
		coupon = make_coupon(company_scope="All Outlets", discount_type="Amount")
		with CACHED_PATCH as mock_cached:
			mock_cached.side_effect = lambda dt, name, field: currencies[name]
			self.assertEqual(
				"Coupon SAVE10 is in USD and cannot be used at Company B.",
				coupon_usable_at(coupon, "Company B"),
			)

	def test_amount_coupon_same_currency_other_company_allowed(self):
		coupon = make_coupon(company_scope="All Outlets", discount_type="Amount")
		with CACHED_PATCH as mock_cached:
			mock_cached.return_value = "IDR"
			self.assertIsNone(coupon_usable_at(coupon, "Company B"))

	def test_percentage_coupon_ignores_currency(self):
		with CACHED_PATCH as mock_cached:
			self.assertIsNone(
				coupon_usable_at(make_coupon(company_scope="All Outlets"), "Company B")
			)
			mock_cached.assert_not_called()


class TestScopeExistingCouponsPatch(unittest.TestCase):
	def test_patch_scopes_existing_coupons_idempotent(self):
		import pos_next.patches.v2_20_0.scope_existing_coupons as scope_patch

		scopes = {}
		parents_with_rows = set()
		created = []
		set_calls = []

		def get_all(doctype, fields=None, **kwargs):
			return [
				SimpleNamespace(
					name="CPN-1", company="Company A", company_scope=scopes.get("CPN-1")
				)
			]

		def db_exists(doctype, filters):
			return filters.get("parent") in parents_with_rows

		def set_value(doctype, name, field, value, **kwargs):
			set_calls.append((name, field, value))
			scopes[name] = value

		def get_doc(values):
			parents_with_rows.add(values["parent"])
			created.append(values)
			return Mock()

		with patch.object(scope_patch, "frappe") as mock_frappe:
			mock_frappe.get_all.side_effect = get_all
			mock_frappe.get_doc.side_effect = get_doc
			mock_frappe.db.exists.side_effect = db_exists
			mock_frappe.db.set_value.side_effect = set_value

			scope_patch.execute()
			self.assertEqual("Selected Outlets", scopes["CPN-1"])
			self.assertEqual(1, len(created))
			self.assertEqual("Company A", created[0]["company"])
			self.assertEqual("companies", created[0]["parentfield"])
			self.assertEqual(1, len(set_calls))

			scope_patch.execute()
			self.assertEqual(1, len(created))
			self.assertEqual(1, len(set_calls))
