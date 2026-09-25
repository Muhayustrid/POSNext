# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Closing-shift endpoint hardening (audit group 6, cluster BE-2).

- COR-BE-06: submit_closing_shift takes the opening shift's row lock as its
  first DB statement and validate's duplicate check is itself a locking read,
  so two closings of one opening cannot both commit.
- COR-BE-15: get_pos_invoices is a pure read; the printed drafts it used to
  submit as a GET side effect are now submitted by the explicit close, which
  recomputes the same totals server-side.
- SEC-NEW-11: get_cashiers drops filter keys outside its whitelist and gates
  reads on profile membership or closing-shift read access.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_closing_shift_race
"""

import json
import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, nowdate

from pos_next.api.invoices import update_invoice
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	get_cashiers,
	get_pos_invoices,
	make_closing_shift_from_opening,
	submit_closing_shift,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin, _set_invoice_type

ADMIN = "Administrator"

# Schedule-safe profile filter, same as api/test_closing_shift_security.py.
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]

OPENING_CASH = 100


class TestDuplicateClosingRace(FrappeTestCase):
	"""COR-BE-06: concurrent closes of one opening serialize on a row lock."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.profile = frappe.db.get_value(
			"POS Profile",
			_PROFILE_FILTER,
			["name", "company"],
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
		if not (cls.profile and cls.mode):
			raise unittest.SkipTest("no schedule-safe POS Profile with a payment method")

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		super().tearDownClass()

	def _open_shift(self):
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": ADMIN,
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": OPENING_CASH}],
			}
		).insert(ignore_permissions=True)
		shift.submit()
		return shift

	def _payload(self, shift):
		return {
			"pos_opening_shift": shift.name,
			"payment_reconciliation": [
				{"mode_of_payment": self.mode[0], "closing_amount": OPENING_CASH}
			],
		}

	def _spy_db_get_value(self):
		seen = []
		original = frappe.db.get_value

		def wrapper(*args, **kwargs):
			seen.append((args, kwargs))
			return original(*args, **kwargs)

		frappe.db.get_value = wrapper
		self.addCleanup(setattr, frappe.db, "get_value", original)
		return seen

	def test_first_opening_read_is_the_row_lock(self):
		# the very first read of the opening inside submit_closing_shift must
		# take the lock: everything after it (duplicate check included) then
		# runs against the state committed by any close that held it before
		shift = self._open_shift()
		seen = self._spy_db_get_value()
		name = submit_closing_shift(json.dumps(self._payload(shift)))
		self.assertEqual(frappe.db.get_value("POS Closing Shift", name, "docstatus"), 1)

		opening_reads = [(a, k) for a, k in seen if a and a[0] == "POS Opening Shift"]
		self.assertTrue(opening_reads)
		self.assertTrue(
			opening_reads[0][1].get("for_update"),
			f"first opening read is not locked: {opening_reads[0]}",
		)

	def test_second_close_for_same_opening_rejected(self):
		shift = self._open_shift()
		first = submit_closing_shift(json.dumps(self._payload(shift)))
		self.assertEqual(frappe.db.get_value("POS Closing Shift", first, "docstatus"), 1)

		with self.assertRaises(frappe.ValidationError):
			submit_closing_shift(json.dumps(self._payload(shift)))
		closings = frappe.get_all(
			"POS Closing Shift", {"pos_opening_shift": shift.name}, pluck="name"
		)
		self.assertEqual(len(closings), 1)


