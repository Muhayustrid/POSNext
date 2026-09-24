# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-03/05/06 acceptance tests: the invoice draft and return APIs cannot be
used across cashiers.

- SEC-03: update_invoice/submit_invoice require POS Profile membership, and a
  draft can only be modified/submitted by its owner (or a doc-level writer);
  identity fields (owner/docstatus) never come from the payload.
- SEC-05: return-flow reads are gated per profile / doc-level read.
- SEC-06: cleanup_old_drafts deletes own drafts only, with the age clamped.

Run via pos_next/_pn_run_tests.py pos_next.api.test_invoice_authorization_security
"""

import json
import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, nowdate

from pos_next.api.invoices import (
	cleanup_old_drafts,
	get_invoice_for_return,
	get_returnable_invoices,
	prepare_return_invoice,
	search_invoice_by_number,
	search_invoices_for_return,
	submit_invoice,
	update_invoice,
)
from pos_next.invoice_type import get_pos_invoice_doctype
from pos_next.tests.price_group_helpers import get_default_customer

ADMIN = "Administrator"

# Same profile filter as api/test_pos_invoice_submit.py: creation-asc on a
# schedule-safe profile whose company chart supports real submits.
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]


class TestInvoiceAuthorizationSecurity(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.profile = frappe.db.get_value(
			"POS Profile",
			_PROFILE_FILTER,
			["name", "company", "warehouse"],
			as_dict=True,
			order_by="creation asc",
		)
		cls.mode = (
			frappe.get_all(
				"POS Payment Method",
				{"parent": cls.profile.name, "parenttype": "POS Profile"},
				pluck="mode_of_payment",
				limit=1,
			)
			if cls.profile
			else None
		)
		cls.item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
			pluck="name",
			limit=1,
		)
		cls.customer = get_default_customer()
		if not (cls.profile and cls.mode and cls.item and cls.customer):
			raise unittest.SkipTest("no usable POS Profile / item / customer")

		cls.doctype = get_pos_invoice_doctype()

		# owner (cashier A) and same-profile cashier C; intruder B is roleless
		# and belongs to no profile.
		cls.cashier = f"inv-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.cashier2 = f"inv-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.intruder = f"inv-sec.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.cashier, cls.cashier2, cls.intruder):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Invoice Sec Tester"}
			).insert(ignore_permissions=True)

		# A and C are both users of the profile: C proves the ownership gate
		# (not just the profile gate) blocks the IDOR. Both get the minimal
		# cashier-adjacent roles (Item/Customer/Account read, no invoice-doctype
		# write — C must stay behind the ownership gate).
		for email, extra_roles in ((cls.cashier, ()), (cls.cashier2, ("Sales User",))):
			for role in ("Stock User",) + extra_roles:
				frappe.get_doc(
					{
						"doctype": "Has Role",
						"parent": email,
						"parenttype": "User",
						"parentfield": "roles",
						"role": role,
					}
				).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "POS Profile User",
					"parent": cls.profile.name,
					"parenttype": "POS Profile",
					"parentfield": "pos_profile_user",
					"user": email,
				}
			).insert(ignore_permissions=True)

		# SEC-06 dropped force/ignore_permissions from cleanup, so the owner
		# needs a role that grants delete on the draft doctype.
		cls.delete_role = None
		for perm_doctype in ("Custom DocPerm", "DocPerm"):
			roles = frappe.get_all(
				perm_doctype,
				filters={"parent": cls.doctype, "delete": 1},
				pluck="role",
				order_by="role",
			)
			non_admin = [r for r in roles if r != "System Manager"]
			if roles:
				cls.delete_role = (non_admin or roles)[0]
				break
		if cls.delete_role:
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": cls.cashier,
					"parenttype": "User",
					"parentfield": "roles",
					"role": cls.delete_role,
				}
			).insert(ignore_permissions=True)
		frappe.clear_cache(user=cls.cashier)
		frappe.clear_cache(user=cls.cashier2)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			# tolerant teardown (see test_closing_shift_security): one leftover
			# must never abort the remaining cleanup on this shared dev site
			try:
				step()
			except Exception:
				pass

		for email in (cls.cashier, cls.cashier2, cls.intruder):
			_safe(lambda email=email: frappe.db.delete("Error Log", {"owner": email}))
			_safe(lambda email=email: frappe.delete_doc("User", email, force=1, ignore_permissions=True))
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(ADMIN)
		self._stocked = False

	def tearDown(self):
		frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- helpers

	def _ensure_stock(self):
		"""Real Material Receipt so the draft stock pre-check and the stock
		ledger on submit pass; skipped when this profile does not block on
		stock (recipe of api/test_pos_invoice_submit.py)."""
		if self._stocked:
			return
		from pos_next.api.invoices import _should_block

		if _should_block(self.profile.name):
			self._make_stock_receipt()
		self._stocked = True

	def _payload(self, **overrides):
		payload = {
			"pos_profile": self.profile.name,
			"customer": self.customer,
			"items": [
				{"item_code": self.item[0], "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
			],
			"payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
		}
		payload.update(overrides)
		return payload

	def _make_stock_receipt(self):
		"""Explicit full receipt for the submit test (recipe of
		api/test_pos_invoice_submit.py)."""
		frappe.set_user(ADMIN)
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"purpose": "Material Receipt",
				"company": self.profile.company,
				"items": [
					{
						"item_code": self.item[0],
						"qty": 5,
						"t_warehouse": self.profile.warehouse,
						"allow_zero_valuation_rate": 1,
					}
				],
			}
		)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()

	def _make_draft(self, user, **overrides):
		self._ensure_stock()
		frappe.set_user(user)
		return update_invoice(self._payload(**overrides))

	def _submit(self, user, draft_name):
		"""Full legit submit as `user`: opening shift first (same recipe as
		api/test_pos_invoice_submit.py)."""
		self._make_stock_receipt()
		frappe.set_user(ADMIN)
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": user,
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
			}
		).insert(ignore_permissions=True)
		shift.reload()
		shift.submit()

		frappe.set_user(user)
		return submit_invoice(
			invoice=json.dumps(
				self._payload(
					name=draft_name,
					doctype=self.doctype,
					posa_pos_opening_shift=shift.name,
				)
			),
			data=json.dumps({}),
		)

	# ---------------------------------------------------------------- SEC-03

	def test_same_profile_cashier_cannot_update_foreign_draft(self):
		draft = self._make_draft(self.cashier)

		frappe.set_user(self.cashier2)
		try:
			with self.assertRaises(frappe.PermissionError):
				update_invoice(
					self._payload(name=draft["name"], customer="Hacked")
				)
		finally:
			frappe.set_user(ADMIN)

		# nothing slipped through
		self.assertEqual(
			frappe.db.get_value(self.doctype, draft["name"], "customer"), self.customer
		)

	def test_non_profile_user_cannot_update_foreign_draft(self):
		draft = self._make_draft(self.cashier)

		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				update_invoice(
					self._payload(name=draft["name"], pos_profile=self.profile.name)
				)
		finally:
			frappe.set_user(ADMIN)

	def test_same_profile_cashier_cannot_submit_foreign_draft(self):
		draft = self._make_draft(self.cashier)

		frappe.set_user(self.cashier2)
		try:
			with self.assertRaises(frappe.PermissionError):
				submit_invoice(
					invoice=json.dumps(
						self._payload(name=draft["name"], doctype=self.doctype)
					),
					data=json.dumps({}),
				)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(frappe.db.get_value(self.doctype, draft["name"], "docstatus"), 0)

	def test_mass_assignment_fields_are_server_owned(self):
		draft = self._make_draft(
			self.cashier, owner=ADMIN, docstatus=1, amended_from="FORGED"
		)
		name = draft["name"]

		self.assertEqual(frappe.db.get_value(self.doctype, name, "owner"), self.cashier)
		self.assertEqual(frappe.db.get_value(self.doctype, name, "docstatus"), 0)
		self.assertFalse(frappe.db.get_value(self.doctype, name, "amended_from"))

	def test_cashier_can_save_update_and_submit_own_draft(self):
		draft = self._make_draft(self.cashier)

		# owner re-save (the normal every-keystroke draft update)
		frappe.set_user(self.cashier)
		update_invoice(
			self._payload(name=draft["name"], customer=self.customer)
		)
		self.assertEqual(frappe.db.get_value(self.doctype, draft["name"], "docstatus"), 0)

		result = self._submit(self.cashier, draft["name"])
		self.assertEqual(
			frappe.db.get_value(self.doctype, result["name"], "docstatus"), 1
		)

	# ---------------------------------------------------------------- SEC-05

	def test_return_reads_gated_per_profile(self):
		draft = self._make_draft(self.cashier)
		self._submit(self.cashier, draft["name"])
		name = draft["name"]

		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_invoice_for_return(name)
			with self.assertRaises(frappe.PermissionError):
				prepare_return_invoice(name)
			with self.assertRaises(frappe.PermissionError):
				get_returnable_invoices(pos_profile=self.profile.name)
			with self.assertRaises(frappe.PermissionError):
				search_invoice_by_number(name[-6:], pos_profile=self.profile.name)
			with self.assertRaises(frappe.PermissionError):
				search_invoices_for_return()
		finally:
			frappe.set_user(ADMIN)

		# same-profile cashiers read fine and see the invoice in the listings;
		# prepare_return_invoice additionally needs doctype create (ERPNext's
		# mapper), which the owner holds via Accounts Manager
		frappe.set_user(self.cashier)
		try:
			prepare_return_invoice(name)  # must not throw
		finally:
			frappe.set_user(ADMIN)

		frappe.set_user(self.cashier2)
		try:
			invoice_dict = get_invoice_for_return(name)
			self.assertEqual(invoice_dict["name"], name)
			returnable = get_returnable_invoices(
				limit=50, pos_profile=self.profile.name
			)
			self.assertIn(name, [row.name for row in returnable])
			matches = search_invoice_by_number(name[-6:], pos_profile=self.profile.name)
			self.assertIn(name, [row.name for row in matches])
			searched = search_invoices_for_return(invoice_name=name)
			self.assertIn(name, [row.name for row in searched["invoices"]])
		finally:
			frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- SEC-06

	def test_cleanup_clamps_age_and_deletes_own_drafts_only(self):
		old_draft = self._make_draft(self.cashier)
		fresh_draft = self._make_draft(self.cashier)
		foreign_old = self._make_draft(self.cashier2)
		for name in (old_draft["name"], foreign_old["name"]):
			frappe.db.set_value(
				self.doctype, name, "modified", "2000-01-01 00:00:00", update_modified=False
			)

		# roleless user without profile access: cleanup deletes nothing
		frappe.set_user(self.intruder)
		try:
			result = cleanup_old_drafts(max_age_hours=0)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(result["deleted"], 0)

		# the owner: max_age_hours=0 is clamped to 24h, so only the aged own
		# draft goes; the fresh one and the other cashier's draft survive
		frappe.set_user(self.cashier)
		try:
			result = cleanup_old_drafts(max_age_hours=0)
		finally:
			frappe.set_user(ADMIN)

		self.assertGreaterEqual(result["deleted"], 1)
		self.assertFalse(frappe.db.exists(self.doctype, old_draft["name"]))
		self.assertTrue(frappe.db.exists(self.doctype, fresh_draft["name"]))
		self.assertTrue(frappe.db.exists(self.doctype, foreign_old["name"]))
