# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
import unittest
import uuid
from unittest import mock

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_inter_company_sales_order
from erpnext.selling.doctype.sales_order.sales_order import make_delivery_note
from frappe import ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, getdate, nowdate

from pos_next.api.purchase_orders import (
	_shipping_statuses,
	attach_purchase_files,
	get_purchase_order,
	get_purchase_orders,
	save_purchase_order,
)
from pos_next.api.purchase_receipts import (
	cancel_purchase_receipt,
	get_intercompany_receipt_draft,
	get_purchase_receipt,
	get_purchase_receipt_draft,
	get_purchase_receipts,
	save_purchase_receipt,
	submit_purchase_receipt,
)

ADMIN = "Administrator"


class TestPurchaseReceiptProxy(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# creation-asc: an unordered first row can land on a demo/test-chart
		# profile (INR _Test Company) whose party accounts break every draft
		cls.pos_profile = frappe.db.get_value(
			"POS Profile", {"disabled": 0}, "name", order_by="creation asc"
		)
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

		cls.fiscal_year = cls._ensure_fy(cls.company)

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
		cls.ext_supplier_name = f"PR Ext Supplier {cls._uniq()}"
		cls.ext_supplier = (
			frappe.get_doc(
				{"doctype": "Supplier", "supplier_name": cls.ext_supplier_name, "supplier_group": supplier_group}
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

		cls.cashier_user = f"pr.cashier.{cls._uniq()}@example.com"
		cashier = frappe.get_doc(
			{"doctype": "User", "email": cls.cashier_user, "first_name": "PR Cashier Tester"}
		).insert()
		cashier.add_roles("Purchase User", "Stock User")

		cls.manager_user = f"pr.manager.{cls._uniq()}@example.com"
		manager = frappe.get_doc(
			{"doctype": "User", "email": cls.manager_user, "first_name": "PR Manager Tester"}
		).insert()
		manager.add_roles("Purchase User", "Stock User", "POSNext Manager")

		# deleting a cancelled voucher only purges its stock/GL ledger rows when
		# this setting is on — turn it on so the cleanup leaves zero residue
		cls.orig_delete_ledger = frappe.db.get_single_value(
			"Accounts Settings", "delete_linked_ledger_entries"
		)
		frappe.db.set_single_value("Accounts Settings", "delete_linked_ledger_entries", 1)

		# intercompany chain: a second company with the same currency hosts the
		# factory-side SO + DN; the fixture supplier is flipped to represent it
		# per-test. DN submit needs (possibly negative) stock movement freedom.
		# Skip companies another internal supplier already represents — else
		# get_internal_party may hand the PR a supplier differing from the PO's.
		cls.other_co = None
		for candidate in frappe.get_all(
			"Company",
			filters={
				"name": ("!=", cls.company),
				"default_currency": frappe.db.get_value("Company", cls.company, "default_currency"),
			},
			pluck="name",
			order_by="creation",
		):
			if not frappe.get_all(
				"Supplier",
				filters={"is_internal_supplier": 1, "represents_company": candidate},
				pluck="name",
			):
				cls.other_co = candidate
				break
		cls.other_fiscal_year = cls._ensure_fy(cls.other_co) if cls.other_co else None
		cls.other_warehouse = None
		if cls.other_co:
			cls.ic_item = cls._make_item(sales=True).name
			parent = frappe.db.get_value("Warehouse", {"company": cls.other_co, "is_group": 1}, "name")
			if parent:
				cls.other_warehouse = (
					frappe.get_doc(
						{
							"doctype": "Warehouse",
							"warehouse_name": f"IC PR Test {cls._uniq()}",
							"company": cls.other_co,
							"is_group": 0,
							"parent_warehouse": parent,
						}
					)
					.insert()
					.name
				)
		# the SO built from the PO is addressed to an internal customer that
		# represents this (buying) company — reuse the site's, else create one
		cls.created_ic_customer = None
		if cls.other_co and cls.other_warehouse:
			cls.ic_customer = frappe.db.get_value(
				"Customer",
				{"is_internal_customer": 1, "represents_company": cls.company, "disabled": 0},
				"name",
			)
			if not cls.ic_customer:
				customer_group = frappe.db.get_value("Customer Group", {"is_group": 0}, "name")
				territory = frappe.db.get_value("Territory", {"is_group": 0}, "name")
				if customer_group and territory:
					cls.created_ic_customer = (
						frappe.get_doc(
							{
								"doctype": "Customer",
								"customer_name": f"IC PR Customer {cls._uniq()}",
								"customer_group": customer_group,
								"territory": territory,
								"is_internal_customer": 1,
								"represents_company": cls.company,
							}
						)
						.insert()
						.name
					)
					cls.ic_customer = cls.created_ic_customer
		cls.orig_negative_stock = frappe.db.get_single_value("Stock Settings", "allow_negative_stock")
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)

	@classmethod
	def _ensure_fy(cls, company):
		"""This site's test company may lack an active fiscal year — the accounts
		validate (validate_date_with_fiscal_year) would reject every document."""
		if not company:
			return None
		covering_fys = frappe.get_all(
			"Fiscal Year",
			filters={
				"disabled": 0,
				"year_start_date": ["<=", nowdate()],
				"year_end_date": [">=", nowdate()],
			},
			pluck="name",
		)
		if covering_fys and frappe.get_all(
			"Fiscal Year Company",
			filters={"company": company, "parent": ["in", covering_fys]},
			pluck="parent",
		):
			return None
		year = getdate(nowdate()).year
		return (
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": f"POS PR Test FY {cls._uniq()}",
					"year_start_date": f"{year}-01-01",
					"year_end_date": f"{year}-12-31",
					"companies": [{"company": company}],
				}
			)
			.insert()
			.name
		)

	@classmethod
	def _make_item(cls, sales=False):
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"PR-T-{cls._uniq()}",
				"item_name": f"PR Proxy Item {cls._uniq()}",
				# creation-asc: an unordered first row can land on a fixture
				# group carrying default Item Tax rows, which silently adds
				# tax rows (and tax money) to every PO built from the item
				"item_group": frappe.db.get_value(
					"Item Group", {"is_group": 0}, "name", order_by="creation asc"
				),
				"stock_uom": frappe.db.get_value("UOM", "Unit", "name") or "Nos",
				"is_stock_item": 1,
				"is_purchase_item": 1,
				"is_sales_item": 1 if sales else 0,
			}
		).insert()

	@staticmethod
	def _uniq():
		return uuid.uuid4().hex[:8]

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		# explicit-rate POs auto-insert Item Prices — drop them with the items
		for item in [getattr(cls, "item", None), getattr(cls, "ic_item", None)]:
			if not item:
				continue
			for price in frappe.get_all("Item Price", filters={"item_code": item}, pluck="name"):
				frappe.delete_doc("Item Price", price, force=1)
		for doctype, name in [
			("Item", getattr(cls, "item", None)),
			("Item", getattr(cls, "ic_item", None)),
			("Customer", getattr(cls, "created_ic_customer", None)),
			("Warehouse", getattr(cls, "other_warehouse", None)),
			("Supplier", getattr(cls, "supplier", None)),
			("Supplier", getattr(cls, "ext_supplier", None)),
			("User", getattr(cls, "user", None)),
			("User", getattr(cls, "cashier_user", None)),
			("User", getattr(cls, "manager_user", None)),
			("Fiscal Year", getattr(cls, "fiscal_year", None)),
			("Fiscal Year", getattr(cls, "other_fiscal_year", None)),
		]:
			if name and frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=1)
		frappe.db.set_single_value(
			"Accounts Settings", "delete_linked_ledger_entries", cls.orig_delete_ledger
		)
		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", cls.orig_negative_stock)
		super().tearDownClass()

	def setUp(self):
		self.pos = []
		# cashier-persona tests run outside a real shift; the shift gate has
		# its own tests (test_shift_gate_*)
		shift = mock.patch("pos_next.api.purchase_orders._has_open_shift", return_value=True)
		self.has_open_shift = shift.start()
		self.addCleanup(shift.stop)
		# the company a purchase acts for: the cashier's (mocked) shift company,
		# and for the manager persona a User Permission on the test company
		shift_profiles = mock.patch(
			"pos_next.api.purchase_orders._open_shift_profiles",
			return_value=[frappe._dict(pos_profile=self.pos_profile, company=self.company)],
		)
		self.open_shift_profiles = shift_profiles.start()
		self.addCleanup(shift_profiles.stop)
		permitted = mock.patch("pos_next.hq_scope.get_permitted_companies", return_value=[self.company])
		permitted.start()
		self.addCleanup(permitted.stop)
		self.receipts = []
		self.sos = []
		self.dns = []
		self.ic_restores = []

	def tearDown(self):
		# every voucher this test created is cancelled + deleted (suite runs on a
		# live dev site); leaves first — PR links to the DN, the DN to the SO,
		# the SO back to the PO
		frappe.set_user(ADMIN)
		for restore in self.ic_restores:
			restore()
		for doctype, names in (
			("Purchase Receipt", self.receipts),
			("Delivery Note", self.dns),
			("Sales Order", self.sos),
			("Purchase Order", self.pos),
		):
			for name in names:
				if not frappe.db.exists(doctype, name):
					continue
				if frappe.db.get_value(doctype, name, "docstatus") == 1:
					frappe.get_doc(doctype, name).cancel()
				frappe.delete_doc(doctype, name, force=1)

	def _po(self, qty=2, submit=True, item=None, supplier=None):
		doc = frappe.get_doc(
			{
				"doctype": "Purchase Order",
				"supplier": supplier or self.supplier,
				"company": self.company,
				"currency": frappe.get_cached_value("Company", self.company, "default_currency"),
				"schedule_date": add_days(nowdate(), 1),
				"set_warehouse": self.warehouse,
				"items": [
					{"item_code": item or self.item, "qty": qty, "rate": 25, "warehouse": self.warehouse}
				],
			}
		)
		doc.set_missing_values()
		doc.insert()
		if submit:
			doc.submit()
		self.pos.append(doc.name)
		return {"name": doc.name}

	def _ic_flip_supplier(self, internal):
		supp = frappe.get_doc("Supplier", self.supplier)
		supp.is_internal_supplier = 1 if internal else 0
		supp.represents_company = self.other_co if internal else None
		supp.set("companies", [{"company": self.company}] if internal else [])
		supp.save()

	def _ic_po(self, qty=2, deliver=True):
		"""Internal-supplier PO here + factory SO/DN in the second company."""
		if not (self.other_co and self.other_warehouse and self.ic_customer):
			raise unittest.SkipTest("no intercompany pair available on this site")
		self._ic_flip_supplier(1)
		self.ic_restores.append(lambda: self._ic_flip_supplier(0))
		po = self._po(qty=qty, item=self.ic_item)
		price_list = frappe.db.get_value("Purchase Order", po["name"], "buying_price_list")
		was_selling = frappe.db.get_value("Price List", price_list, "selling") if price_list else None
		if price_list and not was_selling:
			frappe.db.set_value("Price List", price_list, "selling", 1)
			self.ic_restores.append(lambda: frappe.db.set_value("Price List", price_list, "selling", 0))

		so = make_inter_company_sales_order(po["name"])
		# the mapper carries no delivery date — SO validate requires one
		so.delivery_date = nowdate()
		for row in so.items:
			row.warehouse = self.other_warehouse
			row.delivery_date = nowdate()
		so.insert()
		self.sos.append(so.name)
		so.submit()
		if deliver:
			self._dispatch(so.name)
		return po

	def _dispatch(self, so_name, qty=None, submit=True):
		dn = make_delivery_note(so_name)
		if qty is not None:
			for row in dn.items:
				row.qty = qty
		dn.insert()
		self.dns.append(dn.name)
		if submit:
			dn.submit()
		return dn

	def _shipping(self, po_name):
		return get_purchase_order(po_name)["shipping_status"]

	def test_shipping_status_follows_so_dn_pr(self):
		draft_po = self._po(submit=False)
		self.assertIsNone(_shipping_statuses([draft_po["name"]]).get(draft_po["name"]))  # draft PO
		po = self._ic_po(deliver=False)["name"]
		so_name = self.sos[-1]
		self.assertEqual(self._shipping(po), "preparing")  # submitted SO, nothing shipped
		self._dispatch(so_name, qty=1, submit=False)
		self.assertEqual(self._shipping(po), "preparing")  # a draft DN is not a dispatch
		self._dispatch(so_name, qty=1)
		self.assertEqual(self._shipping(po), "in_transit")
		self._save(self._payload(get_intercompany_receipt_draft(po)), submit=1)
		# the shipment arrived but 1 of 2 is not dispatched yet
		self.assertEqual(self._shipping(po), "preparing")
		self.assertNotEqual(frappe.db.get_value("Purchase Order", po, "per_received"), 100)
		frappe.get_doc("Delivery Note", self.dns[0]).delete()  # drop the stale draft
		self.dns.pop(0)
		self._dispatch(so_name)
		self.assertEqual(self._shipping(po), "in_transit")
		last = self._save(self._payload(get_intercompany_receipt_draft(po)), submit=1)["name"]
		self.assertEqual(self._shipping(po), "received")
		if self.pos_profile:
			row = next(
				o
				for o in get_purchase_orders(pos_profile=self.pos_profile)["orders"]
				if o["name"] == po
			)
			self.assertEqual(row["shipping_status"], "received")
		frappe.get_doc("Purchase Receipt", last).cancel()
		self.assertEqual(self._shipping(po), "in_transit")  # cancelled PR is ignored

	def test_shipping_status_after_return_and_closed_so(self):
		from erpnext.controllers.sales_and_purchase_return import make_return_doc

		po = self._ic_po()["name"]
		pr = self._save(self._payload(get_intercompany_receipt_draft(po)), submit=1)["name"]
		self.assertEqual(self._shipping(po), "received")
		ret = make_return_doc("Purchase Receipt", pr)
		for row in ret.items:
			row.qty = -1
			row.received_qty = -1
		ret.insert()
		ret.submit()
		self.receipts.insert(0, ret.name)  # cancel the return before its original
		# a returned unit is outstanding again; the signed return row nets once
		self.assertEqual(
			flt(frappe.db.get_value("Purchase Order Item", {"parent": po}, "received_qty")), 1
		)
		self.assertNotEqual(self._shipping(po), "received")

		po2 = self._ic_po(deliver=False)["name"]
		so2 = frappe.get_doc("Sales Order", self.sos[-1])
		so2.update_status("Closed")
		self.ic_restores.append(lambda: frappe.get_doc("Sales Order", so2.name).update_status("Draft"))  # unclose to cancel
		self.assertIsNone(self._shipping(po2))  # a closed SO ships nothing more

	def test_shipping_status_is_coarse_for_cashier(self):
		po = self._ic_po()["name"]
		ext = self._po(supplier=self.ext_supplier)["name"]
		self.assertIsNone(_shipping_statuses([ext])[ext])  # no factory SO
		# fully received in Desk without a factory SO is still untracked
		self._save(self._payload(get_purchase_receipt_draft(ext)), submit=1)
		self.assertEqual(flt(frappe.db.get_value("Purchase Order", ext, "per_received")), 100)
		self.assertIsNone(_shipping_statuses([ext])[ext])
		frappe.set_user(self.cashier_user)
		try:
			self.assertEqual(self._shipping(po), "in_transit")
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(_shipping_statuses([]), {})

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
					"delivery_note_item": row["delivery_note_item"],
				}
				for row in draft["items"]
			],
		}
		if draft.get("inter_company_reference"):
			data["inter_company_reference"] = draft["inter_company_reference"]
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
				"delivery_note_item",
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
		self.assertFalse(row["delivery_note_item"])  # PO-sourced draft has no DN links
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
		self.assertNotIn(result["name"], [r["name"] for r in receipts])
		if self.pos_profile:
			scoped = get_purchase_receipts(pos_profile=self.pos_profile)["receipts"]
			self.assertNotIn(result["name"], [r["name"] for r in scoped])

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
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
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
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		name = self._save(self._payload(draft), submit=1)["name"]
		result = cancel_purchase_receipt(name)
		self.assertEqual(result["docstatus"], 2)
		self.assertEqual(result["status"], "Cancelled")
		per_received = frappe.db.get_value("Purchase Order", po["name"], "per_received")
		self.assertEqual(flt(per_received), 0)
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

		# a row UOM the item cannot convert would silently book factor 1
		stock_uom = frappe.db.get_value("Item", self.item, "stock_uom")
		bad_uom = frappe.db.get_value("UOM", {"uom_name": ("!=", stock_uom)}, "uom_name")
		with self.assertRaises(ValidationError):
			save_purchase_receipt(
				{
					"supplier": self.supplier,
					"company": self.company,
					"items": [{"item_code": self.item, "qty": 1, "uom": bad_uom}],
				}
			)

	def test_internal_supplier_po_refused(self):
		# intercompany receipts are born from the selling company's Delivery
		# Note — the POS receive flow must refuse them (double-receipt window)
		po = self._po()
		# flag the supplier AFTER the PO exists: the guard reads the live master,
		# not the snapshot fetched onto the PO at save time
		frappe.db.set_value("Supplier", self.supplier, "is_internal_supplier", 1)
		try:
			with self.assertRaises(ValidationError) as ctx:
				get_purchase_receipt_draft(po["name"])
			self.assertIn("Delivery Note", str(ctx.exception))
			# the create path is guarded even for a hand-crafted payload — with
			# PO links and on the bare supplier alone
			hand_crafted = {
				"supplier": self.supplier,
				"posting_date": nowdate(),
				"company": self.company,
				"items": [
					{
						"item_code": self.item,
						"qty": 1,
						"rate": 25,
						"uom": self.uom,
						"warehouse": self.warehouse,
						"purchase_order": po["name"],
					}
				],
			}
			with self.assertRaises(ValidationError):
				save_purchase_receipt(hand_crafted)
			hand_crafted["items"][0].pop("purchase_order")
			with self.assertRaises(ValidationError):
				save_purchase_receipt(hand_crafted)
		finally:
			frappe.db.set_value("Supplier", self.supplier, "is_internal_supplier", 0)
		# with the flag gone the normal receive flow works again
		draft = get_purchase_receipt_draft(po["name"])
		self.assertEqual(flt(draft["items"][0]["pending_qty"]), 2)

	def test_intercompany_draft_guards(self):
		po = self._po()
		# not an internal supplier -> this flow does not apply
		with self.assertRaises(ValidationError) as ctx:
			get_intercompany_receipt_draft(po["name"])
		self.assertIn("not from an internal supplier", str(ctx.exception))

		# internal, but the selling company hasn't made its Sales Order yet
		self._ic_flip_supplier(1)
		self.ic_restores.append(lambda: self._ic_flip_supplier(0))
		with self.assertRaises(ValidationError) as ctx:
			get_intercompany_receipt_draft(po["name"])
		self.assertIn("No Sales Order", str(ctx.exception))

		# the save-path DN reference must name a submitted Delivery Note
		with self.assertRaises(ValidationError) as ctx:
			save_purchase_receipt(
				{
					"supplier": self.supplier,
					"posting_date": nowdate(),
					"company": self.company,
					"inter_company_reference": "DN-DOES-NOT-EXIST",
					"items": [{"item_code": self.item, "qty": 1, "rate": 25, "uom": self.uom}],
				}
			)
		self.assertIn("is not submitted", str(ctx.exception))

	def test_intercompany_receive_from_delivery_note(self):
		po = self._ic_po()

		def list_row():
			return next(
				o
				for o in get_purchase_orders(pos_profile=self.pos_profile)["orders"]
				if o["name"] == po["name"]
			)

		# the list carries the live SO link and the DN readiness the POS
		# Receive gate keys off — skip silently on sites without a profile
		if self.pos_profile:
			row = list_row()
			so_name = frappe.db.get_value(
				"Sales Order", {"inter_company_order_reference": po["name"]}, "name"
			)
			self.assertEqual(row["inter_company_order_reference"], so_name)
			self.assertTrue(row["delivery_ready"])  # a pending DN exists

		draft = get_intercompany_receipt_draft(po["name"])
		dn_name = draft["inter_company_reference"]
		self.assertTrue(dn_name)
		row = draft["items"][0]
		self.assertEqual(row["item_code"], self.ic_item)
		self.assertEqual(row["purchase_order"], po["name"])  # PO link reconstructed
		self.assertTrue(row["purchase_order_item"])
		self.assertTrue(row["delivery_note_item"])  # DN row link — updates DN received_qty
		self.assertEqual(flt(row["pending_qty"]), 2)
		self.assertEqual(row["warehouse"], self.warehouse)  # receiving side from the PO
		self.assertEqual(
			frappe.db.get_value("Supplier", draft["supplier"], "represents_company"), self.other_co
		)

		result = self._save(self._payload(draft), submit=1)
		self.assertEqual(result["docstatus"], 1)
		# the DN <-> PR cross link survived the POS save
		self.assertEqual(
			frappe.db.get_value("Purchase Receipt", result["name"], "inter_company_reference"), dn_name
		)
		per_received = frappe.db.get_value("Purchase Order", po["name"], "per_received")
		self.assertEqual(flt(per_received), 100)
		# both sides of the chain updated: the PO fully received…
		self.assertEqual(frappe.db.get_value("Purchase Order", po["name"], "status"), "To Bill")
		# …and the factory's DN row marked received
		dn_item = frappe.db.get_value(
			"Delivery Note Item", {"parent": dn_name}, ["name", "received_qty"], as_dict=True
		)
		self.assertEqual(flt(dn_item.received_qty), 2)

		# DN fully received -> the list no longer flags delivery readiness
		if self.pos_profile:
			self.assertFalse(list_row()["delivery_ready"])

		# fully received -> no pending DN draft anymore
		with self.assertRaises(ValidationError) as ctx:
			get_intercompany_receipt_draft(po["name"])
		self.assertIn("No pending Delivery Note", str(ctx.exception))

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

	def test_list_excludes_external_suppliers(self):
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		internal_pr = self._save(self._payload(draft), submit=1)["name"]

		ext_pr = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": self.ext_supplier,
				"company": self.company,
				"currency": frappe.get_cached_value("Company", self.company, "default_currency"),
				"items": [{"item_code": self.item, "qty": 1, "rate": 10, "warehouse": self.warehouse}],
			}
		)
		ext_pr.set_missing_values()
		ext_pr.insert()
		self.receipts.append(ext_pr.name)

		receipts = get_purchase_receipts()["receipts"]
		receipt_names = [r["name"] for r in receipts]
		self.assertIn(internal_pr, receipt_names)
		self.assertNotIn(ext_pr.name, receipt_names)

	def test_get_external_pr_refused(self):
		ext_pr = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": self.ext_supplier,
				"company": self.company,
				"currency": frappe.get_cached_value("Company", self.company, "default_currency"),
				"items": [{"item_code": self.item, "qty": 1, "rate": 10, "warehouse": self.warehouse}],
			}
		)
		ext_pr.set_missing_values()
		ext_pr.insert()
		self.receipts.append(ext_pr.name)

		with self.assertRaises(ValidationError) as ctx:
			get_purchase_receipt(ext_pr.name)
		self.assertIn("external supplier", str(ctx.exception))

	def test_external_pr_mutations_refused(self):
		ext_pr = frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"supplier": self.ext_supplier,
				"company": self.company,
				"currency": frappe.get_cached_value("Company", self.company, "default_currency"),
				"items": [{"item_code": self.item, "qty": 1, "rate": 10, "warehouse": self.warehouse}],
			}
		)
		ext_pr.set_missing_values()
		ext_pr.insert()
		self.receipts.append(ext_pr.name)

		with self.assertRaises(ValidationError) as ctx:
			save_purchase_receipt(
				{
					"name": ext_pr.name,
					"supplier": self.ext_supplier,
					"company": self.company,
					"items": [{"item_code": self.item, "qty": 2, "warehouse": self.warehouse}],
				}
			)
		self.assertIn("external supplier", str(ctx.exception))

		png_base = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
		with self.assertRaises(ValidationError) as ctx:
			attach_purchase_files(
				"Purchase Receipt",
				ext_pr.name,
				json.dumps([{"file_name": "surat.png", "filedata": png_base}]),
			)
		self.assertIn("external supplier", str(ctx.exception))

		ext_pr.submit()
		with self.assertRaises(ValidationError) as ctx:
			cancel_purchase_receipt(ext_pr.name)
		self.assertIn("external supplier", str(ctx.exception))

	def test_cashier_sees_no_prices(self):
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		payload = self._payload(draft)
		result = self._save(payload)
		po_normal = self._po(supplier=self.ext_supplier)

		frappe.set_user(self.cashier_user)
		try:
			summary = get_purchase_receipt(result["name"])
			for k in (
				"rate",
				"amount",
				"net_total",
				"total_taxes_and_charges",
				"grand_total",
				"price_list_rate",
				"price_list",
				"taxes_and_charges",
				"buying_price_list",
			):
				self.assertNotIn(k, summary)
			self.assertTrue(summary["items"])
			for item in summary["items"]:
				for k in (
					"rate",
					"amount",
					"net_total",
					"total_taxes_and_charges",
					"grand_total",
					"price_list_rate",
					"price_list",
					"taxes_and_charges",
					"buying_price_list",
				):
					self.assertNotIn(k, item)

			receipts = get_purchase_receipts()["receipts"]
			self.assertTrue(receipts)
			for r in receipts:
				for k in (
					"rate",
					"amount",
					"net_total",
					"total_taxes_and_charges",
					"grand_total",
					"price_list_rate",
					"price_list",
					"taxes_and_charges",
					"buying_price_list",
				):
					self.assertNotIn(k, r)

			ic_draft = get_intercompany_receipt_draft(po["name"])
			self.assertTrue(ic_draft["items"])
			for item in ic_draft["items"]:
				for k in (
					"rate",
					"amount",
					"net_total",
					"total_taxes_and_charges",
					"grand_total",
					"price_list_rate",
					"price_list",
					"taxes_and_charges",
					"buying_price_list",
				):
					self.assertNotIn(k, item)

			normal_draft = get_purchase_receipt_draft(po_normal["name"])
			self.assertTrue(normal_draft["items"])
			for item in normal_draft["items"]:
				for k in (
					"rate",
					"amount",
					"net_total",
					"total_taxes_and_charges",
					"grand_total",
					"price_list_rate",
					"price_list",
					"taxes_and_charges",
					"buying_price_list",
				):
					self.assertNotIn(k, item)

			# save (edit) response hides prices for cashier
			saved = save_purchase_receipt(self._payload(draft, name=result["name"]))
			for k in (
				"rate",
				"amount",
				"net_total",
				"total_taxes_and_charges",
				"grand_total",
				"price_list_rate",
				"price_list",
				"taxes_and_charges",
				"buying_price_list",
			):
				self.assertNotIn(k, saved)
			for item in saved["items"]:
				for k in ("rate", "amount", "price_list_rate"):
					self.assertNotIn(k, item)

			# submit response hides prices for cashier
			submitted = submit_purchase_receipt(saved["name"])
			for k in (
				"rate",
				"amount",
				"net_total",
				"total_taxes_and_charges",
				"grand_total",
				"price_list_rate",
				"price_list",
				"taxes_and_charges",
				"buying_price_list",
			):
				self.assertNotIn(k, submitted)
		finally:
			frappe.set_user(ADMIN)

	def test_tampered_pr_rate_ignored(self):
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		dn_item_id = draft["items"][0]["delivery_note_item"]
		original_dn_rate = frappe.db.get_value("Delivery Note Item", dn_item_id, "rate")
		self.assertNotEqual(original_dn_rate, 1)

		payload = self._payload(draft)
		payload["items"][0]["rate"] = 1
		frappe.set_user(self.cashier_user)
		try:
			result = self._save(payload)
		finally:
			frappe.set_user(ADMIN)

		saved_rate = frappe.db.get_value(
			"Purchase Receipt Item",
			{"parent": result["name"], "delivery_note_item": dn_item_id},
			"rate",
		)
		self.assertEqual(flt(saved_rate), flt(original_dn_rate))

	def _setup_pr_workflow(self, allowed_role="System Manager"):
		wf_name = "_PNXT PR Test"
		wf_snapshot = frappe.get_all(
			"Workflow",
			filters={"document_type": "Purchase Receipt"},
			fields=["name", "is_active"],
		)
		for state in ("_PNXT Draft", "_PNXT Pending"):
			if not frappe.db.exists("Workflow State", state):
				frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state}).insert(
					ignore_permissions=True
				)
		if not frappe.db.exists("Workflow Action Master", "_PNXT Send"):
			frappe.get_doc(
				{"doctype": "Workflow Action Master", "workflow_action_name": "_PNXT Send"}
			).insert(ignore_permissions=True)

		if frappe.db.exists("Workflow", wf_name):
			frappe.delete_doc("Workflow", wf_name, force=True, ignore_permissions=True)

		frappe.db.set_value("Workflow", {"document_type": "Purchase Receipt"}, "is_active", 0)

		wf = frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": wf_name,
				"document_type": "Purchase Receipt",
				"workflow_state_field": "workflow_state",
				"is_active": 1,
				"send_email_alert": 0,
				"states": [
					{"state": "_PNXT Draft", "doc_status": 0, "allow_edit": "All"},
					{"state": "_PNXT Pending", "doc_status": 0, "allow_edit": "System Manager"},
				],
				"transitions": [
					{
						"state": "_PNXT Draft",
						"action": "_PNXT Send",
						"next_state": "_PNXT Pending",
						"allowed": allowed_role,
						"allow_self_approval": 1,
					}
				],
			}
		).insert(ignore_permissions=True)

		frappe.cache.hdel("workflow", "Purchase Receipt")
		frappe.clear_cache(doctype="Purchase Receipt")

		def cleanup():
			if frappe.db.exists("Workflow", wf_name):
				frappe.delete_doc("Workflow", wf_name, force=True, ignore_permissions=True)
			for w in wf_snapshot:
				if frappe.db.exists("Workflow", w.name):
					frappe.db.set_value("Workflow", w.name, "is_active", w.is_active)
			frappe.cache.hdel("workflow", "Purchase Receipt")
			frappe.clear_cache(doctype="Purchase Receipt")

		self.addCleanup(cleanup)
		return wf

	def test_submit_without_workflow(self):
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		pr_name = self._save(self._payload(draft))["name"]
		res = submit_purchase_receipt(pr_name)
		self.assertEqual(res["docstatus"], 1)
		self.assertEqual(frappe.db.get_value("Purchase Receipt", pr_name, "docstatus"), 1)

	def test_submit_with_active_workflow(self):
		self._setup_pr_workflow(allowed_role="System Manager")
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		pr_name = self._save(self._payload(draft))["name"]

		summary = get_purchase_receipt(pr_name)
		self.assertEqual(summary["submit_actions"], ["_PNXT Send"])
		self.assertTrue(summary["editable"])

		res = submit_purchase_receipt(pr_name)
		self.assertEqual(res["workflow_state"], "_PNXT Pending")
		self.assertEqual(res["docstatus"], 0)
		self.assertFalse(res["editable"])

	def test_submit_with_workflow_no_action_for_role(self):
		self._setup_pr_workflow(allowed_role="Stock Manager")
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		pr_name = self._save(self._payload(draft))["name"]

		frappe.set_user(self.cashier_user)
		try:
			with self.assertRaises(ValidationError):
				submit_purchase_receipt(pr_name)
		finally:
			frappe.set_user(ADMIN)

	def test_po_summary_lists_receipts(self):
		po = self._ic_po()
		draft = get_intercompany_receipt_draft(po["name"])
		pr = self._save(self._payload(draft))

		po_summary = get_purchase_order(po["name"])
		self.assertIn("receipts", po_summary)
		self.assertTrue(po_summary["receipts"])
		self.assertEqual(po_summary["receipts"][0]["name"], pr["name"])
		self.assertEqual(po_summary["receipts"][0]["docstatus"], 0)
		self.assertIn("workflow_state", po_summary["receipts"][0])
