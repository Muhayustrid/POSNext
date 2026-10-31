# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Read-report regressions using transactional ledger snapshots, not stock posting.

Run serially on the isolated FIX site via pos_next/_pn_run_tests.py.
"""

import unittest
import uuid
from unittest.mock import patch

import frappe
from frappe.utils import get_datetime

from pos_next.api.items import export_product_list, get_item_stock_ledger, get_product_list


class TestProductStock(unittest.TestCase):
	def setUp(self):
		self.previous_user = frappe.session.user
		frappe.set_user("Administrator")
		self.addCleanup(frappe.set_user, self.previous_user)
		self.savepoint = f"product_stock_{uuid.uuid4().hex[:8]}"
		frappe.db.savepoint(self.savepoint)
		self.addCleanup(lambda: frappe.db.rollback(save_point=self.savepoint))
		self.prefix = f"PST-{uuid.uuid4().hex[:10]}"
		self.company = frappe.db.get_value("Company", {}, "name")
		self.assertIsNotNone(self.company, "isolated test site needs a Company")
		self.group = frappe.db.get_value("Item Group", {"is_group": 0}, "name")
		self.assertIsNotNone(self.group, "isolated test site needs an Item Group")
		last_rgt = frappe.db.sql("SELECT COALESCE(MAX(rgt), 0) FROM `tabWarehouse`")[0][0]
		self.root = self._insert(
			"Warehouse", "ROOT", company=self.company, is_group=1, lft=last_rgt + 1, rgt=last_rgt + 6
		)
		self.warehouse = self._insert(
			"Warehouse",
			"A",
			company=self.company,
			is_group=0,
			parent_warehouse=self.root,
			lft=last_rgt + 2,
			rgt=last_rgt + 3,
		)
		self.other_warehouse = self._insert(
			"Warehouse",
			"B",
			company=self.company,
			is_group=0,
			parent_warehouse=self.root,
			lft=last_rgt + 4,
			rgt=last_rgt + 5,
		)
		self.profile = self._insert(
			"POS Profile", "POS", company=self.company, warehouse=self.warehouse, disabled=0
		)
		self.item = self._insert(
			"Item",
			"ITEM",
			item_name=f"{self.prefix} Tea",
			item_code=f"{self.prefix}-ITEM",
			item_group=self.group,
			stock_uom="Nos",
			is_stock_item=1,
			is_sales_item=1,
			disabled=0,
			has_variants=0,
		)

	def _insert(self, doctype, suffix, **values):
		# Report fixtures bypass posting hooks: only this transaction sees them.
		name = f"{self.prefix}-{suffix}"
		frappe.get_doc({"doctype": doctype, "name": name, **values}).db_insert()
		return name

	def _entry(self, suffix, when, delta, balance, warehouse=None, **values):
		dt = get_datetime(when)
		return self._insert(
			"Stock Ledger Entry",
			suffix,
			item_code=self.item,
			warehouse=warehouse or self.warehouse,
			company=self.company,
			posting_date=dt.date(),
			posting_time=dt.time(),
			posting_datetime=dt,
			actual_qty=delta,
			qty_after_transaction=balance,
			voucher_type=values.pop("voucher_type", "Stock Entry"),
			voucher_no=f"{self.prefix}-{suffix}-VOUCHER",
			is_cancelled=values.pop("is_cancelled", 0),
			creation=values.pop("creation", when),
			docstatus=1,
			**values,
		)

	def _list(self, **kwargs):
		return get_product_list(
			self.profile, search=self.prefix, from_date="2026-10-02", to_date="2026-10-03", **kwargs
		)

	def _ledger(self, **kwargs):
		return get_item_stock_ledger(
			self.item, self.profile, from_date="2026-10-02", to_date="2026-10-03", **kwargs
		)

	def test_receipts_issues_and_period_end_ignore_bin_and_other_outlets(self):
		self._entry("OPEN", "2026-10-01 08:00:00", 10, 10)
		self._entry("IN", "2026-10-02 00:00:00", 5, 15)
		self._entry("OUT", "2026-10-03 18:00:00", -3, 12)
		self._entry("AFTER", "2026-10-04 00:00:00", 100, 112)
		self._entry("FOREIGN", "2026-10-02 12:00:00", 999, 999, warehouse=self.other_warehouse)
		self._entry("CANCELLED", "2026-10-03 20:00:00", 500, 512, is_cancelled=1)
		self._insert("Bin", "BIN", item_code=self.item, warehouse=self.warehouse, actual_qty=112)
		rows = self._list()
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			{k: rows[0][k] for k in ("opening_qty", "in_qty", "out_qty", "closing_qty")},
			{"opening_qty": 10, "in_qty": 5, "out_qty": 3, "closing_qty": 12},
		)
		ledger = self._ledger()
		self.assertEqual((ledger["opening_qty"], ledger["closing_qty"]), (10, 12))
		self.assertEqual([e["balance"] for e in ledger["entries"]], [15, 12])
		self.assertEqual(
			[e["voucher_no"] for e in ledger["entries"]],
			[
				f"{self.prefix}-IN-VOUCHER",
				f"{self.prefix}-OUT-VOUCHER",
			],
		)

	def test_reconciliation_is_a_movement_even_when_actual_qty_is_zero(self):
		self._entry("OPEN", "2026-10-01 08:00:00", 10, 10)
		self._entry("COUNT", "2026-10-02 09:00:00", 0, 25, voucher_type="Stock Reconciliation")
		self._entry("OUT", "2026-10-03 09:00:00", -5, 20)
		row = self._list()[0]
		self.assertEqual(
			(row["opening_qty"], row["in_qty"], row["out_qty"], row["closing_qty"]), (10, 15, 5, 20)
		)
		ledger = self._ledger()
		self.assertEqual(
			[(e["in_qty"], e["out_qty"], e["balance"]) for e in ledger["entries"]], [(15, 0, 25), (0, 5, 20)]
		)

	def test_group_warehouse_ledger_shows_outlet_running_balance(self):
		frappe.db.set_value("POS Profile", self.profile, "warehouse", self.root)
		frappe.clear_document_cache("POS Profile", self.profile)
		self._entry("OPEN-A", "2026-10-01 08:00:00", 10, 10)
		self._entry("OPEN-B", "2026-10-01 09:00:00", 20, 20, warehouse=self.other_warehouse)
		self._entry("IN-A", "2026-10-02 09:00:00", 5, 15)
		self._entry("OUT-B", "2026-10-03 09:00:00", -2, 18, warehouse=self.other_warehouse)
		ledger = self._ledger()
		self.assertEqual((ledger["opening_qty"], ledger["closing_qty"]), (30, 33))
		self.assertEqual([e["balance"] for e in ledger["entries"]], [35, 33])
		row = self._list()[0]
		self.assertEqual(
			(row["opening_qty"], row["in_qty"], row["out_qty"], row["closing_qty"]), (30, 5, 2, 33)
		)

	def test_last_microsecond_of_end_date_is_included(self):
		self._entry("LAST", "2026-10-03 23:59:59.500000", 4, 4)
		self._entry("NEXT", "2026-10-04 00:00:00", 6, 10)
		row = self._list()[0]
		self.assertEqual((row["in_qty"], row["closing_qty"]), (4, 4))
		ledger = self._ledger()
		self.assertEqual([e["balance"] for e in ledger["entries"]], [4])

	def test_empty_period_carries_forward_opening(self):
		self._entry("OPEN", "2026-10-01 08:00:00", 8, 8)
		row = self._list()[0]
		self.assertEqual(
			(row["opening_qty"], row["in_qty"], row["out_qty"], row["closing_qty"]), (8, 0, 0, 8)
		)
		self.assertEqual(self._ledger()["entries"], [])

	def test_items_that_never_moved_in_the_outlet_are_hidden(self):
		# like ERPNext Stock Balance: no ledger in these warehouses -> no row
		self.assertEqual(self._list(), [])
		self._entry("FOREIGN", "2026-10-02 08:00:00", 5, 5, warehouse=self.other_warehouse)
		self._entry("LATER", "2026-10-04 08:00:00", 5, 5)
		self._entry("VOID", "2026-10-02 09:00:00", 5, 5, is_cancelled=1)
		self.assertEqual(self._list(), [])
		# sold out before the period: still listed, with zero balance
		self._entry("IN", "2026-09-01 08:00:00", 3, 3)
		self._entry("OUT", "2026-09-02 08:00:00", -3, 0)
		row = self._list()[0]
		self.assertEqual(
			(row["opening_qty"], row["in_qty"], row["out_qty"], row["closing_qty"]), (0, 0, 0, 0)
		)

	def test_invalid_dates_are_rejected(self):
		for start, end in [
			("", "2026-10-03"),
			("bad", "2026-10-03"),
			("2026-10-04", "2026-10-03"),
			("2024-01-01", "2026-10-03"),
		]:
			with self.subTest(start=start, end=end), self.assertRaises(frappe.ValidationError):
				get_item_stock_ledger(self.item, self.profile, start, end)

	def test_outsider_cannot_read_list_ledger_or_export(self):
		user = self._insert(
			"User",
			"USER",
			email=f"{self.prefix}@example.com",
			first_name="Stock Report",
			enabled=1,
			user_type="System User",
		)
		frappe.set_user(user)
		for endpoint in [
			self._list,
			self._ledger,
			lambda: export_product_list(self.profile, from_date="2026-10-02", to_date="2026-10-03"),
		]:
			with self.subTest(endpoint=endpoint), self.assertRaises(frappe.PermissionError):
				endpoint()

	def test_ledger_uses_the_same_stock_item_scope_as_the_list(self):
		frappe.db.set_value("Item", self.item, "is_stock_item", 0)
		self.assertEqual(self._list(), [])
		with self.assertRaises(frappe.ValidationError):
			self._ledger()

	def test_period_export_has_the_same_quantities_without_prices(self):
		self._entry("IN", "2026-10-02 08:00:00", 5, 5)
		with patch("frappe.utils.xlsxutils.build_xlsx_response") as build:
			export_product_list(
				self.profile, search=self.prefix, from_date="2026-10-02", to_date="2026-10-03"
			)
		data = build.call_args.args[0]
		self.assertEqual(
			data[0],
			["Item Code", "Item Name", "UOM", "Opening Balance", "Stock In", "Stock Out", "Closing Balance"],
		)
		self.assertEqual(data[1], [self.item, f"{self.prefix} Tea", "Nos", 0, 5, 0, 5])

	def test_ledger_pagination_keeps_the_running_balance(self):
		self._entry("OPEN", "2026-10-01 08:00:00", 10, 10)
		self._entry("IN", "2026-10-02 08:00:00", 5, 15)
		self._entry("OUT", "2026-10-03 08:00:00", -3, 12)
		first = self._ledger(limit=1)
		second = self._ledger(start=1, limit=1)
		self.assertTrue(first["has_more"])
		self.assertFalse(second["has_more"])
		self.assertEqual(first["entries"][0]["balance"], 15)
		self.assertEqual(second["entries"][0]["balance"], 12)
		self.assertEqual(second["opening_qty"], 10)
		self.assertEqual(second["closing_qty"], 12)

	def test_reconciliation_can_reduce_stock_and_fractional_stock_is_retained(self):
		self._entry("OPEN", "2026-10-01 08:00:00", 10.5, 10.5)
		self._entry("COUNT", "2026-10-02 08:00:00", 0, 7.25, voucher_type="Stock Reconciliation")
		row = self._list()[0]
		self.assertEqual(
			(row["opening_qty"], row["in_qty"], row["out_qty"], row["closing_qty"]), (10.5, 0, 3.25, 7.25)
		)
		self.assertEqual(self._ledger()["entries"][0]["out_qty"], 3.25)
		self.assertNotIn("rate", row)

	def test_assigned_cashier_sees_stock_outside_the_sale_catalog_scope(self):
		user = self._insert(
			"User",
			"USER",
			email=f"{self.prefix}@example.com",
			first_name="Stock Report",
			enabled=1,
			user_type="System User",
		)
		self._insert(
			"POS Profile User",
			"MEMBER",
			parent=self.profile,
			parenttype="POS Profile",
			parentfield="applicable_for_users",
			user=user,
		)
		self._entry("IN", "2026-10-02 08:00:00", 5, 5)
		frappe.set_user(user)
		self.assertEqual(self._list()[0]["closing_qty"], 5)
		self.assertEqual(self._ledger()["closing_qty"], 5)
		frappe.set_user("Administrator")
		other_group = self._insert(
			"Item Group", "GROUP", item_group_name=f"{self.prefix} Other", is_group=0, lft=999990, rgt=999991
		)
		self._insert(
			"POS Item Group",
			"GROUP-ROW",
			parent=self.profile,
			parenttype="POS Profile",
			parentfield="item_groups",
			item_group=other_group,
		)
		frappe.clear_document_cache("POS Profile", self.profile)
		frappe.cache().delete_value(f"pos_profile_allowed_item_groups:{self.profile}")
		frappe.set_user(user)
		# Products is a stock view: the profile's item-group/brand scope only
		# limits the sale catalog, so the outlet still sees every stock item.
		self.assertEqual(self._list()[0]["closing_qty"], 5)
		self.assertEqual(self._ledger()["closing_qty"], 5)

	def test_legacy_list_without_dates_still_returns_current_stock(self):
		self._insert("Bin", "BIN", item_code=self.item, warehouse=self.warehouse, actual_qty=17)
		row = get_product_list(self.profile, search=self.prefix)[0]
		self.assertEqual(row["actual_qty"], 17)
		self.assertNotIn("opening_qty", row)
