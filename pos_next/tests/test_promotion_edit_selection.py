"""Task 4.7 - editing a selection in place replaces components, keeps identity.

A payload instance carrying ``replace_instance: <instance_id>`` re-selects the
options of an existing instance on the same draft: the engine drops that
instance's rows and selection, regenerates them from the new picks under the
SAME instance id, and leaves the stored quantity pinned. Payload instances
without the key still hit the I8 refusal on a draft with existing selections.

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

from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionEditSelection(IntegrationTestCase):
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
		self.root_company = self._make_company("_Test Es Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test Es Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test Es Outlet WH", self.outlet_company)

	def _make_item(self, item_code, item_name, is_stock_item):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": item_code,
				"item_name": item_name,
				"item_group": "All Item Groups",
				"is_stock_item": is_stock_item,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

	def _setup_items(self):
		self.parent_item = f"_Test Es Parent {self.suffix}"
		self._make_item(self.parent_item, "Es Parent", 0)
		# A second parent item for the cross-Promotion replacement test: the
		# master guard allows only one enabled Promotion per parent item.
		self.parent_item2 = f"_Test Es Parent2 {self.suffix}"
		self._make_item(self.parent_item2, "Es Parent2", 0)
		self.bread_a = f"_Test Es Bread A {self.suffix}"
		self._make_item(self.bread_a, "Es Roti A", 1)
		self.bread_b = f"_Test Es Bread B {self.suffix}"
		self._make_item(self.bread_b, "Es Roti B", 1)
		self.bread_c = f"_Test Es Bread C {self.suffix}"
		self._make_item(self.bread_c, "Es Roti C", 1)
		# Task 4.7 core case: a second zero-adjustment option so an in-place
		# edit can change the pick WITHOUT changing the parent price.
		self.bread_d = f"_Test Es Bread D {self.suffix}"
		self._make_item(self.bread_d, "Es Roti D", 1)
		self.bread_x = f"_Test Es Bread X {self.suffix}"
		self._make_item(self.bread_x, "Es Roti X", 1)
		self.bread_y = f"_Test Es Bread Y {self.suffix}"
		self._make_item(self.bread_y, "Es Roti Y", 1)
		stock_entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.outlet_company,
				"items": [
					{"item_code": self.bread_a, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 5000},
					{"item_code": self.bread_b, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 6000},
					{"item_code": self.bread_c, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 8000},
					{"item_code": self.bread_d, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 6000},
					{"item_code": self.bread_x, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 5000},
					{"item_code": self.bread_y, "qty": 50, "t_warehouse": self.outlet_warehouse, "basic_rate": 5000},
				],
			}
		).insert(ignore_permissions=True)
		stock_entry.submit()

	def _setup_pos_profile(self):
		self.customer_name = f"_Test Es Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)
		self.pos_profile_name = f"_Test Es POS Profile {self.suffix}"
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
			"promotion_name": f"Promo Es {self.suffix}",
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
				{"choice_group_key": group_key, "item_code": self.bread_d, "price_adjustment": 0.0, "max_per_option": 0},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		doc.update(overrides)
		self.promo = frappe.get_doc(doc).insert(ignore_permissions=True)
		self.group_key = self.promo.choice_groups[0].group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name
		self.option_a = self.promo.options[2].name
		self.option_d = self.promo.options[3].name
		return self.promo

	def _setup_second_promotion(self):
		group_key = f"grp2_{self.suffix}"
		self.promo2 = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"Promo Es Two {self.suffix}",
				"root_company": self.root_company,
				"parent_item": self.parent_item2,
				"base_price": 15000.0,
				"currency": "IDR",
				"enabled": 1,
				"max_instances_per_invoice": 0,
				"choice_groups": [{"group_key": group_key, "label": "Pilih Lagi", "pick_count": 1}],
				"options": [
					{"choice_group_key": group_key, "item_code": self.bread_x, "price_adjustment": 0.0, "max_per_option": 0},
					{"choice_group_key": group_key, "item_code": self.bread_y, "price_adjustment": 0.0, "max_per_option": 0},
				],
				"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		self.group_key2 = self.promo2.choice_groups[0].group_key
		self.option_x = self.promo2.options[0].name
		return self.promo2

	# -- helpers -----------------------------------------------------------

	def _pending(self, instances):
		return json.dumps({"instances": instances})

	def _instance(self, option_row, quantity=None, replace_instance=None, promotion=None, group_key=None):
		row = {
			"promotion": promotion or self.promo.name,
			"selections": [
				{"group_key": group_key or self.group_key, "picks": [{"option_row": option_row, "qty": 1}]}
			],
		}
		if quantity is not None:
			row["quantity"] = quantity
		if replace_instance is not None:
			row["replace_instance"] = replace_instance
		return row

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

	def _insert_draft(self, instances):
		inv = self._new_invoice(pending=self._pending(instances))
		inv.insert()
		return inv

	def _parent_row(self, inv):
		return next(r for r in inv.items if r.pos_promotion_role == "Promotion Parent")

	def _component_codes(self, inv):
		return sorted(r.item_code for r in inv.items if r.pos_promotion_role == "Promotion Component")

	# -- (a) core case: in-place edit swaps components, parent unchanged ----

	def test_replace_same_adjustment_keeps_parent_price_qty_and_instance(self):
		inv = self._insert_draft([self._instance(self.option_b)])
		instance_id = inv.pos_promotion_selections[0].instance_id
		parent_before = self._parent_row(inv)
		self.assertEqual(flt(parent_before.rate), 20000.0)
		self.assertEqual(self._component_codes(inv), [self.bread_a, self.bread_b])

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_d, replace_instance=instance_id)]
		)
		inv.save()

		self.assertEqual(self._component_codes(inv), [self.bread_a, self.bread_d])
		parent = self._parent_row(inv)
		self.assertEqual(flt(parent.rate), 20000.0)
		self.assertEqual(flt(parent.qty), 1.0)
		self.assertEqual(parent.pos_promotion_instance, instance_id)
		self.assertEqual(len(inv.pos_promotion_selections), 1)
		self.assertEqual(inv.pos_promotion_selections[0].instance_id, instance_id)
		self.assertEqual(flt(inv.pos_promotion_selections[0].total_amount), 20000.0)

	def test_replace_with_different_adjustment_reprices_parent(self):
		# Honest engine behaviour: the parent rate is re-asserted from the new
		# selection's total_amount, so picking a priced option re-prices the
		# parent even though quantity and identity are unchanged.
		inv = self._insert_draft([self._instance(self.option_b)])
		instance_id = inv.pos_promotion_selections[0].instance_id

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_c, replace_instance=instance_id)]
		)
		inv.save()

		self.assertEqual(self._component_codes(inv), [self.bread_a, self.bread_c])
		parent = self._parent_row(inv)
		self.assertEqual(flt(parent.rate), 23000.0)
		self.assertEqual(flt(parent.qty), 1.0)
		self.assertEqual(parent.pos_promotion_instance, instance_id)
		self.assertEqual(flt(inv.pos_promotion_selections[0].total_amount), 23000.0)

	# -- (b) untouched second instance --------------------------------------

	def test_replace_leaves_untouched_second_instance_alone(self):
		inv = self._insert_draft([self._instance(self.option_b), self._instance(self.option_d)])
		id_first = inv.pos_promotion_selections[0].instance_id
		id_second = inv.pos_promotion_selections[1].instance_id
		snapshot_second = inv.pos_promotion_selections[1].snapshot
		total_second = flt(inv.pos_promotion_selections[1].total_amount)
		rows_second_before = sorted(
			(r.item_code, flt(r.qty), flt(r.rate), r.pos_promotion_role)
			for r in inv.items
			if r.pos_promotion_instance == id_second
		)

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_c, replace_instance=id_first)]
		)
		inv.save()

		self.assertEqual(len(inv.pos_promotion_selections), 2)
		by_id = {r.instance_id: r for r in inv.pos_promotion_selections}
		self.assertIn(id_first, by_id)
		self.assertIn(id_second, by_id)
		self.assertEqual(by_id[id_second].snapshot, snapshot_second)
		self.assertEqual(flt(by_id[id_second].total_amount), total_second)
		rows_second_after = sorted(
			(r.item_code, flt(r.qty), flt(r.rate), r.pos_promotion_role)
			for r in inv.items
			if r.pos_promotion_instance == id_second
		)
		self.assertEqual(rows_second_after, rows_second_before)

	# -- (c) I8 still applies without the replace key ------------------------

	def test_instance_without_replace_key_still_raises_i8(self):
		inv = self._insert_draft([self._instance(self.option_b)])

		inv.pos_pending_promotions = self._pending([self._instance(self.option_d)])
		with self.assertRaisesRegex(
			frappe.ValidationError,
			r"Cannot apply new promotion payload to an invoice with existing promotion selections",
		):
			inv.save()

	# -- (d) named replacement errors ----------------------------------------

	def test_replace_unknown_instance_rejected(self):
		inv = self._insert_draft([self._instance(self.option_b)])

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_d, replace_instance="inst_doesnotexist")]
		)
		with self.assertRaisesRegex(
			frappe.ValidationError,
			r"Promotion instance inst_doesnotexist does not exist on this invoice",
		):
			inv.save()

	def test_replace_same_instance_twice_in_one_payload_rejected(self):
		inv = self._insert_draft([self._instance(self.option_b)])
		instance_id = inv.pos_promotion_selections[0].instance_id

		inv.pos_pending_promotions = self._pending(
			[
				self._instance(self.option_d, replace_instance=instance_id),
				self._instance(self.option_c, replace_instance=instance_id),
			]
		)
		with self.assertRaisesRegex(
			frappe.ValidationError,
			r"Promotion instance " + instance_id + r" is replaced more than once in one payload",
		):
			inv.save()

	def test_replace_under_different_promotion_rejected(self):
		promo2 = self._setup_second_promotion()
		inv = self._insert_draft([self._instance(self.option_b)])
		instance_id = inv.pos_promotion_selections[0].instance_id

		inv.pos_pending_promotions = self._pending(
			[
				self._instance(
					self.option_x,
					replace_instance=instance_id,
					promotion=promo2.name,
					group_key=self.group_key2,
				)
			]
		)
		with self.assertRaisesRegex(
			frappe.ValidationError,
			r"Promotion instance "
			+ instance_id
			+ r" belongs to Promotion .* and cannot be re-selected under Promotion ",
		):
			inv.save()

	def test_replace_cannot_change_quantity(self):
		inv = self._insert_draft([self._instance(self.option_b, quantity=2)])
		instance_id = inv.pos_promotion_selections[0].instance_id

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_d, quantity=3, replace_instance=instance_id)]
		)
		with self.assertRaisesRegex(
			frappe.ValidationError,
			r"Promotion instance "
			+ instance_id
			+ r" was sold at quantity 2; editing its selection cannot change the quantity",
		):
			inv.save()

	# -- (e) quantity pinning, positive cases --------------------------------

	def test_replace_without_quantity_inherits_stored_quantity(self):
		inv = self._insert_draft([self._instance(self.option_b, quantity=2)])
		instance_id = inv.pos_promotion_selections[0].instance_id

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_d, replace_instance=instance_id)]
		)
		inv.save()

		parent = self._parent_row(inv)
		self.assertEqual(flt(parent.qty), 2.0)
		self.assertEqual(flt(parent.rate), 20000.0)
		self.assertEqual(flt(parent.amount), 40000.0)
		component_qtys = sorted(
			flt(r.qty) for r in inv.items if r.pos_promotion_role == "Promotion Component"
		)
		self.assertEqual(component_qtys, [2.0, 2.0])
		self.assertEqual(self._component_codes(inv), [self.bread_a, self.bread_d])
		snapshot = json.loads(inv.pos_promotion_selections[0].snapshot)
		self.assertEqual(flt(snapshot.get("quantity")), 2.0)

	def test_replace_with_same_quantity_accepted(self):
		inv = self._insert_draft([self._instance(self.option_b, quantity=2)])
		instance_id = inv.pos_promotion_selections[0].instance_id

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_d, quantity=2, replace_instance=instance_id)]
		)
		inv.save()

		parent = self._parent_row(inv)
		self.assertEqual(flt(parent.qty), 2.0)
		self.assertEqual(self._component_codes(inv), [self.bread_a, self.bread_d])
		self.assertEqual(inv.pos_promotion_selections[0].instance_id, instance_id)

	# -- (f) atomicity ---------------------------------------------------------

	def test_rejected_second_replacement_leaves_draft_exactly_as_before(self):
		inv = self._insert_draft([self._instance(self.option_b)])
		instance_id = inv.pos_promotion_selections[0].instance_id
		fresh_before = frappe.get_doc("Sales Invoice", inv.name)
		items_before = sorted(
			(r.item_code, flt(r.qty), flt(r.rate), r.pos_promotion_role, r.pos_promotion_instance)
			for r in fresh_before.items
		)
		snapshot_before = fresh_before.pos_promotion_selections[0].snapshot

		inv.pos_pending_promotions = self._pending(
			[
				self._instance(self.option_d, replace_instance=instance_id),
				self._instance(self.option_d, replace_instance="inst_doesnotexist"),
			]
		)
		with self.assertRaisesRegex(
			frappe.ValidationError,
			r"Promotion instance inst_doesnotexist does not exist on this invoice",
		):
			inv.save()

		fresh_after = frappe.get_doc("Sales Invoice", inv.name)
		items_after = sorted(
			(r.item_code, flt(r.qty), flt(r.rate), r.pos_promotion_role, r.pos_promotion_instance)
			for r in fresh_after.items
		)
		self.assertEqual(len(fresh_after.items), len(fresh_before.items))
		self.assertEqual(items_after, items_before)
		self.assertEqual(len(fresh_after.pos_promotion_selections), 1)
		self.assertEqual(fresh_after.pos_promotion_selections[0].instance_id, instance_id)
		self.assertEqual(fresh_after.pos_promotion_selections[0].snapshot, snapshot_before)

	# -- (g) identity ----------------------------------------------------------

	def test_replaced_instance_keeps_identity_on_selection_and_every_row(self):
		inv = self._insert_draft([self._instance(self.option_b)])
		instance_id = inv.pos_promotion_selections[0].instance_id

		inv.pos_pending_promotions = self._pending(
			[self._instance(self.option_d, replace_instance=instance_id)]
		)
		inv.save()

		self.assertEqual(len(inv.pos_promotion_selections), 1)
		self.assertEqual(inv.pos_promotion_selections[0].instance_id, instance_id)
		self.assertGreater(len(inv.items), 0)
		for row in inv.items:
			self.assertEqual(row.pos_promotion_instance, instance_id)
