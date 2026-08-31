"""Task 4.5 - fixed-component shortage names the item code, fail-closed pre-submit.

``engine._validate_promotion_stock`` runs at ``before_submit`` and refuses the
submission when an outlet warehouse lacks enough of a promotion's FIXED
component, naming the item code, warehouse, required and available quantity.
Chosen options keep ERPNext's generic per-row behaviour (out of scope). The
check is skipped when Stock Settings or the item allow negative stock, and
only fires when update_stock is on.

Mutation rule: deleting any single line of the guard must fail exactly one
test below that asserts on the engine's own message text ("Insufficient stock
for promotion component"), never ERPNext's generic NegativeStockError.

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


class TestPromotionComponentShortage(IntegrationTestCase):
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

	def _make_item(self, code, is_stock_item=1):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": "All Item Groups",
				"is_stock_item": is_stock_item,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		return code

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
		self.root_company = self._make_company("_Test Sh Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test Sh Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test Sh Outlet WH", self.outlet_company)

	def _stock_in(self, item_code, qty):
		if flt(qty) <= 0:
			return
		entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.outlet_company,
				"items": [
					{"item_code": item_code, "qty": qty, "t_warehouse": self.outlet_warehouse, "basic_rate": 5000}
				],
			}
		).insert(ignore_permissions=True)
		entry.submit()

	def _setup_items(self):
		self.parent_item = self._make_item(f"_Test Sh Parent {self.suffix}", is_stock_item=0)
		# Fixed component: deliberately scarce.
		self.bread_a = self._make_item(f"_Test Sh Bread A {self.suffix}")
		self.bread_b = self._make_item(f"_Test Sh Bread B {self.suffix}")
		self.bread_c = self._make_item(f"_Test Sh Bread C {self.suffix}")
		# Plenty of the option items so only the fixed component can run short.
		self._stock_in(self.bread_b, 50)
		self._stock_in(self.bread_c, 50)
		# A component created as a stock item and flipped to non-stock after the
		# Promotion is saved. It cannot be created non-stock up front:
		# Promotion._assert_physical_item (D13 / I12) rejects a non-stock
		# component on save, so this is the only reachable shape — see
		# test_non_stock_fixed_component_never_blocked.
		self.flippable_comp = self._make_item(f"_Test Sh NonStock {self.suffix}")

	def _setup_pos_profile(self):
		self.customer_name = f"_Test Sh Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)
		self.pos_profile_name = f"_Test Sh POS Profile {self.suffix}"
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

	def _setup_promotion(self, component_item=None, component_qty=1.0):
		group_key = f"grp_{self.suffix}"
		self.promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"Promo Sh {self.suffix}",
				"root_company": self.root_company,
				"parent_item": self.parent_item,
				"base_price": 20000.0,
				"currency": "IDR",
				"enabled": 1,
				"max_instances_per_invoice": 0,
				"components": [{"item_code": component_item or self.bread_a, "qty": component_qty}],
				"choice_groups": [{"group_key": group_key, "label": "Pilih Roti", "pick_count": 1}],
				"options": [
					{"choice_group_key": group_key, "item_code": self.bread_b, "price_adjustment": 0.0, "max_per_option": 0},
					{"choice_group_key": group_key, "item_code": self.bread_c, "price_adjustment": 3000.0, "max_per_option": 0},
				],
				"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		self.group_key = self.promo.choice_groups[0].group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name
		return self.promo

	# -- helpers -----------------------------------------------------------

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

	def _new_invoice(self, pending=None, update_stock=1):
		doc = {
			"doctype": "Sales Invoice",
			"is_pos": 1,
			"company": self.outlet_company,
			"pos_profile": self.pos_profile_name,
			"customer": self.customer_name,
			"posting_date": nowdate(),
			"currency": "IDR",
			"update_stock": update_stock,
			"items": [],
			"payments": [{"mode_of_payment": self.mop_name, "amount": 0}],
		}
		if pending is not None:
			doc["pos_pending_promotions"] = pending
		return frappe.get_doc(doc)

	def _submit_paid(self, inv):
		inv.payments[0].amount = flt(inv.grand_total)
		inv.save()
		inv.submit()
		return inv

	# -- tests -------------------------------------------------------------

	def test_insufficient_fixed_component_rejected_naming_item_code(self):
		# Only 1 unit of the fixed component exists; the sale needs 1 but we
		# hold 0, so the named-item pre-check must fire.
		self._stock_in(self.bread_a, 0)  # no-op; bread_a has zero stock
		pending = self._pending([self._instance(self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		with self.assertRaisesRegex(
			frappe.ValidationError, r"Insufficient stock for promotion component .*" + self.bread_a
		):
			inv.payments[0].amount = flt(inv.grand_total)
			inv.submit()

	def test_sufficient_fixed_component_submits(self):
		self._stock_in(self.bread_a, 5)
		pending = self._pending([self._instance(self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		self.assertEqual(inv.docstatus, 1)

	def test_quantity_two_requires_double_and_rejects_single_stock(self):
		# Exactly 1 unit present: a qty-2 instance needs 2, so it must be
		# rejected naming the component and the required quantity.
		self._stock_in(self.bread_a, 1)
		pending = self._pending([self._instance(self.option_b, quantity=2)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		with self.assertRaisesRegex(
			frappe.ValidationError, r"Insufficient stock for promotion component .*" + self.bread_a
		):
			inv.payments[0].amount = flt(inv.grand_total)
			inv.submit()

	def test_quantity_two_with_double_stock_submits(self):
		self._stock_in(self.bread_a, 2)
		pending = self._pending([self._instance(self.option_b, quantity=2)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		self.assertEqual(inv.docstatus, 1)

	def test_allow_negative_stock_lets_shortage_through(self):
		"""The site's explicit tolerance for shortages must not be overridden by the pre-check.

		Construction note: the component is received once (1 unit at 5000) and
		then sold at quantity 3. The receipt is not there to remove the
		shortage — 3 > 1, so the guard would still fire if it ran — but to give
		the item a valuation rate. An item that has never been received has
		none, and ERPNext then rejects the submission with "Valuation Rate for
		the Item ... is required to do accounting entries", which would make
		this test pass for the wrong reason under a guard that was still
		blocking.
		"""
		neg_before = frappe.db.get_single_value("Stock Settings", "allow_negative_stock")
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
		self.addCleanup(
			frappe.db.set_single_value, "Stock Settings", "allow_negative_stock", neg_before
		)
		self._stock_in(self.bread_a, 1)
		pending = self._pending([self._instance(self.option_b, quantity=3)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		# No named-item shortage error: under the negative-stock setting
		# ERPNext's own path governs and the ledger is allowed to go negative.
		self._submit_paid(inv)
		self.assertEqual(inv.docstatus, 1)

	def test_non_stock_fixed_component_never_blocked(self):
		"""A fixed component that is not a stock item must not be balance-checked.

		Construction note: the component is swapped on the Promotion built in
		setUp rather than by inserting a second one — Promotion._validate_parent_item
		refuses a second enabled Promotion sharing a parent item — and the Item
		master is flipped to is_stock_item = 0 only after that save.
		Promotion._assert_physical_item (D13 / I12) rejects a non-stock
		component on save, so a Promotion carrying one cannot be built through
		the controller at all; the post-save flip is the only reachable state,
		and it is exactly the state the engine's is_stock_item skip exists for.
		The flipped item has zero balance, so without that skip the named-item
		shortage error would fire.
		"""
		self.promo.components[0].item_code = self.flippable_comp
		self.promo.save(ignore_permissions=True)
		frappe.db.set_value("Item", self.flippable_comp, "is_stock_item", 0, update_modified=False)
		frappe.clear_cache(doctype="Item")

		pending = self._pending([self._instance(self.option_b)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		self._submit_paid(inv)
		self.assertEqual(inv.docstatus, 1)

	def test_update_stock_off_skips_precheck(self):
		"""With update_stock off no ledger is touched, so the shortage check is skipped.

		Construction note: setting update_stock = 0 on the document is not
		enough. SalesInvoice.set_pos_fields (erpnext/accounts/doctype/
		sales_invoice/sales_invoice.py:1037), reached through validate() on
		every save, overwrites it with cint(POS Profile.update_stock), whose
		shipped default is 1 (pos_profile.json:334). The flag has to be turned
		off on the profile for the document to keep it.
		"""
		frappe.db.set_value("POS Profile", self.pos_profile_name, "update_stock", 0)
		frappe.clear_cache(doctype="POS Profile")

		pending = self._pending([self._instance(self.option_b)])
		inv = self._new_invoice(pending=pending, update_stock=0)
		inv.insert()
		self.assertEqual(inv.update_stock, 0)
		self._submit_paid(inv)
		self.assertEqual(inv.docstatus, 1)

	def test_message_names_warehouse_required_available(self):
		self._stock_in(self.bread_a, 1)
		pending = self._pending([self._instance(self.option_b, quantity=3)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		try:
			inv.payments[0].amount = flt(inv.grand_total)
			inv.submit()
			self.fail("expected shortage rejection")
		except frappe.ValidationError as exc:
			msg = str(exc)
			self.assertIn("Insufficient stock for promotion component", msg)
			self.assertIn(self.bread_a, msg)
			self.assertIn(self.outlet_warehouse, msg)
			self.assertIn("required 3", msg)
			self.assertIn("available 1", msg)

