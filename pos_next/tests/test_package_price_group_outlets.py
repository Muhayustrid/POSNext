"""Outlets are companies: Price Group and POS Package outlet rows carry no warehouse, and a
package follows the Price Group that prices its parent item to every outlet of that group.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_package_price_group_outlets
"""

import zlib

import frappe
from frappe.tests import IntegrationTestCase

from pos_next.api.packages import _eligible_package_names
from pos_next.install import ensure_price_group_custom_fields
from pos_next.tests import price_group_helpers as helpers


class TestPackagePriceGroupOutlets(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		ensure_price_group_custom_fields(quiet=True)
		self.home = helpers.make_test_company("pkgo-home")
		# rollback is per class, so each test gets its own outlet that no earlier group claimed
		suffix = f"pkgo{zlib.crc32(self._testMethodName.encode()) % 10**6}"
		self.outlet = helpers.make_test_company(suffix)
		self.profile = helpers.make_test_pos_profile(
			suffix, self.outlet, helpers.make_test_warehouse(suffix, self.outlet)
		)
		self.component = helpers.make_test_item("pkgo-comp")

	def _package(self, name, outlets=()):
		# parent_item is unique per package
		self.parent = helpers.make_test_item(f"pkgo-parent {name}")
		return frappe.get_doc(
			{
				"doctype": "POS Package",
				"package_name": name,
				"company": self.home,
				"currency": helpers.get_default_currency(self.home),
				"parent_item": self.parent,
				"base_price": 10000,
				"is_lifetime": 1,
				"items": [{"item_code": self.component, "qty": 1}],
				"outlets": list(outlets),
			}
		).insert(ignore_permissions=True)

	def _price_group(self, name, **kw):
		return helpers.make_price_group(
			name, items=[{"item_code": self.parent, "rate": 12000}], outlets=[{"company": self.outlet}], **kw
		)

	def test_package_follows_price_group_outlets(self):
		pkg = self._package("_PKGO follows")
		self.assertNotIn(pkg.name, _eligible_package_names(self.profile))
		self._price_group("PG-PKGO-1")
		self.assertIn(pkg.name, _eligible_package_names(self.profile))

	def test_disabled_price_group_grants_nothing(self):
		pkg = self._package("_PKGO disabled pg")
		self._price_group("PG-PKGO-2", enabled=0)
		self.assertNotIn(pkg.name, _eligible_package_names(self.profile))

	def test_disabled_package_outlet_row_blocks_the_price_group(self):
		pkg = self._package("_PKGO blocked", outlets=[{"company": self.outlet, "enabled": 0}])
		self._price_group("PG-PKGO-3")
		self.assertNotIn(pkg.name, _eligible_package_names(self.profile))

	def test_package_outlet_row_needs_only_the_company(self):
		pkg = self._package("_PKGO by company", outlets=[{"company": self.outlet, "enabled": 1}])
		self.assertIn(pkg.name, _eligible_package_names(self.profile))
		self.assertEqual(pkg.outlets[0].pos_profile, self.profile)

	def test_duplicate_company_rows_are_rejected(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Duplicate outlet"):
			self._package(
				"_PKGO dup", outlets=[{"company": self.outlet, "enabled": 1}, {"company": self.outlet}]
			)
		with self.assertRaisesRegex(frappe.ValidationError, "Duplicate outlet"):
			helpers.make_price_group(
				"PG-PKGO-dup",
				items=[{"item_code": self.parent, "rate": 12000}],
				outlets=[{"company": self.outlet}, {"company": self.outlet}],
			)

	def test_patch_collapses_same_company_rows(self):
		from pos_next.patches.v2_35_0.outlets_by_company import execute

		pkg = self._package("_PKGO patch", outlets=[{"company": self.outlet, "enabled": 0}])
		row = frappe.get_doc("POS Package Outlet", pkg.outlets[0].name)
		row.name = None
		row.idx = 2
		row.enabled = 1
		row.db_insert()

		execute()

		rows = frappe.get_all(
			"POS Package Outlet", filters={"parent": pkg.name}, fields=["company", "enabled"]
		)
		self.assertEqual(rows, [{"company": self.outlet, "enabled": 1}])
