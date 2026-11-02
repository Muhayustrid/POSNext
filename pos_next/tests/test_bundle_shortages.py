# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import json
import uuid
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.items import get_bundle_shortages


def _make_item(code_suffix="", is_stock=1):
	code = f"TEST-BS-{uuid.uuid4().hex[:8]}{code_suffix}"
	doc = {
		"doctype": "Item",
		"item_code": code,
		"item_name": f"Name {code}",
		"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name") or "All Item Groups",
		"stock_uom": "Nos",
		"is_stock_item": is_stock,
	}
	item = frappe.get_doc(doc).insert(ignore_permissions=True)
	if item.name != code:
		frappe.rename_doc("Item", item.name, code, force=True)
	return code


def _make_bundle(bundle_code, components):
	doc = frappe.get_doc(
		{
			"doctype": "Product Bundle",
			"new_item_code": bundle_code,
			"items": [{"item_code": code, "qty": qty} for code, qty in components],
		}
	).insert(ignore_permissions=True)
	return doc


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


class TestBundleShortages(FrappeTestCase):
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

	def test_bundle_shortages_returns_only_short_components(self):
		comp_sufficient = _make_item("-SUFF", is_stock=1)
		comp_empty = _make_item("-EMPTY", is_stock=1)
		bundle_1 = _make_item("-B1", is_stock=0)
		_make_bundle(bundle_1, [(comp_sufficient, 2), (comp_empty, 1)])
		_receipt(self.company, self.warehouse, comp_sufficient, 5)

		comp_ok1 = _make_item("-OK1", is_stock=1)
		comp_ok2 = _make_item("-OK2", is_stock=1)
		bundle_2 = _make_item("-B2", is_stock=0)
		_make_bundle(bundle_2, [(comp_ok1, 1), (comp_ok2, 2)])
		_receipt(self.company, self.warehouse, comp_ok1, 10)
		_receipt(self.company, self.warehouse, comp_ok2, 10)

		regular_item = _make_item("-REG", is_stock=1)

		# Bundle 1 has one short component (comp_empty)
		shortages = get_bundle_shortages([bundle_1], self.warehouse)
		self.assertIn(bundle_1, shortages)
		self.assertEqual(len(shortages[bundle_1]), 1)
		short_comp = shortages[bundle_1][0]
		self.assertEqual(short_comp["item_code"], comp_empty)
		self.assertEqual(short_comp["item_name"], f"Name {comp_empty}")
		self.assertEqual(short_comp["available_qty"], 0.0)
		self.assertEqual(short_comp["required_qty"], 1.0)

		# Bundle 2 has all components sufficient -> omitted
		shortages_b2 = get_bundle_shortages([bundle_2], self.warehouse)
		self.assertEqual(shortages_b2, {})

		# Querying bundle 1, bundle 2, and non-bundle item returns only bundle 1
		shortages_all = get_bundle_shortages([bundle_1, bundle_2, regular_item], self.warehouse)
		self.assertEqual(list(shortages_all.keys()), [bundle_1])

		# JSON string input format
		shortages_json = get_bundle_shortages(json.dumps([bundle_1, bundle_2]), self.warehouse)
		self.assertEqual(list(shortages_json.keys()), [bundle_1])

	def test_multiple_short_components_sorted_by_ratio(self):
		comp_0 = _make_item("-C0", is_stock=1)  # 0/2 = 0.0
		comp_half = _make_item("-CHALF", is_stock=1)  # 1/2 = 0.5
		comp_three_quarters = _make_item("-C34", is_stock=1)  # 3/4 = 0.75
		comp_full = _make_item("-CFULL", is_stock=1)  # 5/5 = 1.0 (not short)

		bundle = _make_item("-B-SORT", is_stock=0)
		_make_bundle(
			bundle,
			[
				(comp_three_quarters, 4),
				(comp_0, 2),
				(comp_full, 5),
				(comp_half, 2),
			],
		)
		_receipt(self.company, self.warehouse, comp_half, 1)
		_receipt(self.company, self.warehouse, comp_three_quarters, 3)
		_receipt(self.company, self.warehouse, comp_full, 5)

		shortages = get_bundle_shortages([bundle], self.warehouse)
		self.assertIn(bundle, shortages)
		short_items = shortages[bundle]
		self.assertEqual(len(short_items), 3)
		self.assertEqual(
			[item["item_code"] for item in short_items],
			[comp_0, comp_half, comp_three_quarters],
		)
		self.assertEqual(short_items[0]["available_qty"], 0.0)
		self.assertEqual(short_items[0]["required_qty"], 2.0)
		self.assertEqual(short_items[1]["available_qty"], 1.0)
		self.assertEqual(short_items[1]["required_qty"], 2.0)
		self.assertEqual(short_items[2]["available_qty"], 3.0)
		self.assertEqual(short_items[2]["required_qty"], 4.0)

	def test_group_warehouse_expansion(self):
		# Find a group warehouse with child warehouses
		group_whs = frappe.db.get_all("Warehouse", filters={"is_group": 1, "disabled": 0}, fields=["name", "company"])
		target_group_wh = None
		child_wh = None
		for gw in group_whs:
			children = frappe.db.get_descendants("Warehouse", gw.name)
			if children:
				target_group_wh = gw.name
				child_wh = children[0]
				break

		if not target_group_wh:
			self.skipTest("no group warehouse with children found")

		company = frappe.db.get_value("Warehouse", child_wh, "company")
		comp_suff = _make_item("-GW-SUFF", is_stock=1)
		comp_short = _make_item("-GW-SHORT", is_stock=1)
		bundle = _make_item("-B-GW", is_stock=0)
		_make_bundle(bundle, [(comp_suff, 2), (comp_short, 1)])
		# Stock is added to the child warehouse, queried via group warehouse
		_receipt(company, child_wh, comp_suff, 5)

		shortages = get_bundle_shortages([bundle], target_group_wh)
		self.assertIn(bundle, shortages)
		self.assertEqual(len(shortages[bundle]), 1)
		self.assertEqual(shortages[bundle][0]["item_code"], comp_short)

	def test_permission_guard(self):
		with mock.patch("pos_next.api.items._assert_warehouse_in_user_scope") as mock_assert:
			mock_assert.side_effect = frappe.PermissionError("unauthorized warehouse")
			with self.assertRaises(frappe.PermissionError):
				get_bundle_shortages(["BUNDLE"], "FOREIGN_WH")

	def test_bundle_shortages_edge_cases(self):
		self.assertEqual(get_bundle_shortages([], self.warehouse), {})
		self.assertEqual(get_bundle_shortages(["NON-EXISTENT"], self.warehouse), {})
		self.assertEqual(get_bundle_shortages(["NON-EXISTENT"], ""), {})
