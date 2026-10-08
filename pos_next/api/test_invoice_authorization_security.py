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
	check_invoice_return_validity,
	get_invoice_for_return,
	get_invoices,
	get_returnable_invoices,
	prepare_return_invoice,
	search_invoice_by_number,
	search_invoices_for_return,
	submit_invoice,
	update_invoice,
)
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE, get_pos_invoice_doctype
from pos_next.tests._posi_test_utils import _set_invoice_type
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
		# fixtures below insert through the Sales Invoice lane without an
		# opening-shift fixture, so an ambient site left in POS Invoice mode
		# breaks every submit with "No open POS Opening Entry" — pin the mode
		# for the run and restore the site's baseline in tearDownClass
		cls._invoice_type_baseline = frappe.db.get_single_value(
			"POS Next Global Settings", "invoice_type"
		)
		_set_invoice_type(SALES_INVOICE)
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
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1, "has_batch_no": 0, "has_serial_no": 0},
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
		# (not just the profile gate) blocks the IDOR. Both get minimal
		# cashier-ADJACENT roles (Item/Customer reads via Stock User, no
		# invoice-doctype write) — an artificial probe persona, NOT the
		# official single-role recipe (POSNext Cashier holds invoice write,
		# which would bypass what this test must prove).
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
		_set_invoice_type(cls._invoice_type_baseline or SALES_INVOICE)

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
		# the foreign-outlet fixture survives the per-test rollback (something in
		# the seed path commits), and a stranded profile poisons any module that
		# picks "the first enabled POS Profile" — the PO proxy tests once priced
		# against this company in its currency instead of the site's. Sweep by name.
		company = "_Test PG Co RetScope"
		profile = "_Test POS Profile RetScope"
		_safe(lambda: frappe.db.delete("POS Invoice", {"company": company}))
		_safe(lambda: frappe.db.delete("Sales Invoice", {"company": company}))
		_safe(lambda: frappe.db.delete("POS Settings", {"pos_profile": profile}))
		_safe(lambda: frappe.delete_doc("POS Profile", profile, force=1, ignore_permissions=True))
		_safe(lambda: frappe.db.delete("Warehouse", {"company": company}))
		_safe(lambda: frappe.delete_doc("Company", company, force=1, ignore_permissions=True))
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

	def _open_shift(self, user):
		"""Open a POS Opening Shift for `user` on the class profile."""
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
		# per-method cleanup: only one open shift per profile is allowed now,
		# leftovers from an earlier method would trip the doctype guard
		self.addCleanup(self._remove_shift, shift)
		return shift

	def _remove_shift(self, shift):
		# tolerant cleanup: one leftover must never abort the remaining cleanup
		try:
			doc = frappe.get_doc("POS Opening Shift", shift.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("POS Opening Shift", shift.name, force=1)
		except Exception:
			pass

	def _submit(self, user, draft_name, doctype=None, shift=None):
		"""Full legit submit as `user`: opening shift first (same recipe as
		api/test_pos_invoice_submit.py). Pass `shift` to reuse an already-open
		one — the guard admits only one open shift per profile."""
		doctype = doctype or self.doctype
		self._make_stock_receipt()
		if shift is None:
			shift = self._open_shift(user)
		frappe.set_user(user)
		return submit_invoice(
			invoice=json.dumps(
				self._payload(
					name=draft_name,
					doctype=doctype,
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

	# ------------------------------------------- outlet isolation (E2 scope)

	def _make_foreign_profile(self):
		"""A second outlet: dedicated company + warehouse + POS Profile with
		no users — the intercompany "1 outlet = 1 company" neighbor."""
		from pos_next.tests.price_group_helpers import (
			make_test_company,
			make_test_pos_profile,
			make_test_warehouse,
		)

		company = make_test_company("RetScope")
		warehouse = make_test_warehouse("RetScope", company)
		profile = make_test_pos_profile("RetScope", company, warehouse)
		return frappe.db.get_value("POS Profile", profile, ["name", "company"], as_dict=True)

	def _seed_invoice_row(self, profile_name, company, doctype=None):
		"""Minimal submitted POS-style invoice under `profile_name`.

		The return READ gates are pure queries (the mapper only runs after the
		gate), so a raw row + one item row is enough — no shift, stock or GL.
		Child row is inserted explicitly: ignore_validate skips the machinery
		that stamps parent/parentfield onto appended children.
		Raw-value surgery mirrors test_cleanup's modified-date touch."""
		doctype = doctype or self.doctype
		doc = frappe.get_doc(
			{
				"doctype": doctype,
				"company": company,
				"customer": self.customer,
				"pos_profile": profile_name,
				"is_pos": 1,
				"is_return": 0,
				"posting_date": nowdate(),
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		item_row = frappe.get_doc(
			{
				"doctype": f"{doctype} Item",
				"parent": doc.name,
				"parenttype": doctype,
				"parentfield": "items",
				"idx": 1,
				"docstatus": 1,
				"item_code": self.item[0],
				"qty": 1,
				"rate": 100,
			}
		)
		item_row.flags.ignore_validate = True
		item_row.flags.ignore_mandatory = True
		item_row.insert(ignore_permissions=True)
		frappe.db.set_value(doctype, doc.name, "docstatus", 1, update_modified=False)
		return doc.name

	def test_return_validity_reports_wrong_outlet_for_foreign_profile(self):
		foreign = self._make_foreign_profile()
		name = self._seed_invoice_row(foreign.name, foreign.company)

		frappe.set_user(self.cashier)
		try:
			result = check_invoice_return_validity(name)
			# truthful: the code is real but belongs to another outlet
			self.assertFalse(result["valid"])
			self.assertEqual(result["error_type"], "wrong_outlet")
			self.assertEqual(result["outlet"], foreign.name)

			# the listings never leak the foreign row for the cashier
			self.assertEqual(search_invoice_by_number(name[-6:]), [])
			returnable = get_returnable_invoices()
			self.assertNotIn(name, [row.name for row in returnable])

			# preparation stays a hard no (defense in depth behind the gate)
			with self.assertRaises(frappe.PermissionError):
				prepare_return_invoice(name)
		finally:
			frappe.set_user(ADMIN)

	def test_return_blocked_across_companies_even_for_member(self):
		# cashier belongs to both outlets, but the open shift is in the home
		# company: the other company's invoice must not be returnable from
		# this drawer (refund would leave B's cash for A's sale)
		foreign = self._make_foreign_profile()
		member = frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": foreign.name,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": self.cashier,
			}
		).insert(ignore_permissions=True)
		# the foreign fixture survives rollback (see tearDownClass): a leaked
		# membership would un-foreign the profile for later tests
		self.addCleanup(lambda: frappe.db.delete("POS Profile User", {"name": member.name}) or frappe.db.commit())
		name = self._seed_invoice_row(foreign.name, foreign.company)
		self._open_shift(self.cashier)

		frappe.set_user(self.cashier)
		try:
			self.assertEqual(check_invoice_return_validity(name)["error_type"], "wrong_outlet")
			self.assertEqual(search_invoice_by_number(name[-6:]), [])
			self.assertNotIn(name, [row.name for row in get_returnable_invoices()])
			with self.assertRaises(frappe.PermissionError):
				prepare_return_invoice(name)
		finally:
			frappe.set_user(ADMIN)

	def test_admin_return_scope_follows_requested_profile(self):
		# own invoice first: the submit pipeline runs inner savepoint rollbacks
		# that would wipe an uncommitted seed created before it
		draft = self._make_draft(self.cashier)
		own_name = self._submit(self.cashier, draft["name"])["name"]

		foreign = self._make_foreign_profile()
		foreign_name = self._seed_invoice_row(foreign.name, foreign.company)

		# without a profile the HQ lane stays unrestricted (admin explicitly:
		# _submit leaves the session as the cashier, whose membership scope
		# rightly hides the foreign outlet)
		frappe.set_user(ADMIN)
		names = [row.name for row in get_returnable_invoices(limit=100)]
		self.assertIn(own_name, names)
		self.assertIn(foreign_name, names)
		self.assertIn(foreign_name, names)

		# with a profile: outlet-pure — the requested outlet only
		names = [
			row.name
			for row in get_returnable_invoices(limit=100, pos_profile=self.profile.name)
		]
		self.assertIn(own_name, names)
		self.assertNotIn(foreign_name, names)

		self.assertEqual(
			[row.name for row in search_invoice_by_number(foreign_name[-6:], pos_profile=self.profile.name)],
			[],
		)
		self.assertIn(
			own_name,
			[row.name for row in search_invoice_by_number(own_name[-6:], pos_profile=self.profile.name)],
		)
		self.assertEqual(
			search_invoices_for_return(invoice_name=foreign_name, pos_profile=self.profile.name)["invoices"],
			[],
		)
		self.assertIn(
			own_name,
			[
				row.name
				for row in search_invoices_for_return(invoice_name=own_name, pos_profile=self.profile.name)[
					"invoices"
				]
			],
		)

	def test_return_fallback_finds_own_invoice_in_other_doctype(self):
		# sell in POS Invoice mode while the site sits on Sales Invoice; the
		# draft must already carry the shift — a shiftless POS Invoice draft
		# falls into ERPNext's native opening-entry validation
		_set_invoice_type(POS_INVOICE)
		try:
			shift = self._open_shift(self.cashier)
			draft = self._make_draft(self.cashier, posa_pos_opening_shift=shift.name)
			name = self._submit(self.cashier, draft["name"], doctype=POS_INVOICE, shift=shift)["name"]
		finally:
			_set_invoice_type(SALES_INVOICE)
		self.assertEqual(frappe.db.get_value(POS_INVOICE, name, "docstatus"), 1)

		# ambient is Sales Invoice again: the code must still be returnable
		# for the outlet (fallback), prepared with the matching mapper
		frappe.set_user(self.cashier)
		try:
			self.assertTrue(check_invoice_return_validity(name)["valid"])
			return_doc = prepare_return_invoice(name)
			self.assertEqual(return_doc["pos_profile"], self.profile.name)
			self.assertEqual(len(return_doc["items"]), 1)
		finally:
			frappe.set_user(ADMIN)

		# a foreign-profile invoice in the other doctype answers wrong_outlet
		# too — the fallback never widens visibility
		foreign = self._make_foreign_profile()
		foreign_name = self._seed_invoice_row(foreign.name, foreign.company, doctype=POS_INVOICE)
		frappe.set_user(self.cashier)
		try:
			result = check_invoice_return_validity(foreign_name)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(result["error_type"], "wrong_outlet")

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


class TestInvoiceProfileGates(FrappeTestCase):
	"""SEC-03 follow-up: the doctype-permission fallbacks are gone.

	The cashier persona here is the REAL daily one — a POS Profile member
	holding POSNext Cashier (which grants Sales Invoice/POS Invoice
	read+write). That doctype permission must no longer let the cashier cross
	outlets (get_invoices / update_invoice profile gate) or touch a same-
	profile colleague's draft (owner gate). Management (POSNext Manager)
	and the draft owner keep passing.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		if not frappe.db.exists("Role", "POSNext Manager"):
			raise unittest.SkipTest("POSNext Manager role does not exist on this site")
		# invoices are created through the Sales Invoice lane (no opening-shift
		# fixture); pin the site switch so an ambient POS Invoice mode cannot
		# force a shift onto every draft, restore the baseline in teardown
		cls._invoice_type_baseline = frappe.db.get_single_value(
			"POS Next Global Settings", "invoice_type"
		)
		_set_invoice_type(SALES_INVOICE)

		profiles = frappe.get_all(
			"POS Profile",
			filters=_PROFILE_FILTER,
			fields=["name", "company", "warehouse"],
			order_by="creation asc",
			limit=2,
		)
		cls.profile = profiles[0] if profiles else None
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
		cls.customer = get_default_customer()
		if not (cls.profile and cls.mode and cls.customer):
			raise unittest.SkipTest("no usable POS Profile / customer")
		cls.doctype = get_pos_invoice_doctype()

		# a plain non-stock item: drafts never enter the stock pre-check, so no
		# Material Receipt fixture is needed (deleted again in teardown)
		from pos_next.tests.price_group_helpers import make_test_item

		cls.item = make_test_item(f"InvGate{uuid.uuid4().hex[:6]}", is_stock_item=0)

		# second outlet: an existing schedule-safe profile when the site has
		# one, else a second profile on the SAME company (same-company keeps
		# customer/currency/account semantics identical; profile + warehouse
		# are swept in teardown)
		cls._created_outlet = None
		if len(profiles) > 1:
			cls.other_profile = profiles[1]
		else:
			from pos_next.tests.price_group_helpers import (
				make_test_pos_profile,
				make_test_warehouse,
			)

			suffix = f"InvGate{uuid.uuid4().hex[:6]}"
			warehouse = make_test_warehouse(suffix, cls.profile.company)
			profile_name = make_test_pos_profile(suffix, cls.profile.company, warehouse)
			cls._created_outlet = (profile_name, warehouse)
			cls.other_profile = frappe.db.get_value(
				"POS Profile", profile_name, ["name", "company", "warehouse"], as_dict=True
			)

		cls.cashier = f"inv-gate.{uuid.uuid4().hex[:8]}@example.com"
		cls.cashier2 = f"inv-gate.{uuid.uuid4().hex[:8]}@example.com"
		cls.manager = f"inv-gate.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.cashier, cls.cashier2, cls.manager):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Invoice Gate Tester"}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", cls.manager).add_roles("POSNext Manager")
		for email in (cls.cashier, cls.cashier2):
			# the daily persona: role grants invoice read+write, which is
			# exactly what used to slip past the gates
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": email,
					"parenttype": "User",
					"parentfield": "roles",
					"role": "POSNext Cashier",
				}
			).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "POS Profile User",
					"parent": cls.profile.name,
					"parenttype": "POS Profile",
					"parentfield": "applicable_for_users",
					"user": email,
				}
			).insert(ignore_permissions=True)
		for email in (cls.cashier, cls.cashier2, cls.manager):
			frappe.clear_cache(user=email)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		_set_invoice_type(cls._invoice_type_baseline or SALES_INVOICE)

		def _safe(step):
			# tolerant teardown (see test_closing_shift_security): one leftover
			# must never abort the remaining cleanup on this shared dev site
			try:
				step()
			except Exception:
				pass

		# invoices first: the teardown commit below would otherwise persist
		# every draft the tests created (owners are the run's throwaway users)
		for email in (cls.cashier, cls.cashier2, cls.manager):
			for doctype in ("Sales Invoice", "POS Invoice"):
				for name in frappe.get_all(doctype, {"owner": email, "docstatus": 0}, pluck="name"):
					_safe(
						lambda d=doctype, n=name: frappe.delete_doc(
							d, n, force=1, ignore_permissions=True
						)
					)
		_safe(lambda: frappe.delete_doc("Item", cls.item, force=1, ignore_permissions=True))
		if cls._created_outlet:
			profile, warehouse = cls._created_outlet
			_safe(lambda: frappe.db.delete("POS Settings", {"pos_profile": profile}))
			_safe(
				lambda: frappe.delete_doc("POS Profile", profile, force=1, ignore_permissions=True)
			)
			_safe(lambda: frappe.delete_doc("Warehouse", warehouse, force=1, ignore_permissions=True))
		for email in (cls.cashier, cls.cashier2, cls.manager):
			_safe(lambda e=email: frappe.db.delete("Error Log", {"owner": e}))
			_safe(lambda e=email: frappe.db.delete("POS Profile User", {"user": e}))
			_safe(lambda e=email: frappe.delete_doc("User", e, force=1, ignore_permissions=True))
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(ADMIN)

	def tearDown(self):
		frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- helpers

	def _payload(self, item_warehouse=None, **overrides):
		payload = {
			"pos_profile": self.profile.name,
			"customer": self.customer,
			"items": [
				{
					"item_code": self.item,
					"qty": 1,
					"rate": 100,
					"warehouse": item_warehouse or self.profile.warehouse,
				}
			],
			"payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
		}
		payload.update(overrides)
		return payload

	def _insert_draft(self, user, **overrides):
		frappe.set_user(user)
		try:
			return update_invoice(self._payload(**overrides))
		finally:
			frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- get_invoices

	def test_cross_profile_cashier_cannot_list_invoices(self):
		frappe.set_user(self.cashier)
		try:
			# premise of the regression: this persona DOES hold doctype read,
			# which the removed fallback (`has_permission(doctype, "read")`)
			# accepted as profile access
			self.assertTrue(frappe.has_permission(self.doctype, "read"))
			self.assertFalse(
				frappe.db.exists(
					"POS Profile User",
					{"parent": self.other_profile.name, "user": self.cashier},
				)
			)
			with self.assertRaises(frappe.PermissionError):
				get_invoices(pos_profile=self.other_profile.name)
		finally:
			frappe.set_user(ADMIN)

	def test_cross_profile_manager_can_list_invoices(self):
		frappe.set_user(self.manager)
		try:
			rows = get_invoices(pos_profile=self.other_profile.name)
		finally:
			frappe.set_user(ADMIN)
		self.assertIsInstance(rows, list)

	def test_own_profile_cashier_can_list_invoices(self):
		frappe.set_user(self.cashier)
		try:
			rows = get_invoices(pos_profile=self.profile.name)
		finally:
			frappe.set_user(ADMIN)
		self.assertIsInstance(rows, list)

	# ---------------------------------------------------------------- update_invoice

	def test_same_profile_cross_owner_update_rejected_manager_allowed(self):
		draft = self._insert_draft(self.cashier2)
		name = draft["name"]

		frappe.set_user(self.cashier)
		try:
			with self.assertRaises(frappe.PermissionError):
				update_invoice(self._payload(name=name, customer="Hacked"))
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(frappe.db.get_value(self.doctype, name, "customer"), self.customer)

		# management is the only non-owner path now
		frappe.set_user(self.manager)
		try:
			update_invoice(self._payload(name=name, remarks="manager-touch"))
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(frappe.db.get_value(self.doctype, name, "remarks"), "manager-touch")

	def test_cross_profile_insert_rejected_for_cashier(self):
		# the profile gate itself: a cashier with invoice write may not create
		# a draft under an outlet they are not a user of (the removed
		# `doctype` fallback used to admit exactly this)
		frappe.set_user(self.cashier)
		try:
			with self.assertRaises(frappe.PermissionError):
				update_invoice(
					self._payload(
						item_warehouse=self.other_profile.warehouse,
						pos_profile=self.other_profile.name,
					)
				)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(
			frappe.db.count(
				self.doctype,
				{"owner": self.cashier, "pos_profile": self.other_profile.name},
			),
			0,
		)

	def test_cross_profile_update_rejected_for_cashier_manager_allowed(self):
		# manager seeds a fully-valid draft in the OTHER outlet through the API
		# (management bypasses the profile gate on insert)...
		draft = self._insert_draft(
			self.manager,
			item_warehouse=self.other_profile.warehouse,
			pos_profile=self.other_profile.name,
		)
		name = draft["name"]
		# ...then the owner is pinned to the cashier at DB level: the owner
		# gate passes for them, so the refusal below pins the PROFILE gate for
		# a caller holding invoice write.
		frappe.db.set_value(self.doctype, name, "owner", self.cashier, update_modified=False)

		frappe.set_user(self.cashier)
		try:
			with self.assertRaises(frappe.PermissionError):
				update_invoice(
					self._payload(
						name=name,
						item_warehouse=self.other_profile.warehouse,
						pos_profile=self.other_profile.name,
					)
				)
		finally:
			frappe.set_user(ADMIN)

		# management is the one non-owner path that accepts the foreign outlet
		frappe.set_user(self.manager)
		try:
			update_invoice(
				self._payload(
					name=name,
					item_warehouse=self.other_profile.warehouse,
					pos_profile=self.other_profile.name,
					remarks="manager-cross",
				)
			)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(frappe.db.get_value(self.doctype, name, "remarks"), "manager-cross")
