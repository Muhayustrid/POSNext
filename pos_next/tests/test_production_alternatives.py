# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from pos_next.api.production import (
	finish_production,
	get_production_recipes,
	start_production,
)
from pos_next.services.production import (
	_bom_matches,
	_stock_qty,
	get_recipe_bom,
)


def _make_item(code_suffix="", **extra):
	code = f"PRD-ALT-{uuid.uuid4().hex[:8]}{code_suffix}"
	doc = {
		"doctype": "Item",
		"item_code": code,
		"item_name": code,
		"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
		"stock_uom": "Nos",
		"allow_alternative_item": 1,
	}
	doc.update(extra)
	item = frappe.get_doc(doc).insert(ignore_permissions=True)
	if item.name != code:
		frappe.rename_doc("Item", item.name, code, force=True)
	return code


def _receipt(company, warehouse, item_code, qty, rate=10):
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"company": company,
			"items": [
				{
					"item_code": item_code,
					"qty": qty,
					"t_warehouse": warehouse,
					"use_serial_batch_fields": 1,
					"basic_rate": rate,
				}
			],
		}
	).insert(ignore_permissions=True)
	se.submit()
	return se


class TestProductionAlternatives(FrappeTestCase):
	def setUp(self):
		super().setUp()
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
		self.fg = _make_item("-FG")
		self.mat = _make_item("-MAT")

	def _make_recipe(self, mat=None, output_qty=5, mat_qty=2):
		doc = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"Recipe {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": output_qty,
				"items": [{"item_code": mat or self.mat, "qty": mat_qty}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		return doc

	def _link_alternative(self, item_code, alt_code, two_way=0):
		alt_doc = frappe.get_doc(
			{
				"doctype": "Item Alternative",
				"item_code": item_code,
				"alternative_item_code": alt_code,
				"two_way": two_way,
			}
		).insert(ignore_permissions=True)
		return alt_doc

	def test_original_sufficient_no_swap(self):
		"""When original material stock is sufficient, alternative is not used."""
		alt = _make_item("-ALT")
		self._link_alternative(self.mat, alt)
		_receipt(self.company, self.warehouse, self.mat, 10, rate=15)
		_receipt(self.company, self.warehouse, alt, 20, rate=25)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		started = start_production(recipe.name, 5, self.pos_profile)
		finished = finish_production(started["work_order"], 5)

		se = frappe.get_doc("Stock Entry", finished["stock_entry"])
		raw_rows = [d for d in se.items if d.s_warehouse and not d.is_finished_item]
		self.assertEqual(len(raw_rows), 1)
		self.assertEqual(raw_rows[0].item_code, self.mat)
		self.assertFalse(raw_rows[0].original_item)
		self.assertEqual(flt(_stock_qty(self.mat, self.warehouse)), 8.0)
		self.assertEqual(flt(_stock_qty(alt, self.warehouse)), 20.0)

	def test_original_short_alternative_sufficient_swap(self):
		"""When original material is short, first eligible alternative covering full qty is used."""
		alt = _make_item("-ALT")
		self._link_alternative(self.mat, alt)
		# mat has 0 stock; alt has 10 stock
		_receipt(self.company, self.warehouse, alt, 10, rate=25)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		started = start_production(recipe.name, 5, self.pos_profile)
		self.assertEqual(started["qty"], 5.0)

		# Work Order must have allow_alternative_item = 1
		wo = frappe.get_doc("Work Order", started["work_order"])
		self.assertEqual(wo.allow_alternative_item, 1)

		finished = finish_production(started["work_order"], 5)
		se = frappe.get_doc("Stock Entry", finished["stock_entry"])
		raw_rows = [d for d in se.items if d.s_warehouse and not d.is_finished_item]
		self.assertEqual(len(raw_rows), 1)
		row = raw_rows[0]
		self.assertEqual(row.item_code, alt)
		self.assertEqual(row.original_item, self.mat)
		self.assertEqual(flt(row.qty), 2.0)
		self.assertEqual(flt(row.basic_rate), 25.0)

		# Alternative stock reduced, original untouched
		self.assertEqual(flt(_stock_qty(alt, self.warehouse)), 8.0)
		self.assertEqual(flt(_stock_qty(self.mat, self.warehouse)), 0.0)

		# Production Log snapshot records actual consumed item
		log = frappe.get_doc("POS Production Log", finished["production_log"])
		items_used = json.loads(log.items_used)
		self.assertEqual(items_used[0]["item_code"], alt)

	def test_both_short_throws_shortage_error(self):
		"""When both original and alternative are short, throw shortage error for original."""
		alt = _make_item("-ALT")
		self._link_alternative(self.mat, alt)
		# Needs 2 units of mat (output_qty=5, producing 5). mat has 0; alt has 1 (insufficient).
		_receipt(self.company, self.warehouse, alt, 1, rate=25)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		with self.assertRaises(ValidationError) as ctx:
			start_production(recipe.name, 5, self.pos_profile)
		self.assertIn(f"Material {self.mat} is short", str(ctx.exception))

	def test_different_stock_uom_alternative_ignored(self):
		"""Alternative with different stock_uom is not eligible."""
		alt = _make_item("-ALT-KG", stock_uom="Kg")
		self._link_alternative(self.mat, alt)
		_receipt(self.company, self.warehouse, alt, 50, rate=10)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		with self.assertRaises(ValidationError) as ctx:
			start_production(recipe.name, 5, self.pos_profile)
		self.assertIn(f"Material {self.mat} is short", str(ctx.exception))

	def test_different_stock_uom_converted_via_shared_uom(self):
		"""Alternative in another stock UOM is used when both items share a UOM: 1 Box = 10 Nos-of-mat
		and 1 Box = 2 Nos-of-alt, so 2 mat = 0.4 alt."""
		alt = _make_item("-ALT-CONV", stock_uom="Kg")
		for code, cf in ((self.mat, 10), (alt, 2)):
			item = frappe.get_doc("Item", code)
			item.append("uoms", {"uom": "Box", "conversion_factor": cf})
			item.save(ignore_permissions=True)
		self._link_alternative(self.mat, alt)
		_receipt(self.company, self.warehouse, alt, 1, rate=50)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		payload = get_production_recipes(self.pos_profile)
		mat_row = next(r for r in payload["recipes"] if r["name"] == recipe.name)["items"][0]
		self.assertEqual(mat_row["alternative_item_code"], alt)
		# 1 Kg of alt covers 5 units of mat
		self.assertAlmostEqual(mat_row["available_qty"], 5.0)

		started = start_production(recipe.name, 5, self.pos_profile)
		finished = finish_production(started["work_order"], 5)
		row = next(
			d
			for d in frappe.get_doc("Stock Entry", finished["stock_entry"]).items
			if d.s_warehouse and not d.is_finished_item
		)
		self.assertEqual((row.item_code, row.original_item, row.uom), (alt, self.mat, "Kg"))
		self.assertAlmostEqual(flt(row.transfer_qty), 0.4)
		self.assertAlmostEqual(flt(_stock_qty(alt, self.warehouse)), 0.6)

	def test_converted_alternative_skipped_when_bom_qty_validated(self):
		"""ERPNext rejects a converted qty when components are validated against the BOM, so the
		alternative is not offered and the material stays short."""
		alt = _make_item("-ALT-CONV", stock_uom="Kg")
		for code, cf in ((self.mat, 10), (alt, 2)):
			item = frappe.get_doc("Item", code)
			item.append("uoms", {"uom": "Box", "conversion_factor": cf})
			item.save(ignore_permissions=True)
		self._link_alternative(self.mat, alt)
		_receipt(self.company, self.warehouse, alt, 1, rate=50)
		before = frappe.db.get_single_value(
			"Manufacturing Settings", "validate_components_quantities_per_bom"
		)
		frappe.db.set_single_value("Manufacturing Settings", "validate_components_quantities_per_bom", 1)
		self.addCleanup(
			frappe.db.set_single_value,
			"Manufacturing Settings",
			"validate_components_quantities_per_bom",
			before,
		)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		with self.assertRaises(ValidationError) as ctx:
			start_production(recipe.name, 5, self.pos_profile)
		self.assertIn(f"Material {self.mat} is short", str(ctx.exception))

	def test_two_way_reverse_lookup(self):
		"""Two-way alternative where alternative_item_code=original is eligible when two_way=1."""
		alt = _make_item("-ALT")
		# Reversed: item_code is alt, alternative_item_code is mat, two_way is 1
		self._link_alternative(alt, self.mat, two_way=1)
		_receipt(self.company, self.warehouse, alt, 10, rate=25)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		started = start_production(recipe.name, 5, self.pos_profile)
		finished = finish_production(started["work_order"], 5)

		se = frappe.get_doc("Stock Entry", finished["stock_entry"])
		raw_rows = [d for d in se.items if d.s_warehouse and not d.is_finished_item]
		self.assertEqual(raw_rows[0].item_code, alt)
		self.assertEqual(raw_rows[0].original_item, self.mat)

	def test_two_way_zero_reverse_lookup_ignored(self):
		"""Reversed alternative with two_way=0 is not eligible."""
		alt = _make_item("-ALT")
		self._link_alternative(alt, self.mat, two_way=0)
		_receipt(self.company, self.warehouse, alt, 10, rate=25)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		with self.assertRaises(ValidationError) as ctx:
			start_production(recipe.name, 5, self.pos_profile)
		self.assertIn(f"Material {self.mat} is short", str(ctx.exception))

	def test_bom_without_alternative_flag_regenerated(self):
		"""BOM with allow_alternative_item=0 is recognized as mismatch and regenerated."""
		recipe = self._make_recipe()
		bom_no = get_recipe_bom(recipe, self.company)
		bom = frappe.get_doc("BOM", bom_no)

		# If BOM has allow_alternative_item = 0, _bom_matches must return False
		frappe.db.set_value("BOM", bom.name, "allow_alternative_item", 0)
		self.assertFalse(_bom_matches(bom.name, recipe, self.company))

		# get_recipe_bom heals it: deactivates old BOM and creates new one
		new_bom_no = get_recipe_bom(recipe, self.company)
		self.assertNotEqual(new_bom_no, bom.name)
		new_bom = frappe.get_doc("BOM", new_bom_no)
		self.assertEqual(new_bom.allow_alternative_item, 1)
		self.assertEqual(new_bom.items[0].allow_alternative_item, 1)
		old_bom = frappe.get_doc("BOM", bom.name)
		self.assertEqual(old_bom.is_active, 0)

	def test_get_recipes_reports_alternative_keys(self):
		"""get_recipes reports alternative_item_code/name and available_qty when alternative has more stock."""
		alt = _make_item("-ALT")
		self._link_alternative(self.mat, alt)
		_receipt(self.company, self.warehouse, alt, 10, rate=25)

		recipe = self._make_recipe(output_qty=5, mat_qty=2)
		payload = get_production_recipes(self.pos_profile)
		ours = [r for r in payload["recipes"] if r["name"] == recipe.name]
		self.assertEqual(len(ours), 1)
		mat_row = ours[0]["items"][0]
		self.assertEqual(mat_row["item_code"], self.mat)
		self.assertEqual(mat_row["available_qty"], 10.0)
		self.assertEqual(mat_row["alternative_item_code"], alt)
		self.assertEqual(mat_row["alternative_item_name"], alt)

		# When original stock is sufficient, alternative keys are None
		_receipt(self.company, self.warehouse, self.mat, 20, rate=15)
		payload2 = get_production_recipes(self.pos_profile)
		ours2 = [r for r in payload2["recipes"] if r["name"] == recipe.name]
		mat_row2 = ours2[0]["items"][0]
		self.assertEqual(mat_row2["available_qty"], 20.0)
		self.assertIsNone(mat_row2.get("alternative_item_code"))
		self.assertIsNone(mat_row2.get("alternative_item_name"))

	def test_batched_material_keeps_batch_flow_no_alternative(self):
		"""Batched materials never pick alternatives."""
		batched_mat = _make_item("-BATCH-MAT", has_batch_no=1)
		alt = _make_item("-ALT")
		self._link_alternative(batched_mat, alt)
		_receipt(self.company, self.warehouse, alt, 10, rate=25)

		recipe = self._make_recipe(mat=batched_mat, output_qty=5, mat_qty=2)
		with self.assertRaises(ValidationError) as ctx:
			start_production(recipe.name, 5, self.pos_profile)
		self.assertIn(f"Material {batched_mat} is short", str(ctx.exception))
