# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate

from pos_next.api.production import create_production, get_production_recipes


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


def _make_test_item(code_suffix="", **extra):
	code = f"PRD-T-{uuid.uuid4().hex[:8]}{code_suffix}"
	doc = {
		"doctype": "Item",
		"item_code": code,
		"item_name": code,
		"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
		"stock_uom": "Nos",
	}
	doc.update(extra)
	frappe.get_doc(doc).insert(ignore_permissions=True)
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

	def _receipt(self, item_code, qty, batch_no=None):
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.company,
				"items": [
					{
						"item_code": item_code,
						"qty": qty,
						"t_warehouse": self.warehouse,
						"use_serial_batch_fields": 1,
						"batch_no": batch_no,
						"basic_rate": 10,
					}
				],
			}
		).insert(ignore_permissions=True)
		se.submit()

	def test_batched_material_lists_mixed_expiry_batches(self):
		self.mat = _make_test_item(has_batch_no=1)
		undated = frappe.get_doc(
			{"doctype": "Batch", "batch_id": f"PRD-B-{uuid.uuid4().hex[:8]}", "item": self.mat}
		).insert(ignore_permissions=True)
		expiry = add_days(getdate(), 30)
		dated = frappe.get_doc(
			{
				"doctype": "Batch",
				"batch_id": f"PRD-B-{uuid.uuid4().hex[:8]}",
				"item": self.mat,
				"expiry_date": expiry,
			}
		).insert(ignore_permissions=True)
		self._receipt(self.mat, 3, batch_no=undated.name)
		self._receipt(self.mat, 5, batch_no=dated.name)
		self._make_recipe([self.company])

		payload = get_production_recipes(self.pos_profile)
		recipe = next(r for r in payload["recipes"] if r["production_item"] == self.fg)
		row = recipe["items"][0]
		self.assertTrue(row["has_batch_no"])
		self.assertEqual(row["available_qty"], 8)
		self.assertEqual(
			[b["batch_no"] for b in row["batches"]], [dated.name, undated.name]
		)
		self.assertEqual(row["batches"][0]["expiry_date"], expiry)


def _seed_stock(item_code, warehouse, qty, batch_no=None):
	se = frappe.new_doc("Stock Entry")
	se.purpose = "Material Receipt"
	se.company = frappe.db.get_value("Warehouse", warehouse, "company")
	se.set_stock_entry_type()
	row = {
		"item_code": item_code,
		"qty": qty,
		"t_warehouse": warehouse,
		"use_serial_batch_fields": 1,
		"basic_rate": 10,
	}
	if batch_no:
		row["batch_no"] = batch_no
	se.append("items", row)
	se.insert()
	se.submit()
	return se.name