class TestCloseReadPurity(POSInvoiceModeMixin, FrappeTestCase):
	"""COR-BE-15: reads must not submit; the close must."""

	def setUp(self):
		super().setUp()

	def _fabricate_printed_draft(self):
		doc = frappe.new_doc("Sales Invoice")
		doc.company = self.profile.company
		doc.customer = self.customer
		doc.is_pos = 1
		doc.posting_date = nowdate()
		doc.posa_pos_opening_shift = self.shift.name
		doc.posa_is_printed = 1
		doc.name = "SI-RACE15-" + frappe.generate_hash(length=8)
		doc.db_insert()
		return doc.name

	def test_get_pos_invoices_leaves_printed_draft_alone(self):
		# a printed draft must survive a pure read of the shift invoices
		name = self._fabricate_printed_draft()
		get_pos_invoices(self.shift.name)
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "docstatus"), 0)

	def test_close_submits_printed_draft_and_counts_it(self):
		if not frappe.db.has_column("Sales Invoice", "posa_is_printed"):
			self.skipTest("no posa_is_printed custom field on Sales Invoice")
		_set_invoice_type("Sales Invoice")
		try:
			created = update_invoice(json.dumps(self._payload()))
			name = created["name"]
			self._created.append(name)
			frappe.db.set_value("Sales Invoice", name, "posa_is_printed", 1)

			payload = {
				"pos_opening_shift": self.shift.name,
				"payment_reconciliation": [
					{"mode_of_payment": self.mode[0], "closing_amount": 100}
				],
			}
			closing_name = submit_closing_shift(json.dumps(payload))
			# let the mixin tearDown cancel + sweep the closing shift
			self.closing = frappe._dict(name=closing_name)

			# the printed draft was submitted by the close, not by a GET
			self.assertEqual(frappe.db.get_value("Sales Invoice", name, "docstatus"), 1)
			closing = frappe.get_doc("POS Closing Shift", closing_name)
			self.assertIn(name, [r.sales_invoice for r in closing.pos_transactions])
		finally:
			_set_invoice_type("POS Invoice")

	def test_desk_style_close_submits_printed_draft_and_counts_it(self):
		"""Review MAJOR on COR-BE-15: the Desk lane (preview fills the form,
		the user saves and submits the doc itself) must not lose printed
		drafts now that the read endpoints are pure."""
		if not frappe.db.has_column("Sales Invoice", "posa_is_printed"):
			self.skipTest("no posa_is_printed custom field on Sales Invoice")
		_set_invoice_type("Sales Invoice")
		try:
			created = update_invoice(json.dumps(self._payload()))
			name = created["name"]
			self._created.append(name)
			frappe.db.set_value("Sales Invoice", name, "posa_is_printed", 1)

			opening = frappe.get_doc("POS Opening Shift", self.shift.name)
			preview = make_closing_shift_from_opening(json.dumps(opening.as_dict(), default=str))
			doc = frappe.get_doc(preview)
			for row in doc.payment_reconciliation:
				row.closing_amount = 100
			doc.flags.ignore_permissions = True
			doc.save()
			doc.submit()
			self.closing = frappe._dict(name=doc.name)

			self.assertEqual(frappe.db.get_value("Sales Invoice", name, "docstatus"), 1)
			closing = frappe.get_doc("POS Closing Shift", doc.name)
			self.assertIn(name, [r.sales_invoice for r in closing.pos_transactions])
		finally:
			_set_invoice_type("POS Invoice")


class TestGetCashiersGate(FrappeTestCase):
	"""SEC-NEW-11: cashier lookup drops unknown filters and gates reads."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.profile = frappe.db.get_value(
			"POS Profile", _PROFILE_FILTER, "name", order_by="creation asc"
		)
		if not cls.profile:
			raise unittest.SkipTest("no schedule-safe POS Profile")
		cls.cashier = f"cashier-gate.{uuid.uuid4().hex[:8]}@example.com"
		frappe.get_doc(
			{"doctype": "User", "email": cls.cashier, "first_name": "Cashier Gate Tester"}
		).insert()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		user = getattr(cls, "cashier", None)
		if user:
			try:
				frappe.db.delete("Error Log", {"owner": user})
				frappe.delete_doc("User", user, force=1)
			except Exception:
				pass
		frappe.db.commit()
		super().tearDownClass()

	def _add_profile_membership(self):
		row = frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": self.profile,
				"parenttype": "POS Profile",
				"parentfield": "pos_users",
				"user": self.cashier,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.db.delete("POS Profile User", {"name": row.name}))

	def test_whitelisted_filter_kept_unknown_dropped(self):
		seen = []
		original = frappe.get_all

		def spy(*args, **kwargs):
			seen.append((args, kwargs))
			return original(*args, **kwargs)

		frappe.get_all = spy
		try:
			get_cashiers(
				"POS Profile User",
				"",
				"user",
				0,
				20,
				{"parent": self.profile, "user": ["in", ["evil@example.com"]], "default": 1},
			)
		finally:
			frappe.get_all = original

		ours = [c for c in seen if c[0] and c[0][0] == "POS Profile User"]
		self.assertTrue(ours)
		self.assertEqual(ours[0][1].get("filters"), {"parent": self.profile})

	def test_roleless_non_member_rejected(self):
		frappe.set_user(self.cashier)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_cashiers("POS Profile User", "", "user", 0, 20, {"parent": self.profile})
		finally:
			frappe.set_user(ADMIN)

	def test_profile_member_may_lookup(self):
		self._add_profile_membership()
		frappe.set_user(self.cashier)
		try:
			# filters also arrive as a JSON string over the real link-query path
			result = get_cashiers(
				"POS Profile User", "", "user", 0, 20, json.dumps({"parent": self.profile})
			)
		finally:
			frappe.set_user(ADMIN)
		self.assertTrue(any(row[0] == self.cashier for row in result))
