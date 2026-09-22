# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-07 acceptance tests: shift authorization cluster in pos_next.api.shifts.

Four gates under test:
- get_closing_shift_data: only the opening shift's owner (or a user with
  per-document POS Opening Shift read) may derive closing data;
- make_closing_shift_from_opening: same ownership rule, POS Closing Shift read;
- check_opening_shift: the client-supplied user param is pinned — only callers
  with POS Opening Shift read may query another user;
- create_opening_shift: POS Profile membership required (PATTERN A) and the
  open-shift duplicate check is serialized (PATTERN C) so two calls for the
  same (profile, user) cannot both open a shift.

Run via pos_next/_pn_run_tests.py pos_next.api.test_shifts_authorization
"""

import json
import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, nowdate

from pos_next.api.shifts import (
	check_opening_shift,
	create_opening_shift,
	get_closing_shift_data,
)
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	make_closing_shift_from_opening,
)

ADMIN = "Administrator"

# Schedule-safe profile: opening a shift must never throw for being outside a
# scheduled window (same filter as api/test_closing_shift_security.py).
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]

OPENING_CASH = 100


class TestShiftsAuthorization(FrappeTestCase):
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
		# roleless users: no POS Opening/Closing Shift permissions of any kind.
		cls.intruder = f"shift-auth.{uuid.uuid4().hex[:8]}@example.com"
		cls.manager = f"shift-auth.{uuid.uuid4().hex[:8]}@example.com"
		cls.cashier = f"shift-auth.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.intruder, cls.manager, cls.cashier):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Shift Auth Tester"}
			).insert()
		# the cashier may open shifts on the profile (POS Profile User row;
		# removed again in teardown — shared dev profile)
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

		for email in (cls.intruder, cls.manager, cls.cashier):
			_safe(lambda e=email: frappe.db.delete("Error Log", {"owner": e}))
			_safe(lambda e=email: frappe.db.delete("POS Profile User", {"user": e}))
			_safe(lambda e=email: frappe.delete_doc("User", e, force=1))
		frappe.db.commit()
		super().tearDownClass()

	def _open_shift(self, user=ADMIN):
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": user,
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": OPENING_CASH}],
			}
		).insert(ignore_permissions=True)
		shift.submit()
		return shift

	def _balance_details(self):
		return json.dumps([{"mode_of_payment": self.mode[0], "opening_amount": OPENING_CASH}])

	# ── get_closing_shift_data (PATTERN B) ───────────────────────────────────

	def test_get_closing_shift_data_owner_allowed(self):
		shift = self._open_shift()
		data = get_closing_shift_data(shift.name)
		self.assertEqual(data.get("pos_opening_shift"), shift.name)

	def test_get_closing_shift_data_non_owner_rejected(self):
		shift = self._open_shift()
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_closing_shift_data(shift.name)
		finally:
			frappe.set_user(ADMIN)
		# the shift itself is untouched
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift.name, "status"), "Open")

	# ── make_closing_shift_from_opening (read gate, T2 style) ────────────────

	def _opening_payload(self, shift):
		# the payload submit_closing_shift itself builds (server-side derive)
		return json.dumps(frappe.get_doc("POS Opening Shift", shift.name).as_dict(), default=str)

	def test_make_closing_shift_owner_allowed(self):
		shift = self._open_shift()
		data = make_closing_shift_from_opening(self._opening_payload(shift))
		self.assertEqual(data.get("pos_opening_shift"), shift.name)

	def test_make_closing_shift_non_owner_rejected(self):
		shift = self._open_shift()
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				make_closing_shift_from_opening(self._opening_payload(shift))
		finally:
			frappe.set_user(ADMIN)

	# ── check_opening_shift (user param pinned) ──────────────────────────────

	def test_check_opening_shift_own_shift_without_param(self):
		# the smooth cashier path: no param, own shift returned
		shift = self._open_shift()
		data = check_opening_shift()
		self.assertIsNotNone(data)
		self.assertEqual(data["pos_opening_shift"].name, shift.name)

	def test_check_opening_shift_other_user_rejected_without_permission(self):
		self._open_shift()
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				check_opening_shift(ADMIN)
		finally:
			frappe.set_user(ADMIN)

	def test_check_opening_shift_read_permitted_user_may_query_others(self):
		shift = self._open_shift()
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
		frappe.set_user(self.manager)
		try:
			data = check_opening_shift(ADMIN)
			self.assertIsNotNone(data)
			self.assertEqual(data["pos_opening_shift"].name, shift.name)
		finally:
			frappe.set_user(ADMIN)

	# ── create_opening_shift (PATTERN A + serialized PATTERN C) ──────────────

	def test_create_opening_shift_rejects_non_profile_user(self):
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
		finally:
			frappe.set_user(ADMIN)
		# nothing was created by the rejected caller
		self.assertEqual(
			frappe.db.count("POS Opening Shift", {"user": self.intruder, "docstatus": 1}), 0
		)

	def test_create_opening_shift_duplicate_is_serialized(self):
		# sequential race (same lock path as concurrent calls): the second
		# create must hit the open-shift check and be rejected — never two
		# open shifts for one (profile, user)
		frappe.set_user(self.cashier)
		try:
			first = create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
			self.assertEqual(first["pos_opening_shift"].get("user"), self.cashier)
			with self.assertRaises(frappe.ValidationError):
				create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(
			frappe.db.count("POS Opening Shift", {"user": self.cashier, "docstatus": 1}), 1
		)
