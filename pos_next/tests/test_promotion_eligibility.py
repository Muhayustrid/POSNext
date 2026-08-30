"""Promotion eligibility domain tests (Task 3 / Gate G6).

Tests fail-closed behaviour on every eligibility dimension:
- resolve_outlet_context with valid, invalid, missing, and misconfigured POS Profile
- eligibility.check:
  - Outlet row missing
  - Outlet row disabled (enabled = 0)
  - Outlet warehouse mismatch
  - Outlet company mismatch
  - Master disabled (enabled = 0)
  - on_date before valid_from
  - on_date after valid_to
  - on_date within valid_from / valid_to window (positive control)
  - Currency mismatch
  - Currency match (positive control)
- Independence of eligibility.check from per-invoice instance cap (max_instances_per_invoice).

Conventions:
- self.addCleanup(frappe.db.rollback) registered FIRST in setUp.
- Zero frappe.db.commit() calls.
- Unique suffix per test run.
"""

import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, getdate, nowdate

from pos_next.promotions import eligibility
from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)


def get_unique_suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionEligibility(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.suffix = get_unique_suffix()
		self._setup_companies_and_warehouses()
		self._setup_items()
		self._setup_pos_profile()
		self._setup_promotion_master()

	def _make_company(self, prefix, parent=None, currency="IDR", is_group=0):
		company_name = f"{prefix} {self.suffix}"
		abbr = self.suffix.upper()[:8]
		# suffix with unique counter for each company call to avoid abbr collisions
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
		self.root_company = self._make_company("_Test PE Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test PE Outlet Co", parent=self.root_company)
		self.other_company = self._make_company("_Test PE Other Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test PE Outlet WH", self.outlet_company)
		self.other_warehouse = self._make_warehouse("_Test PE Other WH", self.outlet_company)
		self.foreign_warehouse = self._make_warehouse("_Test PE Foreign WH", self.other_company)

	def _setup_items(self):
		self.parent_item = f"_Test PE Parent {self.suffix}"
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

		self.bread_a = f"_Test PE Bread A {self.suffix}"
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

		self.bread_b = f"_Test PE Bread B {self.suffix}"
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

	def _setup_pos_profile(self):
		self.customer_name = f"_Test PE Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)

		self.pos_profile_name = f"_Test PE POS Profile {self.suffix}"
		mop_name = get_default_mode_of_payment(self.outlet_company)
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
				"payments": [{"mode_of_payment": mop_name, "default": 1}],
				"write_off_account": write_off_account,
				"write_off_cost_center": write_off_cc,
				"income_account": income_account,
				"expense_account": write_off_account,
				"cost_center": write_off_cc,
				"write_off_limit": 1.0,
			}
		).insert(ignore_permissions=True)

	def _setup_promotion_master(self, **overrides):
		group_key = f"grp_{self.suffix}"
		today = nowdate()
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo Eligibility {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item,
			"base_price": 20000.0,
			"currency": "IDR",
			"enabled": 1,
			"valid_from": add_days(today, -5),
			"valid_to": add_days(today, 5),
			"max_instances_per_invoice": 2,
			"components": [{"item_code": self.bread_a, "qty": 1.0}],
			"choice_groups": [{"group_key": group_key, "label": "Pilih Roti", "pick_count": 1}],
			"options": [
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.bread_a,
					"price_adjustment": 1000.0,
					"max_per_option": 0,
				},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		doc.update(overrides)
		self.promo = frappe.get_doc(doc).insert(ignore_permissions=True)
		return self.promo

	# --- resolve_outlet_context tests -------------------------------------

	def test_resolve_outlet_context_valid_profile_name(self):
		company, warehouse = eligibility.resolve_outlet_context(self.pos_profile_name)
		self.assertEqual(company, self.outlet_company)
		self.assertEqual(warehouse, self.outlet_warehouse)

	def test_resolve_outlet_context_valid_profile_doc(self):
		company, warehouse = eligibility.resolve_outlet_context(self.pos_profile)
		self.assertEqual(company, self.outlet_company)
		self.assertEqual(warehouse, self.outlet_warehouse)

	def test_resolve_outlet_context_missing_or_empty(self):
		with self.assertRaisesRegex(frappe.ValidationError, r"POS Profile is required"):
			eligibility.resolve_outlet_context(None)
		with self.assertRaisesRegex(frappe.ValidationError, r"POS Profile is required"):
			eligibility.resolve_outlet_context("")

	def test_resolve_outlet_context_nonexistent_profile(self):
		with self.assertRaisesRegex(frappe.ValidationError, r"does not exist"):
			eligibility.resolve_outlet_context("NonExistentProfile_12345")

	def test_resolve_outlet_context_missing_company_or_warehouse(self):
		bad_profile_dict = {"company": None, "warehouse": self.outlet_warehouse}
		with self.assertRaisesRegex(frappe.ValidationError, r"company is not set"):
			eligibility.resolve_outlet_context(bad_profile_dict)

		bad_profile_dict2 = {"company": self.outlet_company, "warehouse": None}
		with self.assertRaisesRegex(frappe.ValidationError, r"warehouse is not set"):
			eligibility.resolve_outlet_context(bad_profile_dict2)

	# --- eligibility.check fail-closed dimension tests -------------------

	def test_eligibility_positive_control(self):
		"""Positive control: valid active promotion in valid window matches."""
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertTrue(is_el)
		self.assertEqual(reason, "")

	def test_eligibility_by_promotion_name_string(self):
		is_el, reason = eligibility.check(
			self.promo.name, self.outlet_company, self.outlet_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertTrue(is_el)
		self.assertEqual(reason, "")

	def test_eligibility_fails_on_master_disabled(self):
		self.promo.enabled = 0
		self.promo.save()
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertFalse(is_el)
		self.assertIn("disabled", reason.lower())

	def test_eligibility_fails_on_date_before_valid_from(self):
		past_date = add_days(self.promo.valid_from, -2)
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=past_date, currency="IDR"
		)
		self.assertFalse(is_el)
		self.assertIn("not yet valid", reason.lower())

	def test_eligibility_fails_on_date_after_valid_to(self):
		future_date = add_days(self.promo.valid_to, 2)
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=future_date, currency="IDR"
		)
		self.assertFalse(is_el)
		self.assertIn("expired", reason.lower())

	def test_eligibility_fails_on_currency_mismatch(self):
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=nowdate(), currency="USD"
		)
		self.assertFalse(is_el)
		self.assertIn("currency mismatch", reason.lower())

	def test_eligibility_fails_on_open_ended_promotion_currency_mismatch(self):
		"""Open-ended promotion (valid_to=None) must fail-closed on currency mismatch."""
		self.promo.valid_to = None
		self.promo.save()
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=nowdate(), currency="USD"
		)
		self.assertFalse(is_el)
		self.assertIn("currency mismatch", reason.lower())

	def test_eligibility_fails_on_outlet_row_missing_company(self):
		# Other company (even with matching warehouse name) is not configured in promo.outlets
		is_el, reason = eligibility.check(
			self.promo, self.other_company, self.outlet_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertFalse(is_el)
		self.assertIn("not configured", reason.lower())

	def test_eligibility_fails_on_foreign_company_and_warehouse(self):
		# Other company with foreign warehouse is not configured in promo.outlets
		is_el, reason = eligibility.check(
			self.promo, self.other_company, self.foreign_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertFalse(is_el)
		self.assertIn("not configured", reason.lower())

	def test_eligibility_fails_on_outlet_row_missing_warehouse(self):
		# Outlet company matches, but warehouse is not the configured one
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.other_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertFalse(is_el)
		self.assertIn("not configured", reason.lower())

	def test_eligibility_fails_on_outlet_row_disabled(self):
		# Set outlet row enabled = 0
		self.promo.outlets[0].enabled = 0
		self.promo.save()
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertFalse(is_el)
		self.assertIn("disabled for this promotion", reason.lower())

	def test_eligibility_independent_from_max_instances_per_invoice(self):
		"""D19 / plan item 1: per-invoice instance cap is NOT an eligibility dimension."""
		# Promo has max_instances_per_invoice = 2
		self.assertEqual(self.promo.max_instances_per_invoice, 2)
		is_el, reason = eligibility.check(
			self.promo, self.outlet_company, self.outlet_warehouse, on_date=nowdate(), currency="IDR"
		)
		self.assertTrue(is_el)
		self.assertEqual(reason, "")
