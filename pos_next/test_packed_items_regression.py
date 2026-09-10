# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

"""Regression tests for duplicate rows in Sales Invoice Packed Items (Product Bundles)."""

from contextlib import contextmanager
from types import SimpleNamespace
import sys
import types as _types
import unittest

import frappe

# erpnext.tests.utils runs a full fixture bootstrap at import time
# (module-level BootStrapTestData()) that only works on disposable CI sites;
# on a live site it dies repeatedly (password policy, missing fixture cascade:
# _Test companies/accounts/fiscal years/items that the bootstrap itself never
# creates). Every erpnext test module we import below needs exactly one name
# from it -- ERPNextTestSuite -- so stub that one name to skip the bootstrap
# while keeping the real helper functions (create_sales_invoice, make_item...).
class _ERPNextTestSuiteStub(unittest.TestCase):
	@classmethod
	@contextmanager
	def change_settings(cls, doctype, settings_dict=None, /, **settings):
		"""Mirror of erpnext.tests.utils.ERPNextTestSuite.change_settings."""
		import copy

		if settings_dict is None:
			settings_dict = settings

		doc = frappe.get_doc(doctype)
		previous_settings = copy.deepcopy(settings_dict)
		for key in previous_settings:
			previous_settings[key] = getattr(doc, key)

		for key, value in settings_dict.items():
			setattr(doc, key, value)
		doc.save(ignore_permissions=True)
		try:
			yield
		finally:
			doc = frappe.get_doc(doctype)
			for key, value in previous_settings.items():
				setattr(doc, key, value)
			doc.save(ignore_permissions=True)


if "erpnext.tests.utils" not in sys.modules:
    _stub = _types.ModuleType("erpnext.tests.utils")
    _stub.ERPNextTestSuite = _ERPNextTestSuiteStub
    sys.modules["erpnext.tests.utils"] = _stub

from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.selling.doctype.product_bundle.test_product_bundle import make_product_bundle
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.packed_item import packed_item as packed_item_module
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

import pos_next  # noqa: F401 — ensure app hooks run (packed_item keying patch).


def _assert_no_duplicate_packed_rows(si):
	keys = []
	for row in si.get("packed_items") or []:
		keys.append((row.parent_item, row.item_code, row.parent_detail_docname))
	unique = set(keys)
	if len(keys) != len(unique):
		frappe.throw(f"Duplicate packed_items keys: {keys}")


def _sales_invoice_bundle_context():
	"""Use ERPNext test fixtures when present; otherwise resolve from the live site."""
	if frappe.db.exists("Warehouse", "_Test Warehouse - _TC"):
		return SimpleNamespace(
			company="_Test Company",
			warehouse="_Test Warehouse - _TC",
			customer="_Test Customer",
			debit_to="Debtors - _TC",
			income_account="Sales - _TC",
			expense_account="Cost of Goods Sold - _TC",
			cost_center="_Test Cost Center - _TC",
			naming_series="T-SINV-",
		)

	company = frappe.defaults.get_global_default("company") or frappe.db.get_value(
		"Company", {"name": ["!=", ""]}, "name"
	)
	if not company:
		frappe.throw("No Company found for packed_items regression test.")

	wh = frappe.db.sql(
		"""
		select name from `tabWarehouse`
		where company = %(company)s and disabled = 0 and is_group = 0
		order by (warehouse_name like '%%Stores%%') desc, modified desc
		limit 1
		""",
		{"company": company},
	)
	warehouse = wh[0][0] if wh else None
	if not warehouse:
		frappe.throw(f"No warehouse for company {company}.")

	# prefer a customer that is not internal and not restricted to other
	# companies ("Allowed To Transact With"); live sites commonly have
	# inter-company customers limited to specific companies
	customer = frappe.db.sql(
		"""
		select c.name from `tabCustomer` c
		where c.disabled = 0
		  and not coalesce(c.is_internal_customer, 0)
		  and not exists (
			select 1 from `tabAllowed To Transact With` a where a.parent = c.name
		  )
		order by c.modified desc limit 1
		"""
	)
	customer = customer[0][0] if customer else None
	if not customer:
		customer = frappe.db.get_value("Customer", {"disabled": 0}, "name", order_by="modified desc")
	if not customer:
		frappe.throw("No Customer found for packed_items regression test.")

	comp = frappe.get_doc("Company", company)
	debit_to = comp.default_receivable_account
	income_account = comp.default_income_account
	expense_account = frappe.db.get_value(
		"Account",
		{"company": company, "account_type": "Cost of Goods Sold", "is_group": 0},
		"name",
		order_by="creation asc",
	)
	cost_center = frappe.db.get_value(
		"Cost Center",
		{"company": company, "is_group": 0, "disabled": 0},
		"name",
		order_by="creation asc",
	)
	ns_field = frappe.get_meta("Sales Invoice").get_field("naming_series")
	naming_series = (ns_field.options or "ACC-SINV-.YYYY.-").split("\n")[0].strip()

	return SimpleNamespace(
		company=company,
		warehouse=warehouse,
		customer=customer,
		debit_to=debit_to,
		income_account=income_account,
		expense_account=expense_account,
		cost_center=cost_center,
		naming_series=naming_series,
	)


