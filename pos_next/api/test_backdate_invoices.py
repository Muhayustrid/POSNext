# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Backend tests for the HO backdate entry lane.

Two layers, following the app's existing styles:
- mocked-frappe unit tests for the posting-date policy and the endpoint
  gates (same style as pos_next/test_shift_schedule.py);
- full-cycle integration tests through the real invoice/closing pipeline
  on the shared dev site (same style as api/test_pos_invoice_submit.py and
  api/test_purchase_orders.py).

Run via
pos_next/_pn_run_tests.py pos_next.api.test_backdate_invoices
"""

import json
import unittest
import uuid
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, cint, flt, getdate, now_datetime, nowdate

from pos_next.api.backdate_invoices import (
	allow_change_posting_date,
	get_access,
	get_backdate_context,
	reopen_shift,
	submit_backdate_invoice,
)
from pos_next.api.invoices import (
	_enforce_posting_date_policy,
	_posting_date_shifted,
	prepare_return_invoice,
	submit_invoice,
	validate_return_items,
)
from pos_next.api.shifts import get_closing_shift_data, submit_closing_shift
from pos_next.invoice_type import get_pos_invoice_doctype

ADMIN = "Administrator"

# Schedule-safe profile: opening a shift must never throw for being outside a
# scheduled window (see api/test_pos_invoice_submit.py for the rationale).
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]


def _throw(*args, **kwargs):
	raise RuntimeError(args[0] if args else None)


def _doc(**values):
	doc = Mock()
	doc.get = lambda field: values.get(field)
	return doc


# ---------------------------------------------------------------------------
# Mocked unit tests — posting-date policy
# ---------------------------------------------------------------------------


class TestPostingDateShifted(unittest.TestCase):
	def test_today_without_override_is_not_shifted(self):
		self.assertFalse(_posting_date_shifted(_doc(posting_date=nowdate())))

	def test_past_date_is_shifted(self):
		self.assertTrue(_posting_date_shifted(_doc(posting_date=add_days(nowdate(), -1))))

	def test_set_posting_time_alone_is_shifted(self):
		self.assertTrue(_posting_date_shifted(_doc(posting_date=nowdate(), set_posting_time=1)))

	def test_missing_date_is_not_shifted(self):
		self.assertFalse(_posting_date_shifted(_doc()))

	def test_unparseable_date_fails_closed(self):
		self.assertTrue(_posting_date_shifted(_doc(posting_date="not-a-date")))


class TestEnforcePostingDatePolicy(unittest.TestCase):
	BACKDATED = {"posting_date": "2020-01-01", "set_posting_time": 1}

	def test_unshifted_passes_without_access_checks(self):
		role = Mock()
		with patch("pos_next.api.backdate_invoices.has_backdate_role", role):
			_enforce_posting_date_policy(_doc(posting_date=nowdate()))
		role.assert_not_called()

	def test_backdate_marker_passes(self):
		frappe.flags.pos_next_backdate_entry = True
		try:
			_enforce_posting_date_policy(_doc(**self.BACKDATED))  # must not raise
		finally:
			frappe.flags.pos_next_backdate_entry = None

	def test_role_and_setting_pass(self):
		with (
			patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=True),
			patch("pos_next.api.backdate_invoices.allow_change_posting_date", return_value=True),
		):
			_enforce_posting_date_policy(_doc(**self.BACKDATED, pos_profile="Profile 1"))  # must not raise

	def test_missing_role_rejected(self):
		with (
			patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=False),
			patch("pos_next.api.backdate_invoices.allow_change_posting_date", return_value=True),
			patch("pos_next.api.invoices.frappe.throw", side_effect=_throw),
		):
			with self.assertRaises(RuntimeError):
				_enforce_posting_date_policy(_doc(**self.BACKDATED, pos_profile="Profile 1"))

	def test_setting_off_rejected(self):
		with (
			patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=True),
			patch("pos_next.api.backdate_invoices.allow_change_posting_date", return_value=False),
			patch("pos_next.api.invoices.frappe.throw", side_effect=_throw),
		):
			with self.assertRaises(RuntimeError):
				_enforce_posting_date_policy(_doc(**self.BACKDATED, pos_profile="Profile 1"))


# ---------------------------------------------------------------------------
# Mocked unit tests — access gate and endpoints
# ---------------------------------------------------------------------------


class TestAllowChangePostingDate(unittest.TestCase):
	def test_reads_the_profiles_enabled_settings_row(self):
		get_value = Mock(return_value=1)
		with patch("pos_next.api.backdate_invoices.frappe.db.get_value", get_value):
			self.assertTrue(allow_change_posting_date("Profile 1"))
		filters = get_value.call_args[0][1]
		self.assertEqual(filters, {"enabled": 1, "pos_profile": "Profile 1"})
		self.assertEqual(get_value.call_args[0][2], "allow_change_posting_date")

	def test_off_value_or_missing_profile_is_false(self):
		with patch("pos_next.api.backdate_invoices.frappe.db.get_value", return_value=0):
			self.assertFalse(allow_change_posting_date("Profile 1"))
		self.assertFalse(allow_change_posting_date(None))


class TestGetAccess(unittest.TestCase):
	def test_without_profile_only_the_role_is_judged(self):
		with patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=True):
			self.assertEqual(get_access(), {"roles_ok": True, "setting_on": None, "allowed": True})
		with patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=False):
			self.assertEqual(get_access(), {"roles_ok": False, "setting_on": None, "allowed": False})

	def test_with_profile_role_and_setting_are_both_required(self):
		for roles_ok, setting_on, allowed in ((True, True, True), (True, False, False), (False, True, False)):
			with (
				patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=roles_ok),
				patch("pos_next.api.backdate_invoices.allow_change_posting_date", return_value=setting_on),
			):
				self.assertEqual(get_access("Profile 1")["allowed"], allowed)


class TestSubmitBackdateInvoiceGates(unittest.TestCase):
	OPEN_SHIFT = frappe._dict(
		{
			"name": "POSA-OS-0001",
			"pos_profile": "Profile 1",
			"docstatus": 1,
			"status": "Open",
			"period_start_date": "2020-01-01 08:00:00",
			"posting_date": "2020-01-01",
		}
	)

	def _run(self, invoice, shift=OPEN_SHIFT, roles_ok=True, setting_on=True):
		seen = {}

		def fake_submit_invoice(**kwargs):
			seen["invoice"] = json.loads(kwargs["invoice"])
			seen["marker"] = bool(getattr(frappe.flags, "pos_next_backdate_entry", None))
			return {"name": "NEW-INV-0001"}

		with (
			patch("pos_next.api.backdate_invoices.frappe.db.get_value", return_value=shift),
			patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=roles_ok),
			patch("pos_next.api.backdate_invoices.allow_change_posting_date", return_value=setting_on),
			patch("pos_next.api.invoices.submit_invoice", side_effect=fake_submit_invoice),
		):
			result = submit_backdate_invoice(invoice=invoice)
		return result, seen

	def test_happy_path_sets_server_side_authority(self):
		result, seen = self._run({"posa_pos_opening_shift": "POSA-OS-0001", "posting_date": "2020-01-05", "pos_profile": "tampered"})
		self.assertEqual(result, {"name": "NEW-INV-0001"})
		# profile, posting date and set_posting_time come from the server, and
		# the marker was live while the invoice API ran
		self.assertEqual(seen["invoice"]["pos_profile"], "Profile 1")
		self.assertEqual(seen["invoice"]["posting_date"], "2020-01-05")
		self.assertEqual(seen["invoice"]["set_posting_time"], 1)
		self.assertTrue(seen["marker"])
		self.assertFalse(getattr(frappe.flags, "pos_next_backdate_entry", None))

	def test_without_role_rejected(self):
		with self.assertRaises(frappe.PermissionError):
			self._run({"posa_pos_opening_shift": "POSA-OS-0001", "posting_date": "2020-01-05"}, roles_ok=False)

	def test_setting_off_rejected(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._run({"posa_pos_opening_shift": "POSA-OS-0001", "posting_date": "2020-01-05"}, setting_on=False)
		self.assertIn("disabled", str(ctx.exception))

	def test_shift_must_be_open(self):
		shift = frappe._dict(self.OPEN_SHIFT, status="Closed")
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._run({"posa_pos_opening_shift": "POSA-OS-0001", "posting_date": "2020-01-05"}, shift=shift)
		self.assertIn("must be open", str(ctx.exception))

	def test_posting_date_before_shift_period_rejected(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._run({"posa_pos_opening_shift": "POSA-OS-0001", "posting_date": "2019-12-25"})
		self.assertIn("shift period", str(ctx.exception))

	def test_future_posting_date_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._run({"posa_pos_opening_shift": "POSA-OS-0001", "posting_date": add_days(nowdate(), 1)})

	def test_missing_posting_date_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._run({"posa_pos_opening_shift": "POSA-OS-0001"})


class TestReopenShift(unittest.TestCase):
	CLOSED_SHIFT = frappe._dict(
		{"pos_profile": "Profile 1", "docstatus": 1, "status": "Closed", "pos_closing_shift": "CS-0001"}
	)

	def _run(self, shift_row, closing_docstatus=1, roles_ok=True, setting_on=True):
		cancel = Mock()
		get_values = [shift_row, closing_docstatus, "Open"]

		def get_value(*args, **kwargs):
			return get_values.pop(0)

		with (
			patch("pos_next.api.backdate_invoices.frappe.db.get_value", side_effect=get_value),
			patch("pos_next.api.backdate_invoices.has_backdate_role", return_value=roles_ok),
			patch("pos_next.api.backdate_invoices.allow_change_posting_date", return_value=setting_on),
			patch("pos_next.api.backdate_invoices.frappe.get_doc", return_value=Mock(cancel=cancel)),
		):
			result = reopen_shift("POSA-OS-0001")
		return result, cancel

	def test_cancels_the_closing_and_returns_open_status(self):
		result, cancel = self._run(self.CLOSED_SHIFT)
		cancel.assert_called_once_with()
		self.assertEqual(
			result,
			{
				"pos_opening_shift": "POSA-OS-0001",
				"cancelled_closing_shift": "CS-0001",
				"status": "Open",
			},
		)

	def test_open_shift_rejected(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._run(frappe._dict(self.CLOSED_SHIFT, status="Open"))
		self.assertIn("not closed", str(ctx.exception))

	def test_missing_shift_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._run(None)

	def test_without_role_rejected_without_cancelling(self):
		with self.assertRaises(frappe.PermissionError):
			self._run(self.CLOSED_SHIFT, roles_ok=False)

	def test_setting_off_rejected_without_cancelling(self):
		with self.assertRaises(frappe.ValidationError):
			self._run(self.CLOSED_SHIFT, setting_on=False)

	def test_cancelled_closing_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._run(self.CLOSED_SHIFT, closing_docstatus=2)


# ---------------------------------------------------------------------------
# Integration tests — real pipeline on the shared dev site
# ---------------------------------------------------------------------------


class TestBackdateEntryFlow(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.profile = frappe.db.get_value(
			"POS Profile", _PROFILE_FILTER, ["name", "company", "warehouse"], as_dict=True
		)
		if not cls.profile:
			raise unittest.SkipTest("no schedule-safe POS Profile")
		item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
			pluck="name",
			limit=1,
		)
		cls.customer = frappe.db.get_value("Customer", {"is_internal_customer": 0}, "name")
		cls.mode = frappe.get_all(
			"POS Payment Method",
			{"parent": cls.profile.name, "parenttype": "POS Profile"},
			pluck="mode_of_payment",
			limit=1,
		)
		if not (item and cls.customer and cls.mode):
			raise unittest.SkipTest("no stock sales item / customer / payment method")
		cls.item = item[0]
		cls.user = f"backdate.{cls._uniq()}@example.com"
		frappe.get_doc({"doctype": "User", "email": cls.user, "first_name": "Backdate Tester"}).insert()
		cls._ensure_fiscal_year()
		cls._ensure_settings()

	@staticmethod
	def _uniq():
		return uuid.uuid4().hex[:8]

	@classmethod
	def _ensure_fiscal_year(cls):
		"""Backdated invoices post up to a few days before today — the fiscal
		year must cover that window (same guard as test_purchase_orders)."""
		cls.fiscal_year = None
		# GC any leftover from a run that died between create and teardown
		for name in frappe.get_all(
			"Fiscal Year", filters={"year": ("like", "POS Backdate Test FY%")}, pluck="name"
		):
			frappe.delete_doc("Fiscal Year", name, force=1)
		covering = frappe.get_all(
			"Fiscal Year",
			filters={
				"disabled": 0,
				"year_start_date": ["<=", add_days(nowdate(), -10)],
				"year_end_date": [">=", nowdate()],
			},
			pluck="name",
		)
		if not covering or not frappe.get_all(
			"Fiscal Year Company",
			filters={"company": cls.profile.company, "parent": ["in", covering]},
			pluck="parent",
		):
			year = getdate(nowdate()).year
			cls.fiscal_year = (
				frappe.get_doc(
					{
						"doctype": "Fiscal Year",
						"year": f"POS Backdate Test FY {cls._uniq()}",
						"year_start_date": f"{year}-01-01",
						"year_end_date": f"{year}-12-31",
						"companies": [{"company": cls.profile.company}],
					}
				)
				.insert()
				.name
			)

	@classmethod
	def _ensure_settings(cls):
		name = frappe.db.get_value("POS Settings", {"pos_profile": cls.profile.name}, "name")
		cls.created_settings = False
		if name:
			cls.orig_settings = frappe.db.get_value(
				"POS Settings",
				name,
				["enabled", "allow_change_posting_date", "return_validity_days", "require_refund_code"],
				as_dict=True,
			)
		else:
			name = (
				frappe.get_doc(
					{"doctype": "POS Settings", "pos_profile": cls.profile.name, "enabled": 1}
				)
				.insert(ignore_permissions=True)
				.name
			)
			cls.created_settings = True
		cls.settings_name = name

	@classmethod
	def _restore_settings(cls):
		"""Restore the profile's POS Settings row: its captured originals, or
		the doctype defaults when this class created the row."""
		orig = getattr(cls, "orig_settings", None)
		frappe.db.set_value(
			"POS Settings",
			cls.settings_name,
			{
				"enabled": getattr(orig, "enabled", None) if orig else 1,
				"allow_change_posting_date": getattr(orig, "allow_change_posting_date", None)
				if orig
				else 0,
				"return_validity_days": getattr(orig, "return_validity_days", None) if orig else 0,
				"require_refund_code": getattr(orig, "require_refund_code", None) if orig else 1,
			},
		)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			# tolerant teardown (see test_purchase_orders): one leftover must
			# never abort the remaining cleanup on this shared dev site
			try:
				step()
			except Exception:
				pass

		if getattr(cls, "created_settings", False):
			_safe(lambda: frappe.delete_doc("POS Settings", cls.settings_name, force=1))
		else:
			_safe(cls._restore_settings)
		if getattr(cls, "fiscal_year", None):
			_safe(lambda: frappe.delete_doc("Fiscal Year", cls.fiscal_year, force=1))
		user = getattr(cls, "user", None)
		if user:
			# the gate tests log errors as the plain user; owned Error Logs
			# block User deletion, so clear them first
			_safe(lambda: frappe.db.delete("Error Log", {"owner": user}))
			_safe(lambda: frappe.delete_doc("User", user, force=1))
		# commit BEFORE the class cleanups run: FrappeTestCase registers a
		# db.rollback() that executes after tearDownClass and would otherwise
		# undo this whole cleanup
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.doctype = get_pos_invoice_doctype()
		self.stock_entry = None
		self.shift = None
		# Backdated deliveries post BEFORE today's test receipt (a Stock Entry
		# cannot be dated in the past on this bench), so the ledger recompute
		# would trip the site's negative-stock guard — relax it for the test
		# and restore in tearDown.
		self.orig_allow_negative = cint(
			frappe.db.get_single_value("Stock Settings", "allow_negative_stock")
		)
		if not self.orig_allow_negative:
			frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"purpose": "Material Receipt",
				"company": self.profile.company,
				"items": [
					{
						"item_code": self.item,
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
		self.stock_entry = se

		# backdate lane on for every test; narrow tests flip it off explicitly.
		# require_refund_code off so returns need no HO discount code (the
		# code gate itself is covered by the discount_code suites).
		frappe.db.set_value(
			"POS Settings",
			self.settings_name,
			{
				"enabled": 1,
				"allow_change_posting_date": 1,
				"return_validity_days": 0,
				"require_refund_code": 0,
			},
		)

		# a shift opened days ago: that is the shift HO reopens to fix
		self.shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": ADMIN,
				"posting_date": add_days(nowdate(), -3),
				"period_start_date": add_days(now_datetime(), -3),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
			}
		).insert(ignore_permissions=True)
		self.shift.reload()
		self.shift.submit()

	def tearDown(self):
		frappe.set_user(ADMIN)
		if not getattr(self, "orig_allow_negative", 1):
			frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)
		# closings first: a submitted closing's child links would block the
		# invoice cancels below (on_cancel clears them)
		for name in frappe.get_all(
			"POS Closing Shift", {"pos_opening_shift": self.shift.name}, pluck="name"
		):
			doc = frappe.get_doc("POS Closing Shift", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("POS Closing Shift", name, force=1, ignore_permissions=True)
		for doctype in ("POS Invoice", "Sales Invoice"):
			for name in frappe.get_all(
				doctype, {"posa_pos_opening_shift": self.shift.name}, pluck="name"
			):
				doc = frappe.get_doc(doctype, name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
		if self.stock_entry and frappe.db.exists("Stock Entry", self.stock_entry.name):
			se = frappe.get_doc("Stock Entry", self.stock_entry.name)
			if se.docstatus == 1:
				se.cancel()
			frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)
		if self.shift:
			# staleness-proof teardown (pos_next.test_invoice_type)
			frappe.db.set_value(
				"POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
			)
			frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
		# per-test restore too: the class-level restore can be rolled back by
		# FrappeTestCase's class cleanup when a run dies mid-suite
		if not getattr(self, "created_settings", False):
			self._restore_settings()
		frappe.db.commit()

	def _payload(self, **overrides):
		payload = {
			"pos_profile": self.profile.name,
			"posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"items": [{"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}],
			"payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
		}
		payload.update(overrides)
		return payload

	def test_submit_invoice_rejects_backdated_posting_date_when_disabled(self):
		frappe.db.set_value("POS Settings", self.settings_name, {"allow_change_posting_date": 0})
		with self.assertRaises(frappe.PermissionError) as ctx:
			submit_invoice(invoice=self._payload(posting_date=add_days(nowdate(), -1)))
		self.assertIn("posting date", str(ctx.exception))

	def test_submit_invoice_rejects_backdated_posting_date_without_role(self):
		frappe.set_user(self.user)
		try:
			with self.assertRaises(frappe.PermissionError) as ctx:
				submit_invoice(invoice=self._payload(posting_date=add_days(nowdate(), -1)))
			self.assertIn("posting date", str(ctx.exception))
		finally:
			frappe.set_user(ADMIN)

	def test_endpoint_rejects_when_disabled_or_without_role(self):
		frappe.db.set_value("POS Settings", self.settings_name, {"allow_change_posting_date": 0})
		with self.assertRaises(frappe.ValidationError) as ctx:
			submit_backdate_invoice(invoice=self._payload(posting_date=add_days(nowdate(), -1)))
		self.assertIn("disabled", str(ctx.exception))

		frappe.db.set_value("POS Settings", self.settings_name, {"allow_change_posting_date": 1})
		frappe.set_user(self.user)
		try:
			with self.assertRaises(frappe.PermissionError):
				submit_backdate_invoice(invoice=self._payload(posting_date=add_days(nowdate(), -1)))
		finally:
			frappe.set_user(ADMIN)

	def test_endpoint_enforces_the_shift_period_window(self):
		for bad_date in (add_days(nowdate(), -10), add_days(nowdate(), 1)):
			with self.assertRaises(frappe.ValidationError) as ctx:
				submit_backdate_invoice(invoice=self._payload(posting_date=bad_date))
			self.assertIn("shift period", str(ctx.exception))

	def test_reopen_backdate_reclose_flow(self):
		# 1. the sale that actually happened on the shift
		first = submit_invoice(invoice=self._payload())
		self.assertTrue(first.get("name"))

		# 2. close the shift (regular flow)
		closing1 = get_closing_shift_data(self.shift.name)
		closing1_name = submit_closing_shift(json.dumps(closing1))["name"]
		self.assertEqual(frappe.db.get_value("POS Opening Shift", self.shift.name, "status"), "Closed")
		if frappe.db.has_column(self.doctype, "pos_closing_entry"):
			self.assertEqual(
				frappe.db.get_value(self.doctype, first["name"], "pos_closing_entry"), closing1_name
			)

		# 3. reopen: closing cancelled, invoice links cleared, status Open
		result = reopen_shift(self.shift.name)
		self.assertEqual(result["cancelled_closing_shift"], closing1_name)
		self.assertEqual(result["status"], "Open")
		self.assertEqual(frappe.db.get_value("POS Closing Shift", closing1_name, "docstatus"), 2)
		if frappe.db.has_column(self.doctype, "pos_closing_entry"):
			self.assertFalse(frappe.db.get_value(self.doctype, first["name"], "pos_closing_entry"))

		# 4. the HO backdated entry, posted on the original date
		back = submit_backdate_invoice(invoice=self._payload(posting_date=add_days(nowdate(), -1)))
		doc = frappe.get_doc(self.doctype, back["name"])
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(str(doc.posting_date), add_days(nowdate(), -1))
		self.assertEqual(cint(doc.set_posting_time), 1)
		self.assertEqual(doc.posa_pos_opening_shift, self.shift.name)

		# 5. re-close: the Z-report recomputes with the backdated invoice in
		closing2 = get_closing_shift_data(self.shift.name)
		self.assertEqual(closing2["sales_count"], 2)
		self.assertGreater(flt(closing2["grand_total"]), flt(closing1["grand_total"]))
		submit_closing_shift(json.dumps(closing2))
		self.assertEqual(frappe.db.get_value("POS Opening Shift", self.shift.name, "status"), "Closed")

	def test_reopen_shift_gates(self):
		closing = get_closing_shift_data(self.shift.name)
		submit_closing_shift(json.dumps(closing))

		# the picker context lists this closed shift with its submitted closing
		context = get_backdate_context()
		row = next(r for r in context["shifts"] if r["name"] == self.shift.name)
		self.assertEqual(row["closing_docstatus"], 1)
		self.assertTrue(row["setting_on"])

		frappe.db.set_value("POS Settings", self.settings_name, {"allow_change_posting_date": 0})
		with self.assertRaises(frappe.ValidationError) as ctx:
			reopen_shift(self.shift.name)
		self.assertIn("disabled", str(ctx.exception))

		frappe.db.set_value("POS Settings", self.settings_name, {"allow_change_posting_date": 1})
		frappe.set_user(self.user)
		try:
			with self.assertRaises(frappe.PermissionError):
				reopen_shift(self.shift.name)
			with self.assertRaises(frappe.PermissionError):
				get_backdate_context()
		finally:
			frappe.set_user(ADMIN)

	def test_backdate_return_with_expired_validity(self):
		frappe.db.set_value("POS Settings", self.settings_name, {"return_validity_days": 1})
		# the original sale is itself a backdated entry three days old
		original = submit_backdate_invoice(invoice=self._payload(posting_date=add_days(nowdate(), -3)))

		# normal lane: the return window has expired for a plain cashier
		frappe.set_user(self.user)
		try:
			blocked = validate_return_items(
				original["name"], [{"item_code": self.item, "qty": -1}], doctype=self.doctype
			)
		finally:
			frappe.set_user(ADMIN)
		self.assertFalse(blocked["valid"])
		self.assertIn("Return period has expired", blocked["message"])

		# HO lane: preparing and submitting the backdated return still works
		prepared = prepare_return_invoice(original["name"], pos_opening_shift=self.shift.name)
		self.assertTrue(prepared["items"])

		result = submit_backdate_invoice(
			invoice=self._payload(
				is_return=1,
				return_against=original["name"],
				posting_date=add_days(nowdate(), -2),
				items=[
					{"item_code": self.item, "qty": -1, "rate": 100, "warehouse": self.profile.warehouse}
				],
				payments=[{"mode_of_payment": self.mode[0], "amount": -100}],
			)
		)
		doc = frappe.get_doc(self.doctype, result["name"])
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(cint(doc.is_return), 1)
		self.assertEqual(str(doc.posting_date), add_days(nowdate(), -2))
		self.assertEqual(doc.return_against, original["name"])


if __name__ == "__main__":
	unittest.main()
