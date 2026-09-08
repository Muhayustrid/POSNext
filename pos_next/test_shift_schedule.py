# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for shift schedule enforcement.

Mocked-frappe style — run via
pos_next/_pn_run_tests.py pos_next.test_shift_schedule
"""

import datetime
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe

from pos_next.shift_schedule import (
	_as_time_delta,
	_gate_shifts,
	assert_invoice_sales_allowed,
	assert_sales_allowed,
	apply_schedule_snapshot,
	extend_deadline,
	freeze_schedule_snapshot,
	get_shift_gate,
	resolve_window,
	validate_group_membership,
	validate_invoice,
	validate_profile_schedule,
	validate_schedule_values,
)

WINDOW = {
	"pos_schedule_enabled": 1,
	"pos_schedule_start": "09:00:00",
	"pos_schedule_end": "22:00:00",
	"pos_schedule_warning_minutes": 0,
	"pos_schedule_enforce_closing": 1,
}
OVERNIGHT = {**WINDOW, "pos_schedule_start": "21:00:00", "pos_schedule_end": "05:00:00"}
GATE_ROW = {
	"pos_schedule_enabled": 1,
	"pos_schedule_enforce_closing": 1,
	"pos_schedule_deadline": "2026-09-07 22:00:00",
	"docstatus": 1,
}


def _dt(*args):
	return datetime.datetime(*args)


def _throw(*args, **kwargs):
	raise RuntimeError(args[0] if args else None)


class TestResolveWindow(unittest.TestCase):
	def test_same_day_window(self):
		window = resolve_window(_dt(2026, 9, 7, 12, 0), "09:00:00", "22:00:00")
		self.assertEqual(window, (_dt(2026, 9, 7, 9, 0), _dt(2026, 9, 7, 22, 0)))

	def test_same_day_window_closed(self):
		self.assertIsNone(resolve_window(_dt(2026, 9, 7, 23, 0), "09:00:00", "22:00:00"))
		self.assertIsNone(resolve_window(_dt(2026, 9, 7, 8, 0), "09:00:00", "22:00:00"))

	def test_overnight_evening_open(self):
		window = resolve_window(_dt(2026, 9, 7, 23, 30), OVERNIGHT["pos_schedule_start"], OVERNIGHT["pos_schedule_end"])
		self.assertEqual(window, (_dt(2026, 9, 7, 21, 0), _dt(2026, 9, 8, 5, 0)))

	def test_overnight_after_midnight_binds_to_previous_day(self):
		window = resolve_window(_dt(2026, 9, 8, 2, 0), OVERNIGHT["pos_schedule_start"], OVERNIGHT["pos_schedule_end"])
		self.assertEqual(window, (_dt(2026, 9, 7, 21, 0), _dt(2026, 9, 8, 5, 0)))

	def test_overnight_daytime_closed(self):
		self.assertIsNone(resolve_window(_dt(2026, 9, 8, 12, 0), OVERNIGHT["pos_schedule_start"], OVERNIGHT["pos_schedule_end"]))

	def test_equal_start_end_spans_full_day(self):
		window = resolve_window(_dt(2026, 9, 7, 3, 0), "00:00:00", "00:00:00")
		self.assertEqual(window, (_dt(2026, 9, 7, 0, 0), _dt(2026, 9, 8, 0, 0)))

	def test_exact_deadline_boundary_excludes_window_end(self):
		# window is [start, end): at exactly the end time the window is over
		self.assertIsNone(resolve_window(_dt(2026, 9, 7, 22, 0), "09:00:00", "22:00:00"))

	def test_missing_times(self):
		self.assertIsNone(resolve_window(_dt(2026, 9, 7, 12, 0), None, "22:00:00"))

	def test_time_delta_parsing(self):
		self.assertEqual(_as_time_delta("09:30:00"), datetime.timedelta(hours=9, minutes=30))
		self.assertEqual(_as_time_delta("00:00:00"), datetime.timedelta(0))
		self.assertEqual(_as_time_delta(datetime.time(9, 30)), datetime.timedelta(hours=9, minutes=30))
		self.assertIsNone(_as_time_delta("garbage"))
		self.assertIsNone(_as_time_delta("99:99:99"))


class TestScheduleValidation(unittest.TestCase):
	def _sched(self, **overrides):
		return frappe._dict({"name": "Profile 1", **WINDOW, **overrides})

	def test_enabled_schedule_requires_valid_times(self):
		with patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw):
			with self.assertRaises(RuntimeError):
				validate_schedule_values(self._sched(pos_schedule_start=None))
			with self.assertRaises(RuntimeError):
				validate_schedule_values(self._sched(pos_schedule_end="not-a-time"))
			with self.assertRaises(RuntimeError):
				validate_schedule_values(self._sched(pos_schedule_start="09:00"))  # no seconds

	def test_negative_warning_rejected(self):
		with patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw):
			with self.assertRaises(RuntimeError):
				validate_schedule_values(self._sched(pos_schedule_warning_minutes=-5))

	def test_zero_warning_and_midnight_ok(self):
		validate_schedule_values(self._sched(pos_schedule_start="00:00:00", pos_schedule_warning_minutes=0))

	def test_disabled_schedule_skips_validation(self):
		validate_schedule_values(self._sched(pos_schedule_enabled=0, pos_schedule_start="garbage"))


class TestApplyScheduleSnapshot(unittest.TestCase):
	def _apply(self, schedule, now, group="no-group"):
		# group: "no-group" = field empty, None = group doc missing, str = existing group name
		opening = Mock()
		opening.pos_profile = "Profile 1"
		# pre-set tampered values: apply must overwrite all of them
		opening.pos_schedule_enabled = 0
		opening.pos_schedule_deadline = "1999-01-01 00:00:00"
		values = frappe._dict(
			{
				"name": "Profile 1",
				"company": "Outlet",
				"pos_profile_group": None if group == "no-group" else "Group A",
				**schedule,
			}
		)

		def get_value(doctype, name, fields=None, as_dict=False):
			if doctype == "POS Profile Group":
				return group if group != "no-group" else None
			return values

		with (
			patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=get_value),
			patch("pos_next.shift_schedule.now_datetime", return_value=now),
		):
			apply_schedule_snapshot(opening)
		return opening

	def test_snapshots_schedule_and_deadline(self):
		opening = self._apply(WINDOW, _dt(2026, 9, 7, 12, 0))
		self.assertEqual(opening.pos_schedule_enabled, 1)
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 9, 7, 22, 0))

	def test_overnight_deadline_crosses_midnight(self):
		opening = self._apply(OVERNIGHT, _dt(2026, 9, 7, 23, 30))
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 9, 8, 5, 0))

	def test_overwrites_client_supplied_snapshot(self):
		opening = self._apply(WINDOW, _dt(2026, 9, 7, 12, 0))
		self.assertEqual(opening.pos_schedule_enabled, 1)
		self.assertNotEqual(opening.pos_schedule_deadline, "1999-01-01 00:00:00")

	def test_disabled_schedule_zeroes_snapshot(self):
		opening = self._apply({**WINDOW, "pos_schedule_enabled": 0}, _dt(2026, 9, 7, 12, 0))
		self.assertEqual(opening.pos_schedule_enabled, 0)
		self.assertIsNone(opening.pos_schedule_deadline)

	def test_rejects_opening_outside_enforced_window(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._apply(WINDOW, _dt(2026, 9, 7, 23, 0))

	def test_allows_opening_outside_window_when_not_enforced(self):
		opening = self._apply({**WINDOW, "pos_schedule_enforce_closing": 0}, _dt(2026, 9, 7, 23, 0))
		self.assertIsNone(opening.pos_schedule_deadline)

	def test_cross_company_group_link_accepted(self):
		# groups are company-neutral: any existing group may be linked
		opening = self._apply(WINDOW, _dt(2026, 9, 7, 12, 0), group="HQ Parent")
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 9, 7, 22, 0))

	def test_cross_company_group_link_accepted_when_schedule_disabled(self):
		opening = self._apply({**WINDOW, "pos_schedule_enabled": 0}, _dt(2026, 9, 7, 12, 0), group="HQ Parent")
		self.assertIsNone(opening.pos_schedule_deadline)

	def test_missing_group_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._apply(WINDOW, _dt(2026, 9, 7, 12, 0), group=None)

	def test_no_group_still_works(self):
		opening = self._apply(WINDOW, _dt(2026, 9, 7, 12, 0))
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 9, 7, 22, 0))


class TestFreezeSnapshot(unittest.TestCase):
	def test_existing_doc_restores_stored_snapshot(self):
		opening = Mock()
		opening.name = "POSA-OS-0001"
		opening.pos_schedule_enabled = 0  # tampered by client
		opening.pos_schedule_deadline = "1999-01-01 00:00:00"
		stored = frappe._dict(
			{
				"pos_schedule_enabled": 1,
				"pos_schedule_start": "09:00:00",
				"pos_schedule_end": "22:00:00",
				"pos_schedule_warning_minutes": 5,
				"pos_schedule_enforce_closing": 1,
				"pos_schedule_deadline": "2026-09-07 22:00:00",
			}
		)

		def get_value(doctype, name, fields, as_dict=False):
			self.assertEqual(doctype, "POS Opening Shift")
			self.assertTrue(as_dict)
			return stored

		with (
			patch("pos_next.shift_schedule.frappe.db.exists", return_value=True),
			patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=get_value),
		):
			freeze_schedule_snapshot(opening)

		self.assertEqual(opening.pos_schedule_enabled, 1)
		self.assertEqual(opening.pos_schedule_deadline, "2026-09-07 22:00:00")
		self.assertEqual(opening.pos_schedule_warning_minutes, 5)

	def test_new_doc_untouched(self):
		opening = Mock()
		opening.name = None
		with (
			patch("pos_next.shift_schedule.frappe.db.exists", return_value=False),
			patch("pos_next.shift_schedule.frappe.db.get_value") as mock_get,
		):
			freeze_schedule_snapshot(opening)
		mock_get.assert_not_called()


class TestSalesGate(unittest.TestCase):
	def _patched(self, row, exists=True):
		stack = ExitStack()
		stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=exists))
		stack.enter_context(
			patch("pos_next.shift_schedule.frappe.db.get_value", return_value=frappe._dict(row) if row else row)
		)
		return stack

	def test_expired_enforced_shift_is_gated(self):
		throw = Mock(side_effect=_throw)
		with ExitStack() as stack:
			stack.enter_context(self._patched(GATE_ROW))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 23, 0)))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.throw", throw))
			with self.assertRaises(RuntimeError):
				assert_sales_allowed("POSA-OS-0001")
		throw.assert_called_once()

	def test_exact_deadline_moment_is_expired(self):
		# expiry uses >=: at exactly the deadline the gate is closed
		with ExitStack() as stack:
			stack.enter_context(self._patched(GATE_ROW))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 22, 0)))
			gate = get_shift_gate("POSA-OS-0001")
		self.assertTrue(gate["expired"])

	def test_one_second_before_deadline_is_free(self):
		with ExitStack() as stack:
			stack.enter_context(self._patched(GATE_ROW))
			stack.enter_context(
				patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 21, 59, 59))
			)
			gate = get_shift_gate("POSA-OS-0001")
		self.assertFalse(gate["expired"])

	def test_future_deadline_is_not_gated(self):
		with ExitStack() as stack:
			stack.enter_context(self._patched({**GATE_ROW, "pos_schedule_deadline": "2026-09-08 05:00:00"}))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 23, 0)))
			# must not raise
			assert_sales_allowed("POSA-OS-0001")

	def test_disabled_or_non_enforcing_shifts_are_free(self):
		for row in (
			{**GATE_ROW, "pos_schedule_enabled": 0},
			{**GATE_ROW, "pos_schedule_enforce_closing": 0},
			{**GATE_ROW, "docstatus": 0},
			{**GATE_ROW, "docstatus": 2},
			{**GATE_ROW, "pos_schedule_deadline": None},
			None,
		):
			with self._patched(row, exists=row is not None):
				self.assertIsNone(get_shift_gate("POSA-OS-0001"))


def _doc(shift=None, profile=None, docstatus=1):
	doc = Mock()
	doc.docstatus = docstatus
	doc.pos_profile = profile
	values = {"posa_pos_opening_shift": shift, "pos_opening_shift": None, "pos_profile": profile}
	doc.get = lambda field: values.get(field)
	return doc


class TestInvoiceHook(unittest.TestCase):
	EXPIRED = frappe._dict(GATE_ROW)
	# what _gate_shifts expects from its shift lookup (pos_profile, docstatus, status)
	OPEN_SHIFT_ROW = frappe._dict({"pos_profile": "Profile 1", "docstatus": 1, "status": "Open"})

	def test_skips_non_submitted_docs_without_db_access(self):
		get_value = Mock()
		with patch("pos_next.shift_schedule.frappe.db.get_value", get_value):
			validate_invoice(_doc(docstatus=0))
			validate_invoice(_doc(docstatus=2))
		get_value.assert_not_called()

	def test_uses_shift_from_doc(self):
		throw = Mock(side_effect=_throw)

		def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
			# shift lookup of a trusted open same-profile shift, then its gate
			self.assertEqual(name, "POSA-OS-0001")
			if fields == ("pos_profile", "docstatus", "status"):
				return self.OPEN_SHIFT_ROW
			return self.EXPIRED

		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=True))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=get_value))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 23, 0)))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.throw", throw))
			with self.assertRaises(RuntimeError):
				validate_invoice(_doc(shift="POSA-OS-0001", profile="Profile 1"))
		throw.assert_called_once()

	def test_empty_shift_falls_back_to_profile_open_shift(self):
		throw = Mock(side_effect=_throw)

		def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
			return self.EXPIRED

		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=True))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=get_value))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.get_all", return_value=["POSA-OS-0009"]))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 23, 0)))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.throw", throw))
			with self.assertRaises(RuntimeError):
				validate_invoice(_doc(shift=None, profile="Profile 1"))
		throw.assert_called_once()

	def test_no_open_shift_no_block(self):
		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=True))
			stack.enter_context(
				patch("pos_next.shift_schedule.frappe.db.get_value", return_value=self.EXPIRED)
			)
			stack.enter_context(patch("pos_next.shift_schedule.frappe.get_all", return_value=[]))
			# must not raise
			validate_invoice(_doc(shift=None, profile="Profile 1"))

	def test_no_shift_no_profile_no_block(self):
		get_value = Mock()
		with patch("pos_next.shift_schedule.frappe.db.get_value", get_value):
			validate_invoice(_doc(shift=None, profile=None))
		get_value.assert_not_called()

	def test_created_before_deadline_is_admitted(self):
		# printed draft settled during closing: creation predates the deadline
		doc = _doc(shift="POSA-OS-0001", profile="Profile 1")
		doc.creation = "2026-09-07 21:00:00.000000"

		def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
			if fields == ("pos_profile", "docstatus", "status"):
				return self.OPEN_SHIFT_ROW
			return self.EXPIRED

		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=True))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=get_value))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 23, 0)))
			# must not raise
			validate_invoice(doc)

	def test_created_after_deadline_is_rejected(self):
		doc = _doc(shift="POSA-OS-0001", profile="Profile 1")
		doc.creation = "2026-09-07 22:30:00.000000"

		def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
			if fields == ("pos_profile", "docstatus", "status"):
				return self.OPEN_SHIFT_ROW
			return self.EXPIRED

		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=True))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=get_value))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 23, 0)))
			stack.enter_context(
				patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw)
			)
			with self.assertRaises(RuntimeError):
				validate_invoice(doc)


class TestGateShifts(unittest.TestCase):
	OPEN = frappe._dict({"pos_profile": "Profile 1", "docstatus": 1, "status": "Open"})
	CLOSED = frappe._dict({"pos_profile": "Profile 1", "docstatus": 1, "status": "Closed"})
	DRAFT = frappe._dict({"pos_profile": "Profile 1", "docstatus": 0, "status": "Open"})
	OTHER_PROFILE = frappe._dict({"pos_profile": "Profile 2", "docstatus": 1, "status": "Open"})

	def _patched(self, row, fallback):
		stack = ExitStack()
		stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value", return_value=row))
		stack.enter_context(patch("pos_next.shift_schedule.frappe.get_all", return_value=fallback))
		return stack

	def test_trusted_open_same_profile_shift(self):
		with self._patched(self.OPEN, ["should-not-be-used"]):
			self.assertEqual(_gate_shifts("POSA-OS-0001", "Profile 1"), ["POSA-OS-0001"])
			# profile=None (unknown) still trusts a shift that is open
			self.assertEqual(_gate_shifts("POSA-OS-0001", None), ["POSA-OS-0001"])

	def test_closed_or_draft_shift_falls_back(self):
		for row in (self.CLOSED, self.DRAFT):
			with self._patched(row, ["POSA-OS-0009"]):
				self.assertEqual(_gate_shifts("POSA-OS-0001", "Profile 1"), ["POSA-OS-0009"])

	def test_cancelled_or_missing_shift_falls_back(self):
		with self._patched(None, ["POSA-OS-0009"]):
			self.assertEqual(_gate_shifts("POSA-OS-0001", "Profile 1"), ["POSA-OS-0009"])

	def test_other_profile_shift_falls_back(self):
		# a shift from another profile is never trusted (cashier association)
		with self._patched(self.OTHER_PROFILE, ["POSA-OS-0009"]):
			self.assertEqual(_gate_shifts("POSA-OS-0001", "Profile 1"), ["POSA-OS-0009"])

	def test_no_shift_uses_all_profile_open_shifts(self):
		with self._patched(None, ["POSA-OS-0007", "POSA-OS-0009"]):
			self.assertEqual(_gate_shifts(None, "Profile 1"), ["POSA-OS-0007", "POSA-OS-0009"])

	def test_no_profile_no_shift_no_gates(self):
		with self._patched(None, ["should-not-be-used"]):
			self.assertEqual(_gate_shifts(None, None), [])


class TestAssertInvoiceSalesAllowed(unittest.TestCase):
	def test_missing_invoice_is_noop(self):
		with patch("pos_next.shift_schedule.frappe.db.exists", return_value=False):
			with patch("pos_next.shift_schedule.frappe.db.get_value") as get_value:
				assert_invoice_sales_allowed("INV-0001")
		get_value.assert_not_called()
		assert_invoice_sales_allowed(None)

	def test_doctype_without_shift_columns_is_noop(self):
		has_column = Mock(side_effect=lambda doctype, col: False)
		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=True))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.has_column", has_column))
			get_value = stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value"))
			assert_invoice_sales_allowed("INV-0001")
		get_value.assert_not_called()

	def test_expired_shift_rejects_payment(self):
		expired_gate = frappe._dict(
			{
				"pos_schedule_enabled": 1,
				"pos_schedule_enforce_closing": 1,
				"pos_schedule_deadline": "2026-09-07 22:00:00",
				"docstatus": 1,
			}
		)

		def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
			if fields == ("posa_pos_opening_shift", "pos_profile"):
				return ("POSA-OS-0001", "Profile 1")
			if fields == ("pos_profile", "docstatus", "status"):
				return frappe._dict({"pos_profile": "Profile 1", "docstatus": 1, "status": "Open"})
			return expired_gate

		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.exists", return_value=True))
			stack.enter_context(
				patch(
					"pos_next.shift_schedule.frappe.db.has_column",
					side_effect=lambda doctype, col: True,
				)
			)
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=get_value))
			stack.enter_context(patch("pos_next.shift_schedule.now_datetime", return_value=_dt(2026, 9, 7, 23, 0)))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw))
			with self.assertRaises(RuntimeError):
				assert_invoice_sales_allowed("INV-0001")


class TestExtendDeadline(unittest.TestCase):
	MANDATORY = frappe._dict(
		{
			"pos_schedule_enabled": 1,
			"pos_schedule_enforce_closing": 1,
			"pos_schedule_deadline": "2026-09-07 22:00:00",
			"docstatus": 1,
		}
	)

	def _run(self, row, new_deadline, roles=("System Manager",)):
		set_value = Mock()
		insert = Mock()
		get_doc = Mock(return_value=Mock(insert=insert))
		with ExitStack() as stack:
			stack.enter_context(patch("pos_next.shift_schedule.frappe.get_roles", return_value=roles))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.get_value", return_value=row))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.db.set_value", set_value))
			stack.enter_context(patch("pos_next.shift_schedule.frappe.get_doc", get_doc))
			result = extend_deadline("POSA-OS-0001", new_deadline)
		return result, set_value, get_doc

	def test_requires_system_manager(self):
		with patch("pos_next.shift_schedule.frappe.get_roles", return_value=["POS User"]):
			with patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw):
				with self.assertRaises(RuntimeError):
					extend_deadline("POSA-OS-0001", "2026-09-07 23:00:00")

	def test_non_mandatory_shift_rejected(self):
		for row in (
			frappe._dict({**self.MANDATORY, "pos_schedule_enabled": 0}),
			frappe._dict({**self.MANDATORY, "pos_schedule_enforce_closing": 0}),
			frappe._dict({**self.MANDATORY, "docstatus": 2}),
			None,
		):
			with (
				patch("pos_next.shift_schedule.frappe.get_roles", return_value=["System Manager"]),
				patch("pos_next.shift_schedule.frappe.db.get_value", return_value=row),
				patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw),
			):
				with self.assertRaises(RuntimeError):
					extend_deadline("POSA-OS-0001", "2026-09-08 01:00:00")

	def test_forward_only(self):
		for new_deadline in ("2026-09-07 22:00:00", "2026-09-07 21:00:00"):
			with (
				patch("pos_next.shift_schedule.frappe.get_roles", return_value=["System Manager"]),
				patch("pos_next.shift_schedule.frappe.db.get_value", return_value=self.MANDATORY),
				patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw),
			):
				with self.assertRaises(RuntimeError):
					extend_deadline("POSA-OS-0001", new_deadline)

	def test_extension_writes_deadline_and_audit_comment(self):
		result, set_value, get_doc = self._run(self.MANDATORY, "2026-09-07 23:30:00")
		self.assertEqual(set_value.call_count, 1)
		self.assertEqual(get_doc.call_count, 1)  # audit Comment
		comment = get_doc.call_args[0][0]
		self.assertEqual(comment["doctype"], "Comment")
		self.assertEqual(comment["reference_doctype"], "POS Opening Shift")
		self.assertEqual(comment["reference_name"], "POSA-OS-0001")

	def test_first_deadline_allowed_when_none_set(self):
		row = frappe._dict({**self.MANDATORY, "pos_schedule_deadline": None})
		result, set_value, insert = self._run(row, "2026-09-08 01:00:00")
		set_value.assert_called_once()


class TestProfileGroupHooks(unittest.TestCase):
	def test_profile_save_validates_group_and_schedule(self):
		doc = Mock()
		doc.get = lambda field: {"pos_profile_group": "Group A"}.get(field)
		group_check = Mock()
		membership_check = Mock()
		sched_check = Mock()
		with (
			patch("pos_next.shift_schedule.validate_group_link", group_check),
			patch("pos_next.shift_schedule.validate_group_membership", membership_check),
			patch("pos_next.shift_schedule.validate_schedule_values", sched_check),
		):
			validate_profile_schedule(doc)
		group_check.assert_called_once_with("Group A")
		membership_check.assert_called_once_with(doc)
		sched_check.assert_called_once_with(doc)


class TestGroupMembershipAuthority(unittest.TestCase):
	"""The Members table is authoritative; a bare link must be rejected."""

	def _doc(self, group, name="Profile 1"):
		doc = Mock()
		doc.get = lambda field: {"pos_profile_group": group, "name": name}.get(field)
		return doc

	def test_link_without_member_row_rejected(self):
		with patch("pos_next.shift_schedule.frappe.db.exists", return_value=False):
			with patch("pos_next.shift_schedule.frappe.throw", side_effect=_throw):
				with self.assertRaises(RuntimeError):
					validate_group_membership(self._doc("Group A"))

	def test_link_with_member_row_allowed(self):
		with patch("pos_next.shift_schedule.frappe.db.exists", return_value=True):
			# must not raise
			validate_group_membership(self._doc("Group A"))

	def test_no_link_no_check(self):
		exists = Mock()
		with patch("pos_next.shift_schedule.frappe.db.exists", exists):
			validate_group_membership(self._doc(None))
			validate_group_membership(self._doc(""))
		exists.assert_not_called()

	def test_new_doc_temp_name_skipped(self):
		exists = Mock()
		with patch("pos_next.shift_schedule.frappe.db.exists", exists):
			validate_group_membership(self._doc("Group A", name="new-pos-profile-abc123"))
		exists.assert_not_called()


if __name__ == "__main__":
	unittest.main()
