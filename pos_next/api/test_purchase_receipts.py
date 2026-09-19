# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import unittest
import uuid

import frappe
from frappe import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, getdate, nowdate

from pos_next.api.purchase_orders import save_purchase_order
from pos_next.api.purchase_receipts import (
	cancel_purchase_receipt,
	get_purchase_receipt_draft,
	get_purchase_receipts,
	save_purchase_receipt,
)

ADMIN = "Administrator"


class TestPurchaseReceiptProxy(FrappeTestCase):
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
			# this site's test company has no active fiscal year — the accounts
			# validate (validate_date_with_fiscal_year) would reject every document
			year = getdate(nowdate()).year
			cls.fiscal_year = (
				frappe.get_doc(
					{
						"doctype": "Fiscal Year",
						"year": f"POS PR Test FY {cls._uniq()}",
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
		cls.supplier_name = f"PR Proxy Supplier {cls._uniq()}"
		cls.supplier = (
			frappe.get_doc(
				{"doctype": "Supplier", "supplier_name": cls.supplier_name, "supplier_group": supplier_group}
			)
			.insert()
			.name
		)

		# note: when the site names items by series (Stock Settings item_naming_by),
		# autoname replaces any custom item_code — always use the inserted doc's name
		item = cls._make_item()
		cls.item = item.name
		cls.item_name = item.item_name
		cls.uom = item.stock_uom

		cls.user = f"pr.proxy.{cls._uniq()}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.user, "first_name": "PR Proxy Tester"}).insert()

		# deleting a cancelled voucher only purges its stock/GL ledger rows when
		# this setting is on — turn it on so the cleanup leaves zero residue
		cls.orig_delete_ledger = frappe.db.get_single_value(
			"Accounts Settings", "delete_linked_ledger_entries"
		)
		frappe.db.set_single_value("Accounts Settings", "delete_linked_ledger_entries", 1)

	@classmethod
	def _make_item(cls):
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"PR-T-{cls._uniq()}",
				"item_name": f"PR Proxy Item {cls._uniq()}",
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
		# explicit-rate POs auto-insert Item Prices — drop them with the item
		for price in frappe.get_all("Item Price", filters={"item_code": cls.item}, pluck="name"):
			frappe.delete_doc("Item Price", price, force=1)
		for doctype, name in [
			("Item", getattr(cls, "item", None)),
			("Supplier", getattr(cls, "supplier", None)),
			("User", getattr(cls, "user", None)),
			("Fiscal Year", getattr(cls, "fiscal_year", None)),
		]:
			if name and frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=1)
		frappe.db.set_single_value(
			"Accounts Settings", "delete_linked_ledger_entries", cls.orig_delete_ledger
		)
		super().tearDownClass()

	def setUp(self):
		self.pos = []
		self.receipts = []

	def tearDown(self):
		# every PR / PO this test created is cancelled + deleted (suite runs on a
		# live dev site); PRs first — they link to the PO's item rows
		frappe.set_user(ADMIN)
		for doctype, names in (("Purchase Receipt", self.receipts), ("Purchase Order", self.pos)):
			for name in names:
				if not frappe.db.exists(doctype, name):
					continue
				if frappe.db.get_value(doctype, name, "docstatus") == 1:
					frappe.get_doc(doctype, name).cancel()
				frappe.delete_doc(doctype, name, force=1)

	def _po(self, qty=2, submit=True):
		result = save_purchase_order(
			{
				"supplier": self.supplier,
				"company": self.company,
				"set_warehouse": self.warehouse,
				"items": [{"item_code": self.item, "qty": qty, "rate": 25, "warehouse": self.warehouse}],
			},
			submit=1 if submit else 0,
		)
		self.pos.append(result["name"])
		return result

	def _payload(self, draft, qty=None, name=None):
		data = {
			"supplier": draft["supplier"],
			"posting_date": draft["posting_date"],
			"company": draft["company"],
			"set_warehouse": draft["set_warehouse"],
			"items": [
				{
					"item_code": row["item_code"],
					"qty": qty if qty is not None else row["pending_qty"],
					"rate": row["rate"],
					"uom": row["uom"],
					"warehouse": row["warehouse"],
					"purchase_order": row["purchase_order"],
					"purchase_order_item": row["purchase_order_item"],
				}
				for row in draft["items"]
			],
		}
		if name:
			data["name"] = name
		return data

	def _save(self, data, **kwargs):
		result = save_purchase_receipt(data, **kwargs)
		self.receipts.append(result["name"])
		return result

	def test_draft_shape_from_mapper(self):
		po = self._po()
		draft = get_purchase_receipt_draft(po["name"])
		self.assertEqual(
			set(draft),
			{"supplier", "supplier_name", "posting_date", "company", "currency", "set_warehouse", "items"},
		)
		self.assertEqual(draft["supplier"], self.supplier)
		self.assertEqual(draft["supplier_name"], self.supplier_name)
		self.assertEqual(draft["posting_date"], nowdate())
		self.assertEqual(draft["company"], self.company)
		self.assertEqual(draft["set_warehouse"], self.warehouse)
		row = draft["items"][0]
		self.assertEqual(
			set(row),
			{
				"item_code",
				"item_name",
				"purchase_order",
				"purchase_order_item",
				"ordered_qty",
				"received_qty",
				"pending_qty",
				"uom",
				"rate",
				"warehouse",
			},
		)
		self.assertEqual(row["item_code"], self.item)
		self.assertEqual(row["purchase_order"], po["name"])
		self.assertTrue(row["purchase_order_item"])  # PO item row link for per-row status
		self.assertEqual(flt(row["ordered_qty"]), 2)
		self.assertEqual(flt(row["received_qty"]), 0)
		self.assertEqual(flt(row["pending_qty"]), 2)  # mapper returns the pending qty
		self.assertEqual(row["uom"], self.uom)
		self.assertEqual(flt(row["rate"]), 25)
		self.assertEqual(row["warehouse"], self.warehouse)

	def test_create_draft(self):
		po = self._po()
		result = self._save(self._payload(get_purchase_receipt_draft(po["name"])))
		self.assertEqual(result["docstatus"], 0)
		self.assertEqual(result["status"], "Draft")
		self.assertEqual(result["supplier"], self.supplier)
		self.assertEqual(result["supplier_name"], self.supplier_name)
		self.assertEqual(result["posting_date"], nowdate())
		self.assertEqual(result["company"], self.company)
		self.assertEqual(flt(result["net_total"]), 50)
		self.assertEqual(flt(result["grand_total"]), 50)
		row = result["items"][0]
		self.assertEqual(row["purchase_order"], po["name"])
		self.assertTrue(row["purchase_order_item"])
		self.assertEqual(flt(row["qty"]), 2)
		self.assertEqual(flt(row["rate"]), 25)
		self.assertEqual(flt(row["amount"]), 50)
		self.assertEqual(row["warehouse"], self.warehouse)

	def test_submit_creates_stock_and_updates_po(self):
		po = self._po()
		result = self._save(self._payload(get_purchase_receipt_draft(po["name"])), submit=1)
		self.assertEqual(result["docstatus"], 1)
		self.assertEqual(result["status"], "To Bill")  # received, nothing billed yet

		stock_qty = sum(
			flt(q)
			for q in frappe.get_all(
				"Stock Ledger Entry",
				filters={
					"voucher_type": "Purchase Receipt",
					"voucher_no": result["name"],
					"is_cancelled": 0,
				},
				pluck="actual_qty",
			)
		)
		self.assertEqual(stock_qty, 2)

		per_received, po_status = frappe.db.get_value(
			"Purchase Order", po["name"], ["per_received", "status"]
		)
		self.assertEqual(flt(per_received), 100)
		# fully received but unbilled — native status stays "To Bill" until the
		# Purchase Invoice (billed from Desk) completes it
		self.assertEqual(po_status, "To Bill")

		receipts = get_purchase_receipts()["receipts"]
		self.assertIn(result["name"], [r["name"] for r in receipts])
		if self.pos_profile:
			scoped = get_purchase_receipts(pos_profile=self.pos_profile)["receipts"]
			self.assertIn(result["name"], [r["name"] for r in scoped])

	def test_partial_receive_two_stage(self):
		po = self._po(qty=4)
		self._save(self._payload(get_purchase_receipt_draft(po["name"]), qty=2), submit=1)
		per_received, po_status = frappe.db.get_value(
			"Purchase Order", po["name"], ["per_received", "status"]
		)
		self.assertEqual(flt(per_received), 50)
		self.assertEqual(po_status, "To Receive and Bill")

		# the draft now carries the remaining qty only
		draft = get_purchase_receipt_draft(po["name"])
		row = draft["items"][0]
		self.assertEqual(flt(row["ordered_qty"]), 4)
		self.assertEqual(flt(row["received_qty"]), 2)
		self.assertEqual(flt(row["pending_qty"]), 2)
		self._save(self._payload(draft), submit=1)
		per_received, po_status = frappe.db.get_value(
			"Purchase Order", po["name"], ["per_received", "status"]
		)
		self.assertEqual(flt(per_received), 100)
		self.assertEqual(po_status, "To Bill")

	def test_update_draft(self):
		po = self._po()
		draft = get_purchase_receipt_draft(po["name"])
		name = self._save(self._payload(draft))["name"]
		result = self._save(self._payload(draft, qty=1, name=name))
		self.assertEqual(result["name"], name)
		self.assertEqual(result["docstatus"], 0)
		self.assertEqual(len(result["items"]), 1)
		self.assertEqual(flt(result["items"][0]["qty"]), 1)
		# the reduced qty is what actually lands on submit
		self._save(self._payload(draft, qty=1, name=name), submit=1)
		self.assertEqual(flt(frappe.db.get_value("Purchase Order", po["name"], "per_received")), 50)

	def test_cancel_restores_po(self):
		po = self._po()
		name = self._save(self._payload(get_purchase_receipt_draft(po["name"])), submit=1)["name"]
		result = cancel_purchase_receipt(name)
		self.assertEqual(result["docstatus"], 2)
		self.assertEqual(result["status"], "Cancelled")
		per_received, po_status = frappe.db.get_value(
			"Purchase Order", po["name"], ["per_received", "status"]
		)
		self.assertEqual(flt(per_received), 0)
		self.assertEqual(po_status, "To Receive and Bill")
		stock_qty = sum(
			flt(q)
			for q in frappe.get_all(
				"Stock Ledger Entry",
				filters={
					"voucher_type": "Purchase Receipt",
					"voucher_no": name,
					"is_cancelled": 0,
				},
				pluck="actual_qty",
			)
		)
		self.assertEqual(stock_qty, 0)  # stock reversed

	def test_validation_and_mapper_errors(self):
		with self.assertRaises(ValidationError):
			save_purchase_receipt(
				{
					"supplier": self.supplier,
					"posting_date": nowdate(),
					"company": self.company,
					"items": [],
				}
			)
		# the mapper refuses a non-submitted PO — its native error passes through
		draft_po = self._po(submit=False)
		with self.assertRaises(ValidationError):
			get_purchase_receipt_draft(draft_po["name"])

	def test_permission_denied_without_role(self):
		po = self._po()
		draft = get_purchase_receipt_draft(po["name"])
		name = self._save(self._payload(draft), submit=1)["name"]
		frappe.set_user(self.user)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_purchase_receipt_draft(po["name"])
			with self.assertRaises(frappe.PermissionError):
				save_purchase_receipt(self._payload(draft))
			with self.assertRaises(frappe.PermissionError):
				cancel_purchase_receipt(name)
		finally:
			frappe.set_user(ADMIN)
