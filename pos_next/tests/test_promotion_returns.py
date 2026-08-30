"""Task 5 — Return semantics (I6 / D11 / G3, plus G7 point 10).

Covers:
- Full-instance return passes and reverses stock and refund exactly.
- Partial returns throw: one component dropped, one component short-qty.
- A standalone return (no ``return_against``) carrying promotion rows throws,
  including when it carries its own selection rows so that I15 would pass.
- A promotion return row that references no source row throws.
- A promotion instance absent from the source invoice throws.
- The source invoice's selections and snapshots are never rewritten.
- The sale-side instance cap is never re-checked on a return.

Conventions:
- self.addCleanup(frappe.db.rollback) registered FIRST in setUp.
- Zero frappe.db.commit().
- Unique suffix per test run (promotions are undeletable once referenced).
"""

import json
import uuid

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)

INSTANCE_FIELD = "pos_promotion_instance"
ROLE_FIELD = "pos_promotion_role"
PENDING_FIELD = "pos_pending_promotions"
SELECTIONS_FIELD = "pos_promotion_selections"


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionReturns(IntegrationTestCase):
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
		self._company_counter = 0
		self._setup_companies_and_warehouses()
		self._setup_items()
		self._setup_pos_profile()
		self._setup_promotion()

	# --- fixtures ----------------------------------------------------------

	def _make_company(self, prefix, parent=None, is_group=0):
		company_name = f"{prefix} {self.suffix}"
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

		self.root_company = self._make_company("_Test Ret Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test Ret Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test Ret Outlet WH", self.outlet_company)

	def _make_item(self, code, *, is_stock_item):
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

	def _setup_items(self):
		self.parent_item = self._make_item(f"_Test Ret Parent {self.suffix}", is_stock_item=0)
		self.bread_a = self._make_item(f"_Test Ret Bread A {self.suffix}", is_stock_item=1)
		self.bread_b = self._make_item(f"_Test Ret Bread B {self.suffix}", is_stock_item=1)
		self.bread_c = self._make_item(f"_Test Ret Bread C {self.suffix}", is_stock_item=1)

		stock_entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.outlet_company,
				"items": [
					{
						"item_code": code,
						"qty": 50,
						"t_warehouse": self.outlet_warehouse,
						"basic_rate": 5000,
					}
					for code in (self.bread_a, self.bread_b, self.bread_c)
				],
			}
		).insert(ignore_permissions=True)
		stock_entry.submit()

	def _setup_pos_profile(self):
		self.customer_name = f"_Test Ret Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)

		self.mop_name = get_default_mode_of_payment(self.outlet_company)
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

		self.pos_profile_name = f"_Test Ret POS Profile {self.suffix}"
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
		self.promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"Promo Ret {self.suffix}",
				"root_company": self.root_company,
				"parent_item": self.parent_item,
				"base_price": 20000.0,
				"currency": "IDR",
				"enabled": 1,
				"max_instances_per_invoice": 0,
				"components": [{"item_code": self.bread_a, "qty": 2.0}],
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
				],
				"outlets": [
					{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}
				],
			}
		).insert(ignore_permissions=True)
		self.group_key = group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name

	def _instance(self, option_row):
		return {
			"promotion": self.promo.name,
			"selections": [{"group_key": self.group_key, "picks": [{"option_row": option_row, "qty": 1}]}],
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
			"items": items if items is not None else [],
			"payments": [{"mode_of_payment": self.mop_name, "amount": 0}],
		}
		if pending is not None:
			doc[PENDING_FIELD] = pending
		return frappe.get_doc(doc)

	def _submit_paid(self, inv):
		inv.payments[0].amount = flt(inv.grand_total)
		inv.save()
		inv.submit()
		return inv

	def _repay(self, ret, amount):
		"""Restate the refund after rows were removed from a mapped return.

		``make_sales_return`` negates the source's full payment, so a return that
		deliberately carries fewer rows must restate it or ERPNext rejects the
		refund as larger than the grand total.
		"""
		ret.payments[0].amount = amount
		ret.paid_amount = amount
		return ret

	def _sell(self, options=(None,)):
		"""Submit one paid Sales Invoice carrying one instance per given option row."""
		option_rows = [opt or self.option_b for opt in options]
		pending = json.dumps({"instances": [self._instance(opt) for opt in option_rows]})
		inv = self._new_invoice(pending=pending)
		inv.insert()
		return self._submit_paid(inv)

	# --- I6 pass path: full-instance return --------------------------------

	def test_full_instance_return_passes_and_reverses_exactly(self):
		sale = self._sell()
		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()

		self.assertEqual(ret.docstatus, 1)
		self.assertEqual(len(ret.items), 3)
		# Model C: the parent alone carries revenue, both components stay at zero.
		parent = next(r for r in ret.items if r.get(ROLE_FIELD) == "Promotion Parent")
		self.assertEqual(flt(parent.qty), -1.0)
		self.assertEqual(flt(parent.rate), 20000.0)
		self.assertEqual(flt(parent.amount), -20000.0)
		for row in ret.items:
			if row.get(ROLE_FIELD) == "Promotion Component":
				self.assertEqual(flt(row.rate), 0.0)
				self.assertEqual(flt(row.amount), 0.0)
			self.assertEqual(row.warehouse, self.outlet_warehouse)
		self.assertEqual(flt(ret.grand_total), -20000.0)
		self.assertEqual(flt(ret.paid_amount), -20000.0)
		# Stock reversal is native: the return carries the negated component demand
		# and nothing for the non-stock parent. Its Stock Ledger Entries appear at
		# consolidation, which the model-C spike measured end to end pre-port.
		reversal = {row.item_code: flt(row.stock_qty) for row in ret.items}
		self.assertEqual(reversal[self.bread_a], -2.0)
		self.assertEqual(reversal[self.bread_b], -1.0)
		self.assertEqual(reversal[self.parent_item], -1.0)
		self.assertEqual(frappe.db.get_value("Item", self.parent_item, "is_stock_item"), 0)

	def test_full_return_of_two_instances_passes(self):
		sale = self._sell(options=(self.option_b, self.option_c))
		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()

		self.assertEqual(ret.docstatus, 1)
		self.assertEqual(len(ret.items), 6)
		# 20,000 + 23,000 sold, so the whole reversal is exactly -43,000.
		self.assertEqual(flt(ret.grand_total), -43000.0)

	def test_return_of_one_whole_instance_out_of_two_passes(self):
		"""Absent is as valid as complete: dropping every row of one instance is legal."""
		sale = self._sell(options=(self.option_b, self.option_c))
		ret = make_sales_return(sale.name)
		kept_instance = ret.items[0].get(INSTANCE_FIELD)
		ret.items = [row for row in ret.items if row.get(INSTANCE_FIELD) == kept_instance]
		self._repay(ret, -20000.0)
		ret.insert()
		ret.submit()

		self.assertEqual(ret.docstatus, 1)
		self.assertEqual(len(ret.items), 3)
		self.assertEqual(flt(ret.grand_total), -20000.0)

	# --- I6 fail paths -----------------------------------------------------

	def test_partial_return_is_blocked_at_submit_too(self):
		"""before_validate runs for the submit action as well, so a draft cannot slip through.

		The draft is inserted complete, then a component is removed and the document
		is submitted directly. Frappe runs ``before_validate`` for both the save and
		the submit action (``run_before_save_methods``), so the guard fires on this
		path without a second call at ``before_submit``.
		"""
		sale = self._sell()
		ret = make_sales_return(sale.name)
		ret.insert()
		dropped = next(r for r in ret.items if r.get(ROLE_FIELD) == "Promotion Component")
		ret.items = [row for row in ret.items if row is not dropped]

		with self.assertRaisesRegex(frappe.ValidationError, r"must be returned in full"):
			ret.submit()

		self.assertEqual(frappe.db.get_value("Sales Invoice", ret.name, "docstatus"), 0)

	def test_partial_return_dropping_one_component_throws(self):
		sale = self._sell()
		ret = make_sales_return(sale.name)
		# Mapped child rows are unsaved and share an empty name, so the row to drop
		# is identified by object identity rather than by name.
		dropped = next(r for r in ret.items if r.get(ROLE_FIELD) == "Promotion Component")
		ret.items = [row for row in ret.items if row is not dropped]

		with self.assertRaisesRegex(frappe.ValidationError, r"must be returned in full"):
			ret.insert()

	def test_partial_return_with_short_component_qty_throws(self):
		sale = self._sell()
		ret = make_sales_return(sale.name)
		# The fixed component sells 2 units; returning 1 is a short quantity.
		short = next(r for r in ret.items if r.item_code == self.bread_a)
		self.assertEqual(flt(short.qty), -2.0)
		short.qty = -1.0

		with self.assertRaisesRegex(frappe.ValidationError, r"must be returned in full"):
			ret.insert()

	def test_partial_return_dropping_the_parent_row_throws(self):
		sale = self._sell()
		ret = make_sales_return(sale.name)
		ret.items = [row for row in ret.items if row.get(ROLE_FIELD) != "Promotion Parent"]
		self._repay(ret, 0.0)

		with self.assertRaisesRegex(frappe.ValidationError, r"must be returned in full"):
			ret.insert()

	def test_return_row_for_instance_absent_from_source_throws(self):
		sale = self._sell()
		ret = make_sales_return(sale.name)
		for row in ret.items:
			row.set(INSTANCE_FIELD, "inst_not_in_source")

		with self.assertRaisesRegex(frappe.ValidationError, r"not present on"):
			ret.insert()

	def test_return_carrying_an_item_never_sold_under_the_instance_throws(self):
		"""Completeness is per item, so a smuggled extra item is not a complete instance."""
		sale = self._sell()
		ret = make_sales_return(sale.name)
		instance_id = ret.items[0].get(INSTANCE_FIELD)
		ret.append(
			"items",
			{
				"item_code": self.bread_c,
				"qty": -1,
				"rate": 0.0,
				"warehouse": self.outlet_warehouse,
				INSTANCE_FIELD: instance_id,
				ROLE_FIELD: "Promotion Component",
			},
		)

		with self.assertRaisesRegex(frappe.ValidationError, r"was never sold on"):
			ret.insert()

	def test_return_row_carrying_only_a_role_throws_the_return_error(self):
		"""The guard selects on either field so the named error wins over ERPNext's."""
		sale = self._sell()
		ret = make_sales_return(sale.name)
		orphan = next(r for r in ret.items if r.get(ROLE_FIELD) == "Promotion Component")
		orphan.set(INSTANCE_FIELD, None)

		with self.assertRaisesRegex(frappe.ValidationError, r"must carry its promotion instance"):
			ret.insert()

	def test_positive_promotion_row_cannot_stand_in_for_returned_quantity(self):
		"""Without a sign check, a +2 component would satisfy the expected 2 units."""
		sale = self._sell()
		ret = make_sales_return(sale.name)
		flipped = next(r for r in ret.items if r.item_code == self.bread_a)
		self.assertEqual(flt(flipped.qty), -2.0)
		flipped.qty = 2.0

		with self.assertRaisesRegex(frappe.ValidationError, r"must carry a negative quantity"):
			ret.insert()

	def test_zero_quantity_promotion_row_is_a_short_return(self):
		sale = self._sell()
		ret = make_sales_return(sale.name)
		zeroed = next(r for r in ret.items if r.item_code == self.bread_a)
		zeroed.qty = 0.0

		with self.assertRaisesRegex(frappe.ValidationError, r"must be returned in full"):
			ret.insert()

	# --- I6 standalone returns ---------------------------------------------

	def _standalone_return_rows(self, instance_id):
		return [
			{
				"item_code": self.parent_item,
				"qty": -1,
				"rate": 20000.0,
				"warehouse": self.outlet_warehouse,
				INSTANCE_FIELD: instance_id,
				ROLE_FIELD: "Promotion Parent",
			},
			{
				"item_code": self.bread_a,
				"qty": -2,
				"rate": 0.0,
				"warehouse": self.outlet_warehouse,
				INSTANCE_FIELD: instance_id,
				ROLE_FIELD: "Promotion Component",
			},
			{
				"item_code": self.bread_b,
				"qty": -1,
				"rate": 0.0,
				"warehouse": self.outlet_warehouse,
				INSTANCE_FIELD: instance_id,
				ROLE_FIELD: "Promotion Component",
			},
		]

	def test_standalone_return_with_promotion_rows_throws(self):
		inv = self._new_invoice(items=self._standalone_return_rows("inst_standalone"))
		inv.is_return = 1
		inv.payments[0].amount = -20000.0
		inv.paid_amount = -20000.0

		with self.assertRaisesRegex(frappe.ValidationError, r"without a source invoice"):
			inv.insert()

	def test_standalone_return_carrying_its_own_selections_still_throws(self):
		"""The source reference is what proves completeness, so a self-declared selection cannot."""
		sale = self._sell()
		selection = sale.get(SELECTIONS_FIELD)[0]
		instance_id = selection.instance_id
		inv = self._new_invoice(items=self._standalone_return_rows(instance_id))
		inv.is_return = 1
		inv.payments[0].amount = -20000.0
		inv.paid_amount = -20000.0
		inv.append(
			SELECTIONS_FIELD,
			{
				"instance_id": instance_id,
				"promotion": selection.promotion,
				"total_amount": selection.total_amount,
				"snapshot": selection.snapshot,
			},
		)

		with self.assertRaisesRegex(frappe.ValidationError, r"without a source invoice"):
			inv.insert()

	def test_standalone_return_without_promotion_rows_is_allowed(self):
		inv = self._new_invoice(
			items=[
				{"item_code": self.bread_b, "qty": -1, "rate": 10000.0, "warehouse": self.outlet_warehouse}
			]
		)
		inv.is_return = 1
		inv.payments[0].amount = -10000.0
		inv.paid_amount = -10000.0
		inv.insert()

		self.assertEqual(len(inv.items), 1)
		self.assertEqual(flt(inv.grand_total), -10000.0)

	# --- I6 side conditions -------------------------------------------------

	def _source_snapshot(self, sale_name):
		"""Everything a return must leave untouched on the source invoice.

		Child-row ``modified`` stamps are included so a save that rewrote and
		restored identical values is still caught, and the item rows are included
		because "the source is untouched" is a wider claim than the selection
		table alone.

		The parent row's ``modified`` is deliberately excluded (retarget mechanic,
		no assertion weakened): on this tree a POS Sales Invoice return legitimately
		stamps the SOURCE invoice. Sales Invoice.make_gl_entries runs
		update_voucher_outstanding(voucher_no=self.return_against) for is_pos sales
		(erpnext/accounts/doctype/sales_invoice/sales_invoice.py:1600-1625), and that
		helper writes the source row via frappe.db.set_value + set_status +
		notify_update (erpnext/accounts/utils.py:2171-2187). ERPNext's POS Invoice
		return path never stamped its source, so the source app's snapshot could pin
		``modified``. Content equality — totals, selections, item rows — is still
		asserted in full.
		"""
		return {
			"selections": frappe.get_all(
				"POS Promotion Selection",
				filters={"parent": sale_name},
				fields=["name", "instance_id", "promotion", "total_amount", "snapshot", "modified"],
				order_by="idx asc",
			),
			"items": frappe.get_all(
				"Sales Invoice Item",
				filters={"parent": sale_name},
				fields=[INSTANCE_FIELD, ROLE_FIELD, "item_code", "qty", "rate", "amount", "modified"],
				order_by="idx asc",
			),
			"invoice": frappe.db.get_value(
				"Sales Invoice", sale_name, ["grand_total", "paid_amount"], as_dict=True
			),
		}

	def test_return_never_rewrites_the_source_invoice(self):
		sale = self._sell(options=(self.option_b, self.option_c))
		before = self._source_snapshot(sale.name)

		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()

		self.assertEqual(before, self._source_snapshot(sale.name))

	def test_full_return_passes_after_the_cap_is_lowered_below_the_sold_count(self):
		"""G7 point 10: the sale-side cap is never re-checked on a return."""
		sale = self._sell(options=(self.option_b, self.option_c))
		self.promo.max_instances_per_invoice = 1
		self.promo.save()
		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()

		self.assertEqual(ret.docstatus, 1)
		self.assertEqual(len(ret.items), 6)

	def test_return_of_a_standalone_item_alongside_a_full_instance_passes(self):
		pending = json.dumps({"instances": [self._instance(self.option_b)]})
		inv = self._new_invoice(
			pending=pending,
			items=[
				{"item_code": self.bread_c, "qty": 3, "rate": 10000.0, "warehouse": self.outlet_warehouse}
			],
		)
		inv.insert()
		sale = self._submit_paid(inv)
		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()

		self.assertEqual(ret.docstatus, 1)
		self.assertEqual(flt(ret.grand_total), -50000.0)

	def test_partial_return_of_the_standalone_row_only_passes(self):
		"""Ordinary rows keep ERPNext's native partial-return freedom."""
		pending = json.dumps({"instances": [self._instance(self.option_b)]})
		inv = self._new_invoice(
			pending=pending,
			items=[
				{"item_code": self.bread_c, "qty": 3, "rate": 10000.0, "warehouse": self.outlet_warehouse}
			],
		)
		inv.insert()
		sale = self._submit_paid(inv)
		ret = make_sales_return(sale.name)
		ret.items = [row for row in ret.items if not row.get(INSTANCE_FIELD)]
		self._repay(ret, -30000.0)
		ret.insert()
		ret.submit()

		self.assertEqual(ret.docstatus, 1)
		self.assertEqual(len(ret.items), 1)
		self.assertEqual(flt(ret.grand_total), -30000.0)
