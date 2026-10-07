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
  same (profile, user) cannot both open a shift;
- single open shift per POS Profile: the doctype before_submit guard refuses
  a second opening while another shift (any user) holds the profile — one
  profile is one cash drawer. Exempt by design: a user who may cancel a POS
  Closing Shift (manager) may deliberately open over an in-use profile.

Run via pos_next/_pn_run_tests.py pos_next.api.test_shifts_authorization
"""

import json
import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime, nowdate

from pos_next.api.shifts import (
	check_opening_shift,
	create_opening_shift,
	get_closing_shift_data,
	get_period_dashboard,
	get_period_summary,
	get_session_summary,
	get_shift_dashboard,
)
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	make_closing_shift_from_opening,
	submit_closing_shift,
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
		# second profile member: needed to prove the single-open-shift guard
		# fires for a DIFFERENT user on the same profile
		cls.cashier2 = f"shift-auth.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.intruder, cls.manager, cls.cashier, cls.cashier2):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Shift Auth Tester"}
			).insert()
		# the cashiers may open shifts on the profile (POS Profile User rows;
		# removed again in teardown — shared dev profile)
		for member, default in ((cls.cashier, 1), (cls.cashier2, 0)):
			frappe.get_doc(
				{
					"doctype": "POS Profile User",
					"parent": cls.profile.name,
					"parenttype": "POS Profile",
					"parentfield": "applicable_for_users",
					"user": member,
					"default": default,
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

		for email in (cls.intruder, cls.manager, cls.cashier, cls.cashier2):
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
		# per-method cleanup: only ONE open shift may exist per profile now
		# (single-open-shift guard), so shifts left open by an earlier test
		# method would make every later _open_shift in this class throw
		self.addCleanup(self._remove_shift, shift)
		return shift

	def _balance_details(self):
		return json.dumps([{"mode_of_payment": self.mode[0], "opening_amount": OPENING_CASH}])

	def _remove_shift(self, shift):
		# tolerant cleanup (same spirit as tearDownClass): restore the
		# possibly db-spoofed profile link, then cancel + delete so this run
		# leaves no open shifts behind
		try:
			frappe.db.set_value("POS Opening Shift", shift.name, "pos_profile", self.profile.name)
			doc = frappe.get_doc("POS Opening Shift", shift.name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("POS Opening Shift", shift.name, force=1)
		except Exception:
			pass

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

	# ── check_opening_shift: dangling POS Profile links ─────────────────────
	# Real incident: leftover test shifts referenced deleted POS Profiles and
	# check_opening_shift 500'd, driving the SPA into a stale cached shift.

	def test_check_opening_shift_skips_shift_with_deleted_profile(self):
		shift = self._open_shift(user=self.cashier)
		# db-level set, bypassing Link validation — mirrors what a bad
		# test-fixture teardown left behind
		frappe.db.set_value("POS Opening Shift", shift.name, "pos_profile", "_Deleted Profile XYZ")
		frappe.set_user(self.cashier)
		try:
			# must not raise; the cashier has no other open shift
			self.assertIsNone(check_opening_shift())
		finally:
			frappe.set_user(ADMIN)
			self._remove_shift(shift)

	def test_check_opening_shift_prefers_older_valid_shift(self):
		older = self._open_shift(user=self.cashier)
		# a LIVE second open shift on this profile is blocked by the single-
		# open-shift guard; simulate the legacy leftover it stands in for by
		# promoting a draft at DB level (exactly what a bad teardown leaves)
		newer = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": self.cashier,
				"posting_date": nowdate(),
				"period_start_date": add_to_date(now_datetime(), minutes=1),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": OPENING_CASH}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"POS Opening Shift",
			newer.name,
			{"pos_profile": "_Deleted Profile XYZ", "docstatus": 1, "status": "Open"},
		)
		frappe.set_user(self.cashier)
		try:
			data = check_opening_shift()
			self.assertIsNotNone(data)
			self.assertEqual(data["pos_opening_shift"].name, older.name)
		finally:
			frappe.set_user(ADMIN)
			self._remove_shift(older)
			self._remove_shift(newer)

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

	# ── single open shift per profile (doctype before_submit guard) ──────────

	def test_create_opening_shift_blocked_while_profile_in_use(self):
		# one POS Profile = one cash drawer: a second member must be refused
		# while another shift holds the profile (regression: two openings on
		# one drawer, each counting the same physical cash)
		first = create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
		self.addCleanup(self._remove_shift, first["pos_opening_shift"])
		frappe.set_user(self.cashier2)
		try:
			with self.assertRaises(frappe.ValidationError):
				create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(
			frappe.db.count(
				"POS Opening Shift",
				{"pos_profile": self.profile.name, "docstatus": 1, "status": "Open"},
			),
			1,
		)

	def test_create_opening_shift_allowed_after_previous_closed(self):
		first = create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
		self.addCleanup(self._remove_shift, first["pos_opening_shift"])
		submit_closing_shift(json.dumps({"pos_opening_shift": first["pos_opening_shift"].name}))
		# the drawer is free again: a second member may open it
		frappe.set_user(self.cashier2)
		try:
			second = create_opening_shift(
				self.profile.name, self.profile.company, self._balance_details()
			)
			self.addCleanup(self._remove_shift, second["pos_opening_shift"])
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(second["pos_opening_shift"].get("user"), self.cashier2)

	def test_create_opening_shift_manager_bypasses_profile_guard(self):
		# deliberate exemption: whoever may cancel a POS Closing Shift (the
		# manager-only right; cashiers hold submit, never cancel) may open a
		# second shift over an in-use profile — a manager taking the register
		# while the previous shift waits for its close
		first = create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
		self.addCleanup(self._remove_shift, first["pos_opening_shift"])
		frappe.get_doc("User", self.cashier2).add_roles("POSNext Manager")
		self.addCleanup(self._revoke_role, self.cashier2, "POSNext Manager")
		frappe.set_user(self.cashier2)
		try:
			second = create_opening_shift(
				self.profile.name, self.profile.company, self._balance_details()
			)
			self.addCleanup(self._remove_shift, second["pos_opening_shift"])
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(second["pos_opening_shift"].get("user"), self.cashier2)
		self.assertEqual(
			frappe.db.count(
				"POS Opening Shift",
				{"pos_profile": self.profile.name, "docstatus": 1, "status": "Open"},
			),
			2,
		)

	@staticmethod
	def _revoke_role(user, role):
		frappe.db.delete("Has Role", {"parent": user, "parenttype": "User", "role": role})
		frappe.clear_cache(user=user)

	def test_create_opening_shift_duplicate_is_serialized(self):
		# sequential race (same lock path as concurrent calls): the second
		# create must hit the open-shift check and be rejected — never two
		# open shifts for one (profile, user)
		frappe.set_user(self.cashier)
		try:
			first = create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
			# per-method cleanup (same reason as _open_shift): the leftover
			# open shift would trip the single-open-shift guard in later tests
			self.addCleanup(self._remove_shift, first["pos_opening_shift"])
			self.assertEqual(first["pos_opening_shift"].get("user"), self.cashier)
			with self.assertRaises(frappe.ValidationError):
				create_opening_shift(self.profile.name, self.profile.company, self._balance_details())
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(
			frappe.db.count("POS Opening Shift", {"user": self.cashier, "docstatus": 1}), 1
		)


class TestShiftManagerGates(FrappeTestCase):
	"""Management-only gates added after the doctype-permission fallbacks.

	- get_closing_shift_data / get_session_summary / get_shift_dashboard:
	  owner-or-management. A same-profile colleague (holding the cashier's
	  POS Opening/Closing Shift read) used to pass the removed
	  `frappe.has_permission(..., doc=...)` fallback; now only the owner and
	  management do.
	- get_period_summary / get_period_dashboard: management-only, on top of
	  the membership gate. The manager fixture is deliberately NOT a POS
	  Profile User: `shifts._check_profile_access` carries its own management
	  bypass, so a manager outside the profile passes both gates.

	Each test opens its own shift through create_opening_shift (the real API
	path) and registers a per-test cleanup — only one open shift may hold a
	profile, so leftovers would break the next test.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		for role in ("POSNext Manager", "POSNext Cashier"):
			if not frappe.db.exists("Role", role):
				raise unittest.SkipTest(f"{role} role does not exist on this site")
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

		cls.cashier_a = f"shift-gate.{uuid.uuid4().hex[:8]}@example.com"
		cls.cashier_b = f"shift-gate.{uuid.uuid4().hex[:8]}@example.com"
		cls.manager = f"shift-gate.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.cashier_a, cls.cashier_b, cls.manager):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Shift Gate Tester"}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", cls.manager).add_roles("POSNext Manager")
		cls.addClassCleanup(cls._revoke_role, cls.manager, "POSNext Manager")
		# the cashiers hold the daily persona role, which DOES grant POS
		# Opening/Closing Shift read: that doctype permission used to be the
		# removed fallback, so its presence is the whole point of the probe
		for email in (cls.cashier_a, cls.cashier_b):
			frappe.get_doc("User", email).add_roles("POSNext Cashier")
			cls.addClassCleanup(cls._revoke_role, email, "POSNext Cashier")
		# only the cashiers are profile members; the manager stays OUT of the
		# profile — shifts._check_profile_access has its own management
		# bypass, and the period tests prove a non-member manager still gets
		# through both gates
		for email in (cls.cashier_a, cls.cashier_b):
			frappe.get_doc(
				{
					"doctype": "POS Profile User",
					"parent": cls.profile.name,
					"parenttype": "POS Profile",
					"parentfield": "applicable_for_users",
					"user": email,
				}
			).insert(ignore_permissions=True)
			frappe.clear_cache(user=email)

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

		for email in (cls.cashier_a, cls.cashier_b, cls.manager):
			# sweep any open shift a failed test left behind (invoice → closing
			# → opening → profile users → users)
			for name in frappe.get_all(
				"POS Opening Shift", {"user": email, "docstatus": ["<", 2]}, pluck="name"
			):
				_safe(
					lambda n=name: frappe.db.set_value(
						"POS Opening Shift", n, "pos_profile", cls.profile.name
					)
				)

				def _purge(n):
					doc = frappe.get_doc("POS Opening Shift", n)
					doc.flags.ignore_permissions = True
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc("POS Opening Shift", n, force=1, ignore_permissions=True)

				_safe(lambda n=name: _purge(n))
			_safe(lambda e=email: frappe.db.delete("Error Log", {"owner": e}))
			_safe(lambda e=email: frappe.db.delete("POS Profile User", {"user": e}))
			_safe(lambda e=email: frappe.delete_doc("User", e, force=1, ignore_permissions=True))
		frappe.db.commit()
		super().tearDownClass()

	@staticmethod
	def _revoke_role(user, role):
		frappe.db.delete("Has Role", {"parent": user, "parenttype": "User", "role": role})
		frappe.clear_cache(user=user)

	def _balance_details(self):
		return json.dumps([{"mode_of_payment": self.mode[0], "opening_amount": OPENING_CASH}])

	def _open_shift_as(self, user):
		"""Open a shift through the API as `user` (owner = session user)."""
		frappe.set_user(user)
		try:
			data = create_opening_shift(
				self.profile.name, self.profile.company, self._balance_details()
			)
		finally:
			frappe.set_user(ADMIN)
		shift = data["pos_opening_shift"]
		self.addCleanup(self._remove_shift, shift)
		return shift

	def _remove_shift(self, shift):
		# tolerant cleanup: one leftover must never abort the remaining cleanup;
		# the profile link may be db-spoofed by a test, restore before cancel
		name = shift.get("name")
		try:
			frappe.db.set_value("POS Opening Shift", name, "pos_profile", self.profile.name)
			doc = frappe.get_doc("POS Opening Shift", name)
			doc.flags.ignore_permissions = True
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("POS Opening Shift", name, force=1, ignore_permissions=True)
		except Exception:
			pass

	# ── period reports: membership AND management ────────────────────────────

	def test_period_summary_rejects_profile_cashier_allows_manager(self):
		from_date = to_date = nowdate()

		frappe.set_user(self.cashier_a)
		try:
			# premise: membership alone passed the pre-existing profile gate —
			# the refusal comes from the new management gate
			self.assertTrue(
				frappe.db.exists(
					"POS Profile User",
					{"parent": self.profile.name, "user": self.cashier_a},
				)
			)
			with self.assertRaises(frappe.PermissionError):
				get_period_summary(self.profile.name, from_date, to_date)
		finally:
			frappe.set_user(ADMIN)

		# ...and management is a pure bypass: the manager is NOT a member of
		# the profile (see setUpClass), yet passes both gates
		self.assertFalse(
			frappe.db.exists(
				"POS Profile User",
				{"parent": self.profile.name, "user": self.manager},
			)
		)
		frappe.set_user(self.manager)
		try:
			summary = get_period_summary(self.profile.name, from_date, to_date)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(summary["pos_profile"], self.profile.name)

	def test_period_dashboard_rejects_profile_cashier_allows_manager(self):
		from_date = to_date = nowdate()

		frappe.set_user(self.cashier_a)
		try:
			self.assertTrue(
				frappe.db.exists(
					"POS Profile User",
					{"parent": self.profile.name, "user": self.cashier_a},
				)
			)
			with self.assertRaises(frappe.PermissionError):
				get_period_dashboard(self.profile.name, from_date, to_date)
		finally:
			frappe.set_user(ADMIN)

		self.assertFalse(
			frappe.db.exists(
				"POS Profile User",
				{"parent": self.profile.name, "user": self.manager},
			)
		)
		frappe.set_user(self.manager)
		try:
			dashboard = get_period_dashboard(self.profile.name, from_date, to_date)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(dashboard["pos_profile"], self.profile.name)

	def test_non_member_manager_passes_period_reports(self):
		# dedicated probe for the shifts._check_profile_access management
		# bypass: a manager with no POS Profile User row at all
		self.assertFalse(
			frappe.db.exists(
				"POS Profile User",
				{"parent": self.profile.name, "user": self.manager},
			)
		)
		frappe.set_user(self.manager)
		try:
			summary = get_period_summary(self.profile.name, nowdate(), nowdate())
			dashboard = get_period_dashboard(self.profile.name, nowdate(), nowdate())
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(summary["pos_profile"], self.profile.name)
		self.assertEqual(dashboard["pos_profile"], self.profile.name)

	# ── owner-or-management read gates ───────────────────────────────────────

	def test_closing_shift_data_rejects_non_owner_allows_manager(self):
		shift = self._open_shift_as(self.cashier_a)
		name = shift.get("name")

		frappe.set_user(self.cashier_b)
		try:
			# premise: the removed doc-level fallback accepted exactly this
			# doctype read (a same-profile cashier colleague)
			self.assertTrue(frappe.has_permission("POS Opening Shift", "read"))
			with self.assertRaises(frappe.PermissionError):
				get_closing_shift_data(name)
		finally:
			frappe.set_user(ADMIN)

		frappe.set_user(self.manager)
		try:
			data = get_closing_shift_data(name)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(data["pos_opening_shift"], name)

	def test_session_summary_rejects_non_owner_allows_manager(self):
		shift = self._open_shift_as(self.cashier_a)
		name = shift.get("name")

		frappe.set_user(self.cashier_b)
		try:
			self.assertTrue(frappe.has_permission("POS Opening Shift", "read"))
			with self.assertRaises(frappe.PermissionError):
				get_session_summary(name)
		finally:
			frappe.set_user(ADMIN)

		frappe.set_user(self.manager)
		try:
			summary = get_session_summary(name)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(summary["opening_shift"], name)

	def test_shift_dashboard_rejects_non_owner_allows_manager(self):
		shift = self._open_shift_as(self.cashier_a)
		name = shift.get("name")

		frappe.set_user(self.cashier_b)
		try:
			self.assertTrue(frappe.has_permission("POS Opening Shift", "read"))
			with self.assertRaises(frappe.PermissionError):
				get_shift_dashboard(name)
		finally:
			frappe.set_user(ADMIN)

		frappe.set_user(self.manager)
		try:
			dashboard = get_shift_dashboard(name)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(dashboard["opening_shift"], name)

	def test_owner_can_view_own_session_summary_and_dashboard(self):
		shift = self._open_shift_as(self.cashier_a)
		name = shift.get("name")

		frappe.set_user(self.cashier_a)
		try:
			summary = get_session_summary(name)
			dashboard = get_shift_dashboard(name)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(summary["opening_shift"], name)
		self.assertEqual(dashboard["opening_shift"], name)

	# ── double-shift guard: manager bypass via is_management_user ────────────

	def test_manager_bypasses_double_shift_guard(self):
		# cashier_a holds the profile; a second member would be refused by the
		# doctype guard, but a manager may deliberately open over an in-use
		# profile (the refactored is_management_user() bypass)
		first = self._open_shift_as(self.cashier_a)
		second = self._open_shift_as(self.manager)

		self.assertEqual(second.get("user"), self.manager)
		for shift in (first, second):
			meta = frappe.db.get_value(
				"POS Opening Shift", shift.get("name"), ["docstatus", "status"], as_dict=True
			)
			self.assertEqual(meta.docstatus, 1)
			self.assertEqual(meta.status, "Open")
