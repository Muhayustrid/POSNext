# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Items outside the POS Profile's item groups/brands stay out of the POS.

- search_by_barcode applies the same scope as the catalog query;
- saving a POS Profile drops the cached scope right away (Desk edits).

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
