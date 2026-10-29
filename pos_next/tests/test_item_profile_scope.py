# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Items outside the POS Profile's item groups/brands stay out of the POS.

- search_by_barcode applies the same scope as the catalog query;
- saving a POS Profile drops the cached scope right away (Desk edits);
- only items with a valid selling Item Price on the profile price list show.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_item_profile_scope
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api import items


class TestItemProfileScope(FrappeTestCase):
	def _check(self, item_group, brand=None, groups=("Products", "Food"), brands=()):
		item = frappe._dict(name="ITEM-1", item_group=item_group, brand=brand)
		profile = frappe._dict(name="Outlet 1")
		with (
			mock.patch.object(items, "_get_pos_profile_allowed_item_groups", return_value=list(groups)),
			mock.patch.object(items, "_get_allowed_profile_brands", return_value=list(brands)),
		):
			items._assert_item_in_profile_scope(item, profile)

	def test_item_in_profile_group_passes(self):
		self._check("Food")

	def test_item_outside_profile_groups_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self._check("Raw Material")

	def test_unrestricted_profile_passes_any_group(self):
		self._check("Raw Material", groups=())

	def test_brand_scope_enforced(self):
		self._check("Food", brand="Roti", brands=("Roti",))
		with self.assertRaises(frappe.ValidationError):
			self._check("Food", brand="Other", brands=("Roti",))

	def test_profile_save_clears_cached_scope(self):
		cache = frappe.cache()
		keys = [
			f"{k}:Outlet 1"
			for k in (
				"pos_item_groups",
				"pos_profile_allowed_item_groups",
				"pos_brands",
				"pos_profile_brands_raw",
			)
		]
		for key in keys:
			cache.set_value(key, ["stale"])
		items.clear_profile_item_caches(frappe._dict(name="Outlet 1"))
		for key in keys:
			self.assertIsNone(cache.get_value(key), key)


class TestPricedItemScope(FrappeTestCase):
	"""Only items with a valid selling Item Price on the profile's price list
	reach the sale catalog / barcode lookup."""

	def setUp(self):
		codes = frappe.get_all(
			"Item", filters={"disabled": 0, "is_sales_item": 1, "has_variants": 0}, pluck="name", limit=2
		)
		if len(codes) < 2:
			self.skipTest("needs two sales items")
		self.priced, self.unpriced = codes
		self.price_list = frappe.get_doc(
			{
				"doctype": "Price List",
				"price_list_name": f"PN Scope {frappe.generate_hash(length=6)}",
				"selling": 1,
				"currency": frappe.db.get_default("currency") or "IDR",
			}
		).insert(ignore_permissions=True)
		self._price(self.priced)

	def _price(self, item_code, valid_upto=None):
		return frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": item_code,
				"price_list": self.price_list.name,
				"price_list_rate": 1000,
				"valid_from": "2020-01-01",
				**({"valid_upto": valid_upto} if valid_upto else {}),
			}
		).insert(ignore_permissions=True)

	def _has(self, item_code):
		return items._has_selling_price(frappe._dict(name=item_code), self.price_list.name)

	def test_item_on_price_list_passes(self):
		self.assertTrue(self._has(self.priced))

	def test_item_missing_from_price_list_refused(self):
		self.assertFalse(self._has(self.unpriced))

	def test_expired_price_does_not_count(self):
		self._price(self.unpriced, valid_upto="2021-01-01")
		self.assertFalse(self._has(self.unpriced))

	def test_profile_without_price_list_is_unrestricted(self):
		self.assertTrue(items._has_selling_price(frappe._dict(name=self.unpriced), None))
