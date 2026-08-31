"""Task 4.4 - instance quantity scales every row, total_amount stays per-unit.

Pricing's ``quote(promotion, choices, context, quantity=...)`` multiplies each
row's qty and the parent amount by quantity while rate and total_price
(sold as the selection's total_amount) remain per-unit. The instance cap
sums quantities with the same message text.

Conventions:
- IntegrationTestCase, self.addCleanup(frappe.db.rollback) FIRST.
- Zero frappe.db.commit().
- Unique suffix per run. Stock Settings pinned off, restored on cleanup.
"""

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from pos_next.promotions import pricing
from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionQuantityScaling(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		auto_insert_before = frappe.db.get_single_value(
			"Stock Settings", "auto_insert_price_list_rate_if_missing"
		)
		frappe.db.set_single_value("Stock Settings", "auto_insert_price_list_rate_if_missing", 0)
		self.addCleanup(
			frappe.db.set_single_value,
			"Stock Settings",
			"auto_insert_price_list_rate_if_missing",
			auto_insert_before,
		)
		self.suffix = _suffix()
		self._setup_companies_and_warehouses()
		self._setup_items()
		self._setup_pos_profile()
		self._setup_promotion()

	# -- fixtures ----------------------------------------------------------

	def _make_company(self, prefix, parent=None, is_group=0):
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
				"default_currency": "IDR",
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
		if not frappe.db.exists("Warehouse Type", "Transit"):
			frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(ignore_permissions=True)
		if not frappe.db.exists("Fiscal Year", "2026"):
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": "2026",
					"year_start_date": "2026-01-01",
					"year_end_date": "2026-12-31",
				}
			).insert(ignore_permissions=True)
		self.root_company = self._make_company("_Test Qs Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test Qs Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test Qs Outlet WH", self.outlet_company)

	def _setup_items(self):
		self.parent_item = f"_Test Qs Parent {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.parent_item,
				"item_name": "Qs Parent",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		self.bread_a = f"_Test Qs Bread A {self.suffix}"
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
		self.bread_b = f"_Test Qs Bread B {self.suffix}"
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
		self.bread_c = f"_Test Qs Bread C {self.suffix}"
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
		stock_entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.outlet_company,
				"items": [
					{"item_code": self.bread_a, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 5000},
					{"item_code": self.bread_b, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 6000},
					{"item_code": self.bread_c, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 8000},
				],
			}
		).insert(ignore_permissions=True)
		stock_entry.submit()

	def _setup_pos_profile(self):
		self.customer_name = f"_Test Qs Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)
		self.pos_profile_name = f"_Test Qs POS Profile {self.suffix}"
		mop = get_default_mode_of_payment(self.outlet_company)
		self.mop_name = mop
		write_off_account = get_default_account(self.outlet_company, "Expense")
		write_off_cc = get_default_cost_center(self.outlet_company)
		income_account = (
			frappe.db.get_value(
				"Account",
				{"company": self.outlet_company, "root_type": "Income", "is_group": 0},
				"name",
				order_by="creation asc",
			)
			or write_off_account
		)
		if not frappe.db.exists("Price List", "Standard Selling"):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": "Standard Selling",
					"selling": 1,
					"currency": "IDR",
				}
			).insert(ignore_permissions=True)
		self.pos_profile = frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": self.pos_profile_name,
				"company": self.outlet_company,
				"warehouse": self.outlet_warehouse,
				"customer": self.customer_name,
				"currency": "IDR",
				"selling_price_list": "Standard Selling",
				"payments": [{"mode_of_payment": mop, "default": 1}],
				"write_off_account": write_off_account,
				"write_off_cost_center": write_off_cc,
				"income_account": income_account,
				"expense_account": write_off_account,
				"cost_center": write_off_cc,
				"write_off_limit": 1.0,
			}
		).insert(ignore_permissions=True)

	def _setup_promotion(self, **overrides):
		group_key = f"grp_{self.suffix}"
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo Qs {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item,
			"base_price": 20000.0,
			"currency": "IDR",
			"enabled": 1,
			"max_instances_per_invoice": 0,
			"components": [{"item_code": self.bread_a, "qty": 1.0}],
			"choice_groups": [{"group_key": group_key, "label": "Pilih Roti", "pick_count": 1}],
			"options": [
				{"choice_group_key": group_key, "item_code": self.bread_b, "price_adjustment": 0.0, "max_per_option": 0},
				{"choice_group_key": group_key, "item_code": self.bread_c, "price_adjustment": 3000.0, "max_per_option": 0},
				{"choice_group_key": group_key, "item_code": self.bread_a, "price_adjustment": 1000.0, "max_per_option": 0},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		doc.update(overrides)
		self.promo = frappe.get_doc(doc).insert(ignore_permissions=True)
		self.group_key = self.promo.choice_groups[0].group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name
		self.option_a = self.promo.options[2].name
		return self.promo

	# -- helpers -----------------------------------------------------------

	def _choices_b(self):
		return [{"choice_group_key": self.group_key, "options": [{"option_id": self.option_b, "qty": 1}]}]

	def _pending(self, instances):
		return json.dumps({"instances": instances})

	def _instance(self, option_row, quantity=None):
		row = {
			"promotion": self.promo.name,
			"selections": [{"group_key": self.group_key, "picks": [{"option_row": option_row, "qty": 1}]}],
		}
		if quantity is not None:
			row["quantity"] = quantity
		return row

	def _context(self):
		return {"warehouse": self.outlet_warehouse}

	def _new_invoice(self, pending=None, items=None):
		doc = {
			"doctype": "Sales Invoice",
			"is_pos": 1,
			"company": self.outlet_company,
			"pos_profile": self.pos_profile_name,
			"customer": self.customer_name,
			"posting_date": nowdate(),
			"currency": "IDR",
		}
		if pending is not None:
			doc["pos_pending_promotions"] = pending
		doc["items"] = items if items is not None else []
		doc["payments"] = [{"mode_of_payment": self.mop_name, "amount": 0}]
		return frappe.get_doc(doc)

	def _submit_paid(self, inv):
		inv.payments[0].amount = flt(inv.grand_total)
		inv.save()
		inv.submit()
		return inv

	def _snapshot_quantity(self, inv):
		row = inv.pos_promotion_selections[0]
		return flt(json.loads(row.snapshot).get("quantity"))

	def _stock_quantity(self, item_code):
		from erpnext.stock.utils import get_stock_balance

		return flt(get_stock_balance(item_code, self.outlet_warehouse))

	# -- pricing domain: qty 2 scales components + parent, rate stays per-unit -

	def test_pricing_quantity_two_scales_components_parent_quantity_rate_stays_per_unit(self):
		result1 = pricing.quote(self.promo, self._choices_b(), self._context(), quantity=1)
		result2 = pricing.quote(self.promo, self._choices_b(), self._context(), quantity=2)
		self.assertEqual(result1["total_price"], result2["total_price"])
		for row in result2["component_rows"]:
			self.assertEqual(flt(row["qty"]), 2.0)
		self.assertEqual(result2["parent_row"]["qty"], 2)
		self.assertEqual(flt(result2["parent_row"]["rate"]), result1["total_price"])
		self.assertEqual(flt(result2["parent_row"]["amount"]), flt(result2["parent_row"]["rate"]) * 2)

	def test_pricing_quantity_default_is_one(self):
		default = pricing.quote(self.promo, self._choices_b(), self._context())
		explicit = pricing.quote(self.promo, self._choices_b(), self._context(), quantity=1)
		self.assertEqual(default["quantity"], 1)
		self.assertEqual(default["parent_row"]["qty"], 1)
		self.assertEqual(flt(default["parent_row"]["amount"]), flt(default["total_price"]))
		self.assertEqual(default["total_price"], explicit["total_price"])

	def test_invoice_quantity_two_doubles_component_and_parent_rows_and_snapshot(self):
		pending = self._pending([self._instance(self.option_b, quantity=2)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		parent = next(r for r in inv.items if r.pos_promotion_role == "Promotion Parent")
		self.assertEqual(flt(parent.qty), 2)
		self.assertEqual(flt(parent.rate), 20000.0)
		self.assertEqual(flt(parent.amount), 40000.0)
		component_qtys = sorted(flt(r.qty) for r in inv.items if r.pos_promotion_role == "Promotion Component")
		self.assertEqual(component_qtys, [2.0, 2.0])
		self.assertEqual(flt(inv.get("pos_promotion_selections")[0].total_amount), 20000.0)
		self.assertEqual(self._snapshot_quantity(inv), 2.0)
		snapshot = json.loads(inv.get("pos_promotion_selections")[0].snapshot)
		self.assertEqual(flt(snapshot["fixed_components"][0]["qty"]), 1.0)
		for opt in snapshot["chosen_options"]:
			self.assertEqual(flt(opt["qty"]), 1.0)
		self.assertEqual(flt(inv.grand_total), 40000.0)

	def test_submit_quantity_two_deducts_double_per_component(self):
		before_a = self._stock_quantity(self.bread_a)
		before_b = self._stock_quantity(self.bread_b)
		pending = self._pending([self._instance(self.option_b, quantity=2)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		self.assertEqual(flt(inv.grand_total), 40000.0)

		def _sle_qty(item_code, invoice_name):
			rows = frappe.get_all(
				"Stock Ledger Entry",
				filters={"voucher_no": invoice_name, "item_code": item_code, "is_cancelled": 0},
				fields=["actual_qty"],
			)
			return sum(flt(r.actual_qty) for r in rows)

		self.assertEqual(_sle_qty(self.bread_a, inv.name), -2.0)
		self.assertEqual(_sle_qty(self.bread_b, inv.name), -2.0)
		self.assertEqual(self._stock_quantity(self.bread_a), before_a - 2.0)
		self.assertEqual(self._stock_quantity(self.bread_b), before_b - 2.0)

	def test_spec_two_of_one_flavour_at_quantity_two_consumes_four_units(self):
		before_a = self._stock_quantity(self.bread_a)
		before_b = self._stock_quantity(self.bread_b)
		pending = self._pending(
			[
				self._instance(self.option_b, quantity=2),
				self._instance(self.option_b, quantity=2),
			]
		)
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		self.assertEqual(len(inv.pos_promotion_selections), 2)
		self.assertEqual(flt(sum(flt(r.qty) for r in inv.items if r.pos_promotion_role == "Promotion Component")), 8)
		self.assertEqual(self._stock_quantity(self.bread_a), before_a - 4.0)
		self.assertEqual(self._stock_quantity(self.bread_b), before_b - 4.0)
		self.assertEqual(flt(inv.grand_total), 80000.0)

	# -- invalid quantities -------------------------------------------------

	def test_pricing_rejects_non_integer_quantities(self):
		for invalid in (0, -1, 1.5, True, "abc", "1.5", 0.0, None, ""):
			with self.subTest(invalid=invalid):
				with self.assertRaisesRegex(frappe.ValidationError, r"must be a positive integer"):
					pricing.quote(self.promo, self._choices_b(), self._context(), quantity=invalid)

	def test_invoice_rejects_non_integer_quantities(self):
		for invalid in (0, -1, 1.5, "abc"):
			with self.subTest(invalid=invalid):
				pending = self._pending([self._instance(self.option_b, quantity=invalid)])
				inv = self._new_invoice(pending=pending)
				with self.assertRaisesRegex(frappe.ValidationError, r"must be a positive integer"):
					inv.insert()

	def test_invoice_rejects_fractional_string_quantity(self):
		pending = self._pending([self._instance(self.option_b, quantity=" 1.5 ")])
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"must be a positive integer"):
			inv.insert()

	def test_invoice_accepts_string_integer_quantity(self):
		pending = self._pending([self._instance(self.option_b, quantity="2")])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		parent = next(r for r in inv.items if r.pos_promotion_role == "Promotion Parent")
		self.assertEqual(flt(parent.qty), 2)

	# -- cap vs summed quantity --------------------------------------------

	def test_cap_sums_quantities_not_instances_one_instance_qty_three_rejected(self):
		self.promo.max_instances_per_invoice = 2
		self.promo.save()
		pending = self._pending([self._instance(self.option_b, quantity=3)])
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 2 instance\(s\) per invoice"):
			inv.insert()
		self.assertEqual(len(inv.items), 0)

	def test_cap_across_instances_sum_two_plus_one_rejected_at_cap_two(self):
		self.promo.max_instances_per_invoice = 2
		self.promo.save()
		pending = self._pending(
			[self._instance(self.option_b, quantity=2), self._instance(self.option_c, quantity=1)]
		)
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 2 instance\(s\) per invoice"):
			inv.insert()

	def test_cap_allows_summed_quantity_within_limit(self):
		self.promo.max_instances_per_invoice = 3
		self.promo.save()
		pending = self._pending(
			[self._instance(self.option_b, quantity=2), self._instance(self.option_c, quantity=1)]
		)
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self.assertEqual(len(inv.pos_promotion_selections), 2)

	def test_two_qty_one_instances_name_the_limit(self):
		# Original cap regex still kills this — two qty-1 instances still count as 2.
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		pending = self._pending([self._instance(self.option_b), self._instance(self.option_c)])
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 1 instance\(s\) per invoice"):
			inv.insert()

