# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Task 2: Stock Reconciliation override (blind count) + outlet scoping.

Outlets submit draft Stock Reconciliations; the override keeps counted rows
that equal system stock while the doc is a draft (ERPNext would delete them
and leak the system qty), enforces one open opname per warehouse, and persists
the workflow state before ERPNext's background submit (>100 rows) reloads the
doc from DB. Outlet-only users see only their POS Profile warehouse.

Known gap (not covered here, needs a follow-up): a single-role Accounts
Manager has no Item read permission, so ERPNext's submit-time validation
(`get_stock_balance`) throws PermissionError for them — both for the queued
`_submit` and a direct `submit()`. The background test therefore runs
`execute_action` as Administrator (worker context). Production approval needs
a Custom DocPerm stock-read row for Accounts Manager like the cashier fixture.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_stock_opname_override
"""

import unittest
import uuid
from unittest import mock

import frappe
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import StockReconciliation
from frappe.model.document import Document, execute_action
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase

from pos_next.api.test_production import _make_test_item, _seed_stock
from pos_next.stock_opname_setup import STATE_APPROVED, STATE_PENDING_AM

ADMIN = "Administrator"
DOCTYPE = "Stock Reconciliation"


def _make_user(role):
	"""A user holding exactly `role` (fresh users have no roles)."""
	email = f"stock-opname-{uuid.uuid4().hex[:10]}@example.com"
	frappe.get_doc({"doctype": "User", "email": email, "first_name": "SO Test"}).insert(
		ignore_permissions=True
	)
	frappe.get_doc(
		{
			"doctype": "Has Role",
			"parent": email,
			"parenttype": "User",
			"parentfield": "roles",
			"role": role,
		}
	).insert(ignore_permissions=True)
	frappe.clear_cache(user=email)
	return email


class TestStockOpnameOverride(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		cls.company = "_Test Company"
		cls.warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"SO-T-{uuid.uuid4().hex[:8]}",
					"company": cls.company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.foreign_warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"SO-T-{uuid.uuid4().hex[:8]}",
					"company": cls.company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.cashier = _make_user("POSNext Cashier")
		cls.stock_manager = _make_user("Stock Manager")
		cls.accounts_manager = _make_user("Accounts Manager")
		template = frappe.db.get_value(
			"POS Profile",
			[["disabled", "=", 0], ["pos_schedule_enforce_closing", "=", 0]],
			"name",
			order_by="creation asc",
		)
		if not template:
			raise unittest.SkipTest("no schedule-safe POS Profile")
		src = frappe.get_doc("POS Profile", template)
		profile = frappe.copy_doc(src)
		profile.name = f"SO-T-{uuid.uuid4().hex[:8]}"
		profile.company = cls.company
		profile.warehouse = cls.warehouse
		profile.pos_schedule_enabled = 0
		profile.pos_schedule_enforce_closing = 0
		profile.applicable_for_users = []
		profile.insert(ignore_permissions=True, set_name=profile.name)
		cls.profile = profile.name
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": cls.profile,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": cls.cashier,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		# Stock docs, bins and warehouses roll back with the class
		# transaction; only users + the cloned profile (referenced by name
		# in User records) are deleted explicitly.
		frappe.set_user(ADMIN)
		if frappe.db.exists("POS Profile", cls.profile):
			frappe.delete_doc("POS Profile", cls.profile, force=True, ignore_permissions=True)
		for user in (cls.cashier, cls.stock_manager, cls.accounts_manager):
			if frappe.db.exists("User", user):
				frappe.delete_doc("User", user, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user(ADMIN)
		super().tearDown()

	def _make_sr(self, warehouse, counts):
		"""Draft SR with explicit valuation (seeded at basic_rate 10)."""
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"company": self.company,
				"purpose": "Stock Reconciliation",
				"set_warehouse": warehouse,
				"items": [
					{
						"item_code": code,
						"warehouse": warehouse,
						"qty": qty,
						"valuation_rate": 10,
					}
					for code, qty in counts
				],
			}
		)
		self.addCleanup(self._delete_sr, doc.name if not doc.is_new() else None, doc)
		return doc

	def _delete_sr(self, name, doc):
		# Best-effort: frees the one-open-opname lock between tests in the
		# same class transaction; leftovers roll back with the class.
		try:
			frappe.set_user(ADMIN)
			name = name or (doc.name if not doc.is_new() else None)
			if name and frappe.db.exists(DOCTYPE, name):
				frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True)
		except Exception:
			pass
		finally:
			frappe.set_user(ADMIN)

	def test_draft_keeps_rows_equal_to_system(self):
		from pos_next.overrides.stock_reconciliation import CustomStockReconciliation

		self.assertTrue(issubclass(CustomStockReconciliation, StockReconciliation))
		equal = _make_test_item()
		changed = _make_test_item()
		_seed_stock(equal, self.warehouse, 5)
		_seed_stock(changed, self.warehouse, 5)
		doc = self._make_sr(self.warehouse, [(equal, 5), (changed, 3)])
		doc.insert(ignore_permissions=True)
		doc.save()
		self.assertEqual({r.item_code for r in doc.items}, {equal, changed})

	def test_draft_skip_still_resets_difference_amount(self):
		# ERPNext >=16.52 validate_data does `difference_amount +=` for rows
		# without a valuation rate; the draft skip must leave it a number.
		doc = frappe.new_doc(DOCTYPE)
		doc.difference_amount = None
		doc.remove_items_with_no_change()
		self.assertEqual(doc.difference_amount, 0.0)

	def test_draft_blanks_misleading_differences(self):
		# Draft rows have no current qty, so ERPNext's difference = counted - 0.
		item = _make_test_item()
		_seed_stock(item, self.warehouse, 5)
		doc = self._make_sr(self.warehouse, [(item, 3)])
		doc.insert(ignore_permissions=True)
		row = doc.items[0]
		self.assertEqual(
			(row.current_qty, row.quantity_difference, row.amount_difference, doc.difference_amount),
			(0, 0, 0, 0),
		)

	def test_draft_fills_per_uom_valuation_rate(self):
		# warehouse_app shows "Valuation Rate (as per UOM)" next to the native
		# rate; an outlet count never sends a rate, so it must mirror the native one.
		if not frappe.get_meta("Stock Reconciliation Item").has_field("custom_valuation_rate_per_uom"):
			self.skipTest("warehouse_app per-UOM fields not installed")
		item = _make_test_item(uoms=[{"uom": "Box", "conversion_factor": 6}])
		_seed_stock(item, self.warehouse, 12)
		doc = frappe.get_doc(
			{
				"doctype": DOCTYPE,
				"company": self.company,
				"purpose": "Stock Reconciliation",
				"set_warehouse": self.warehouse,
				"items": [
					{"item_code": item, "warehouse": self.warehouse, "custom_uom": "Box", "custom_qty_after": 1}
				],
			}
		)
		self.addCleanup(self._delete_sr, None, doc)
		doc.insert(ignore_permissions=True)
		row = doc.items[0]
		self.assertTrue(row.valuation_rate)
		self.assertAlmostEqual(row.custom_valuation_rate_per_uom, row.valuation_rate * 6, places=2)

	def test_submit_with_all_rows_equal_raises_clear_message(self):
		item = _make_test_item()
		_seed_stock(item, self.warehouse, 5)
		doc = self._make_sr(self.warehouse, [(item, 5)])
		doc.insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.submit()
		self.assertIn("matches the system stock", str(ctx.exception))

	def test_submit_drops_equal_rows_keeps_changed(self):
		equal = _make_test_item()
		changed = _make_test_item()
		_seed_stock(equal, self.warehouse, 5)
		_seed_stock(changed, self.warehouse, 5)
		doc = self._make_sr(self.warehouse, [(equal, 5), (changed, 3)])
		doc.insert(ignore_permissions=True)
		doc.submit()
		doc.reload()
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual([r.item_code for r in doc.items], [changed])
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": changed, "warehouse": self.warehouse}, "actual_qty"),
			3,
		)

	def test_second_open_opname_same_warehouse_rejected(self):
		item = _make_test_item()
		_seed_stock(item, self.warehouse, 5)
		first = self._make_sr(self.warehouse, [(item, 4)])
		first.insert(ignore_permissions=True)
		second = self._make_sr(self.warehouse, [(item, 4)])
		with self.assertRaises(frappe.ValidationError) as ctx:
			second.insert(ignore_permissions=True)
		self.assertIn(first.name, str(ctx.exception))

	def test_background_submit_keeps_approved_state(self):
		rows = []
		for _i in range(101):
			code = _make_test_item()
			_seed_stock(code, self.warehouse, 1)
			rows.append((code, 2))
		doc = self._make_sr(self.warehouse, rows)
		doc.insert(ignore_permissions=True)
		doc.db_set("workflow_state", STATE_PENDING_AM)
		frappe.set_user(self.accounts_manager)
		doc = frappe.get_doc(DOCTYPE, doc.name)
		calls = []

		def _record(self, action, **kwargs):
			calls.append((action, kwargs))

		with mock.patch.object(Document, "queue_action", _record):
			apply_workflow(doc, "Approve")
		self.assertEqual([c[0] for c in calls], ["submit"])
		doc.unlock()
		# The RQ worker runs with full rights; the single-role Accounts
		# Manager test user has no Item read (see module docstring).
		frappe.set_user(ADMIN)
		execute_action(DOCTYPE, doc.name, "_submit")
		doc.reload()
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.workflow_state, STATE_APPROVED)
		comments = frappe.get_all(
			"Comment",
			{"reference_doctype": DOCTYPE, "reference_name": doc.name},
			["content"],
		)
		self.assertFalse(any("Action Failed" in (c.content or "") for c in comments))

	def test_outlet_user_sees_only_own_warehouse(self):
		from pos_next.overrides.stock_reconciliation import is_hq_user, outlet_warehouses

		item = _make_test_item()
		_seed_stock(item, self.warehouse, 5)
		own = self._make_sr(self.warehouse, [(item, 5)])
		own.insert(ignore_permissions=True)
		foreign = self._make_sr(self.foreign_warehouse, [(item, 5)])
		foreign.insert(ignore_permissions=True)

		self.assertEqual(outlet_warehouses(self.cashier), [self.warehouse])
		self.assertFalse(is_hq_user(self.cashier))
		self.assertTrue(is_hq_user(self.stock_manager))

		frappe.set_user(self.cashier)
		visible = frappe.get_list(DOCTYPE, pluck="name")
		self.assertIn(own.name, visible)
		self.assertNotIn(foreign.name, visible)
		self.assertFalse(frappe.has_permission(DOCTYPE, "read", foreign.name))

		frappe.set_user(self.stock_manager)
		visible = frappe.get_list(DOCTYPE, pluck="name")
		self.assertIn(own.name, visible)
		self.assertIn(foreign.name, visible)

	def test_empty_profile_warehouse_resolves_company_fallback(self):
		from pos_next.overrides.stock_reconciliation import outlet_warehouses

		fallback = frappe.db.get_value(
			"Warehouse", {"company": self.company, "is_group": 0, "disabled": 0}, "name"
		)
		self.assertTrue(fallback, "site needs a default warehouse for _Test Company")
		src = frappe.get_doc("POS Profile", self.profile)
		profile = frappe.copy_doc(src)
		profile.name = f"SO-T-{uuid.uuid4().hex[:8]}"
		profile.applicable_for_users = []
		profile.insert(ignore_permissions=True, set_name=profile.name)
		# warehouse is mandatory on the form; empty it direct-to-DB to mimic
		# legacy data and exercise the _resolve_profile company fallback.
		profile.db_set("warehouse", None)
		self.addCleanup(frappe.delete_doc, "POS Profile", profile.name, force=True, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": profile.name,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": self.cashier,
			}
		).insert(ignore_permissions=True)

		self.assertIn(fallback, outlet_warehouses(self.cashier))
