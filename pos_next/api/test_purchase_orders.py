# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
import unittest
import uuid

import frappe
from frappe import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate, nowdate

from pos_next.api.purchase_orders import (
	cancel_purchase_order,
	get_purchase_item_details,
	get_purchase_order,
	get_purchase_orders,
	get_supplier_details,
	save_purchase_order,
	search_purchase_items,
	search_suppliers,
	submit_purchase_order,
)

ADMIN = "Administrator"


class TestPurchaseOrderProxy(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		cls.profile_company = cls.warehouse = None
		if cls.pos_profile:
			cls.profile_company, cls.warehouse = frappe.db.get_value(
				"POS Profile", cls.pos_profile, ["company", "warehouse"]
			)
		if cls.profile_company:
			# the profile's (company, warehouse) pair is the only consistent stock
			# context on this site — the first company has no leaf warehouse
			cls.company = cls.profile_company
		else:
			cls.company = frappe.db.get_value("Company", {}, "name")
			cls.warehouse = frappe.db.get_value(
				"Warehouse", {"company": cls.company, "is_group": 0, "disabled": 0}, "name"
			)
		if not (cls.company and cls.warehouse):
			raise unittest.SkipTest("no company + warehouse pair on this site")
		cls.price_list = frappe.db.get_value("Price List", {"buying": 1, "enabled": 1}, "name")

		cls.fiscal_year = None
		covering_fys = frappe.get_all(
			"Fiscal Year",
			filters={
				"disabled": 0,
				"year_start_date": ["<=", nowdate()],
				"year_end_date": [">=", nowdate()],
			},
			pluck="name",
		)
		if covering_fys and not frappe.get_all(
			"Fiscal Year Company",
			filters={"company": cls.company, "parent": ["in", covering_fys]},
			pluck="parent",
		):
			# this site's test company has no active fiscal year — PO validate
			# (validate_date_with_fiscal_year) would reject every document
			year = getdate(nowdate()).year
			cls.fiscal_year = (
				frappe.get_doc(
					{
						"doctype": "Fiscal Year",
						"year": f"POS PO Test FY {cls._uniq()}",
						"year_start_date": f"{year}-01-01",
						"year_end_date": f"{year}-12-31",
						"companies": [{"company": cls.company}],
					}
				)
				.insert()
				.name
			)

		supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		if not supplier_group:
			raise unittest.SkipTest("no leaf Supplier Group on this site")
		cls.supplier_name = f"PO Proxy Supplier {cls._uniq()}"
		cls.supplier = (
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": cls.supplier_name,
					"supplier_group": supplier_group,
					**({"default_price_list": cls.price_list} if cls.price_list else {}),
				}
			)
			.insert()
			.name
		)

		# note: when the site names items by series (Stock Settings item_naming_by),
		# autoname replaces any custom item_code — always use the inserted doc's name
		item = cls._make_item()
		cls.item = item.name
		cls.item_name = item.item_name

		# dedicated item for price lookups: POs saved with explicit rates make
		# ERPNext auto-insert Item Prices, so the shared item must not carry one
		cls.priced_item = cls.priced_item_name = None
		cls.item_prices = []
		if cls.price_list:
			priced = cls._make_item()
			cls.priced_item = priced.name
			cls.priced_item_name = priced.item_name
			# new POs default to the Standard Buying price list (doctype field
			# default), while the supplier's default price list drives
			# get_purchase_item_details — rate fixtures must cover both
			price_lists = [cls.price_list]
			standard = frappe.db.get_value("Price List", "Standard Buying", "name")
			if standard and standard not in price_lists:
				price_lists.append(standard)
			for price_list in price_lists:
				cls.item_prices.append(
					frappe.get_doc(
						{
							"doctype": "Item Price",
							"price_list": price_list,
							"item_code": cls.priced_item,
							"price_list_rate": 10,
							"currency": frappe.db.get_value("Price List", price_list, "currency")
							or frappe.get_cached_value("Company", cls.company, "default_currency"),
						}
					)
					.insert()
					.name
				)

		cls.user = f"po.proxy.{cls._uniq()}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.user, "first_name": "PO Proxy Tester"}).insert()

	@classmethod
	def _make_item(cls):
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"PO-T-{cls._uniq()}",
				"item_name": f"PO Proxy Item {cls._uniq()}",
				"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
				"stock_uom": frappe.db.get_value("UOM", "Unit", "name") or "Nos",
				"is_stock_item": 1,
				"is_purchase_item": 1,
			}
		).insert()

	@staticmethod
	def _uniq():
		return uuid.uuid4().hex[:8]

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		for name in getattr(cls, "item_prices", []) or []:
			if frappe.db.exists("Item Price", name):
				frappe.delete_doc("Item Price", name, force=1)
		for doctype, name in [
			("Item", getattr(cls, "item", None)),
			("Item", getattr(cls, "priced_item", None)),
			("Supplier", getattr(cls, "supplier", None)),
			("User", getattr(cls, "user", None)),
			("Fiscal Year", getattr(cls, "fiscal_year", None)),
		]:
			if name and frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=1)
		super().tearDownClass()

	def setUp(self):
		self.pos = []

	def tearDown(self):
		# every PO this test created is cancelled + deleted (suite runs on a live dev site)
		frappe.set_user(ADMIN)
		for name in self.pos:
			if not frappe.db.exists("Purchase Order", name):
				continue
			if frappe.db.get_value("Purchase Order", name, "docstatus") == 1:
				frappe.get_doc("Purchase Order", name).cancel()
			frappe.delete_doc("Purchase Order", name, force=1)

	def _data(self, **overrides):
		data = {
			"supplier": self.supplier,
			"company": self.company,
			"set_warehouse": self.warehouse,
			"items": [{"item_code": self.item, "qty": 2, "rate": 25, "warehouse": self.warehouse}],
		}
		data.update(overrides)
		return data

	def _save(self, data=None, **kwargs):
		result = save_purchase_order(data if data is not None else self._data(), **kwargs)
		self.pos.append(result["name"])
		return result

	def test_create_draft_with_explicit_rate(self):
		result = self._save()
		self.assertEqual(result["docstatus"], 0)
		self.assertEqual(result["supplier"], self.supplier)
		self.assertEqual(result["supplier_name"], self.supplier_name)
		self.assertEqual(result["transaction_date"], nowdate())
		self.assertEqual(result["schedule_date"], add_days(nowdate(), 1))
		self.assertEqual(flt(result["net_total"]), 50)
		row = result["items"][0]
		self.assertEqual(row["item_code"], self.item)
		self.assertEqual(flt(row["qty"]), 2)
		self.assertEqual(flt(row["rate"]), 25)
		self.assertEqual(flt(row["amount"]), 50)
		self.assertEqual(row["warehouse"], self.warehouse)
		self.assertEqual(row["schedule_date"], add_days(nowdate(), 1))

	def test_create_draft_fills_missing_rate_uom_and_row_defaults(self):
		result = self._save(
			{
				"supplier": self.supplier,
				"company": self.company,
				"set_warehouse": self.warehouse,
				"items": [{"item_code": self.priced_item, "qty": 2}],
			}
		)
		row = result["items"][0]
		self.assertEqual(flt(row["rate"]), 10)  # from the Item Price via supplier's price list
		self.assertEqual(row["item_code"], self.priced_item)
		self.assertEqual(row["uom"], "Unit")
		self.assertEqual(row["warehouse"], self.warehouse)  # falls back to parent set_warehouse
		self.assertEqual(row["schedule_date"], add_days(nowdate(), 1))

	def test_create_with_submit_flag(self):
		result = self._save(submit=1)
		self.assertEqual(result["docstatus"], 1)
		self.assertNotEqual(result["status"], "Draft")

	def test_submit_existing_draft(self):
		name = self._save()["name"]
		result = submit_purchase_order(name)
		self.assertEqual(result["docstatus"], 1)

	def test_update_draft_replaces_items(self):
		name = self._save()["name"]
		result = self._save(
			json.dumps(
				{
					"name": name,
					"supplier": self.supplier,
					"company": self.company,
					"items": [{"item_code": self.item, "qty": 3, "rate": 25, "warehouse": self.warehouse}],
				}
			)
		)
		self.assertEqual(result["name"], name)
		self.assertEqual(result["docstatus"], 0)
		self.assertEqual(len(result["items"]), 1)
		self.assertEqual(flt(result["items"][0]["qty"]), 3)

	def test_remarks_persist_via_terms_field(self):
		result = self._save(self._data(remarks="POS proxy remarks"))
		self.assertEqual(result["remarks"], "POS proxy remarks")
		# remarks ride the native terms field — must survive a server reload
		self.assertEqual(get_purchase_order(result["name"])["remarks"], "POS proxy remarks")

	def test_malformed_json_payload_throws_cleanly(self):
		with self.assertRaises(ValidationError):
			save_purchase_order("{not json")

	def test_edit_non_draft_throws(self):
		name = self._save(submit=1)["name"]
		with self.assertRaises(ValidationError) as ctx:
			self._save(self._data(name=name))
		self.assertIn("Draft", str(ctx.exception))

	def test_cancel_submitted(self):
		name = self._save(submit=1)["name"]
		result = cancel_purchase_order(name)
		self.assertEqual(result["docstatus"], 2)
		self.assertEqual(result["status"], "Cancelled")

	def test_submit_and_cancel_only_in_right_docstatus(self):
		name = self._save()["name"]
		with self.assertRaises(ValidationError):
			cancel_purchase_order(name)  # draft cannot be cancelled
		submit_purchase_order(name)
		with self.assertRaises(ValidationError):
			submit_purchase_order(name)  # submitted cannot be submitted again

	def test_validation_supplier_items_and_schedule_date(self):
		with self.assertRaises(ValidationError):
			self._save(self._data(supplier=None))
		with self.assertRaises(ValidationError):
			self._save(self._data(items=[]))
		with self.assertRaises(ValidationError):
			self._save(self._data(schedule_date=add_days(nowdate(), -5)))

	def test_get_purchase_order_summary(self):
		name = self._save()["name"]
		summary = get_purchase_order(name)
		self.assertEqual(summary["name"], name)
		self.assertEqual(summary["supplier"], self.supplier)
		self.assertEqual(len(summary["items"]), 1)

	def test_list_and_search_find_purchase_orders(self):
		name = self._save()["name"]
		orders = get_purchase_orders()["orders"]
		self.assertIn(name, [o["name"] for o in orders])

		found = get_purchase_orders(search_term=name)["orders"]
		self.assertEqual([o["name"] for o in found], [name])

		self.assertIn(name, [o["name"] for o in get_purchase_orders(status="Draft")["orders"]])
		self.assertNotIn(name, [o["name"] for o in get_purchase_orders(status="Completed")["orders"]])

		if self.pos_profile:
			scoped = get_purchase_orders(pos_profile=self.pos_profile)["orders"]
			self.assertIn(name, [o["name"] for o in scoped])

	def test_pos_profile_supplies_company_and_warehouse(self):
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		result = self._save(
			{"supplier": self.supplier, "items": [{"item_code": self.item, "qty": 1, "rate": 5}]},
			pos_profile=self.pos_profile,
		)
		self.assertEqual(result["company"], self.profile_company)
		self.assertEqual(result["items"][0]["warehouse"], self.warehouse)

	def test_search_suppliers_and_purchase_items(self):
		suppliers = search_suppliers(self.supplier_name)["suppliers"]
		match = next(s for s in suppliers if s["name"] == self.supplier)
		self.assertEqual(match["supplier_name"], self.supplier_name)
		self.assertIn("supplier_group", match)

		items = search_purchase_items(self.item_name)["items"]
		entry = next(i for i in items if i["item_code"] == self.item)
		self.assertEqual(entry["stock_uom"], "Unit")

	def test_get_supplier_details_shape(self):
		details = get_supplier_details(self.supplier)
		self.assertEqual(
			set(details), {"supplier_name", "currency", "buying_price_list", "taxes_and_charges"}
		)
		self.assertEqual(details["supplier_name"], self.supplier_name)
		if self.price_list:
			self.assertEqual(details["buying_price_list"], self.price_list)
		profiled = get_supplier_details(self.supplier, pos_profile=self.pos_profile)
		self.assertEqual(profiled["supplier_name"], self.supplier_name)

	def test_get_purchase_item_details_shape(self):
		details = get_purchase_item_details(
			self.priced_item, supplier=self.supplier, warehouse=self.warehouse
		)
		self.assertEqual(
			set(details),
			{
				"item_code",
				"item_name",
				"uom",
				"stock_uom",
				"conversion_factor",
				"price_list_rate",
				"rate",
				"warehouse",
			},
		)
		self.assertEqual(details["item_code"], self.priced_item)
		self.assertEqual(details["uom"], "Unit")
		self.assertEqual(details["stock_uom"], "Unit")
		self.assertEqual(flt(details["conversion_factor"]), 1)
		self.assertEqual(flt(details["price_list_rate"]), 10)
		# standalone get_item_details leaves rate at 0 — the PO fills it on save
		self.assertEqual(flt(details["rate"]), 0)
		self.assertEqual(details["warehouse"], self.warehouse)

	def test_permission_denied_without_purchasing_role(self):
		name = self._save()["name"]
		frappe.set_user(self.user)
		try:
			with self.assertRaises(frappe.PermissionError):
				save_purchase_order(self._data())
			with self.assertRaises(frappe.PermissionError):
				get_purchase_order(name)
			with self.assertRaises(frappe.PermissionError):
				submit_purchase_order(name)
		finally:
			frappe.set_user(ADMIN)
