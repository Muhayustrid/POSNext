"""Task 4.2 — Single-source promotion validation at the API boundary.

Every promotion input the server honors must pass through
``pos_pending_promotions → pricing.quote``. Nothing the client writes into
``pos_promotion_selections`` (or per-row markers) should survive or be trusted
at the ``update_invoice``/``submit_invoice`` boundary — the engine is the
single writer. The ``pos_pending_promotions`` field itself is the only valid
promotion input and is intentionally NOT stripped.

Covered here (a–e):
  (a) over pick_count
  (b) over max_per_option
  (c) foreign option (option row belongs to a different promotion/group)
  (d) max_instances_per_invoice breach
  (e) direct ``pos_promotion_selections`` injection stripped by _strip_server_managed_fields
      (no pos_pending_promotions → len(selections) == 0 after update_invoice)

Conventions:
- IntegrationTestCase, self.addCleanup(frappe.db.rollback) FIRST in setUp.
- Zero frappe.db.commit().
- Unique suffix per test run.

Refs: pricing.quote, engine._materialize_pending_promotions,
      engine._enforce_instance_cap, api/invoices._strip_server_managed_fields.
"""

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionApiValidation(IntegrationTestCase):
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
		self._setup_second_promotion()

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
		self.root_company = self._make_company("_Test Val Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test Val Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test Val Outlet WH", self.outlet_company)

	def _setup_items(self):
		self.parent_item = f"_Test Val Parent {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.parent_item,
				"item_name": "Val Parent",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

		self.bread_a = f"_Test Val Bread A {self.suffix}"
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

		self.bread_b = f"_Test Val Bread B {self.suffix}"
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

		self.bread_c = f"_Test Val Bread C {self.suffix}"
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
		self.customer_name = f"_Test Val Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)

		self.pos_profile_name = f"_Test Val POS Profile {self.suffix}"
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

	def _setup_promotion(self):
		group_key = f"grp_{self.suffix}"
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo Val {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item,
			"base_price": 20000.0,
			"currency": "IDR",
			"enabled": 1,
			"max_instances_per_invoice": 0,
			"components": [{"item_code": self.bread_a, "qty": 1.0}],
			"choice_groups": [{"group_key": group_key, "label": "Pilih Roti", "pick_count": 1}],
			"options": [
				{"choice_group_key": group_key, "item_code": self.bread_b, "price_adjustment": 0.0, "max_per_option": 1},
				{"choice_group_key": group_key, "item_code": self.bread_c, "price_adjustment": 3000.0, "max_per_option": 0},
				{"choice_group_key": group_key, "item_code": self.bread_a, "price_adjustment": 1000.0, "max_per_option": 0},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		self.promo = frappe.get_doc(doc).insert(ignore_permissions=True)
		self.group_key = self.promo.choice_groups[0].group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name
		self.option_a = self.promo.options[2].name

	def _setup_second_promotion(self):
		# Distinct promotion for the foreign-option vector: an option row that
		# belongs to this promo (and its distinct group_key) must be rejected
		# when presented as a choice for the first promotion.
		self.parent_item2 = f"_Test Val Parent2 {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.parent_item2,
				"item_name": "Val Parent 2",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		group_key2 = f"grp2_{self.suffix}"
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo Val2 {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item2,
			"base_price": 15000.0,
			"currency": "IDR",
			"enabled": 1,
			"max_instances_per_invoice": 0,
			"components": [{"item_code": self.bread_a, "qty": 1.0}],
			"choice_groups": [{"group_key": group_key2, "label": "Pilih 2", "pick_count": 1}],
			"options": [
				{"choice_group_key": group_key2, "item_code": self.bread_b, "price_adjustment": 0.0, "max_per_option": 0},
				{"choice_group_key": group_key2, "item_code": self.bread_c, "price_adjustment": 0.0, "max_per_option": 0},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		self.promo2 = frappe.get_doc(doc).insert(ignore_permissions=True)
		self.promo2_group_key = self.promo2.choice_groups[0].group_key
		self.promo2_option_b = self.promo2.options[0].name

	# -- helpers -----------------------------------------------------------

	def _pending(self, instances):
		return json.dumps({"instances": instances})

	def _instance(self, promo_name, option_row, qty=1):
		return {
			"promotion": promo_name,
			"selections": [{"group_key": self.group_key, "picks": [{"option_row": option_row, "qty": qty}]}],
		}

	def _base_payload(self, **overrides):
		payload = {
			"doctype": "Sales Invoice",
			"is_pos": 1,
			"company": self.outlet_company,
			"pos_profile": self.pos_profile_name,
			"customer": self.customer_name,
			"posting_date": nowdate(),
			"currency": "IDR",
			"items": [],
			"payments": [{"mode_of_payment": self.mop_name, "amount": 0}],
		}
		payload.update(overrides)
		return payload

	# -- a: over pick_count ----------------------------------------------

	def test_api_rejects_over_pick_count(self):
		# pick_count is 1; picking 2 must be rejected as over-pick via pricing.quote,
		# whether the call comes from quote_promotion or the invoice boundary.
		pending = self._pending([self._instance(self.promo.name, self.option_b, qty=2)])
		payload = self._base_payload(pos_pending_promotions=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"over-pick"):
			update_invoice(json.dumps(payload))

	# -- b: over max_per_option ------------------------------------------

	def test_api_rejects_over_max_per_option(self):
		# option_b has max_per_option 1; qty 2 must be rejected via pricing.quote.
		# Over-pick is checked before max_per_option, so pick_count is widened to 2
		# for this vector so the per-option cap is the gate that fires.
		self.promo.choice_groups[0].pick_count = 2
		self.promo.choice_groups[0].allow_repeats = 1
		self.promo.save()
		pending = self._pending([self._instance(self.promo.name, self.option_b, qty=2)])
		payload = self._base_payload(pos_pending_promotions=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"max_per_option"):
			update_invoice(json.dumps(payload))

	# -- c: foreign option ------------------------------------------------

	def test_api_rejects_foreign_option(self):
		# An option row from a different promotion must be rejected — it is not found
		# in the target promotion's option set, or does not belong to the group.
		pending = self._pending([self._instance(self.promo.name, self.promo2_option_b, qty=1)])
		payload = self._base_payload(pos_pending_promotions=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"not found|does not belong"):
			update_invoice(json.dumps(payload))

	# -- d: max_instances_per_invoice breach ------------------------------

	def test_api_rejects_max_instances_breach(self):
		# Capping the promotion at 1 instance per invoice; posting 2 instances in a
		# single payload must be rejected by _enforce_instance_cap before any row
		# is materialized. This is the same gate quote_promotion relies on.
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		pending = self._pending(
			[
				self._instance(self.promo.name, self.option_b, qty=1),
				self._instance(self.promo.name, self.option_c, qty=1),
			]
		)
		payload = self._base_payload(pos_pending_promotions=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 1 instance\(s\) per invoice"):
			update_invoice(json.dumps(payload))

	# -- submit path also goes through the same core --------------------

	def test_api_rejects_over_pick_count_via_submit(self):
		# Same over-pick payload as (a), but through submit_invoice. The
		# rejection still comes from pricing.quote, not a separate submit-only
		# guard, so popping the validation would break both call sites.
		pending = self._pending([self._instance(self.promo.name, self.option_b, qty=2)])
		payload = self._base_payload(pos_pending_promotions=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"over-pick"):
			submit_invoice(invoice=json.dumps(payload), data=json.dumps({}))

	# -- e: direct selections injection stripped ---------------------------

	def test_direct_selections_injection_is_stripped(self):
		# A forged pos_promotion_selections must not survive update_invoice.
		# When no pos_pending_promotions drives materialization, the stored
		# document must have zero selections — _strip_server_managed_fields is
		# the boundary that enforces this (defensively also drops total_amount/
		# snapshot inside any injected row). A real line item keeps the draft
		# valid so set_total_in_words does not blow up on a zero-row invoice.
		payload = self._base_payload(
			pos_promotion_selections=[
				{
					"promotion": self.promo.name,
					"instance_id": "inst_forged",
					"total_amount": 99999,
					"snapshot": json.dumps({"forged": True}),
				}
			],
			items=[
				{
					"item_code": self.bread_b,
					"qty": 1,
					"rate": 10000,
					"warehouse": self.outlet_warehouse,
				}
			],
		)
		draft = update_invoice(json.dumps(payload))
		doc = frappe.get_doc("Sales Invoice", draft["name"])
		self.assertEqual(len(doc.get("pos_promotion_selections") or []), 0)
		self.assertEqual(len(draft.get("pos_promotion_selections") or []), 0)
		# Honest row survived; injected selection did not.
		self.assertEqual(len(doc.items or []), 1)

