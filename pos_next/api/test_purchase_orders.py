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
	get_po_defaults,
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
		cls.stock_uom = item.stock_uom

		# the production site carries a per-item custom default UOM
		# (custom_default_uom_warehouse) — recreate it locally so the fallback
		# chain is testable, plus a convertible Box UOM on the fixture item.
		# create_custom_fields (not a plain insert): something in this bench
		# recreates the same field on Item inserts, and the helper is idempotent.
		from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

		create_custom_fields(
			{
				"Item": [
					{
						"fieldname": "custom_default_uom_warehouse",
						"label": "Default Purchase UOM",
						"fieldtype": "Link",
						"options": "UOM",
						"insert_after": "stock_uom",
					}
				]
			}
		)
		cls.custom_uom_field = "Item-custom_default_uom_warehouse"
		cls.uom_box = f"PR Box {cls._uniq()}"
		frappe.get_doc({"doctype": "UOM", "uom_name": cls.uom_box}).insert()
		item = frappe.get_doc("Item", cls.item)
		item.append("uoms", {"uom": cls.uom_box, "conversion_factor": 10})
		item.save()
		item_uoms = set(frappe.get_all("UOM Conversion Detail", filters={"parent": cls.item}, pluck="uom"))
		cls.uom_invalid = next(
			(u for u in ("Nos", "Meter", "Kg", "Pcs") if frappe.db.exists("UOM", u) and u not in item_uoms),
			None,
		)

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

		cls.tax_template = None
		tax_account = frappe.db.get_value(
			"Account", {"company": cls.company, "account_type": "Tax", "is_group": 0, "disabled": 0}, "name"
		)
		if tax_account:
			cls.tax_template = (
				frappe.get_doc(
					{
						"doctype": "Purchase Taxes and Charges Template",
						"title": f"POS PO Test Tax {cls._uniq()}",
						"company": cls.company,
						"is_default": 0,
						"taxes": [
							{
								"category": "Total",
								"add_deduct_tax": "Add",
								"charge_type": "On Net Total",
								"account_head": tax_account,
								"rate": 5,
								"description": "POS PO test tax",
							}
						],
					}
				)
				.insert()
				.name
			)

	@classmethod
	def _make_item(cls):
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"PO-T-{cls._uniq()}",
				"item_name": f"PO Proxy Item {cls._uniq()}",
				# creation-asc: an unordered first row can land on a fixture
				# group carrying default Item Tax rows, which silently adds
				# tax rows (and tax money) to every PO built from the item
				"item_group": frappe.db.get_value(
					"Item Group", {"is_group": 0}, "name", order_by="creation asc"
				),
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
			("Purchase Taxes and Charges Template", getattr(cls, "tax_template", None)),
			("Item", getattr(cls, "item", None)),
			("Item", getattr(cls, "priced_item", None)),
			# tolerant teardown — the bench's mystery recreator may own it too
			(
				"Custom Field",
				frappe.db.get_value(
					"Custom Field", {"dt": "Item", "fieldname": "custom_default_uom_warehouse"}, "name"
				),
			),
			("UOM", getattr(cls, "uom_box", None)),
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

	def test_tax_template_explicit_set_and_clear_on_full_payload(self):
		if not self.tax_template:
			self.skipTest("no tax account for a template fixture on this site")

		# create path: empty template means no template and no tax rows
		result = self._save(self._data(taxes_and_charges=""))
		doc = frappe.get_doc("Purchase Order", result["name"])
		self.assertIsNone(doc.taxes_and_charges)
		self.assertEqual(doc.taxes, [])

		# update: template set → rows expanded by set_missing_values
		name = self._save(self._data(taxes_and_charges=self.tax_template))["name"]
		# summary must round-trip the template so the POS edit flow can prefill it
		self.assertEqual(get_purchase_order(name)["taxes_and_charges"], self.tax_template)
		doc = frappe.get_doc("Purchase Order", name)
		self.assertEqual(doc.taxes_and_charges, self.tax_template)
		self.assertTrue(doc.taxes)

		# update with present-but-empty template: cleared, stale rows dropped,
		# and the save's set_missing_values must not re-expand them
		self._save(self._data(name=name, taxes_and_charges=""))
		doc.reload()
		self.assertIsNone(doc.taxes_and_charges)
		self.assertEqual(doc.taxes, [])
		self.assertIsNone(get_purchase_order(name)["taxes_and_charges"])

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

	def test_pos_settings_supply_po_defaults(self):
		"""POS Settings (per profile) is the default supplier/warehouse source."""
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")

		# a different leaf warehouse proves the setting wins over the profile's
		alt_warehouse = frappe.db.get_value(
			"Warehouse",
			{"company": self.company, "is_group": 0, "disabled": 0, "name": ["!=", self.warehouse]},
			"name",
		)
		target_warehouse = alt_warehouse or self.warehouse

		settings_name = frappe.db.get_value("POS Settings", {"pos_profile": self.pos_profile}, "name")
		created = False
		orig = None
		try:
			if settings_name:
				orig = frappe.db.get_value(
					"POS Settings",
					settings_name,
					[
						"enabled",
						"po_default_supplier",
						"po_default_warehouse",
						"po_receive_requires_delivery_note",
					],
					as_dict=True,
				)
			else:
				settings_name = (
					frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.pos_profile}).insert().name
				)
				created = True
			frappe.db.set_value(
				"POS Settings",
				settings_name,
				{
					"enabled": 1,
					"po_default_supplier": self.supplier,
					"po_default_warehouse": target_warehouse,
					"po_receive_requires_delivery_note": 1,
				},
			)

			defaults = get_po_defaults(self.pos_profile)
			self.assertEqual(defaults["supplier"], self.supplier)
			self.assertEqual(defaults["supplier_name"], self.supplier_name)
			self.assertEqual(defaults["warehouse"], target_warehouse)
			# the Receive-gate flag rides along for the POS button
			self.assertEqual(defaults["receive_requires_delivery_note"], 1)

			# payload omits supplier AND set_warehouse — settings drive the PO
			result = self._save(
				{"items": [{"item_code": self.item, "qty": 1, "rate": 10}]},
				pos_profile=self.pos_profile,
			)
			self.assertEqual(result["supplier"], self.supplier)
			self.assertEqual(result["set_warehouse"], target_warehouse)
			self.assertEqual(result["items"][0]["warehouse"], target_warehouse)
		finally:
			if created and settings_name:
				frappe.delete_doc("POS Settings", settings_name, force=1)
			elif settings_name and orig:
				frappe.db.set_value(
					"POS Settings",
					settings_name,
					{
						"enabled": orig.enabled,
						"po_default_supplier": orig.po_default_supplier,
						"po_default_warehouse": orig.po_default_warehouse,
						"po_receive_requires_delivery_note": orig.po_receive_requires_delivery_note,
					},
				)

	def test_purchase_item_details_default_uom(self):
		# no custom default set -> the stock UOM, and the item's UOMs ride along
		details = get_purchase_item_details(self.item)
		self.assertEqual(details["default_uom"], self.stock_uom)
		self.assertEqual(details["uom"], self.stock_uom)
		self.assertIn(self.uom_box, {d["uom"] for d in details["uoms"]})

		# the per-item custom default wins and prices in that UOM already
		frappe.db.set_value("Item", self.item, "custom_default_uom_warehouse", self.uom_box)
		try:
			details = get_purchase_item_details(self.item)
			self.assertEqual(details["default_uom"], self.uom_box)
			self.assertEqual(details["uom"], self.uom_box)
			self.assertEqual(flt(details["conversion_factor"]), 10)

			# an explicit cashier pick overrides the custom default
			details = get_purchase_item_details(self.item, uom=self.stock_uom)
			self.assertEqual(details["uom"], self.stock_uom)

			# a custom default the item can't convert falls back to the stock UOM
			if self.uom_invalid:
				frappe.db.set_value("Item", self.item, "custom_default_uom_warehouse", self.uom_invalid)
				details = get_purchase_item_details(self.item)
				self.assertEqual(details["default_uom"], self.stock_uom)
		finally:
			frappe.db.set_value("Item", self.item, "custom_default_uom_warehouse", None)

	def test_pos_settings_price_list_drives_the_po(self):
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		pl = (
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": f"POS PO Test PL {self._uniq()}",
					"buying": 1,
					"selling": 0,
					"currency": frappe.get_cached_value("Company", self.company, "default_currency"),
				}
			)
			.insert()
			.name
		)
		settings_name = frappe.db.get_value("POS Settings", {"pos_profile": self.pos_profile}, "name")
		created = False
		orig = None
		try:
			if settings_name:
				orig = frappe.db.get_value(
					"POS Settings",
					settings_name,
					["enabled", "po_default_price_list"],
					as_dict=True,
				)
			else:
				settings_name = (
					frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.pos_profile}).insert().name
				)
				created = True
			frappe.db.set_value("POS Settings", settings_name, {"enabled": 1, "po_default_price_list": pl})

			self.assertEqual(get_po_defaults(self.pos_profile)["price_list"], pl)
			# the payload carries no price list — the setting drives the PO
			result = self._save(self._data(), pos_profile=self.pos_profile)
			self.assertEqual(frappe.db.get_value("Purchase Order", result["name"], "buying_price_list"), pl)
		finally:
			if created and settings_name:
				frappe.delete_doc("POS Settings", settings_name, force=1)
			elif settings_name and orig:
				frappe.db.set_value(
					"POS Settings",
					settings_name,
					{"enabled": orig.enabled, "po_default_price_list": orig.po_default_price_list},
				)
			frappe.delete_doc("Price List", pl, force=1)

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
				"default_uom",
				"uoms",
				"conversion_factor",
				"price_list_rate",
				"rate",
				"warehouse",
			},
		)
		self.assertEqual(details["item_code"], self.priced_item)
		self.assertEqual(details["uom"], "Unit")
		self.assertEqual(details["stock_uom"], "Unit")
		# no custom default on this item -> the stock UOM; conversion list rides along
		self.assertEqual(details["default_uom"], "Unit")
		self.assertIn("Unit", {d["uom"] for d in details["uoms"]})
		self.assertEqual(flt(details["conversion_factor"]), 1)
		self.assertEqual(flt(details["price_list_rate"]), 10)
		# standalone get_item_details leaves rate at 0 — the PO fills it on save
		self.assertEqual(flt(details["rate"]), 0)
		self.assertEqual(details["warehouse"], self.warehouse)

	def test_permission_denied_without_purchasing_role(self):
		name = self._save()["name"]
		# submitted while Administrator so cancel has a submitted doc to act on
		submit_purchase_order(name)
		frappe.set_user(self.user)
		try:
			with self.assertRaises(frappe.PermissionError):
				save_purchase_order(self._data())
			with self.assertRaises(frappe.PermissionError):
				get_purchase_order(name)
			with self.assertRaises(frappe.PermissionError):
				submit_purchase_order(name)
			with self.assertRaises(frappe.PermissionError):
				cancel_purchase_order(name)
		finally:
			frappe.set_user(ADMIN)