class TestCreateProduction(FrappeTestCase):
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
		self.recipe = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"CR {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [{"item_code": self.mat, "qty": 2}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)

	def _bin_qty(self, item_code):
		return flt(
			frappe.db.get_value(
				"Bin", {"item_code": item_code, "warehouse": self.warehouse}, "actual_qty"
			)
		)

	def test_happy_path_moves_stock_and_creates_log(self):
		_seed_stock(self.mat, self.warehouse, 10)
		result = create_production(
			recipe=self.recipe.name,
			qty=3,
			items=json.dumps([{"item_code": self.mat, "qty": 6}]),
			pos_profile=self.pos_profile,
		)
		# material consumed, finished goods produced
		self.assertEqual(self._bin_qty(self.mat), 4)
		self.assertEqual(self._bin_qty(self.fg), 3)

		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		self.assertEqual(se.docstatus, 1)
		self.assertEqual(se.purpose, "Manufacture")
		self.assertIn("POS Production:", se.remarks)
		self.assertTrue(any(d.is_finished_item for d in se.items))

		log = frappe.get_doc("POS Production Log", result["production_log"])
		self.assertEqual(log.docstatus, 1)
		self.assertEqual(log.stock_entry, se.name)
		self.assertEqual(log.recipe, self.recipe.name)
		self.assertEqual(flt(log.qty), 3)
		used = json.loads(log.items_used)
		self.assertEqual(used[0]["item_code"], self.mat)

	def test_insufficient_stock_rejected_before_entry(self):
		_seed_stock(self.mat, self.warehouse, 1)
		with self.assertRaises(ValidationError) as ctx:
			create_production(
				recipe=self.recipe.name,
				qty=1,
				items=json.dumps([{"item_code": self.mat, "qty": 5}]),
				pos_profile=self.pos_profile,
			)
		self.assertIn(self.mat, str(ctx.exception))
		# nothing was created for this recipe (FrappeTestCase rolls back per class,
		# not per test, so a global count would see entries from earlier tests)
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remarks": ["like", f"%{self.recipe.recipe_name}%"]}), 0
		)

	def test_disabled_material_rejected_before_entry(self):
		_seed_stock(self.mat, self.warehouse, 10)
		frappe.db.set_value("Item", self.mat, "disabled", 1)
		with self.assertRaises(ValidationError) as ctx:
			create_production(
				recipe=self.recipe.name,
				qty=1,
				items=json.dumps([{"item_code": self.mat, "qty": 2}]),
				pos_profile=self.pos_profile,
			)
		self.assertIn(self.mat, str(ctx.exception))
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remarks": ["like", f"%{self.recipe.recipe_name}%"]}), 0
		)

	def test_batch_of_other_item_rejected(self):
		other_item = _make_test_item(has_batch_no=1)
		foreign_batch = frappe.get_doc(
			{"doctype": "Batch", "batch_id": f"B-{uuid.uuid4().hex[:8]}", "item": other_item}
		).insert(ignore_permissions=True)
		_seed_stock(self.mat, self.warehouse, 10)
		frappe.db.set_value("Item", self.mat, "has_batch_no", 1)
		with self.assertRaises(ValidationError) as ctx:
			create_production(
				recipe=self.recipe.name,
				qty=1,
				items=json.dumps([{"item_code": self.mat, "qty": 2}]),
				pos_profile=self.pos_profile,
				batches=json.dumps({self.mat: foreign_batch.name}),
			)
		self.assertIn(foreign_batch.name, str(ctx.exception))
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remarks": ["like", f"%{self.recipe.recipe_name}%"]}), 0
		)

	def test_recipe_of_other_company_rejected(self):
		other = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
		if not other:
			self.skipTest("only one company on this site")
		frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"OC {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [{"item_code": self.mat, "qty": 1}],
				"companies": [{"company": other, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(ValidationError):
			create_production(
				recipe=frappe.get_all("POS Production Recipe", limit=1, order_by="creation desc")[0].name,
				qty=1,
				items=json.dumps([{"item_code": self.mat, "qty": 1}]),
				pos_profile=self.pos_profile,
			)

	def test_batch_material_consumes_chosen_batch(self):
		mat_b = _make_test_item(has_batch_no=1)
		batch = frappe.new_doc("Batch")
		batch.batch_id = f"B-{uuid.uuid4().hex[:8]}"
		batch.item = mat_b
		batch.insert(ignore_permissions=True)
		_seed_stock(mat_b, self.warehouse, 4, batch_no=batch.name)
		_seed_stock(self.mat, self.warehouse, 4)

		result = create_production(
			recipe=self.recipe.name,
			qty=2,
			items=json.dumps(
				[
					{"item_code": self.mat, "qty": 2},
					{"item_code": mat_b, "qty": 3},
				]
			),
			pos_profile=self.pos_profile,
			batches=json.dumps({mat_b: batch.name}),
		)
		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		row = next(d for d in se.items if d.item_code == mat_b)
		self.assertTrue(row.serial_and_batch_bundle or row.batch_no)
		self.assertEqual(flt(frappe.db.get_value("Batch", batch.name, "batch_qty")), 1)

	def test_finished_good_with_batch_gets_new_batch(self):
		frappe.db.set_value("Item", self.fg, "has_batch_no", 1)
		_seed_stock(self.mat, self.warehouse, 10)
		result = create_production(
			recipe=self.recipe.name,
			qty=2,
			items=json.dumps([{"item_code": self.mat, "qty": 4}]),
			pos_profile=self.pos_profile,
		)
		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		fg_row = next(d for d in se.items if d.is_finished_item)
		self.assertTrue(fg_row.serial_and_batch_bundle or fg_row.batch_no)
