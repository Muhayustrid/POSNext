# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC/A5 acceptance tests: endpoints that took a client-supplied pos_profile
(or a raw warehouse) without checking that the caller belongs to it.

Covered guards:
- items.py catalog endpoints (get_items, get_item_groups, get_brands, ...)
  require POS Profile membership; management bypasses (cross-outlet
  monitoring/backdate).
- items.py warehouse/company-parameter endpoints (get_stock_quantities,
  get_batch_serial_details, get_batch_serial_data_for_items,
  get_item_warehouse_availability) only accept warehouses/companies of the
  caller's own POS Profiles; management bypasses.
- pos_profile.py profile-scoped reads (get_payment_methods, get_taxes, ...)
  require membership or management.
- get_sales_persons stays reachable for the checkout cashier but strips the
  payroll-sensitive `commission_rate` for non-management callers (T4
  decision: the PaymentDialog dropdown needs the names for cashiers).
- queue.get_next_queue_number, invoices.apply_offers / validate_cart_items /
  validate_return_items gate the profile/invoice they are asked about.

Standalone module (own users/profile/warehouse/invoice fixtures); do not
batch it with another shift-touching module in one process.

Run via pos_next/_pn_run_tests.py pos_next.api.test_ungated_endpoints_security
"""

import json
import unittest
import uuid
from contextlib import contextmanager
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.invoices import (
	apply_offers,
	validate_cart_items,
	validate_return_items,
)
from pos_next.api.items import get_items, get_stock_quantities
from pos_next.api.pos_profile import (
	get_payment_methods,
	get_sales_persons,
	get_taxes,
)
from pos_next.api.queue import get_next_queue_number
from pos_next.tests.price_group_helpers import get_default_customer, make_test_pos_profile

ADMIN = "Administrator"

# schedule-safe profile that can actually serve the item catalog
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
	["warehouse", "is", "set"],
	["selling_price_list", "is", "set"],
]

PROFILE_B_SUFFIX = "PNXT Ungated B"
PROFILE_B = f"_Test POS Profile {PROFILE_B_SUFFIX}"


@contextmanager
def _as(user):
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(ADMIN)


class TestUngatedEndpointsSecurity(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Role", "POSNext Manager"):
			raise unittest.SkipTest("POSNext Manager role missing (fixtures not synced?)")
		frappe.set_user(ADMIN)
		cls.profile = frappe.db.get_value(
			"POS Profile",
			_PROFILE_FILTER,
			["name", "company", "warehouse"],
			as_dict=True,
			order_by="creation asc",
		)
		if not cls.profile:
			raise unittest.SkipTest("no schedule-safe POS Profile with warehouse + price list")

		cls.cashier = f"ungated-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.manager = f"ungated-sec.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.cashier, cls.manager):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Ungated Sec Tester"}
			).insert(ignore_permissions=True)
		cls._grant_role(cls.cashier, "POSNext Cashier")
		cls._grant_role(cls.manager, "POSNext Manager")

		# cashier belongs to profile A ONLY
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": cls.profile.name,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": cls.cashier,
				"default": 1,
			}
		).insert(ignore_permissions=True)

		# cross-profile target: a second register on the same company
		cls.created_profile_b = not frappe.db.exists("POS Profile", PROFILE_B)
		cls.profile_b = make_test_pos_profile(
			PROFILE_B_SUFFIX, cls.profile.company, cls.profile.warehouse
		)

		# a warehouse outside the cashier's profile scope
		cls.created_warehouse = None
		cls.foreign_warehouse = cls._find_foreign_warehouse()

		# seeded sales person so the dropdown assertions are not vacuous
		cls.created_sales_person = None
		cls.sales_person = cls._ensure_sales_person()

		# a raw submitted invoice row under profile B for the return-scope
		# test (the gate reads only pos_profile/posting_date)
		cls.customer = get_default_customer()
		cls.foreign_invoice = cls._seed_invoice(cls.profile_b, cls.profile.company)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			# tolerant teardown: one leftover must never abort the remaining
			# cleanup on this shared dev site
			try:
				step()
			except Exception:
				pass

		if getattr(cls, "foreign_invoice", None):
			_safe(lambda: frappe.db.delete("Sales Invoice", {"name": cls.foreign_invoice}))
		if getattr(cls, "created_sales_person", None):
			_safe(
				lambda: frappe.delete_doc(
					"Sales Person", cls.created_sales_person, force=1, ignore_permissions=True
				)
			)
		if getattr(cls, "created_warehouse", None):
			_safe(
				lambda: frappe.delete_doc(
					"Warehouse", cls.created_warehouse, force=1, ignore_permissions=True
				)
			)
		if getattr(cls, "created_profile_b", False) and getattr(cls, "profile_b", None):
			_safe(lambda: frappe.delete_doc("POS Profile", cls.profile_b, force=1, ignore_permissions=True))
		for email in (getattr(cls, "cashier", None), getattr(cls, "manager", None)):
			if not email:
				continue
			_safe(lambda e=email: frappe.db.delete("Error Log", {"owner": e}))
			_safe(lambda e=email: frappe.db.delete("POS Profile User", {"user": e}))
			_safe(lambda e=email: frappe.db.delete("Has Role", {"parent": e, "parenttype": "User"}))
			_safe(lambda e=email: frappe.delete_doc("User", e, force=1, ignore_permissions=True))
		# commit BEFORE super() — FrappeTestCase's class-level rollback would
		# otherwise undo every deletion above (project trap)
		frappe.db.commit()
		super().tearDownClass()

	# ── fixture helpers ──────────────────────────────────────────────────────

	@staticmethod
	def _grant_role(user, role):
		if not frappe.db.exists("Role", role):
			return
		frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": user,
				"parenttype": "User",
				"parentfield": "roles",
				"role": role,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=user)

	@classmethod
	def _find_foreign_warehouse(cls):
		rows = frappe.get_all(
			"Warehouse",
			filters={"disabled": 0, "is_group": 0, "name": ["!=", cls.profile.warehouse]},
			pluck="name",
			order_by="creation asc",
			limit=1,
		)
		if rows:
			return rows[0]
		try:
			doc = frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"PNXT Ungated Out {uuid.uuid4().hex[:6]}",
					"company": cls.profile.company,
				}
			)
			doc.flags.ignore_validate = True
			doc.insert(ignore_permissions=True)
		except Exception:
			raise unittest.SkipTest("no out-of-scope warehouse on site and none could be created")
		cls.created_warehouse = doc.name
		return doc.name

	@classmethod
	def _ensure_sales_person(cls):
		filters = {"enabled": 1, "is_group": 0}
		if frappe.db.has_column("Sales Person", "company"):
			filters["company"] = cls.profile.company
		existing = frappe.get_all(
			"Sales Person", filters=filters, pluck="name", order_by="creation asc", limit=1
		)
		if existing:
			return existing[0]
		doc = frappe.get_doc(
			{
				"doctype": "Sales Person",
				"sales_person_name": f"PNXT Ungated SP {uuid.uuid4().hex[:6]}",
				"enabled": 1,
				"is_group": 0,
			}
		)
		if frappe.db.has_column("Sales Person", "company"):
			doc.company = cls.profile.company
		doc.flags.ignore_permissions = True
		doc.insert()
		cls.created_sales_person = doc.name
		return doc.name

	@classmethod
	def _seed_invoice(cls, profile_name, company):
		"""Minimal submitted Sales-Invoice row under `profile_name` (raw
		insert; the return gate only queries pos_profile/posting_date)."""
		doctype = "Sales Invoice"
		doc = frappe.get_doc(
			{
				"doctype": doctype,
				"company": company,
				"customer": cls.customer,
				"pos_profile": profile_name,
				"is_pos": 1,
				"is_return": 0,
				"posting_date": nowdate(),
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		frappe.db.set_value(doctype, doc.name, "docstatus", 1, update_modified=False)
		return doc.name

	# ── items.py: profile membership ─────────────────────────────────────────

	def test_cashier_get_items_own_profile_allowed(self):
		with _as(self.cashier):
			items = get_items(self.profile.name)
		self.assertIsInstance(items, list)

	def test_cashier_get_items_cross_profile_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				get_items(self.profile_b)

	def test_manager_get_items_cross_profile_allowed(self):
		# management needs the cross-outlet catalog for monitoring/backdate
		with _as(self.manager):
			items = get_items(self.profile_b)
		self.assertIsInstance(items, list)

	# ── items.py: raw warehouse scope ────────────────────────────────────────

	def test_cashier_stock_quantities_own_warehouse_allowed(self):
		with _as(self.cashier):
			result = get_stock_quantities(json.dumps(["NO-SUCH-ITEM"]), self.profile.warehouse)
		self.assertIsInstance(result, list)

	def test_cashier_stock_quantities_foreign_warehouse_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				get_stock_quantities(json.dumps(["NO-SUCH-ITEM"]), self.foreign_warehouse)

	def test_manager_stock_quantities_foreign_warehouse_allowed(self):
		with _as(self.manager):
			result = get_stock_quantities(json.dumps(["NO-SUCH-ITEM"]), self.foreign_warehouse)
		self.assertIsInstance(result, list)

	# ── pos_profile.py: profile-scoped reads + sales persons ─────────────────

	def test_cashier_payment_methods_cross_profile_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				get_payment_methods(self.profile_b)

	def test_cashier_payment_methods_own_profile_allowed(self):
		with _as(self.cashier):
			methods = get_payment_methods(self.profile.name)
		self.assertIsInstance(methods, list)

	def test_cashier_taxes_cross_profile_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				get_taxes(self.profile_b)

	def test_cashier_taxes_own_profile_allowed(self):
		with _as(self.cashier):
			taxes = get_taxes(self.profile.name)
		self.assertIsInstance(taxes, list)

	def test_manager_sales_persons_include_commission(self):
		with _as(self.manager):
			persons = get_sales_persons(self.profile_b)
		self.assertTrue(any(p.get("name") == self.sales_person for p in persons))
		self.assertTrue(all("commission_rate" in p for p in persons))

	def test_cashier_sales_persons_hide_commission(self):
		# T4 decision: the checkout dropdown keeps working for cashiers, but
		# the payroll-sensitive commission_rate never reaches a non-manager.
		with _as(self.cashier):
			persons = get_sales_persons(self.profile.name)
		self.assertTrue(any(p.get("name") == self.sales_person for p in persons))
		self.assertTrue(all("commission_rate" not in p for p in persons))

	# ── queue.py ─────────────────────────────────────────────────────────────

	def test_cashier_queue_number_cross_profile_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				get_next_queue_number(self.profile_b)

	def test_cashier_queue_number_own_profile_allowed(self):
		# env setting faked off so the assertion stays read-only (an enabled
		# queue would allocate + commit a counter row mid-test)
		with _as(self.cashier), patch(
			"pos_next.api.queue.get_effective_pos_setting", return_value=0
		):
			out = get_next_queue_number(self.profile.name)
		self.assertEqual(out, {"enabled": False})

	# ── invoices.py low gates ────────────────────────────────────────────────

	def test_cashier_apply_offers_cross_profile_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				apply_offers(json.dumps({"pos_profile": self.profile_b}))

	def test_cashier_apply_offers_own_profile_allowed(self):
		with _as(self.cashier):
			out = apply_offers(json.dumps({"pos_profile": self.profile.name}))
		self.assertEqual(out, {"items": []})

	def test_cashier_validate_cart_items_cross_profile_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				validate_cart_items(json.dumps([]), self.profile_b)

	def test_cashier_validate_return_items_foreign_invoice_denied(self):
		with _as(self.cashier):
			with self.assertRaises(frappe.PermissionError):
				validate_return_items(
					self.foreign_invoice,
					[{"item_code": "NO-SUCH-ITEM", "qty": -1}],
					doctype="Sales Invoice",
				)
