# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-02 acceptance tests: POS Closing Shift submit cannot be forged.

The submit endpoint must hold three properties:
- a non-owner without closing-shift rights is rejected outright;
- whatever the owner sends in the payload (forged expected_amount, fake
  reconciliation rows), the stored numbers are recomputed server-side and
  the client only contributes the counted closing_amount per mode;
- the owner's legitimate (even minimal) payload still closes the shift.

Run via pos_next/_pn_run_tests.py pos_next.api.test_closing_shift_security
"""

import json
import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, now_datetime, nowdate

from pos_next.api.shifts import get_closing_shift_data, submit_closing_shift

ADMIN = "Administrator"

# Schedule-safe profile: opening a shift must never throw for being outside a
# scheduled window (same filter as api/test_backdate_invoices.py).
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]

OPENING_CASH = 100


class TestClosingShiftSubmitSecurity(FrappeTestCase):
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
		# roleless users: no POS Closing Shift permissions of any kind.
		# Two of them: the manager-branch test grants a role and its redis
		# role cache outlives the test's DB rollback, so it must never share
		# a user with the rejection test.
		cls.intruder = f"closing-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.manager = f"closing-sec.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.intruder, cls.manager):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Closing Sec Tester"}
			).insert()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			# tolerant teardown (see test_backdate_invoices): one leftover must
			# never abort the remaining cleanup on this shared dev site
			try:
				step()
			except Exception:
				pass

		user = getattr(cls, "intruder", None)
		if user:
			_safe(lambda: frappe.db.delete("Error Log", {"owner": user}))
			_safe(lambda: frappe.delete_doc("User", user, force=1))
		user = getattr(cls, "manager", None)
		if user:
			_safe(lambda: frappe.db.delete("Error Log", {"owner": user}))
			_safe(lambda: frappe.delete_doc("User", user, force=1))
		frappe.db.commit()
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

	def test_non_owner_without_role_is_rejected(self):
		shift = self._open_shift()
		closing = get_closing_shift_data(shift.name)
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				submit_closing_shift(json.dumps(closing))
		finally:
			frappe.set_user(ADMIN)
		# nothing slipped through: the shift is still open
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift.name, "status"), "Open")

	def test_non_owner_with_submit_permission_may_close(self):
		# the other gate branch: a permitted user (e.g. HO/manager role) may
		# close someone else's shift — with server-computed numbers only
		frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": self.manager,
				"parenttype": "User",
				"parentfield": "roles",
				"role": "System Manager",
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=self.manager)
		shift = self._open_shift()
		closing = get_closing_shift_data(shift.name)
		frappe.set_user(self.manager)
		try:
			name = submit_closing_shift(json.dumps(closing))["name"]
			self.assertEqual(frappe.db.get_value("POS Closing Shift", name, "docstatus"), 1)
		finally:
			frappe.set_user(ADMIN)

	def test_forged_payload_cannot_override_server_numbers(self):
		shift = self._open_shift()
		closing = get_closing_shift_data(shift.name)

		# forger marks the drawer "balanced" by faking expected_amount and
		# slips in a reconciliation row for a mode that never existed
		closing["payment_reconciliation"][0]["expected_amount"] = 0
		closing["payment_reconciliation"][0]["closing_amount"] = OPENING_CASH
		closing["payment_reconciliation"].append(
			{
				"mode_of_payment": "Forged Mode",
				"opening_amount": 0,
				"expected_amount": 0,
				"closing_amount": 1000,
			}
		)
		closing["grand_total"] = 999

		name = submit_closing_shift(json.dumps(closing))["name"]
		doc = frappe.get_doc("POS Closing Shift", name)

		self.assertEqual(len(doc.payment_reconciliation), 1)
		row = doc.payment_reconciliation[0]
		# expected_amount is the server's number (the opening float), not 0
		self.assertEqual(flt(row.expected_amount), OPENING_CASH)
		self.assertEqual(flt(row.closing_amount), OPENING_CASH)
		self.assertEqual(flt(row.difference), 0)
		# header totals are recomputed too, not trusted from the payload
		self.assertEqual(flt(doc.grand_total), 0)

	def test_owner_minimal_payload_still_closes_shift(self):
		shift = self._open_shift()
		# identity + counted amounts only: no totals, no expected values
		payload = {
			"pos_opening_shift": shift.name,
			"payment_reconciliation": [
				{"mode_of_payment": self.mode[0], "closing_amount": OPENING_CASH}
			],
		}
		name = submit_closing_shift(json.dumps(payload))["name"]
		self.assertEqual(frappe.db.get_value("POS Closing Shift", name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift.name, "status"), "Closed")
