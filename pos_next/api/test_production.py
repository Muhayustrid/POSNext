# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate

from pos_next.api.production import (
	_uom_whole_field,
	close_production,
	create_production,
	cancel_production,
	finish_production,
	get_active_productions,
	get_finish_context,
	get_production_history,
	get_production_recipes,
	start_production,
)


def _set_invoice_type(value):
	"""Pin the site's ambient invoice_type so submit-path tests are hermetic.

	Written at the DB level past the switch guard (shared dev site holds real
	open shifts); the baseline is restored in tearDownClass."""
	frappe.db.set_single_value("POS Next Global Settings", "invoice_type", value)
	try:
		del frappe.local._pos_next_invoice_doctype
	except AttributeError:
		pass  # not cached yet (`in frappe.local` is unreliable on v16)


class InvoiceTypeAmbientMixin:
	"""Pin the invoice_type ambient for classes whose flows hit submit paths."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._invoice_type_baseline = frappe.db.get_single_value(
			"POS Next Global Settings", "invoice_type"
		)
		_set_invoice_type("Sales Invoice")

	@classmethod
	def tearDownClass(cls):
		_set_invoice_type(cls._invoice_type_baseline)
		super().tearDownClass()


class TestProductionDoctypes(FrappeTestCase):
	def test_doctypes_and_key_fields_exist(self):
		for doctype, fields in [
			(
				"POS Production Recipe",
				["recipe_name", "production_item", "output_qty", "disabled", "items", "companies"],
			),
			("POS Production Recipe Item", ["item_code", "qty"]),
			("POS Production Recipe Company", ["company", "enabled", "bom_no"]),
			(
				"POS Production Log",
				[
					"recipe",
					"production_item",
					"qty",
					"items_used",
					"stock_entry",
					"work_order",
					"pos_profile",
					"company",
				],
			),
			("POS Settings", ["production_source_warehouse", "production_fg_warehouse"]),
		]:
			meta = frappe.get_meta(doctype)
			for fieldname in fields:
				self.assertTrue(meta.has_field(fieldname), f"{doctype} missing {fieldname}")

	def test_log_is_submittable(self):
		self.assertTrue(frappe.get_meta("POS Production Log").is_submittable)

	def test_work_order_posa_custom_fields_registered(self):
		# install.py CUSTOM_FIELDS feed after_migrate's sync; the meta only
		# carries them once the site was migrated with them.
		for fieldname in ("posa_pos_profile", "posa_operator", "posa_recipe"):
			self.assertTrue(
				frappe.get_meta("Work Order").has_field(fieldname),
				f"Work Order missing custom field {fieldname} (site needs migrate --sync)",
			)


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
	item = frappe.get_doc(doc).insert(ignore_permissions=True)
	# Naming-series sites re-code items on insert; pin the requested code so
	# every later reference (recipe links, bins, receipts) resolves.
	if item.name != code:
		frappe.rename_doc("Item", item.name, code, force=True)
	return code


class TestGetProductionRecipes(InvoiceTypeAmbientMixin, FrappeTestCase):
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
		self.other_company = frappe.db.get_value("Company", {"name": ["!=", self.company]}, "name")
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
		self.assertEqual([b["batch_no"] for b in row["batches"]], [dated.name, undated.name])
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


class TestBomSync(InvoiceTypeAmbientMixin, FrappeTestCase):
	"""D1: saving a recipe keeps one active BOM per enabled company row."""

	def setUp(self):
		self.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		self.company = frappe.db.get_value("POS Profile", self.pos_profile, "company")
		self.warehouse = frappe.db.get_value(
			"POS Profile", self.pos_profile, "warehouse"
		) or frappe.db.get_value(
			"Warehouse", {"company": self.company, "is_group": 0, "disabled": 0}, "name"
		)
		self.fg = _make_test_item()
		self.mat = _make_test_item()

	def _recipe(self, output_qty=1, mat_qty=2, companies=None):
		doc = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"BOM {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": output_qty,
				"items": [{"item_code": self.mat, "qty": mat_qty}],
				"companies": companies or [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		# the sync hook db_sets the child rows after insert — read them back
		doc.reload()
		return doc

	def _bom(self, bom_no):
		return frappe.get_doc("BOM", bom_no)

	def test_recipe_save_creates_active_bom_per_company(self):
		doc = self._recipe(output_qty=4, mat_qty=2)
		row = next(r for r in doc.companies if r.company == self.company)
		self.assertTrue(row.bom_no)
		bom = self._bom(row.bom_no)
		self.assertEqual(bom.docstatus, 1)
		self.assertTrue(bom.is_active)
		self.assertFalse(bom.with_operations)
		self.assertEqual(bom.item, self.fg)
		self.assertEqual(bom.company, self.company)
		self.assertEqual(flt(bom.quantity), 4)
		self.assertEqual([i.item_code for i in bom.items], [self.mat])
		self.assertEqual(flt(bom.items[0].qty), 2)

	def test_recipe_change_rotates_bom(self):
		doc = self._recipe()
		old_bom = doc.companies[0].bom_no
		doc.append("items", {"item_code": _make_test_item(), "qty": 1})
		doc.save(ignore_permissions=True)
		doc.reload()
		new_bom = doc.companies[0].bom_no
		self.assertNotEqual(new_bom, old_bom)
		self.assertEqual(self._bom(old_bom).is_active, 0)
		self.assertEqual(self._bom(new_bom).is_active, 1)
		self.assertEqual(len(self._bom(new_bom).items), 2)

	def test_output_qty_change_rotates_bom(self):
		doc = self._recipe(output_qty=2)
		old_bom = doc.companies[0].bom_no
		doc.output_qty = 3
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertNotEqual(doc.companies[0].bom_no, old_bom)
		self.assertEqual(flt(self._bom(doc.companies[0].bom_no).quantity), 3)

	def test_open_wo_survives_bom_rotation(self):
		# D1 versioning promise: the submitted WO keeps its BOM snapshot — the
		# rotation must not break production already in flight
		doc = self._recipe(output_qty=1, mat_qty=2)
		_seed_stock(self.mat, self.warehouse, 10)
		started = start_production(doc.name, 5, self.pos_profile)
		old_bom = frappe.db.get_value("Work Order", started["work_order"], "bom_no")

		doc.append("items", {"item_code": _make_test_item(), "qty": 1})
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(self._bom(old_bom).is_active, 0)
		self.assertNotEqual(doc.companies[0].bom_no, old_bom)

		done = finish_production(started["work_order"], good_qty=5)
		self.assertEqual(done["status"], "Completed")
		se = frappe.get_doc("Stock Entry", done["stock_entry"])
		self.assertEqual(se.bom_no, old_bom)
		# consumption follows the WO's snapshot (2/run), not the rotated BOM
		self.assertEqual([d.item_code for d in se.items if d.s_warehouse], [self.mat])

	def test_unrelated_save_keeps_same_bom(self):
		doc = self._recipe()
		before = doc.companies[0].bom_no
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertEqual(doc.companies[0].bom_no, before)
		self.assertEqual(self._bom(before).is_active, 1)

	def test_disabled_company_row_deactivates_its_bom(self):
		doc = self._recipe()
		bom_no = doc.companies[0].bom_no
		doc.companies[0].enabled = 0
		doc.save(ignore_permissions=True)
		doc.reload()
		self.assertIsNone(doc.companies[0].bom_no)
		self.assertEqual(self._bom(bom_no).is_active, 0)


class TestCreateProduction(InvoiceTypeAmbientMixin, FrappeTestCase):
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
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": self.warehouse}, "actual_qty")
		)

	def test_happy_path_moves_stock_and_creates_log(self):
		_seed_stock(self.mat, self.warehouse, 10)
		result = create_production(
			recipe=self.recipe.name,
			qty=3,
			pos_profile=self.pos_profile,
		)
		# materials derived from the recipe: 3 runs x 2 = 6 consumed, 3 produced
		self.assertEqual(self._bin_qty(self.mat), 4)
		self.assertEqual(self._bin_qty(self.fg), 3)

		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		self.assertEqual(se.docstatus, 1)
		self.assertEqual(se.purpose, "Manufacture")
		self.assertIn("POS Production:", se.remarks)
		self.assertEqual(se.work_order, result["work_order"])
		self.assertTrue(any(d.is_finished_item for d in se.items))

		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(wo.docstatus, 1)
		self.assertEqual(flt(wo.produced_qty), 3)
		self.assertEqual(wo.posa_pos_profile, self.pos_profile)
		self.assertEqual(wo.posa_recipe, self.recipe.name)
		self.assertEqual(wo.company, self.company)
		self.assertEqual(wo.fg_warehouse, self.warehouse)
		self.assertEqual(wo.source_warehouse, self.warehouse)
		self.assertTrue(wo.skip_transfer)
		recipe_row = next(r for r in self.recipe.companies if r.company == self.company)
		self.assertEqual(
			wo.bom_no,
			frappe.db.get_value("POS Production Recipe Company", recipe_row.name, "bom_no"),
		)
		self.assertEqual(wo.status, "Completed")

		log = frappe.get_doc("POS Production Log", result["production_log"])
		self.assertEqual(log.docstatus, 1)
		self.assertEqual(log.stock_entry, se.name)
		self.assertEqual(log.work_order, wo.name)
		self.assertEqual(log.recipe, self.recipe.name)
		self.assertEqual(flt(log.qty), 3)
		used = json.loads(log.items_used)
		self.assertEqual(used[0]["item_code"], self.mat)
		self.assertEqual(flt(used[0]["qty"]), 6)

	def test_one_shot_with_good_and_loss_qty(self):
		# 92/8 dari 100: the SE carries fg_completed_qty=100 with the FG row only
		# the good 92 — ERPNext derives process_loss_qty 8 natively
		_seed_stock(self.mat, self.warehouse, 250)
		result = create_production(
			recipe=self.recipe.name,
			qty=100,
			good_qty=92,
			loss_qty=8,
			pos_profile=self.pos_profile,
		)
		# raw consumed GROSS (100 runs x 2), FG only the good qty
		self.assertEqual(self._bin_qty(self.mat), 50)
		self.assertEqual(self._bin_qty(self.fg), 92)

		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		self.assertEqual(flt(se.fg_completed_qty), 100)
		self.assertEqual(flt(se.process_loss_qty), 8)
		fg_row = next(d for d in se.items if d.is_finished_item)
		self.assertEqual(flt(fg_row.qty), 92)

		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(flt(wo.produced_qty), 92)
		self.assertEqual(flt(wo.process_loss_qty), 8)
		self.assertEqual(wo.status, "Completed")
		self.assertEqual(result["pos_status"], "Completed")
		self.assertEqual(flt(result["good_qty"]), 92)
		self.assertEqual(flt(result["loss_qty"]), 8)
		# the log keeps the good qty as "Qty Produced"
		log = frappe.get_doc("POS Production Log", result["production_log"])
		self.assertEqual(flt(log.qty), 92)

	def test_fractional_qty_scales_partially(self):
		if not frappe.db.exists("UOM", "Litre"):
			self.skipTest("no Litre UOM on this site")
		if frappe.db.get_value("UOM", "Litre", _uom_whole_field()):
			self.skipTest("Litre UOM requires whole quantities")
		fg = _make_test_item(stock_uom="Litre")
		mat = _make_test_item(stock_uom="Litre")
		doc = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"FR {uuid.uuid4().hex[:6]}",
				"production_item": fg,
				"output_qty": 4,
				"items": [{"item_code": mat, "qty": 2}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		_seed_stock(mat, self.warehouse, 10)
		result = create_production(recipe=doc.name, qty=1, pos_profile=self.pos_profile)
		# WO qty 1 on a BOM of 4: a quarter run of material, 1 FG out
		self.assertEqual(self._bin_qty(mat), 9.5)
		self.assertEqual(self._bin_qty(fg), 1)

		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		self.assertEqual(flt(next(d for d in se.items if d.is_finished_item).qty), 1)

	def test_client_item_overrides_are_ignored(self):
		_seed_stock(self.mat, self.warehouse, 10)
		# stale/tampered client payload: wrong material qty must not reach the stock entry
		create_production(
			recipe=self.recipe.name,
			qty=2,
			items=json.dumps([{"item_code": self.mat, "qty": 0.1}]),
			pos_profile=self.pos_profile,
		)
		self.assertEqual(self._bin_qty(self.mat), 6)  # derived: 2 runs x 2
		self.assertEqual(self._bin_qty(self.fg), 2)

	def test_whole_uom_rejects_fractional_qty(self):
		if not frappe.db.exists("UOM", "Nos"):
			self.skipTest("no Nos UOM on this site")
		frappe.db.set_value("UOM", "Nos", _uom_whole_field(), 1)
		_seed_stock(self.mat, self.warehouse, 10)
		with self.assertRaises(ValidationError) as ctx:
			create_production(recipe=self.recipe.name, qty=1.5, pos_profile=self.pos_profile)
		self.assertIn("whole", str(ctx.exception))
		# rejected before any document exists
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remarks": ["like", f"%{self.recipe.recipe_name}%"]}), 0
		)
		self.assertEqual(frappe.db.count("Work Order", {"posa_recipe": self.recipe.name}), 0)

	def test_insufficient_stock_rejected_before_entry(self):
		_seed_stock(self.mat, self.warehouse, 1)
		with self.assertRaises(ValidationError) as ctx:
			create_production(
				recipe=self.recipe.name,
				qty=1,
				pos_profile=self.pos_profile,
			)
		self.assertIn(self.mat, str(ctx.exception))
		# nothing was created for this recipe (FrappeTestCase rolls back per class,
		# not per test, so a global count would see entries from earlier tests)
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remarks": ["like", f"%{self.recipe.recipe_name}%"]}), 0
		)
		self.assertEqual(frappe.db.count("Work Order", {"posa_recipe": self.recipe.name}), 0)

	def test_disabled_material_rejected_before_entry(self):
		_seed_stock(self.mat, self.warehouse, 10)
		frappe.db.set_value("Item", self.mat, "disabled", 1)
		with self.assertRaises(ValidationError) as ctx:
			create_production(recipe=self.recipe.name, qty=1, pos_profile=self.pos_profile)
		self.assertIn(self.mat, str(ctx.exception))
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remarks": ["like", f"%{self.recipe.recipe_name}%"]}), 0
		)

	def test_batch_material_auto_picks_fifo_and_ignores_client_batches(self):
		mat_b = _make_test_item(has_batch_no=1)
		older = frappe.new_doc("Batch")
		older.batch_id = f"B-OLD-{uuid.uuid4().hex[:6]}"
		older.item = mat_b
		older.expiry_date = add_days(getdate(), 30)
		older.insert(ignore_permissions=True)
		other_batched = _make_test_item(has_batch_no=1)
		foreign = frappe.new_doc("Batch")
		foreign.batch_id = f"B-FGN-{uuid.uuid4().hex[:6]}"
		foreign.item = other_batched
		foreign.insert(ignore_permissions=True)
		_seed_stock(mat_b, self.warehouse, 5, batch_no=older.name)
		_seed_stock(self.mat, self.warehouse, 10)

		recipe = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"BP {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [
					{"item_code": self.mat, "qty": 1},
					{"item_code": mat_b, "qty": 1},
				],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)

		# legacy params point at a foreign batch; server must still FIFO-pick
		result = create_production(
			recipe=recipe.name,
			qty=1,
			items=json.dumps([{"item_code": self.mat, "qty": 99}]),
			pos_profile=self.pos_profile,
			batches=json.dumps({mat_b: foreign.name}),
		)
		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		row = next(d for d in se.items if d.item_code == mat_b)
		self.assertEqual(row.batch_no, older.name)
		self.assertEqual(flt(frappe.db.get_value("Batch", older.name, "batch_qty")), 4)

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
				pos_profile=self.pos_profile,
			)

	def test_finished_good_with_batch_gets_new_batch(self):
		fg = frappe.get_doc("Item", self.fg)
		fg.has_batch_no = 1
		# native auto-batch (D7) only mints WO batches when the item opts in
		fg.create_new_batch = 1
		fg.save(ignore_permissions=True)  # document save invalidates Item cache
		_seed_stock(self.mat, self.warehouse, 10)
		result = create_production(recipe=self.recipe.name, qty=2, pos_profile=self.pos_profile)
		se = frappe.get_doc("Stock Entry", result["stock_entry"])
		fg_row = next(d for d in se.items if d.is_finished_item)
		self.assertTrue(fg_row.serial_and_batch_bundle or fg_row.batch_no)

	def test_overproduction_is_rejected_by_erpnext(self):
		# the service never caps the good qty itself: ERPNext's own
		# overproduction_percentage_for_work_order guard must fire on insert
		_seed_stock(self.mat, self.warehouse, 100)
		wo = start_production(self.recipe.name, 3, self.pos_profile)
		with self.assertRaises(ValidationError):
			finish_production(wo["work_order"], good_qty=4)

	# ---- SEC-NEW-03: production endpoints profile-membership gate ----

	def _make_user(self, first_name):
		# uuid users: any granted role's redis cache outlives the class
		# rollback, so users must never be reused across runs/classes
		user = f"prod-sec.{uuid.uuid4().hex[:8]}@example.com"
		frappe.get_doc(
			{"doctype": "User", "email": user, "first_name": first_name}
		).insert(ignore_permissions=True)
		return user

	def test_profile_user_mismatch_rejected(self):
		# SEC-NEW-03: pos_profile selects the warehouses the production posts
		# to. Without the gate a roleless user could post against another
		# outlet's warehouse just by naming that profile in the call.
		outsider = self._make_user("Prod Sec Intruder")
		_seed_stock(self.mat, self.warehouse, 10)
		frappe.set_user(outsider)
		try:
			with self.assertRaises(frappe.PermissionError):
				create_production(recipe=self.recipe.name, qty=1, pos_profile=self.pos_profile)
		finally:
			frappe.set_user("Administrator")
		# the gate fired before any stock moved
		self.assertEqual(
			frappe.db.count("Stock Entry", {"remarks": ["like", f"%{self.recipe.recipe_name}%"]}), 0
		)

	def test_non_pos_work_order_actions_rejected(self):
		# a Work Order without posa_pos_profile never came from POS; its
		# lifecycle belongs to Desk, not to these endpoints
		from pos_next.api.production import _profile_of

		with self.assertRaises(ValidationError):
			_profile_of("NONEXISTENT-WO")

	def test_assigned_cashier_can_produce_on_own_profile(self):
		# acceptance branch of the gate: a user assigned to the profile (via
		# POS Profile User) still produces on it. Nexus POS Manager rather than
		# POSNext Cashier because the permission-enforced POS Production Log
		# submit needs write, which the cashier role does not carry.
		if not frappe.db.exists("Role", "Nexus POS Manager"):
			self.skipTest("Nexus POS Manager role missing")
		cashier = self._make_user("Prod Sec Cashier")
		frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": cashier,
				"parenttype": "User",
				"parentfield": "roles",
				"role": "Nexus POS Manager",
			}
		).insert(ignore_permissions=True)
		# membership is exactly what the gate checks
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": self.pos_profile,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": cashier,
				"default": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=cashier)
		_seed_stock(self.mat, self.warehouse, 10)
		frappe.set_user(cashier)
		try:
			result = create_production(recipe=self.recipe.name, qty=1, pos_profile=self.pos_profile)
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(result["stock_entry"])
		self.assertTrue(result["production_log"])

	def test_posnext_cashier_can_produce_on_own_profile(self):
		# the real cashier lane: POSNext Cashier carries read on Recipe/Item but
		# no Stock Entry or POS Production Log perms — the API's profile gate
		# (SEC-NEW-03) is the access control, so production itself must run
		# under the function's own ignore_permissions flags
		if not frappe.db.exists("Role", "POSNext Cashier"):
			self.skipTest("POSNext Cashier role missing")
		cashier = self._make_user("Prod Cashier Lane")
		frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": cashier,
				"parenttype": "User",
				"parentfield": "roles",
				"role": "POSNext Cashier",
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": self.pos_profile,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": cashier,
				"default": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=cashier)
		_seed_stock(self.mat, self.warehouse, 10)
		frappe.set_user(cashier)
		try:
			result = create_production(recipe=self.recipe.name, qty=1, pos_profile=self.pos_profile)
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(result["stock_entry"])
		log = frappe.get_doc("POS Production Log", result["production_log"])
		self.assertEqual(log.docstatus, 1)
		self.assertEqual(log.recipe, self.recipe.name)


class TestProductionLifecycle(InvoiceTypeAmbientMixin, FrappeTestCase):
	"""Fase 2: Start / Finish / Close / Cancel two-phase lifecycle (D3, D10)."""

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
				"recipe_name": f"LC {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [{"item_code": self.mat, "qty": 2}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)

	def _bin_qty(self, item_code):
		return flt(
			frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": self.warehouse}, "actual_qty")
		)

	def _start(self, qty, seed=None):
		if seed is not None:
			_seed_stock(self.mat, self.warehouse, seed)
		return start_production(self.recipe.name, qty, self.pos_profile)

	def test_start_creates_submitted_wo_without_moving_stock(self):
		result = self._start(5, seed=10)
		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(wo.docstatus, 1)
		self.assertEqual(wo.status, "Not Started")
		self.assertEqual(flt(wo.qty), 5)
		self.assertEqual(flt(wo.produced_qty), 0)
		self.assertTrue(wo.skip_transfer)
		self.assertEqual(wo.source_warehouse, self.warehouse)
		self.assertEqual(wo.fg_warehouse, self.warehouse)
		self.assertEqual(wo.posa_operator, "Administrator")
		recipe_row = next(r for r in self.recipe.companies if r.company == self.company)
		self.assertEqual(
			wo.bom_no,
			frappe.db.get_value("POS Production Recipe Company", recipe_row.name, "bom_no"),
		)
		# required_items snapshotted from the BOM
		self.assertEqual([d.item_code for d in wo.required_items], [self.mat])
		self.assertEqual(flt(wo.required_items[0].required_qty), 10)
		# no stock moved yet
		self.assertEqual(self._bin_qty(self.mat), 10)
		self.assertEqual(self._bin_qty(self.fg), 0)
		self.assertEqual(result["pos_status"], "Not Started")

	def test_active_list_shows_started_wo(self):
		result = self._start(5, seed=10)
		payload = get_active_productions(self.pos_profile)
		mine = [p for p in payload["productions"] if p["work_order"] == result["work_order"]]
		self.assertEqual(len(mine), 1)
		row = mine[0]
		self.assertEqual(row["status"], "Not Started")
		self.assertEqual(row["pos_status"], "Not Started")
		self.assertEqual(row["recipe"], self.recipe.name)
		self.assertEqual(row["operator"], "Administrator")
		self.assertEqual(flt(row["qty"]), 5)
		# epoch seconds — elapsed time must be computed from an absolute instant
		self.assertIsInstance(row["started_at"], int)
		# a completed production is not "active" anymore
		finish_production(result["work_order"], good_qty=5)
		payload = get_active_productions(self.pos_profile)
		self.assertFalse(any(p["work_order"] == result["work_order"] for p in payload["productions"]))

	def test_finish_moves_stock_and_completes(self):
		result = self._start(5, seed=10)
		done = finish_production(result["work_order"], good_qty=5)
		self.assertEqual(done["status"], "Completed")
		self.assertEqual(done["pos_status"], "Completed")
		self.assertEqual(self._bin_qty(self.mat), 0)
		self.assertEqual(self._bin_qty(self.fg), 5)

		se = frappe.get_doc("Stock Entry", done["stock_entry"])
		self.assertEqual(se.purpose, "Manufacture")
		self.assertEqual(se.work_order, result["work_order"])
		self.assertIn("POS Production:", se.remarks)
		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(flt(wo.produced_qty), 5)

	def test_finish_with_loss_records_native_process_loss(self):
		result = self._start(10, seed=20)
		done = finish_production(result["work_order"], good_qty=7, loss_qty=3)
		# good 7 out, raw consumed GROSS (20), loss 3 native-derived
		self.assertEqual(self._bin_qty(self.mat), 0)
		self.assertEqual(self._bin_qty(self.fg), 7)

		se = frappe.get_doc("Stock Entry", done["stock_entry"])
		self.assertEqual(flt(se.fg_completed_qty), 10)
		self.assertEqual(flt(se.process_loss_qty), 3)
		fg_row = next(d for d in se.items if d.is_finished_item)
		self.assertEqual(flt(fg_row.qty), 7)

		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(flt(wo.produced_qty), 7)
		self.assertEqual(flt(wo.process_loss_qty), 3)
		self.assertEqual(wo.status, "Completed")
		# the log keeps the good qty as "Qty Produced"
		log = frappe.get_doc("POS Production Log", done["production_log"])
		self.assertEqual(flt(log.qty), 7)
		self.assertEqual(done["loss_qty"], 3)

	def test_finish_partial_keeps_wo_in_process(self):
		result = self._start(10, seed=20)
		done = finish_production(result["work_order"], good_qty=4)
		self.assertEqual(done["status"], "In Process")
		self.assertEqual(done["pos_status"], "In Progress")
		row = next(
			p
			for p in get_active_productions(self.pos_profile)["productions"]
			if p["work_order"] == result["work_order"]
		)
		self.assertEqual(flt(row["produced_qty"]), 4)

	def test_finish_on_closed_wo_rejected(self):
		result = self._start(5, seed=10)
		close_production(result["work_order"])
		with self.assertRaises(ValidationError):
			finish_production(result["work_order"], good_qty=1)

	def test_close_partial_counts_as_partially_completed(self):
		result = self._start(10, seed=20)
		finish_production(result["work_order"], good_qty=4)
		done = close_production(result["work_order"])
		self.assertEqual(done["status"], "Closed")
		self.assertEqual(done["pos_status"], "Partially Completed")
		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(wo.status, "Closed")
		self.assertEqual(flt(wo.produced_qty), 4)

	def test_close_without_output_counts_as_failed(self):
		result = self._start(5, seed=10)
		done = close_production(result["work_order"])
		self.assertEqual(done["pos_status"], "Failed")
		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(wo.status, "Closed")
		self.assertEqual(flt(wo.produced_qty), 0)
		# no writeoff requested: the material stays in the warehouse
		self.assertEqual(self._bin_qty(self.mat), 10)
		self.assertIsNone(done["material_issue"])

	def test_close_writeoff_issues_remaining_material_to_expense(self):
		result = self._start(10, seed=20)
		done = close_production(result["work_order"], writeoff=1)
		issue_name = done["material_issue"]
		self.assertTrue(issue_name)
		issue = frappe.get_doc("Stock Entry", issue_name)
		self.assertEqual(issue.purpose, "Material Issue")
		# ERPNext strips work_order on Material Issue — the remark owns the link
		self.assertEqual(issue.docstatus, 1)
		self.assertIn(result["work_order"], issue.remarks)
		self.assertEqual(flt(issue.items[0].qty), 20)
		self.assertEqual(issue.items[0].s_warehouse, self.warehouse)
		self.assertEqual(
			issue.items[0].expense_account,
			frappe.db.get_value("Company", self.company, "stock_adjustment_account"),
		)
		self.assertEqual(self._bin_qty(self.mat), 0)
		# total failure: Closed && produced 0 -> "Failed"
		self.assertEqual(done["pos_status"], "Failed")

	def test_close_writeoff_after_partial_issue_only_remaining(self):
		result = self._start(10, seed=20)
		finish_production(result["work_order"], good_qty=6)
		done = close_production(result["work_order"], writeoff=1)
		issue = frappe.get_doc("Stock Entry", done["material_issue"])
		# 6 runs consumed 12; only the remaining 8 is written off
		self.assertEqual(flt(issue.items[0].qty), 8)
		self.assertEqual(done["pos_status"], "Partially Completed")

	def test_cancel_before_any_entry_cancels_wo_only(self):
		result = self._start(5, seed=10)
		out = cancel_production(result["work_order"])
		self.assertEqual(out["cancelled_stock_entries"], [])
		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(wo.docstatus, 2)
		self.assertEqual(self._bin_qty(self.mat), 10)

	def test_cancel_does_not_touch_other_wos_writeoff_via_notes(self):
		# the write-off remark is matched on the canonical prefix, not bare
		# tokens: WO A closed with notes naming WO B must survive cancelling B
		result_a = self._start(5, seed=10)
		result_b = self._start(5, seed=10)
		close_production(result_a["work_order"], writeoff=1, notes=f"sisanya untuk {result_b['work_order']}")
		cancel_production(result_b["work_order"])
		a_issue = frappe.get_all(
			"Stock Entry",
			filters={
				"purpose": "Material Issue",
				"remarks": ["like", f"%{result_a['work_order']}%"],
				"docstatus": 1,
			},
		)
		self.assertEqual(len(a_issue), 1)

	def test_cancel_after_finish_reverses_entries_lifo(self):
		result = self._start(5, seed=10)
		done = finish_production(result["work_order"], good_qty=2)
		out = cancel_production(result["work_order"])
		self.assertEqual(out["cancelled_stock_entries"], [done["stock_entry"]])
		self.assertEqual(frappe.db.get_value("Stock Entry", done["stock_entry"], "docstatus"), 2)
		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(wo.docstatus, 2)
		self.assertEqual(self._bin_qty(self.mat), 10)
		self.assertEqual(self._bin_qty(self.fg), 0)

	def test_cancelled_entry_rewinds_wo_and_allows_refinish(self):
		# D10 case 6 (koreksi): cancelling the wrong SE steps the WO aggregates
		# and status back natively; the corrected finish then completes the WO
		result = self._start(5, seed=10)
		done = finish_production(result["work_order"], good_qty=2)
		self.assertEqual(done["status"], "In Process")

		se = frappe.get_doc("Stock Entry", done["stock_entry"])
		# the submitted log references the SE and would block its cancel — the
		# same unwind order cancel_production uses (logs first, entries LIFO)
		log = frappe.get_doc("POS Production Log", done["production_log"])
		log.flags.ignore_permissions = True
		log.cancel()
		se.flags.ignore_permissions = True
		se.cancel()

		wo = frappe.get_doc("Work Order", result["work_order"])
		self.assertEqual(flt(wo.produced_qty), 0)
		self.assertEqual(wo.status, "Not Started")
		self.assertEqual(self._bin_qty(self.mat), 10)
		self.assertEqual(self._bin_qty(self.fg), 0)

		redone = finish_production(result["work_order"], good_qty=5)
		self.assertEqual(redone["status"], "Completed")
		self.assertEqual(self._bin_qty(self.mat), 0)
		self.assertEqual(self._bin_qty(self.fg), 5)

	def test_settings_warehouses_override_profile_warehouse(self):
		# D2b: production_source/fg_warehouse on the POS Settings row wins;
		# empty values coalesce to the profile warehouse in the service
		from pos_next.services.production import _production_warehouses

		self.assertEqual(_production_warehouses(self.pos_profile, self.warehouse),
			(self.warehouse, self.warehouse))

		others = frappe.get_all(
			"Warehouse",
			filters={"company": self.company, "is_group": 0, "disabled": 0, "name": ["!=", self.warehouse]},
			fields=["name"],
			limit=2,
		)
		if len(others) < 2:
			self.skipTest("need two more warehouses in the company")
		src, fgw = others[0].name, others[1].name
		existing = frappe.db.get_value("POS Settings", {"pos_profile": self.pos_profile}, "name")
		baseline = (
			frappe.db.get_value(
				"POS Settings",
				existing,
				["enabled", "production_source_warehouse", "production_fg_warehouse"],
				as_dict=True,
			)
			if existing
			else None
		)
		if existing:
			frappe.db.set_value(
				"POS Settings",
				existing,
				{"enabled": 1, "production_source_warehouse": src, "production_fg_warehouse": fgw},
			)
		else:
			frappe.get_doc(
				{
					"doctype": "POS Settings",
					"pos_profile": self.pos_profile,
					"enabled": 1,
					"production_source_warehouse": src,
					"production_fg_warehouse": fgw,
				}
			).insert(ignore_permissions=True)
		try:
			self.assertEqual(_production_warehouses(self.pos_profile, self.warehouse), (src, fgw))
			_seed_stock(self.mat, src, 10)
			result = start_production(self.recipe.name, 2, self.pos_profile)
			wo = frappe.get_doc("Work Order", result["work_order"])
			self.assertEqual(wo.source_warehouse, src)
			self.assertEqual(wo.fg_warehouse, fgw)
		finally:
			# the resolver row is read live on every call — restore it so
			# sibling tests in this class keep the profile's own warehouse
			if existing:
				frappe.db.set_value(
					"POS Settings",
					existing,
					{
						"enabled": baseline.enabled,
						"production_source_warehouse": baseline.production_source_warehouse,
						"production_fg_warehouse": baseline.production_fg_warehouse,
					},
				)
			else:
				frappe.db.delete(
					"POS Settings", {"pos_profile": self.pos_profile, "enabled": 1}
				)


class TestGetProductionHistory(InvoiceTypeAmbientMixin, FrappeTestCase):
	"""D5: the Work Order itself is the history — no separate read model."""

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
				"recipe_name": f"HIST {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [{"item_code": self.mat, "qty": 2}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)

	def _start(self, qty, seed=None):
		if seed is not None:
			_seed_stock(self.mat, self.warehouse, seed)
		return start_production(self.recipe.name, qty, self.pos_profile)

	def _rows_by_wo(self, payload):
		return {r["work_order"]: r for r in payload["productions"]}

	def test_history_lists_completed_with_quantities(self):
		result = self._start(5, seed=10)
		finish_production(result["work_order"], good_qty=5)
		rows = self._rows_by_wo(get_production_history(self.pos_profile))
		self.assertIn(result["work_order"], rows)
		row = rows[result["work_order"]]
		self.assertEqual(row["pos_status"], "Completed")
		self.assertEqual(flt(row["qty"]), 5)
		self.assertEqual(flt(row["produced_qty"]), 5)
		self.assertEqual(flt(row["process_loss_qty"]), 0)
		self.assertEqual(
			row["production_item_name"], frappe.db.get_value("Item", self.fg, "item_name")
		)
		self.assertEqual(row["recipe_name"], self.recipe.recipe_name)
		self.assertEqual(row["operator"], "Administrator")

	def test_history_excludes_active_productions(self):
		result = self._start(5, seed=10)
		rows = self._rows_by_wo(get_production_history(self.pos_profile))
		self.assertNotIn(result["work_order"], rows)

	def test_history_labels_closed_partial_and_failed(self):
		partial = self._start(10, seed=20)
		finish_production(partial["work_order"], good_qty=4)
		close_production(partial["work_order"])
		failed = self._start(5, seed=10)
		close_production(failed["work_order"])
		rows = self._rows_by_wo(get_production_history(self.pos_profile))
		self.assertEqual(rows[partial["work_order"]]["status"], "Closed")
		self.assertEqual(rows[partial["work_order"]]["pos_status"], "Partially Completed")
		self.assertEqual(flt(rows[partial["work_order"]]["produced_qty"]), 4)
		self.assertEqual(rows[failed["work_order"]]["pos_status"], "Failed")

	def test_history_includes_cancelled(self):
		result = self._start(3, seed=10)
		cancel_production(result["work_order"])
		rows = self._rows_by_wo(get_production_history(self.pos_profile))
		self.assertEqual(rows[result["work_order"]]["pos_status"], "Cancelled")

	def test_history_pagination_slices_newest_first(self):
		mine = []
		for _ in range(3):
			r = self._start(2, seed=10)
			finish_production(r["work_order"], good_qty=2)
			mine.append(r["work_order"])
		page = get_production_history(self.pos_profile, limit=2)
		self.assertLessEqual(len(page["productions"]), 2)
		self.assertTrue(page["has_more"])
		seen = {p["work_order"] for p in page["productions"]}
		self.assertTrue(seen <= set(mine))
		offset = 2
		while page["has_more"]:
			page = get_production_history(self.pos_profile, limit=2, offset=offset)
			self.assertLessEqual(len(page["productions"]), 2)
			seen |= {p["work_order"] for p in page["productions"]}
			offset += 2
		self.assertTrue(set(mine) <= seen)


class TestFinishBatchPicks(InvoiceTypeAmbientMixin, FrappeTestCase):
	"""Batch pickers on the finish form: explicit pick validated pre-submit,
	empty pick falls back to server FEFO (single batch per material, D-DUAKILAU)."""

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
		self.mat_b = _make_test_item(has_batch_no=1)
		# two batches, one item: FEFO eats b_old (earlier expiry); b_new exists
		# precisely so an explicit pick can diverge from the FEFO default
		self.b_old = frappe.new_doc("Batch")
		self.b_old.batch_id = f"B-OLD-{uuid.uuid4().hex[:6]}"
		self.b_old.item = self.mat_b
		self.b_old.expiry_date = add_days(getdate(), 10)
		self.b_old.insert(ignore_permissions=True)
		self.b_new = frappe.new_doc("Batch")
		self.b_new.batch_id = f"B-NEW-{uuid.uuid4().hex[:6]}"
		self.b_new.item = self.mat_b
		self.b_new.expiry_date = add_days(getdate(), 90)
		self.b_new.insert(ignore_permissions=True)
		self.recipe = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"FBP {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [
					{"item_code": self.mat, "qty": 1},
					{"item_code": self.mat_b, "qty": 2},
				],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)

	def _seed_all(self):
		_seed_stock(self.mat, self.warehouse, 10)
		_seed_stock(self.mat_b, self.warehouse, 10, batch_no=self.b_old.name)
		_seed_stock(self.mat_b, self.warehouse, 10, batch_no=self.b_new.name)

	def _batch_qty(self, batch_id):
		return flt(frappe.db.get_value("Batch", batch_id, "batch_qty"))

	def _mat_row(self, stock_entry):
		return next(d for d in stock_entry.items if d.item_code == self.mat_b)

	def test_finish_context_lists_batches_fefo(self):
		self._seed_all()
		started = start_production(self.recipe.name, 3, self.pos_profile)
		ctx = get_finish_context(started["work_order"])
		self.assertEqual(flt(ctx["wo_qty"]), 3)
		by_code = {m["item_code"]: m for m in ctx["materials"]}
		self.assertTrue(by_code[self.mat_b]["has_batch_no"])
		batches = by_code[self.mat_b]["batches"]
		self.assertEqual([b["batch_no"] for b in batches], [self.b_old.name, self.b_new.name])
		self.assertFalse(by_code[self.mat]["has_batch_no"])

	def test_finish_without_picks_falls_back_to_fefo(self):
		self._seed_all()
		started = start_production(self.recipe.name, 3, self.pos_profile)
		done = finish_production(started["work_order"], good_qty=3)
		se = frappe.get_doc("Stock Entry", done["stock_entry"])
		self.assertEqual(self._mat_row(se).batch_no, self.b_old.name)
		self.assertEqual(self._batch_qty(self.b_old.name), 4)
		self.assertEqual(self._batch_qty(self.b_new.name), 10)

	def test_finish_with_explicit_pick_consumes_that_batch(self):
		self._seed_all()
		started = start_production(self.recipe.name, 3, self.pos_profile)
		done = finish_production(
			started["work_order"], good_qty=3, batch_picks={self.mat_b: self.b_new.name}
		)
		se = frappe.get_doc("Stock Entry", done["stock_entry"])
		self.assertEqual(self._mat_row(se).batch_no, self.b_new.name)
		self.assertEqual(self._batch_qty(self.b_new.name), 4)
		self.assertEqual(self._batch_qty(self.b_old.name), 10)

	def test_finish_with_foreign_batch_rejected_before_submit(self):
		self._seed_all()
		other_item = _make_test_item(has_batch_no=1)
		foreign = frappe.new_doc("Batch")
		foreign.batch_id = f"B-FGN-{uuid.uuid4().hex[:6]}"
		foreign.item = other_item
		foreign.insert(ignore_permissions=True)
		started = start_production(self.recipe.name, 3, self.pos_profile)
		with self.assertRaises(ValidationError) as ctx:
			finish_production(
				started["work_order"], good_qty=3, batch_picks={self.mat_b: foreign.name}
			)
		self.assertIn(foreign.name, str(ctx.exception))
		# nothing posted: WO untouched, no Stock Entry from this attempt
		wo = frappe.get_doc("Work Order", started["work_order"])
		self.assertEqual(flt(wo.produced_qty), 0)
		self.assertEqual(wo.status, "Not Started")

	def test_finish_with_insufficient_pick_rejected_before_submit(self):
		# b_old holds only 2, the WO needs 2/unit × 3 = 6: the explicit pick must
		# be validated against the same live ledger the FEFO pick reads
		_seed_stock(self.mat, self.warehouse, 10)
		_seed_stock(self.mat_b, self.warehouse, 2, batch_no=self.b_old.name)
		_seed_stock(self.mat_b, self.warehouse, 10, batch_no=self.b_new.name)
		started = start_production(self.recipe.name, 3, self.pos_profile)
		with self.assertRaises(ValidationError) as ctx:
			finish_production(
				started["work_order"], good_qty=3, batch_picks={self.mat_b: self.b_old.name}
			)
		self.assertIn(self.b_old.name, str(ctx.exception))
		wo = frappe.get_doc("Work Order", started["work_order"])
		self.assertEqual(flt(wo.produced_qty), 0)
