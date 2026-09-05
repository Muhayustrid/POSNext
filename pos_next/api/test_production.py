# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.production import get_production_recipes


class TestProductionDoctypes(FrappeTestCase):
	def test_doctypes_and_key_fields_exist(self):
		for doctype, fields in [
			("POS Production Recipe", ["recipe_name", "production_item", "output_qty", "disabled", "items", "companies"]),
			("POS Production Recipe Item", ["item_code", "qty"]),
			("POS Production Recipe Company", ["company", "enabled"]),
			("POS Production Log", ["recipe", "production_item", "qty", "items_used", "stock_entry", "pos_profile", "company"]),
		]:
			meta = frappe.get_meta(doctype)
			for fieldname in fields:
				self.assertTrue(meta.has_field(fieldname), f"{doctype} missing {fieldname}")

	def test_log_is_submittable(self):
		self.assertTrue(frappe.get_meta("POS Production Log").is_submittable)


def _make_test_item(code_suffix=""):
	code = f"PRD-T-{uuid.uuid4().hex[:8]}{code_suffix}"
	frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": code,
			"item_name": code,
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
			"stock_uom": "Nos",
		}
	).insert(ignore_permissions=True)
	return code


class TestGetProductionRecipes(FrappeTestCase):
	def setUp(self):
		self.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		self.company, self.warehouse = frappe.db.get_value(
			"POS Profile", self.pos_profile, ["company", "warehouse"]
		)
		if not self.warehouse:
			self.warehouse = frappe.db.get_value(
				"Warehouse", {"company": self.company, "is_group": 0, "disabled": 0}, "name"
			)
		self.fg = _make_test_item()
		self.mat = _make_test_item()
		self.other_company = frappe.db.get_value(
			"Company", {"name": ["!=", self.company]}, "name"
		)
		if not self.other_company:
			self.skipTest("only one company on this site")

	def _make_recipe(self, companies):
		doc = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"Recipe {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 5,
				"items": [{"item_code": self.mat, "qty": 2}],
				"companies": [{"company": c, "enabled": 1} for c in companies],
			}
		).insert(ignore_permissions=True)
		return doc.name

	def test_lists_enabled_recipes_for_profile_company(self):
		self._make_recipe([self.company])
		self._make_recipe([self.other_company])
		payload = get_production_recipes(self.pos_profile)
		self.assertEqual(payload["company"], self.company)
		ours = [r for r in payload["recipes"] if r["production_item"] == self.fg]
		self.assertEqual(len(ours), 1)
		recipe = ours[0]
		self.assertEqual(recipe["output_qty"], 5)
		self.assertEqual(len(recipe["items"]), 1)
		row = recipe["items"][0]
		self.assertEqual(row["item_code"], self.mat)
		self.assertEqual(row["qty"], 2)
		self.assertEqual(row["stock_uom"], "Nos")
		self.assertIn("available_qty", row)
		self.assertIn("has_batch_no", row)
		self.assertIn("batches", row)
		self.assertIn("fg_stock", recipe)
		self.assertIn("fg_has_batch_no", recipe)

	def test_disabled_recipe_excluded(self):
		name = self._make_recipe([self.company])
		frappe.db.set_value("POS Production Recipe", name, "disabled", 1)
		payload = get_production_recipes(self.pos_profile)
		self.assertFalse(any(r["name"] == name for r in payload["recipes"]))

	def test_disabled_company_row_excluded(self):
		name = self._make_recipe([self.company, self.other_company])
		# disable the profile-company row; recipe must disappear for this company
		doc = frappe.get_doc("POS Production Recipe", name)
		for row in doc.companies:
			if row.company == self.company:
				row.enabled = 0
		doc.save(ignore_permissions=True)
		payload = get_production_recipes(self.pos_profile)
		self.assertFalse(any(r["name"] == name for r in payload["recipes"]))
