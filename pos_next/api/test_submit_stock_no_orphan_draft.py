# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Acceptance tests for stock validation on draft save without orphan drafts.

ROOT CAUSE:
Online checkout is two steps: update_invoice (creates docstatus=0 draft) then
submit_invoice. In update_invoice, the pre-save stock check ran before
_enforce_profile_warehouse, so draft save validated availability against
the client-supplied items[].warehouse instead of the POS Profile's warehouse.
When stock was sufficient in the client-supplied warehouse but short in the
profile warehouse, update_invoice succeeded and saved a draft, but submit_invoice
threw ValidationError, leaving an orphan server draft.

Moving _enforce_profile_warehouse above the stock check ensures:
1. Availability is checked against the POS Profile warehouse before save.
2. If stock is insufficient, update_invoice throws ValidationError and NO draft
   is saved (save() is never reached).
3. The error payload in _collect_stock_errors retains
   {item_code, warehouse, requested_qty, available_qty} and gains item_name.

Run via:
    docker exec -e PN_SITE=fix.localhost erpnext-fix-backend-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_submit_stock_no_orphan_draft
"""

import json
import unittest
from unittest import mock

import frappe

from pos_next.api.invoices import (
	_collect_stock_errors,
	_enforce_profile_warehouse,
	update_invoice,
)


class MockItem:
	def __init__(self, **kwargs):
		self.item_code = kwargs.get("item_code")
		self.warehouse = kwargs.get("warehouse")
		self.qty = kwargs.get("qty", 1)
		self.stock_qty = kwargs.get("stock_qty", 1)
		self.conversion_factor = kwargs.get("conversion_factor", 1)
		self.rate = kwargs.get("rate", 100)
		self.pricing_rules = kwargs.get("pricing_rules", "")
		self.branch = kwargs.get("branch")
		self.name = kwargs.get("name")
		self.item_name = kwargs.get("item_name")

	def get(self, k, default=None):
		return getattr(self, k, default)

	def as_dict(self):
		return {
			"item_code": self.item_code,
			"warehouse": self.warehouse,
			"qty": self.qty,
			"stock_qty": self.stock_qty,
			"conversion_factor": self.conversion_factor,
			"rate": self.rate,
			"pricing_rules": self.pricing_rules,
			"branch": self.branch,
			"name": self.name,
			"item_name": self.item_name,
		}


class MockInvoice:
	def __init__(self, data=None):
		data = data or {}
		self.doctype = data.get("doctype", "POS Invoice")
		self.name = data.get("name")
		self.owner = getattr(frappe.session, "user", "Administrator")
		self.items = [MockItem(**it) for it in data.get("items", [])]
		self.payments = []
		self.customer = data.get("customer", "Test Customer")
		self.company = data.get("company", "Test Company")
		self.currency = data.get("currency", "USD")
		self.pos_profile = data.get("pos_profile")
		self.flags = frappe._dict()
		self.is_return = 0
		self.return_against = None
		self.posting_date = frappe.utils.nowdate()
		self.set_warehouse = None
		self.docstatus = 0
		self.grand_total = 100.0
		self.base_grand_total = 100.0
		self.total_qty = 1
		self.total = 100.0
		self.discount_amount = 0
		self.apply_discount_on = "Grand Total"
		self.disable_rounded_total = 1
		self.pos_coupon_code = None
		self.save = mock.MagicMock()
		self.set_missing_values = mock.MagicMock()
		self.calculate_taxes_and_totals = mock.MagicMock()

	def get(self, k, default=None):
		return getattr(self, k, default)

	def set(self, k, v):
		setattr(self, k, v)

	def as_dict(self):
		return {"name": self.name or "ACC-POS-001", "docstatus": self.docstatus}


class TestSubmitStockNoOrphanDraft(unittest.TestCase):
	"""Regression tests ensuring draft save enforces profile warehouse before stock check."""

	def test_collect_stock_errors_carries_item_name_from_row(self):
		"""_collect_stock_errors returns item_name when already present on row."""
		item = {
			"item_code": "ITEM-A",
			"item_name": "Artisan Sourdough",
			"warehouse": "Stores - Test",
			"qty": 5,
			"stock_qty": 5,
		}
		with mock.patch("pos_next.api.invoices._get_available_stock", return_value=2):
			errors = _collect_stock_errors([item])

		self.assertEqual(len(errors), 1)
		self.assertEqual(errors[0]["item_code"], "ITEM-A")
		self.assertEqual(errors[0]["warehouse"], "Stores - Test")
		self.assertEqual(errors[0]["requested_qty"], 5)
		self.assertEqual(errors[0]["available_qty"], 2)
		self.assertEqual(errors[0].get("item_name"), "Artisan Sourdough")

	def test_collect_stock_errors_fetches_item_name_from_db_when_absent(self):
		"""_collect_stock_errors looks up item_name via cached Item lookup when missing on row."""
		item = {
			"item_code": "ITEM-B",
			"warehouse": "Stores - Test",
			"qty": 3,
			"stock_qty": 3,
		}
		real_get_value = frappe.db.get_value

		def fake_get_value(doctype, name, fieldname=None, *args, **kwargs):
			if doctype == "Item" and name == "ITEM-B" and fieldname == "item_name":
				return "Butter Croissant"
			return real_get_value(doctype, name, fieldname, *args, **kwargs)

		with (
			mock.patch("pos_next.api.invoices._get_available_stock", return_value=0),
			mock.patch("frappe.db.get_value", side_effect=fake_get_value),
		):
			errors = _collect_stock_errors([item])

		self.assertEqual(len(errors), 1)
		self.assertEqual(errors[0]["item_code"], "ITEM-B")
		self.assertEqual(errors[0].get("item_name"), "Butter Croissant")

	def test_collect_stock_errors_item_name_optional_when_missing_in_db(self):
		"""_collect_stock_errors keeps contract when item_name cannot be resolved."""
		item = {
			"item_code": "ITEM-UNKNOWN",
			"warehouse": "Stores - Test",
			"qty": 1,
			"stock_qty": 1,
		}
		real_get_value = frappe.db.get_value

		def fake_get_value(doctype, name, fieldname=None, *args, **kwargs):
			if doctype == "Item" and name == "ITEM-UNKNOWN" and fieldname == "item_name":
				return None
			return real_get_value(doctype, name, fieldname, *args, **kwargs)

		with (
			mock.patch("pos_next.api.invoices._get_available_stock", return_value=0),
			mock.patch("frappe.db.get_value", side_effect=fake_get_value),
		):
			errors = _collect_stock_errors([item])

		self.assertEqual(len(errors), 1)
		self.assertEqual(errors[0]["item_code"], "ITEM-UNKNOWN")
		# item_name may be None or omitted, but core 4 fields must be present
		self.assertIn("item_code", errors[0])
		self.assertIn("warehouse", errors[0])
		self.assertIn("requested_qty", errors[0])
		self.assertIn("available_qty", errors[0])

	def test_enforce_profile_warehouse_noop_when_profile_has_no_warehouse(self):
		"""_enforce_profile_warehouse does nothing when profile warehouse is unset."""
		doc = MockInvoice({"items": [{"item_code": "ITEM-1", "warehouse": "Original-WH"}]})
		profile_without_wh = frappe._dict({"name": "P-NOWH", "warehouse": None})
		_enforce_profile_warehouse(doc, profile_without_wh)
		self.assertEqual(doc.items[0].warehouse, "Original-WH")
		self.assertIsNone(doc.set_warehouse)

	def test_update_invoice_profile_warehouse_shortage_throws_and_no_draft_saved(self):
		"""RED regression: update_invoice with non-profile warehouse where only profile WH

		is short must throw ValidationError AND must not call save() (no orphan draft).
		"""
		profile_wh = "Stores - Profile"
		client_wh = "Stores - Client"
		item_code = "ITEM-1"

		mock_inv = None

		def fake_get_doc(*args, **kwargs):
			nonlocal mock_inv
			data = args[0] if args else kwargs.get("data")
			if isinstance(data, dict):
				mock_inv = MockInvoice(data)
				return mock_inv
			if kwargs.get("doctype") == "Error Log":
				m = mock.MagicMock()
				m.insert = mock.MagicMock()
				return m
			return MockInvoice({"doctype": "POS Invoice"})

		mock_profile = frappe._dict({
			"name": "TEST-PROFILE",
			"warehouse": profile_wh,
			"company": "Test Company",
			"currency": "USD",
			"branch": None,
		})

		def fake_db_get_value(doctype, filters=None, fieldname=None, *args, **kwargs):
			if doctype == "Bin":
				wh = filters.get("warehouse") if isinstance(filters, dict) else None
				if wh == client_wh:
					return 10.0  # Client warehouse has plenty
				if wh == profile_wh:
					return 0.0  # Profile warehouse is short!
				return 0.0
			if doctype == "Item" and fieldname == "item_name":
				return "Test Item Name"
			if doctype == "Customer":
				return "Test Customer"
			return None

		def fake_db_exists(dt, name, *args, **kwargs):
			return True

		payload = {
			"pos_profile": "TEST-PROFILE",
			"customer": "Test Customer",
			"items": [
				{
					"item_code": item_code,
					"warehouse": client_wh,
					"qty": 1,
					"stock_qty": 1,
					"rate": 100,
				}
			],
		}

		def fake_get_all(doctype, *args, **kwargs):
			filters = kwargs.get("filters", {})
			if doctype == "Item":
				if isinstance(filters, dict) and filters.get("allow_negative_stock"):
					return []
				return [item_code]
			return []

		patches = (
			mock.patch("frappe.get_doc", side_effect=fake_get_doc),
			mock.patch("frappe.get_cached_doc", return_value=mock_profile),
			mock.patch("frappe.db.get_value", side_effect=fake_db_get_value),
			mock.patch("frappe.db.exists", side_effect=fake_db_exists),
			mock.patch("frappe.get_all", side_effect=fake_get_all),
			mock.patch("pos_next.api.invoices._check_profile_access", return_value=None),
			mock.patch("pos_next.api.invoices.get_pos_invoice_doctype", return_value="Sales Invoice"),
			mock.patch("pos_next.api.invoices._backdate_entry_active", return_value=False),
			mock.patch("pos_next.api.invoices.get_effective_pos_settings", return_value={}),
			mock.patch("pos_next.api.invoices._validate_item_rates", return_value=set()),
			mock.patch("pos_next.api.invoices._should_block", return_value=True),
		)

		for p in patches:
			p.start()
			self.addCleanup(p.stop)

		with self.assertRaises(frappe.ValidationError) as ctx:
			update_invoice(json.dumps(payload))

		# Verify error payload shape and content
		err_data = json.loads(ctx.exception.args[0])
		self.assertIn("errors", err_data)
		self.assertEqual(len(err_data["errors"]), 1)
		err = err_data["errors"][0]
		self.assertEqual(err["item_code"], item_code)
		self.assertEqual(err["warehouse"], profile_wh)
		self.assertEqual(err["requested_qty"], 1.0)
		self.assertEqual(err["available_qty"], 0.0)
		self.assertEqual(err.get("item_name"), "Test Item Name")

		# Crucial: invoice draft must NOT have been saved!
		self.assertIsNotNone(mock_inv)
		mock_inv.save.assert_not_called()


class TestValidateCartItemsSkipsNonStock(unittest.TestCase):
	"""Product Bundle parents are non-stock (no Bin): the cart pre-check must skip
	them like update_invoice does, or every bundle sale reads "available 0"."""

	def test_bundle_parent_is_not_reported_as_short(self):
		from pos_next.api.invoices import validate_cart_items

		items = [
			{"item_code": "BUNDLE-A", "warehouse": "Stores - Test", "qty": 1, "stock_qty": 1},
			{"item_code": "STOCK-B", "warehouse": "Stores - Test", "qty": 3, "stock_qty": 3},
		]

		def fake_get_all(doctype, *args, **kwargs):
			filters = kwargs.get("filters", {})
			if doctype == "Item" and isinstance(filters, dict) and filters.get("is_stock_item"):
				return ["STOCK-B"]
			return []

		with (
			mock.patch("pos_next.api.invoices._should_block", return_value=True),
			mock.patch("pos_next.api.invoices._get_available_stock", return_value=0),
			mock.patch("frappe.get_all", side_effect=fake_get_all),
			mock.patch("frappe.db.get_value", return_value=None),
		):
			errors = validate_cart_items(json.dumps(items))

		self.assertEqual([e["item_code"] for e in errors], ["STOCK-B"])
