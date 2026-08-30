"""Promotion pricing domain tests (Task 3).

Tests valid quote calculation, upcharge, negative adjustment bounded,
choice validation failures, and returned row descriptors.

Conventions:
- self.addCleanup(frappe.db.rollback) registered FIRST in setUp.
- Zero frappe.db.commit() calls.
- Unique suffix per test run.
"""

import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt

from pos_next.promotions import pricing


def get_unique_suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionPricing(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.suffix = get_unique_suffix()
		self._setup_companies_and_warehouses()
		self._setup_items()
		self._setup_promotion_master()

	def _make_company(self, prefix, parent=None, currency="IDR", is_group=0):
		company_name = f"{prefix} {self.suffix}"
		if not hasattr(self, "_company_counter"):
			self._company_counter = 0
		self._company_counter += 1
		abbr = f"{self.suffix.upper()[:6]}{self._company_counter}"
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company_name,
				"abbr": abbr,
				"is_group": is_group,
				"parent_company": parent,
				"default_currency": currency,
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)
		return company_name

	def _make_warehouse(self, prefix, company):
		doc = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": f"{prefix} {self.suffix}",
				"company": company,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _setup_companies_and_warehouses(self):
		self.root_company = self._make_company("_Test PP Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test PP Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test PP Outlet WH", self.outlet_company)

	def _setup_items(self):
		self.parent_item = f"_Test PP Parent {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.parent_item,
				"item_name": "Combo Test Parent",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

		self.bread_a = f"_Test PP Bread A {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.bread_a,
				"item_name": "Roti A",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

		self.bread_b = f"_Test PP Bread B {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.bread_b,
				"item_name": "Roti B",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

		self.bread_c = f"_Test PP Bread C {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.bread_c,
				"item_name": "Roti C",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

		self.bread_d = f"_Test PP Bread D {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.bread_d,
				"item_name": "Roti D",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

	def _setup_promotion_master(self, **overrides):
		group_key = f"grp_{self.suffix}"
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo Pricing {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item,
			"base_price": 10000.0,
			"currency": "IDR",
			"enabled": 1,
			"max_instances_per_invoice": 2,
			"components": [{"item_code": self.bread_a, "qty": 1.0}],
			"choice_groups": [{"group_key": group_key, "label": "Pilih Roti", "pick_count": 2}],
			"options": [
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 1,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.bread_c,
					"price_adjustment": 1500.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.bread_d,
					"price_adjustment": -500.0,
					"max_per_option": 0,
				},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		doc.update(overrides)
		self.promo = frappe.get_doc(doc).insert(ignore_permissions=True)
		self.group_key = self.promo.choice_groups[0].group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name
		self.option_d = self.promo.options[2].name
		return self.promo

	def _context(self):
		return {"warehouse": self.outlet_warehouse}

	# --- positive quote tests ---------------------------------------------

	def test_valid_quote_with_mixed_options_and_descriptors(self):
		# Quote with valid choices fulfilling pick_count=2 with zero-adjustment option B twice
		# But max_per_option=1 blocks B qty=2 -> use B(1) + C(1)
		choices = [
			{
				"choice_group_key": self.group_key,
				"options": [{"option_id": self.option_b, "qty": 1}, {"option_id": self.option_c, "qty": 1}],
			}
		]
		result = pricing.quote(self.promo, choices, self._context())
		# base 10000 + B 0*1 + C 1500*1 = 11500
		self.assertAlmostEqual(result["total_price"], 11500.0)
		self.assertEqual(result["parent_row"]["warehouse"], self.outlet_warehouse)
		self.assertEqual(result["parent_row"]["role"], "Promotion Parent")
		for row in result["component_rows"]:
			self.assertIn(row["role"], ("Promotion Component",))
			self.assertEqual(row["warehouse"], self.outlet_warehouse)
			if row["item_code"] != self.parent_item:
				self.assertEqual(row["rate"], 0.0)
				self.assertEqual(row["amount"], 0.0)

	def test_upcharge_on_options(self):
		# Pick two C options (upcharge applies)
		choices = [{"choice_group_key": self.group_key, "options": [{"option_id": self.option_c, "qty": 2}]}]
		result = pricing.quote(self.promo, choices, self._context())
		# base 10000 + C 1500*2 = 13000
		self.assertAlmostEqual(result["total_price"], 13000.0)

	def test_negative_adjustment_still_positive_total(self):
		# Use D with negative adjustment; still total positive
		choices = [
			{
				"choice_group_key": self.group_key,
				"options": [{"option_id": self.option_d, "qty": 1}, {"option_id": self.option_b, "qty": 1}],
			}
		]
		result = pricing.quote(self.promo, choices, self._context())
		# base 10000 + D -500*1 + B 0*1 = 9500
		self.assertAlmostEqual(result["total_price"], 9500.0)

	def test_negative_total_is_rejected(self):
		# Create promo with large negative adjustment pushing total below zero
		group_key = f"grp2_{self.suffix}"
		neg_parent = f"_Test PP NegParent {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": neg_parent,
				"item_name": "NegParent",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		bread_e = f"_Test PP Bread E {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": bread_e,
				"item_name": "Roti E",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		bread_f = f"_Test PP Bread F {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": bread_f,
				"item_name": "Roti F",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		# Bypass master validation for negative total bound:
		# we need a small base and large negative adjustment that passes master check.
		# Master validates per-option: base + adj >= 0.
		# Quote validates total = base + adj1*qty + adj2*qty < 0.
		# Use base 100, adj -90 and -80 with pick_count 2 -> total = 100 - 90 - 80 = -70
		neg_promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"Neg Promo {self.suffix}",
				"root_company": self.root_company,
				"parent_item": neg_parent,
				"base_price": 100.0,
				"currency": "IDR",
				"enabled": 1,
				"components": [{"item_code": self.bread_a, "qty": 1.0}],
				"choice_groups": [{"group_key": group_key, "label": "Grup Neg", "pick_count": 2}],
				"options": [
					{
						"choice_group_key": group_key,
						"item_code": bread_e,
						"price_adjustment": -90.0,
						"max_per_option": 0,
					},
					{
						"choice_group_key": group_key,
						"item_code": bread_f,
						"price_adjustment": -80.0,
						"max_per_option": 0,
					},
				],
				"outlets": [
					{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}
				],
			}
		).insert(ignore_permissions=True)
		neg_gk = neg_promo.choice_groups[0].group_key
		neg_opt_e = neg_promo.options[0].name
		neg_opt_f = neg_promo.options[1].name
		choices = [
			{
				"choice_group_key": neg_gk,
				"options": [{"option_id": neg_opt_e, "qty": 1}, {"option_id": neg_opt_f, "qty": 1}],
			}
		]
		with self.assertRaisesRegex(frappe.ValidationError, r"must not be negative"):
			pricing.quote(neg_promo, choices, self._context())

	def test_flexible_choices_shape_group_key_and_picks(self):
		choices = [
			{
				"group_key": self.group_key,
				"picks": [{"option_row": self.option_b, "qty": 1}, {"option_row": self.option_c, "qty": 1}],
			}
		]
		result = pricing.quote(self.promo, choices, self._context())
		self.assertAlmostEqual(result["total_price"], 11500.0)

	def test_returned_row_descriptors_complete(self):
		choices = [{"choice_group_key": self.group_key, "options": [{"option_id": self.option_c, "qty": 2}]}]
		result = pricing.quote(self.promo, choices, self._context())
		self.assertEqual(result["parent_row"]["item_code"], self.parent_item)
		self.assertAlmostEqual(result["parent_row"]["rate"], result["total_price"])
		self.assertAlmostEqual(result["parent_row"]["amount"], result["total_price"])
		self.assertEqual(result["parent_row"]["is_free_item"], 0)
		self.assertEqual(result["parent_row"]["warehouse"], self.outlet_warehouse)
		self.assertEqual(result["parent_row"]["role"], "Promotion Parent")
		# Component rows: 1 fixed (bread_a) + 2 choice (two C)
		self.assertEqual(len(result["component_rows"]), 2)
		# Verify fixed component present
		fixed = [r for r in result["component_rows"] if r["item_code"] == self.bread_a]
		self.assertEqual(len(fixed), 1)
		for row in result["component_rows"]:
			self.assertEqual(row["rate"], 0.0)
			self.assertEqual(row["amount"], 0.0)
			self.assertEqual(row["is_free_item"], 1)
			self.assertEqual(row["warehouse"], self.outlet_warehouse)
			self.assertEqual(row["role"], "Promotion Component")

	def test_quote_exposes_max_instances_per_invoice(self):
		choices = [{"choice_group_key": self.group_key, "options": [{"option_id": self.option_c, "qty": 2}]}]
		result = pricing.quote(self.promo, choices, self._context())
		self.assertEqual(result["max_instances_per_invoice"], 2)
		self.assertEqual(result["currency"], "IDR")

	# --- choice validation failure tests --------------------------------

	def test_missing_group_raises(self):
		choices: list[dict] = []
		with self.assertRaisesRegex(frappe.ValidationError, r"Missing choices"):
			pricing.quote(self.promo, choices, self._context())

	def test_under_pick_raises(self):
		choices = [{"choice_group_key": self.group_key, "options": [{"option_id": self.option_c, "qty": 1}]}]
		with self.assertRaisesRegex(frappe.ValidationError, r"under-pick"):
			pricing.quote(self.promo, choices, self._context())

	def test_over_pick_raises(self):
		choices = [{"choice_group_key": self.group_key, "options": [{"option_id": self.option_c, "qty": 3}]}]
		with self.assertRaisesRegex(frappe.ValidationError, r"over-pick"):
			pricing.quote(self.promo, choices, self._context())

	def test_option_not_in_group_raises(self):
		# Create another promotion with a second group to get an option belonging elsewhere
		second_parent = f"_Test PP SecondParent {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": second_parent,
				"item_name": "SecondParent",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		other_gk = f"grp_other_{self.suffix}"
		second_promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"Other Promo {self.suffix}",
				"root_company": self.root_company,
				"parent_item": second_parent,
				"base_price": 10000.0,
				"currency": "IDR",
				"enabled": 0,
				"components": [{"item_code": self.bread_a, "qty": 1.0}],
				"choice_groups": [{"group_key": other_gk, "label": "Other Grup", "pick_count": 1}],
				"options": [
					{
						"choice_group_key": other_gk,
						"item_code": self.bread_b,
						"price_adjustment": 0.0,
						"max_per_option": 0,
					},
					{
						"choice_group_key": other_gk,
						"item_code": self.bread_c,
						"price_adjustment": 0.0,
						"max_per_option": 0,
					},
				],
				"outlets": [
					{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}
				],
			}
		).insert(ignore_permissions=True)
		other_opt = second_promo.options[0].name
		# Place other promotion's option row into original promo's group key
		choices = [{"choice_group_key": self.group_key, "options": [{"option_id": other_opt, "qty": 2}]}]
		with self.assertRaisesRegex(frappe.ValidationError, r"not found|does not belong"):
			pricing.quote(self.promo, choices, self._context())

	def test_option_exceeding_max_per_option_raises(self):
		# option_b has max_per_option == 1; qty 2 exceeds it
		choices = [{"choice_group_key": self.group_key, "options": [{"option_id": self.option_b, "qty": 2}]}]
		with self.assertRaisesRegex(frappe.ValidationError, r"max_per_option"):
			pricing.quote(self.promo, choices, self._context())

	def test_negative_or_zero_qty_raises(self):
		for qty in (0, -1):
			choices = [
				{"choice_group_key": self.group_key, "options": [{"option_id": self.option_c, "qty": qty}]}
			]
			with self.assertRaisesRegex(frappe.ValidationError, r"greater than zero|positive integer"):
				pricing.quote(self.promo, choices, self._context())
