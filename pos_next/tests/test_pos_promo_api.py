"""POS promotion picker HTTP facade tests.

Covers ``pos_next.overrides.pos_promo_api``:
- permission gate (Promotion read; no-role user is refused)
- delegation: available list is outlet-filtered, detail carries group/option
  identities usable in quotes, quote equals the domain quote
- JSON-string choices normalization and named rejection of non-list choices
- end-to-end: an instance quoted through the wrapper materializes via the
  engine when the payload is set on a Sales Invoice draft

Conventions:
- self.addCleanup(frappe.db.rollback) registered FIRST in setUp.
- Zero frappe.db.commit().
- Unique suffix per test run (promotions are undeletable once referenced).
"""

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from pos_next.overrides import pos_promo_api
from pos_next.promotions import pricing
from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPosPromoApi(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.suffix = _suffix()
		self._setup_org()
		self._setup_items()
		self._setup_profile_and_customer()
		self._setup_promotion_a()
		self._setup_promotion_b_other_outlet()

	# --- fixtures -----------------------------------------------------------

	def _make_company(self, prefix, abbr_tag, is_group=0, parent=None):
		company_name = f"{prefix} {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company_name,
				"abbr": f"{self.suffix.upper()[:6]}{abbr_tag}",
				"is_group": is_group,
				"parent_company": parent,
				"default_currency": "IDR",
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)
		return company_name

	def _make_warehouse(self, prefix, company):
		return (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"{prefix} {self.suffix}",
					"company": company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _setup_org(self):
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

		self.root_company = self._make_company("_Test PA Root Co", "R", is_group=1)
		self.outlet_company = self._make_company("_Test PA Outlet Co", "O", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test PA Outlet WH", self.outlet_company)
		self.other_warehouse = self._make_warehouse("_Test PA Other WH", self.outlet_company)

	def _make_item(self, code, stock):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": "All Item Groups",
				"is_stock_item": stock,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		return code

	def _setup_items(self):
		self.parent_a = self._make_item(f"_Test PA Parent A {self.suffix}", 0)
		self.parent_b = self._make_item(f"_Test PA Parent B {self.suffix}", 0)
		self.bread_a = self._make_item(f"_Test PA Bread A {self.suffix}", 1)
		self.bread_b = self._make_item(f"_Test PA Bread B {self.suffix}", 1)

	def _setup_profile_and_customer(self):
		self.customer_name = f"_Test PA Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)

		if not frappe.db.exists("Price List", "Standard Selling"):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": "Standard Selling",
					"selling": 1,
					"currency": "IDR",
				}
			).insert(ignore_permissions=True)

		self.mop_name = get_default_mode_of_payment(self.outlet_company)
		write_off = get_default_account(self.outlet_company, "Expense")
		cc = get_default_cost_center(self.outlet_company)
		income = (
			frappe.db.get_value(
				"Account",
				{"company": self.outlet_company, "root_type": "Income", "is_group": 0},
				"name",
				order_by="creation asc",
			)
			or write_off
		)
		self.pos_profile_name = f"_Test PA POS Profile {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": self.pos_profile_name,
				"company": self.outlet_company,
				"warehouse": self.outlet_warehouse,
				"customer": self.customer_name,
				"currency": "IDR",
				"selling_price_list": "Standard Selling",
				"payments": [{"mode_of_payment": self.mop_name, "default": 1}],
				"write_off_account": write_off,
				"write_off_cost_center": cc,
				"income_account": income,
				"expense_account": write_off,
				"cost_center": cc,
				"write_off_limit": 1.0,
			}
		).insert(ignore_permissions=True)

	def _make_promotion(self, name, parent_item, outlet_warehouse, base_price=20000.0):
		group_key = f"grp_{self.suffix}_{name[-1].lower()}"
		promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": name,
				"root_company": self.root_company,
				"parent_item": parent_item,
				"base_price": base_price,
				"currency": "IDR",
				"enabled": 1,
				"max_instances_per_invoice": 0,
				"components": [{"item_code": self.bread_a, "qty": 1.0}],
				"choice_groups": [{"group_key": group_key, "label": "Pilih Roti", "pick_count": 1}],
				"options": [
					{"choice_group_key": group_key, "item_code": self.bread_b, "price_adjustment": 0.0},
					{"choice_group_key": group_key, "item_code": self.bread_a, "price_adjustment": 1000.0},
				],
				"outlets": [{"company": self.outlet_company, "warehouse": outlet_warehouse, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		return promo

	def _setup_promotion_a(self):
		self.promo_a = self._make_promotion(f"Promo PA A {self.suffix}", self.parent_a, self.outlet_warehouse)
		self.group_key_a = self.promo_a.choice_groups[0].group_key
		self.option_free = self.promo_a.options[0].name
		self.option_paid = self.promo_a.options[1].name

	def _setup_promotion_b_other_outlet(self):
		# Same company, different warehouse: never eligible for the profile above.
		self.promo_b = self._make_promotion(f"Promo PA B {self.suffix}", self.parent_b, self.other_warehouse)

	# --- permission gate ------------------------------------------------------

	def _no_permission_user(self):
		email = f"posapi-noperm-{self.suffix}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "POS API NoPerm",
				"send_welcome_email": 0,
			}
		).insert(ignore_permissions=True)
		return email

	def test_no_role_user_is_refused_for_all_three_endpoints(self):
		user = self._no_permission_user()
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			pos_promo_api.get_available_promotions(self.pos_profile_name)
		with self.assertRaises(frappe.PermissionError):
			pos_promo_api.get_promotion_detail(self.promo_a.name, self.pos_profile_name)
		with self.assertRaises(frappe.PermissionError):
			pos_promo_api.quote_promotion(self.promo_a.name, [], self.pos_profile_name)

	def test_posnext_cashier_can_use_all_three_endpoints(self):
		if not frappe.db.exists("Role", "POSNext Cashier"):
			frappe.get_doc(
				{
					"doctype": "Role",
					"role_name": "POSNext Cashier",
					"desk_access": 0,
				}
			).insert(ignore_permissions=True)
		user = self._no_permission_user()
		user_doc = frappe.get_doc("User", user)
		user_doc.append("roles", {"role": "POSNext Cashier"})
		user_doc.save(ignore_permissions=True)
		profile = frappe.get_doc("POS Profile", self.pos_profile_name)
		profile.append("applicable_for_users", {"user": user, "default": 1})
		profile.save(ignore_permissions=True)
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.clear_cache(user=user)
		frappe.set_user(user)

		available = pos_promo_api.get_available_promotions(self.pos_profile_name)
		self.assertIn(self.promo_a.name, {row["promotion"] for row in available})
		detail = pos_promo_api.get_promotion_detail(self.promo_a.name, self.pos_profile_name)
		self.assertTrue(detail["eligibility"]["is_eligible"])
		quote = pos_promo_api.quote_promotion(
			self.promo_a.name,
			json.dumps(
				[
					{
						"choice_group_key": self.group_key_a,
						"options": [{"option_id": self.option_free, "qty": 1}],
					}
				]
			),
			self.pos_profile_name,
		)
		self.assertEqual(quote["total_price"], 20000.0)

	# --- delegation ------------------------------------------------------------

	def test_available_promotions_is_outlet_filtered(self):
		available = pos_promo_api.get_available_promotions(self.pos_profile_name)
		names = [row["promotion"] for row in available]
		self.assertIn(self.promo_a.name, names)
		self.assertNotIn(self.promo_b.name, names)

	def test_detail_exposes_option_identities_and_eligibility(self):
		detail = pos_promo_api.get_promotion_detail(self.promo_a.name, self.pos_profile_name)
		self.assertTrue(detail["eligibility"]["is_eligible"])
		group = detail["choice_groups"][0]
		self.assertEqual(group["group_key"], self.group_key_a)
		self.assertEqual({opt["name"] for opt in group["options"]}, {self.option_free, self.option_paid})

		wrong = pos_promo_api.get_promotion_detail(self.promo_b.name, self.pos_profile_name)
		self.assertFalse(wrong["eligibility"]["is_eligible"])
		self.assertTrue(wrong["eligibility"]["reason"])

	def test_quote_wrapper_accepts_json_string_and_equals_domain_quote(self):
		choices = [
			{
				"choice_group_key": self.group_key_a,
				"options": [{"option_id": self.option_paid, "qty": 1}],
			}
		]
		wrapped = pos_promo_api.quote_promotion(self.promo_a.name, json.dumps(choices), self.pos_profile_name)
		direct = pricing.quote(
			self.promo_a, choices, {"company": self.outlet_company, "warehouse": self.outlet_warehouse}
		)
		self.assertEqual(wrapped["total_price"], direct["total_price"])
		self.assertEqual(wrapped["parent_row"], direct["parent_row"])
		self.assertEqual(wrapped["component_rows"], direct["component_rows"])
		self.assertEqual(wrapped["total_price"], 21000.0)

	def test_quote_wrapper_rejects_non_list_choices_with_named_error(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			pos_promo_api.quote_promotion(self.promo_a.name, {"group_key": "x"}, self.pos_profile_name)
		self.assertIn("must be a list", str(ctx.exception))

	def test_quote_wrapper_rejects_ineligible_promotion(self):
		choices = [
			{
				"choice_group_key": self.promo_b.choice_groups[0].group_key,
				"options": [{"option_id": self.promo_b.options[0].name, "qty": 1}],
			}
		]
		with self.assertRaises(frappe.ValidationError) as ctx:
			pos_promo_api.quote_promotion(self.promo_b.name, json.dumps(choices), self.pos_profile_name)
		self.assertIn("not eligible", str(ctx.exception))

	# --- end to end -------------------------------------------------------------

	def test_wrapper_quoted_instance_materializes_on_draft_save(self):
		choices = [
			{
				"choice_group_key": self.group_key_a,
				"options": [{"option_id": self.option_free, "qty": 1}],
			}
		]
		quote = pos_promo_api.quote_promotion(self.promo_a.name, json.dumps(choices), self.pos_profile_name)
		payload = {"instances": [{"promotion": self.promo_a.name, "selections": choices}]}

		inv = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"is_pos": 1,
				"company": self.outlet_company,
				"pos_profile": self.pos_profile_name,
				"customer": self.customer_name,
				"posting_date": nowdate(),
				"currency": "IDR",
				"items": [],
				"payments": [{"mode_of_payment": self.mop_name, "amount": 0}],
				"pos_pending_promotions": json.dumps(payload),
			}
		).insert(ignore_permissions=True)

		self.assertFalse(inv.get("pos_pending_promotions"))
		selections = inv.get("pos_promotion_selections")
		self.assertEqual(len(selections), 1)
		self.assertEqual(selections[0].total_amount, quote["total_price"])

		roles = {row.item_code: row.pos_promotion_role for row in inv.items}
		self.assertEqual(roles.get(self.parent_a), "Promotion Parent")
		self.assertEqual(roles.get(self.bread_a), "Promotion Component")
		self.assertEqual(roles.get(self.bread_b), "Promotion Component")
		parent_row = next(row for row in inv.items if row.item_code == self.parent_a)
		self.assertEqual(parent_row.rate, quote["total_price"])