class TestPackedItemsNoDuplicates(FrappeTestCase):
	def test_strip_server_managed_fields_removes_packed_items(self):
		"""API payloads must not replay client packed_items (regenerated on save)."""
		from pos_next.api.invoices import _strip_server_managed_fields

		payload = {"name": "SINV-1", "packed_items": [{"item_code": "X", "qty": 1}]}
		cleaned = _strip_server_managed_fields(payload)
		self.assertNotIn("packed_items", cleaned)
		self.assertEqual(cleaned.get("name"), "SINV-1")

	def setUp(self):
		super().setUp()
		self.assertTrue(
			getattr(packed_item_module, "_pos_next_packed_item_keying_patched", False),
			msg="pos_next packed_item patch must be active (import pos_next before ERPNext saves).",
		)
		# the live site names items by series, which would override the explicit
		# _PNXB*/_PNXC* item codes this module keys on; flip for the test
		# (restored by the change_settings context manager on cleanup)
		_naming = _ERPNextTestSuiteStub.change_settings("Stock Settings", {"item_naming_by": "Item Code"})
		_naming.__enter__()
		self.addCleanup(_naming.__exit__, None, None, None)

	def _unique_codes(self):
		sfx = frappe.generate_hash(length=8)
		return f"_PNXB{sfx}", f"_PNXC{sfx}"

	def test_repeated_reload_save_no_duplicate_packed_items(self):
		"""Mimic POS saving a draft invoice multiple times; packed rows must stay deduped."""
		ctx = _sales_invoice_bundle_context()
		bundle_code, child_code = self._unique_codes()

		make_item(child_code, {"is_stock_item": 1, "stock_uom": "Nos", "uom": "Nos"})
		bundle_item = make_item(bundle_code, {"is_stock_item": 0, "stock_uom": "Nos", "uom": "Nos"})
		bundle_item.reload()
		for row in bundle_item.item_defaults:
			if row.company == ctx.company:
				row.default_warehouse = ctx.warehouse
				break
		else:
			bundle_item.append(
				"item_defaults",
				{"company": ctx.company, "default_warehouse": ctx.warehouse},
			)
		bundle_item.save(ignore_permissions=True)

		make_product_bundle(bundle_code, [child_code], qty=2)
		make_stock_entry(
			item_code=child_code,
			target=ctx.warehouse,
			qty=400,
			rate=50,
			company=ctx.company,
		)

		si = create_sales_invoice(
			item_code=bundle_code,
			company=ctx.company,
			customer=ctx.customer,
			debit_to=ctx.debit_to,
			income_account=ctx.income_account,
			expense_account=ctx.expense_account,
			cost_center=ctx.cost_center,
			update_stock=1,
			warehouse=ctx.warehouse,
			posting_date=nowdate(),
			# pin the doc currency to the company's, else the bootstrap's INR
			# "Standard Selling" price list leaks in (see test_two_bundle_lines)
			currency=frappe.get_cached_value("Company", ctx.company, "default_currency"),
			do_not_submit=True,
		)
		_assert_no_duplicate_packed_rows(si)
		self.assertEqual(len(si.packed_items), 1)

		for i in range(12):
			doc = frappe.get_doc("Sales Invoice", si.name)
			doc.contact_display = f"pos-save-{i}"
			doc.save()
			_assert_no_duplicate_packed_rows(doc)
			self.assertEqual(len(doc.packed_items), 1)

	def test_two_bundle_lines_repeated_save_no_duplicate_packed_items(self):
		"""Two separate invoice lines selling the same bundle SKU each get one packed row."""
		ctx = _sales_invoice_bundle_context()
		bundle_code, child_code = self._unique_codes()

		make_item(child_code, {"is_stock_item": 1, "stock_uom": "Nos", "uom": "Nos"})
		bundle_item = make_item(bundle_code, {"is_stock_item": 0, "stock_uom": "Nos", "uom": "Nos"})
		bundle_item.reload()
		for row in bundle_item.item_defaults:
			if row.company == ctx.company:
				row.default_warehouse = ctx.warehouse
				break
		else:
			bundle_item.append(
				"item_defaults",
				{"company": ctx.company, "default_warehouse": ctx.warehouse},
			)
		bundle_item.save(ignore_permissions=True)

		make_product_bundle(bundle_code, [child_code], qty=1)
		make_stock_entry(
			item_code=child_code,
			target=ctx.warehouse,
			qty=400,
			rate=50,
			company=ctx.company,
		)

		si = frappe.new_doc("Sales Invoice")
		si.company = ctx.company
		si.customer = ctx.customer
		si.debit_to = ctx.debit_to
		# Pin the doc currency to the company's, else the site's global default
		# currency (IDR here, INR fixtures on CI) leaks in and trips the party
		# account currency check.
		si.currency = frappe.get_cached_value("Company", ctx.company, "default_currency")
		si.update_stock = 1
		si.posting_date = nowdate()
		si.naming_series = ctx.naming_series
		for _ in range(2):
			si.append(
				"items",
				{
					"item_code": bundle_code,
					"qty": 1,
					"uom": "Nos",
					"stock_uom": "Nos",
					"rate": 100,
					"price_list_rate": 100,
					"warehouse": ctx.warehouse,
					"income_account": ctx.income_account,
					"expense_account": ctx.expense_account,
					"cost_center": ctx.cost_center,
				},
			)
		si.insert()
		_assert_no_duplicate_packed_rows(si)
		self.assertEqual(len(si.packed_items), 2)

		for i in range(8):
			doc = frappe.get_doc("Sales Invoice", si.name)
			doc.contact_display = f"pos-2l-{i}"
			doc.save()
			_assert_no_duplicate_packed_rows(doc)
			self.assertEqual(len(doc.packed_items), 2)
