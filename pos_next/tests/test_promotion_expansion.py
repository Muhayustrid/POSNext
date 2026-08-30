"""Task 4 — Sales Invoice expansion and enforcement (I1, I2, I3, I8, I13, I15, I16 / G2, G7).

Covers:
- Bare parent fail-closed (I15)
- Rate and warehouse re-assertion (I1, I2, I13 / G2)
- Single expansion / draft re-save is no-op (I8)
- Immutability after submit (I3)
- Per-invoice instance cap (I16 / D19 / G7) including atomic over-cap,
  second-payload rejection, independent limits, and history stability.

Conventions:
- self.addCleanup(frappe.db.rollback) registered FIRST in setUp.
- Zero frappe.db.commit().
- Unique suffix per test run (promotions are undeletable once referenced).
"""

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionExpansion(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		# Environment pin (port mechanics, no assertion touched): this site's Stock Settings
		# enable auto_insert_price_list_rate_if_missing, which makes ERPNext's
		# get_item_details.insert_item_price record a selling Item Price for the promotion
		# parent when the priced combo line is submitted. That row violates the D12
		# precondition ("the promotion engine is the only writer of the parent row's rate",
		# promotion._validate_parent_item), so any post-sale promo.save() would throw. The
		# source bench ran with the setting off; pin it off here and restore on cleanup.
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
		# SalesInvoice.validate() requires an open POS Opening Entry even for a draft
		# insert (erpnext/accounts/doctype/pos_invoice/pos_invoice.py:210), so the
		# shift must exist before the first insert in every test.

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

		self.root_company = self._make_company("_Test Exp Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test Exp Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test Exp Outlet WH", self.outlet_company)
		# Second warehouse for warehouse-mismatch tests (same company, different warehouse)
		self.other_warehouse = self._make_warehouse("_Test Exp Other WH", self.outlet_company)

	def _setup_items(self):
		self.parent_item = f"_Test Exp Parent {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.parent_item,
				"item_name": "Exp Parent",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

		self.bread_a = f"_Test Exp Bread A {self.suffix}"
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

		self.bread_b = f"_Test Exp Bread B {self.suffix}"
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

		self.bread_c = f"_Test Exp Bread C {self.suffix}"
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

		# Stock for submit paths
		stock_entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.outlet_company,
				"items": [
					{
						"item_code": self.bread_a,
						"qty": 50,
						"t_warehouse": self.outlet_warehouse,
						"basic_rate": 5000,
					},
					{
						"item_code": self.bread_b,
						"qty": 50,
						"t_warehouse": self.outlet_warehouse,
						"basic_rate": 6000,
					},
					{
						"item_code": self.bread_c,
						"qty": 50,
						"t_warehouse": self.outlet_warehouse,
						"basic_rate": 8000,
					},
				],
			}
		).insert(ignore_permissions=True)
		stock_entry.submit()

	def _setup_pos_profile(self):
		self.customer_name = f"_Test Exp Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)

		self.pos_profile_name = f"_Test Exp POS Profile {self.suffix}"
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
			"promotion_name": f"Promo Exp {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item,
			"base_price": 20000.0,
			"currency": "IDR",
			"enabled": 1,
			"max_instances_per_invoice": 0,
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
					"item_code": self.bread_c,
					"price_adjustment": 3000.0,
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
		self.group_key = self.promo.choice_groups[0].group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name
		self.option_a = self.promo.options[2].name
		return self.promo

	def _make_second_promotion(self, parent_suffix="2", **overrides):
		parent_item = f"_Test Exp Parent2 {self.suffix}{parent_suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": parent_item,
				"item_name": "Exp Parent 2",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		group_key = f"grp2_{self.suffix}"
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo Exp2 {self.suffix}{parent_suffix}",
			"root_company": self.root_company,
			"parent_item": parent_item,
			"base_price": 15000.0,
			"currency": "IDR",
			"enabled": 1,
			"max_instances_per_invoice": 1,
			"components": [{"item_code": self.bread_a, "qty": 1.0}],
			"choice_groups": [{"group_key": group_key, "label": "Pilih", "pick_count": 1}],
			"options": [
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.bread_c,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		doc.update(overrides)
		promo2 = frappe.get_doc(doc).insert(ignore_permissions=True)
		return promo2

	def _pending(self, instances):
		return json.dumps({"instances": instances})

	def _instance(self, promo_name, option_row):
		return {
			"promotion": promo_name,
			"selections": [{"group_key": self.group_key, "picks": [{"option_row": option_row, "qty": 1}]}],
		}

	def _instance_for_promo2(self, promo2, option_row):
		gk = promo2.choice_groups[0].group_key
		return {
			"promotion": promo2.name,
			"selections": [{"group_key": gk, "picks": [{"option_row": option_row, "qty": 1}]}],
		}

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
		if items is not None:
			doc["items"] = items
		else:
			doc["items"] = []
		doc["payments"] = [{"mode_of_payment": self.mop_name, "amount": 0}]
		return frappe.get_doc(doc)

	def _submit_paid(self, inv):
		"""Pay the invoice in full and submit.

		ERPNext rejects a POS submit whose paid_amount is below the total
		(PartialPaymentValidationError), so the payment row is set from the
		server-calculated grand total rather than a hardcoded number.
		"""
		inv.payments[0].amount = flt(inv.grand_total)
		inv.save()
		inv.submit()
		return inv

	# --- I15 bare parent ---------------------------------------------------

	def test_bare_parent_row_fails_closed(self):
		inv = self._new_invoice(
			items=[
				{"item_code": self.parent_item, "qty": 1, "rate": 20000.0, "warehouse": self.outlet_warehouse}
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"cannot be sold on its own"):
			inv.insert()
		self.assertFalse(frappe.db.exists("Sales Invoice", {"pos_profile": self.pos_profile_name}))

	def test_bare_parent_with_role_but_no_selection_fails(self):
		inv = self._new_invoice(
			items=[
				{
					"item_code": self.parent_item,
					"qty": 1,
					"rate": 20000.0,
					"warehouse": self.outlet_warehouse,
					"pos_promotion_instance": "inst_fake",
					"pos_promotion_role": "Promotion Parent",
				}
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"no backing promotion selection"):
			inv.insert()
		self.assertFalse(frappe.db.exists("Sales Invoice", {"pos_profile": self.pos_profile_name}))

	def test_non_parent_item_standalone_is_allowed(self):
		inv = self._new_invoice(
			items=[{"item_code": self.bread_b, "qty": 1, "rate": 10000.0, "warehouse": self.outlet_warehouse}]
		)
		inv.insert()
		self.assertEqual(len(inv.items), 1)

	# --- G2 rate re-assertion ----------------------------------------------

	def test_parent_rate_reassertion_after_manual_rewrite(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		parent = next(
			r for r in inv.items if r.pos_promotion_role == "Promotion Parent"
		)
		self.assertEqual(flt(parent.rate), 20000.0)
		parent.rate = 99999.0
		parent.amount = 99999.0
		inv.save()
		parent_after = next(
			r for r in inv.items if r.pos_promotion_role == "Promotion Parent"
		)
		self.assertEqual(flt(parent_after.rate), 20000.0)
		# The recalculated total must follow the restored rate, not the rewritten one.
		self.assertEqual(flt(inv.grand_total), 20000.0)
		self.assertEqual(flt(frappe.db.get_value("Sales Invoice Item", parent_after.name, "rate")), 20000.0)

	def test_component_zero_rate_reassertion_after_manual_rewrite(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		for row in inv.items:
			if row.pos_promotion_role == "Promotion Component":
				row.rate = 999.0
				row.amount = 999.0
		inv.save()
		component_rows = [
			r for r in inv.items if r.pos_promotion_role == "Promotion Component"
		]
		self.assertEqual(len(component_rows), 2)
		for row in component_rows:
			self.assertEqual(flt(row.rate), 0.0)
			self.assertEqual(flt(row.amount), 0.0)
			self.assertEqual(flt(frappe.db.get_value("Sales Invoice Item", row.name, "rate")), 0.0)
		# Zero-revenue components must not leak into the invoice total either.
		self.assertEqual(flt(inv.grand_total), 20000.0)

	def test_warehouse_reassertion_after_manual_change(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		for row in inv.items:
			if row.pos_promotion_role in ("Promotion Parent", "Promotion Component"):
				row.warehouse = self.other_warehouse
		inv.save()
		promotion_rows = [r for r in inv.items if r.pos_promotion_role]
		self.assertEqual(len(promotion_rows), 3)
		for row in promotion_rows:
			self.assertEqual(row.warehouse, self.outlet_warehouse)

	def test_discount_and_margin_zeroed_on_reassertion(self):
		pending = self._pending([self._instance(self.promo.name, self.option_c)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		# option_c carries a 3,000 upcharge on the 20,000 base
		self.assertEqual(flt(inv.pos_promotion_selections[0].total_amount), 23000.0)
		for row in inv.items:
			if row.pos_promotion_role:
				row.discount_percentage = 10.0
				row.discount_amount = 1000.0
				row.margin_type = "Percentage"
				row.margin_rate_or_amount = 5.0
		inv.save()
		for row in inv.items:
			if row.pos_promotion_role:
				self.assertEqual(flt(row.discount_percentage), 0.0)
				self.assertEqual(flt(row.discount_amount), 0.0)
		self.assertEqual(flt(inv.grand_total), 23000.0)

	# --- I8 single expansion / draft re-save -------------------------------

	def test_draft_resave_without_new_payload_is_noop(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		# One instance = 1 parent + 1 fixed component + 1 chosen option component.
		self.assertEqual(len(inv.items), 3)
		self.assertEqual(len(inv.pos_promotion_selections), 1)
		total = flt(inv.grand_total)
		inv.save()
		self.assertEqual(len(inv.items), 3)
		self.assertEqual(len(inv.pos_promotion_selections), 1)
		self.assertEqual(flt(inv.grand_total), total)
		inv.save()
		self.assertEqual(len(inv.items), 3)
		self.assertEqual(len(inv.pos_promotion_selections), 1)
		self.assertEqual(flt(inv.grand_total), total)

	def test_second_payload_on_draft_with_selections_fails_closed(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		n_items = len(inv.items)
		n_sels = len(inv.pos_promotion_selections)
		# New pending payload on same draft must fail closed, nothing materialized
		inv.pos_pending_promotions = self._pending(
			[self._instance(self.promo.name, self.option_c)]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"existing promotion selections"):
			inv.save()
		# Reload and ensure counts unchanged (save threw, so rollback to before second payload)
		inv2 = frappe.get_doc("Sales Invoice", inv.name)
		self.assertEqual(len(inv2.items), n_items)
		self.assertEqual(len(inv2.pos_promotion_selections), n_sels)

	# --- payload input validation (Task 7 hardening) ------------------------

	def test_malformed_json_payload_is_rejected_with_named_error(self):
		# Deleting the JSON parse guard would let json.JSONDecodeError (a plain
		# ValueError) escape instead of the named ValidationError.
		inv = self._new_invoice(pending="{ instances :")
		with self.assertRaisesRegex(frappe.ValidationError, r"Invalid JSON in pending promotions payload"):
			inv.insert()
		self.assertFalse(frappe.db.exists("Sales Invoice", {"pos_profile": self.pos_profile_name}))

	def test_payload_instance_missing_promotion_is_rejected(self):
		# Deleting the promotion-key guard falls through to the not-found check,
		# whose message names None instead of stating the requirement.
		inv = self._new_invoice(pending=json.dumps({"instances": [{"selections": []}]}))
		with self.assertRaisesRegex(
			frappe.ValidationError, r"Promotion is required for every promotion instance"
		):
			inv.insert()
		self.assertFalse(frappe.db.exists("Sales Invoice", {"pos_profile": self.pos_profile_name}))

	# --- I3 immutability ---------------------------------------------------

	def test_post_submit_mutation_of_selections_throws(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		# Try to mutate selections after submit
		inv.pos_promotion_selections[0].total_amount = 99999.0
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			inv.save()
		self.assertEqual(
			flt(
				frappe.db.get_value(
					"POS Promotion Selection",
					inv.pos_promotion_selections[0].name,
					"total_amount",
				)
			),
			20000.0,
		)

	def test_post_submit_mutation_of_rate_throws_or_reasserts(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		parent = next(
			r for r in inv.items if r.pos_promotion_role == "Promotion Parent"
		)
		parent.rate = 1.0
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			inv.save()
		self.assertEqual(flt(frappe.db.get_value("Sales Invoice Item", parent.name, "rate")), 20000.0)

	# --- I16 per-invoice cap ------------------------------------------------

	def test_cap_zero_allows_multiple_instances(self):
		# Default promo has max 0 = unlimited
		pending = self._pending(
			[self._instance(self.promo.name, self.option_b), self._instance(self.promo.name, self.option_c)]
		)
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self.assertEqual(len(inv.pos_promotion_selections), 2)

	def test_cap_one_rejects_two_instances_atomically(self):
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		pending = self._pending(
			[self._instance(self.promo.name, self.option_b), self._instance(self.promo.name, self.option_c)]
		)
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 1 instance\(s\) per invoice"):
			inv.insert()
		self.assertEqual(len(inv.items), 0)
		self.assertFalse(
			frappe.db.exists(
				"Sales Invoice", {"pos_profile": self.pos_profile_name, "owner": frappe.session.user}
			)
		)

	def test_cap_two_rejects_three_atomically(self):
		self.promo.max_instances_per_invoice = 2
		self.promo.save()
		pending = self._pending(
			[
				self._instance(self.promo.name, self.option_b),
				self._instance(self.promo.name, self.option_c),
				self._instance(self.promo.name, self.option_a),
			]
		)
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 2 instance\(s\) per invoice"):
			inv.insert()
		self.assertEqual(len(inv.items), 0)
		self.assertEqual(frappe.db.count("POS Promotion Selection", {"promotion": self.promo.name}), 0)

	def test_cap_counts_instances_not_rows(self):
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		# Identical choices still count as two separate instances
		pending = self._pending(
			[self._instance(self.promo.name, self.option_b), self._instance(self.promo.name, self.option_b)]
		)
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"but 2 were requested"):
			inv.insert()

	def test_two_promotions_have_independent_limits(self):
		promo2 = self._make_second_promotion()
		# promo max 1, promo2 max 1 -> one instance each should be allowed
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		promo2_opt_b = promo2.options[0].name
		# Build payload with one instance of each promotion
		payload = {
			"instances": [
				self._instance(self.promo.name, self.option_b),
				self._instance_for_promo2(promo2, promo2_opt_b),
			]
		}
		inv = self._new_invoice(pending=json.dumps(payload))
		inv.insert()
		self.assertEqual(len(inv.pos_promotion_selections), 2)
		# Two instances of promo2 would fail, but one of each is independent
		payload2 = {
			"instances": [
				self._instance_for_promo2(promo2, promo2.options[0].name),
				self._instance_for_promo2(promo2, promo2.options[1].name),
			]
		}
		inv2 = self._new_invoice(pending=json.dumps(payload2))
		with self.assertRaisesRegex(frappe.ValidationError, rf"{promo2.name} allows at most 1 instance"):
			inv2.insert()

	def test_standalone_items_do_not_count_toward_cap(self):
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(
			pending=pending,
			items=[
				{"item_code": self.bread_b, "qty": 5, "rate": 10000.0, "warehouse": self.outlet_warehouse}
			],
		)
		inv.insert()
		self.assertEqual(len(inv.pos_promotion_selections), 1)
		# Standalone bread_b row exists alongside promo
		standalone = [r for r in inv.items if not r.pos_promotion_instance]
		self.assertEqual(len(standalone), 1)
		self.assertEqual(standalone[0].item_code, self.bread_b)

	def test_manual_duplicate_rows_cannot_bypass_selection_count(self):
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		# Manually add another parent row without selection — should fail via I15, not via cap
		inv.append(
			"items",
			{
				"item_code": self.parent_item,
				"qty": 1,
				"rate": 20000.0,
				"warehouse": self.outlet_warehouse,
				"pos_promotion_instance": "inst_manual",
				"pos_promotion_role": "Promotion Parent",
			},
		)
		# A manually duplicated parent row is rejected because it has no backing
		# selection (I15), not because the cap counted rows.
		with self.assertRaisesRegex(frappe.ValidationError, r"no backing promotion selection"):
			inv.save()

	def test_duplicate_parent_row_for_one_instance_fails(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		parent = next(
			r for r in inv.items if r.pos_promotion_role == "Promotion Parent"
		)
		inv.append(
			"items",
			{
				"item_code": self.parent_item,
				"qty": 1,
				"rate": 20000.0,
				"warehouse": self.outlet_warehouse,
				"pos_promotion_instance": parent.pos_promotion_instance,
				"pos_promotion_role": "Promotion Parent",
			},
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"already has a parent row"):
			inv.save()

	def test_promotion_row_with_instance_but_no_role_fails(self):
		inv = self._new_invoice(
			items=[
				{
					"item_code": self.bread_b,
					"qty": 1,
					"rate": 10000.0,
					"warehouse": self.outlet_warehouse,
					"pos_promotion_instance": "inst_orphan",
				}
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"both a promotion instance and a role"):
			inv.insert()

	def test_unknown_promotion_role_fails(self):
		# The engine validate hook runs before Frappe's own Select validation
		# (run_before_save_methods precedes _validate), so an unrecognized role
		# reaches this guard first.
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		component = next(
			r for r in inv.items if r.pos_promotion_role == "Promotion Component"
		)
		component.pos_promotion_role = "Promotion Bonus"
		with self.assertRaisesRegex(frappe.ValidationError, r"unknown promotion role"):
			inv.save()

	def test_parent_instance_without_component_rows_fails(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		inv.items = [
			r for r in inv.items if r.pos_promotion_role != "Promotion Component"
		]
		with self.assertRaisesRegex(frappe.ValidationError, r"carries no component rows"):
			inv.save()

	def test_over_cap_initial_payload_is_atomic_no_partial_rows(self):
		self.promo.max_instances_per_invoice = 2
		self.promo.save()
		pending = self._pending(
			[
				self._instance(self.promo.name, self.option_b),
				self._instance(self.promo.name, self.option_c),
				self._instance(self.promo.name, self.option_a),
			]
		)
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 2 instance\(s\) per invoice"):
			inv.insert()
		# The over-cap payload must leave the in-memory document untouched too:
		# no partial parent row, no partial selection.
		self.assertEqual(len(inv.items), 0)
		self.assertEqual(len(inv.get("pos_promotion_selections") or []), 0)
		self.assertEqual(frappe.db.count("POS Promotion Selection", {"promotion": self.promo.name}), 0)

	def test_lowering_cap_after_submit_does_not_invalidate_submitted(self):
		self.promo.max_instances_per_invoice = 2
		self.promo.save()
		pending = self._pending(
			[self._instance(self.promo.name, self.option_b), self._instance(self.promo.name, self.option_c)]
		)
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		submitted_total = flt(inv.grand_total)

		self.promo.max_instances_per_invoice = 1
		self.promo.save()

		# The submitted transaction keeps both instances and its total: the cap is a
		# materialization-time gate, never re-evaluated against history.
		reloaded = frappe.get_doc("Sales Invoice", inv.name)
		self.assertEqual(reloaded.docstatus, 1)
		self.assertEqual(len(reloaded.pos_promotion_selections), 2)
		self.assertEqual(flt(reloaded.grand_total), submitted_total)

		# The lowered cap does bind the next invoice, which proves the master change
		# took effect rather than being silently ignored.
		next_inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"at most 1 instance\(s\) per invoice"):
			next_inv.insert()

	def test_second_payload_after_selections_fails_closed_g7_point_11(self):
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		n_sels = len(inv.pos_promotion_selections)
		n_rows = len(inv.items)
		inv.pos_pending_promotions = self._pending(
			[self._instance(self.promo.name, self.option_c)]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"existing promotion selections"):
			inv.save()
		# Verify reload unchanged
		reloaded = frappe.get_doc("Sales Invoice", inv.name)
		self.assertEqual(len(reloaded.pos_promotion_selections), n_sels)
		self.assertEqual(len(reloaded.items), n_rows)

	# --- I5 eligibility gate at materialization -----------------------------

	def test_disabled_promotion_in_payload_is_rejected(self):
		self.promo.enabled = 0
		self.promo.save()
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"not eligible for this transaction"):
			inv.insert()
		self.assertEqual(len(inv.items), 0)

	def test_promotion_without_an_enabled_outlet_row_is_rejected(self):
		self.promo.outlets[0].enabled = 0
		self.promo.save()
		pending = self._pending([self._instance(self.promo.name, self.option_b)])
		inv = self._new_invoice(pending=pending)
		with self.assertRaisesRegex(frappe.ValidationError, r"not eligible for this transaction"):
			inv.insert()
		self.assertEqual(len(inv.items), 0)
