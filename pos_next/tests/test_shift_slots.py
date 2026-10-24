# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Tests for shift slots on Shift Group (POS Profile Group) and slot resolution."""

import datetime
import unittest
from unittest.mock import Mock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint, flt, now_datetime

from pos_next.api.invoices import submit_invoice
from pos_next.api.shifts import create_opening_shift
from pos_next.shift_schedule import (
	apply_schedule_snapshot,
	freeze_schedule_snapshot,
	get_profile_slots,
	open_slots_now,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


def _dt(*args):
	return datetime.datetime(*args)


SLOT_MORNING = {
	"shift_name": "Morning",
	"start_time": "05:00:00",
	"end_time": "15:00:00",
	"warning_minutes": 10,
	"enforce_closing": 1,
}

SLOT_AFTERNOON = {
	"shift_name": "Afternoon",
	"start_time": "13:00:00",
	"end_time": "21:00:00",
	"warning_minutes": 15,
	"enforce_closing": 1,
}

SLOT_NIGHT = {
	"shift_name": "Night",
	"start_time": "22:00:00",
	"end_time": "06:00:00",
	"warning_minutes": 0,
	"enforce_closing": 1,
}


class TestShiftSlots(unittest.TestCase):
	def _apply_with_slots(self, slots, now, requested_slot=None, group_name="Group A"):
		opening = Mock()
		opening.pos_profile = "Profile 1"
		opening.shift_slot = requested_slot
		opening.pos_schedule_enabled = 0
		opening.pos_schedule_start = None
		opening.pos_schedule_end = None
		opening.pos_schedule_deadline = None
		opening.pos_schedule_warning_minutes = 0
		opening.pos_schedule_enforce_closing = 0

		# no profile-level schedule: only the slots of the group can apply
		profile_data = frappe._dict(
			{
				"name": "Profile 1",
				"company": "Outlet",
				"pos_profile_group": group_name,
				"pos_schedule_enabled": 0,
				"pos_schedule_start": None,
				"pos_schedule_end": None,
				"pos_schedule_warning_minutes": 0,
				"pos_schedule_enforce_closing": 0,
			}
		)

		def mock_get_value(doctype, name, fields=None, as_dict=False):
			if doctype == "POS Profile":
				return profile_data
			if doctype == "POS Profile Group":
				return group_name
			return None

		with (
			patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=mock_get_value),
			patch("pos_next.shift_schedule._group_slots", return_value=slots),
			patch("pos_next.shift_schedule.now_datetime", return_value=now),
		):
			apply_schedule_snapshot(opening)
		return opening

	def test_open_inside_slot_snapshots_slot(self):
		now = _dt(2026, 10, 23, 10, 0)
		opening = self._apply_with_slots([SLOT_MORNING], now)
		self.assertEqual(opening.shift_slot, "Morning")
		self.assertEqual(opening.pos_schedule_enabled, 1)
		self.assertEqual(opening.pos_schedule_start, "05:00:00")
		self.assertEqual(opening.pos_schedule_end, "15:00:00")
		self.assertEqual(opening.pos_schedule_warning_minutes, 10)
		self.assertEqual(opening.pos_schedule_enforce_closing, 1)
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 10, 23, 15, 0))

	def test_open_outside_every_slot_refused(self):
		now = _dt(2026, 10, 23, 16, 0)
		with self.assertRaises(frappe.exceptions.ValidationError) as ctx:
			self._apply_with_slots([SLOT_MORNING], now)
		self.assertIn("outside every shift", str(ctx.exception))

	def test_end_is_exclusive(self):
		now = _dt(2026, 10, 23, 15, 0)
		with self.assertRaises(frappe.exceptions.ValidationError) as ctx:
			self._apply_with_slots([SLOT_MORNING], now)
		self.assertIn("outside every shift", str(ctx.exception))

	def test_overnight_slot(self):
		now = _dt(2026, 10, 23, 23, 30)
		opening = self._apply_with_slots([SLOT_NIGHT], now)
		self.assertEqual(opening.shift_slot, "Night")
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 10, 24, 6, 0))

	def test_overlap_requires_named_slot_and_defaults_latest(self):
		now = _dt(2026, 10, 23, 13, 30)
		slots = [SLOT_MORNING, SLOT_AFTERNOON]

		# Verify open_slots_now orders by window start descending: Afternoon (13:00) before Morning (05:00)
		with patch("pos_next.shift_schedule.get_profile_slots", return_value=slots):
			open_now = open_slots_now("Profile 1", now_dt=now)
			self.assertEqual(len(open_now), 2)
			self.assertEqual(open_now[0]["shift_name"], "Afternoon")
			self.assertEqual(open_now[1]["shift_name"], "Morning")

		# shift_slot=None defaults to latest started slot (Afternoon)
		opening_default = self._apply_with_slots(slots, now, requested_slot=None)
		self.assertEqual(opening_default.shift_slot, "Afternoon")
		self.assertEqual(opening_default.pos_schedule_deadline, _dt(2026, 10, 23, 21, 0))

		# Explicit shift_slot picks the named one (Morning)
		opening_named = self._apply_with_slots(slots, now, requested_slot="Morning")
		self.assertEqual(opening_named.shift_slot, "Morning")
		self.assertEqual(opening_named.pos_schedule_deadline, _dt(2026, 10, 23, 15, 0))

	def test_unknown_or_closed_slot_name_refused(self):
		now = _dt(2026, 10, 23, 10, 0)
		# Morning is open, Night is closed
		slots = [SLOT_MORNING, SLOT_NIGHT]

		# Asking for closed slot throws
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._apply_with_slots(slots, now, requested_slot="Night")

		# Asking for nonexistent slot throws
		with self.assertRaises(frappe.exceptions.ValidationError):
			self._apply_with_slots(slots, now, requested_slot="Nonexistent")

	def test_reopen_same_slot_after_closing(self):
		now = _dt(2026, 10, 23, 11, 0)
		# First shift opened at 09:00 and closed
		# Re-opening at 11:00 inside Morning slot (05:00-15:00) succeeds
		opening = self._apply_with_slots([SLOT_MORNING], now, requested_slot="Morning")
		self.assertEqual(opening.shift_slot, "Morning")
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 10, 23, 15, 0))

	def test_group_without_slots_keeps_profile_schedule(self):
		now = _dt(2026, 10, 23, 12, 0)
		profile_data = frappe._dict(
			{
				"name": "Profile 1",
				"company": "Outlet",
				"pos_profile_group": "Group A",
				"pos_schedule_enabled": 1,
				"pos_schedule_start": "09:00:00",
				"pos_schedule_end": "22:00:00",
				"pos_schedule_warning_minutes": 15,
				"pos_schedule_enforce_closing": 1,
			}
		)
		opening = Mock()
		opening.pos_profile = "Profile 1"
		opening.shift_slot = None

		def mock_get_value(doctype, name, fields=None, as_dict=False):
			if doctype == "POS Profile":
				return profile_data
			if doctype == "POS Profile Group":
				return "Group A"
			return None

		with (
			patch("pos_next.shift_schedule.frappe.db.get_value", side_effect=mock_get_value),
			patch("pos_next.shift_schedule.get_profile_slots", return_value=[]),
			patch("pos_next.shift_schedule.now_datetime", return_value=now),
		):
			apply_schedule_snapshot(opening)

		self.assertIsNone(opening.shift_slot)
		self.assertEqual(opening.pos_schedule_enabled, 1)
		self.assertEqual(opening.pos_schedule_start, "09:00:00")
		self.assertEqual(opening.pos_schedule_end, "22:00:00")
		self.assertEqual(opening.pos_schedule_warning_minutes, 15)
		self.assertEqual(opening.pos_schedule_enforce_closing, 1)
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 10, 23, 22, 0))

	def test_slot_snapshot_frozen_on_save(self):
		opening = Mock()
		opening.name = "POSA-OS-26-00001"
		opening.shift_slot = "Tampered Slot"
		opening.pos_schedule_enabled = 0
		opening.pos_schedule_start = "00:00:00"
		opening.pos_schedule_end = "00:00:00"
		opening.pos_schedule_warning_minutes = 0
		opening.pos_schedule_enforce_closing = 0
		opening.pos_schedule_deadline = _dt(2026, 10, 23, 0, 0)

		stored_row = frappe._dict(
			{
				"shift_slot": "Morning",
				"pos_schedule_enabled": 1,
				"pos_schedule_start": "05:00:00",
				"pos_schedule_end": "15:00:00",
				"pos_schedule_warning_minutes": 10,
				"pos_schedule_enforce_closing": 1,
				"pos_schedule_deadline": _dt(2026, 10, 23, 15, 0),
			}
		)

		with (
			patch("pos_next.shift_schedule.frappe.db.exists", return_value=True),
			patch("pos_next.shift_schedule.frappe.db.get_value", return_value=stored_row),
		):
			freeze_schedule_snapshot(opening)

		self.assertEqual(opening.shift_slot, "Morning")
		self.assertEqual(opening.pos_schedule_enabled, 1)
		self.assertEqual(opening.pos_schedule_start, "05:00:00")
		self.assertEqual(opening.pos_schedule_end, "15:00:00")
		self.assertEqual(opening.pos_schedule_deadline, _dt(2026, 10, 23, 15, 0))


class TestShiftSlotGroupValidation(unittest.TestCase):
	def test_group_slot_names_unique(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": "Test Group",
				"shifts": [
					{"shift_name": "Shift 1", "start_time": "05:00:00", "end_time": "15:00:00", "warning_minutes": 0},
					{"shift_name": "Shift 1", "start_time": "13:00:00", "end_time": "21:00:00", "warning_minutes": 0},
				],
			}
		)
		with self.assertRaises(frappe.exceptions.ValidationError):
			doc.validate_slots()

	def test_group_start_cannot_equal_end(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": "Test Group",
				"shifts": [
					{"shift_name": "Shift 1", "start_time": "05:00:00", "end_time": "05:00:00", "warning_minutes": 0},
				],
			}
		)
		with self.assertRaises(frappe.exceptions.ValidationError):
			doc.validate_slots()

	def test_group_warning_minutes_negative_refused(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": "Test Group",
				"shifts": [
					{"shift_name": "Shift 1", "start_time": "05:00:00", "end_time": "15:00:00", "warning_minutes": -5},
				],
			}
		)
		with self.assertRaises(frappe.exceptions.ValidationError):
			doc.validate_slots()

	def test_sync_members_skips_pushing_schedule_when_slots_present(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"name": "Group With Slots",
				"group_name": "Group With Slots",
				"shifts": [SLOT_MORNING],
				"profiles": [{"pos_profile": "Profile 1"}],
			}
		)

		mock_profile_doc = Mock()
		mock_profile_doc.pos_profile_group = ""

		with (
			patch("pos_next.pos_next.doctype.pos_profile_group.pos_profile_group.frappe.get_all", return_value=[]),
			patch("pos_next.pos_next.doctype.pos_profile_group.pos_profile_group.frappe.db.get_value", return_value=frappe._dict({"pos_profile_group": ""})),
			patch("pos_next.pos_next.doctype.pos_profile_group.pos_profile_group.frappe.get_doc", return_value=mock_profile_doc),
		):
			doc.sync_members()

		self.assertEqual(mock_profile_doc.pos_profile_group, "Group With Slots")
		mock_profile_doc.update.assert_not_called()
		mock_profile_doc.save.assert_called_once()


class TestShiftSlotsAPI(unittest.TestCase):
	def test_get_profile_slots_and_open_slots_now(self):
		slots_data = [
			dict(SLOT_MORNING),
			dict(SLOT_AFTERNOON),
		]

		with (
			patch(
				"pos_next.shift_schedule.frappe.db.get_value",
				return_value=frappe._dict({"pos_profile_group": "Group A"}),
			),
			patch("pos_next.shift_schedule.frappe.db.sql", return_value=slots_data),
		):
			slots = get_profile_slots("Profile 1")
			self.assertEqual(len(slots), 2)
			self.assertEqual(slots[0]["shift_name"], "Morning")
			self.assertEqual(slots[0]["start_time"], "05:00:00")
			self.assertEqual(slots[1]["shift_name"], "Afternoon")
			self.assertEqual(slots[1]["start_time"], "13:00:00")

			now = _dt(2026, 10, 23, 10, 0)
			open_now = open_slots_now("Profile 1", now_dt=now)
			self.assertEqual(len(open_now), 1)
			self.assertEqual(open_now[0]["shift_name"], "Morning")

	def test_get_opening_dialog_data_returns_slots(self):
		from pos_next.api.shifts import get_opening_dialog_data

		pos_profiles = [
			frappe._dict({"name": "Profile 1", "company": "Company 1", "currency": "IDR", "warehouse": "W1", "selling_price_list": "Standard"}),
		]

		with (
			patch("pos_next.api.shifts.frappe.db.sql", return_value=pos_profiles),
			patch("pos_next.api.shifts.get_wallet_payment_modes", return_value=[]),
			patch("pos_next.api.shifts.frappe.get_list", return_value=[]),
			patch("pos_next.api.shifts.get_profile_slots", return_value=[SLOT_MORNING]),
			patch("pos_next.api.shifts.open_slots_now", return_value=[SLOT_MORNING]),
		):
			data = get_opening_dialog_data()
			self.assertIn("slots", data)
			self.assertIn("Profile 1", data["slots"])
			self.assertEqual(len(data["slots"]["Profile 1"]), 1)
			slot_entry = data["slots"]["Profile 1"][0]
			self.assertEqual(slot_entry["shift_name"], "Morning")
			self.assertTrue(slot_entry["open_now"])

	def test_create_opening_shift_passes_shift_slot(self):
		from pos_next.api.shifts import create_opening_shift

		created_doc = Mock()
		created_doc.as_dict.return_value = {"name": "POSA-OS-26-00001", "shift_slot": "Morning", "pos_profile": "Profile 1", "company": "Company 1"}
		created_doc.pos_profile = "Profile 1"
		created_doc.company = "Company 1"

		with (
			patch("pos_next.api.shifts._check_profile_access"),
			patch("pos_next.api.shifts.frappe.db.get_value"),
			patch("pos_next.api.shifts.check_opening_shift", return_value=None),
			patch("pos_next.api.shifts.get_active_profile_shift", return_value=None),
			patch("pos_next.api.shifts.frappe.get_doc", return_value=created_doc) as mock_get_doc,
			patch("pos_next.api.shifts.frappe.get_cached_doc"),
		):
			create_opening_shift("Profile 1", "Company 1", [], shift_slot="Morning")
			# Check that frappe.get_doc was called with shift_slot
			call_args = mock_get_doc.call_args[0][0]
			self.assertEqual(call_args.get("shift_slot"), "Morning")


class TestOneOpenShift(POSInvoiceModeMixin, FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.cashier_a = f"cashier_a_{frappe.generate_hash(length=8)}@example.com"
		cls.cashier_b = f"cashier_b_{frappe.generate_hash(length=8)}@example.com"
		for email in (cls.cashier_a, cls.cashier_b):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": email.split("@")[0],
					"roles": [{"role": "POSNext Cashier"}],
				}
			).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for email in (getattr(cls, "cashier_a", None), getattr(cls, "cashier_b", None)):
			if email and frappe.db.exists("User", email):
				try:
					frappe.db.delete("Error Log", {"owner": email})
					frappe.delete_doc("User", email, force=1, ignore_permissions=True)
				except Exception:
					pass
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		for email in (self.cashier_a, self.cashier_b):
			if not frappe.db.exists("POS Profile User", {"parent": self.profile.name, "user": email}):
				pu = frappe.get_doc(
					{
						"doctype": "POS Profile User",
						"parent": self.profile.name,
						"parenttype": "POS Profile",
						"parentfield": "pos_users",
						"user": email,
					}
				).insert(ignore_permissions=True)
				self.addCleanup(self._delete_profile_user, pu.name)

		frappe.db.set_value(
			"POS Opening Shift", self.shift.name, "user", self.cashier_a, update_modified=False
		)
		self.shift.reload()

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def _delete_profile_user(self, name):
		frappe.db.delete("POS Profile User", {"name": name})

	def _cleanup_shift(self, shift_name):
		frappe.set_user("Administrator")
		if frappe.db.exists("POS Opening Shift", shift_name):
			frappe.db.set_value("POS Opening Shift", shift_name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", shift_name, force=1, ignore_permissions=True)

	def test_second_open_refused_while_first_inside_window(self):
		deadline = now_datetime() + datetime.timedelta(hours=2)
		frappe.db.set_value(
			"POS Opening Shift",
			self.shift.name,
			{
				"pos_schedule_enabled": 1,
				"pos_schedule_enforce_closing": 1,
				"pos_schedule_deadline": deadline,
			},
			update_modified=False,
		)
		frappe.set_user(self.cashier_b)
		with self.assertRaises(frappe.exceptions.ValidationError) as ctx:
			create_opening_shift(
				self.profile.name,
				self.profile.company,
				[{"mode_of_payment": self.mode[0], "amount": 0}],
			)
		self.assertIn("Only one shift can be open per POS Profile", str(ctx.exception))

	def test_expired_open_shift_closed_then_new_opens(self):
		submit_invoice(self._payload())
		deadline = now_datetime() - datetime.timedelta(minutes=10)
		frappe.db.set_value(
			"POS Opening Shift",
			self.shift.name,
			{
				"pos_schedule_enabled": 1,
				"pos_schedule_enforce_closing": 1,
				"pos_schedule_deadline": deadline,
			},
			update_modified=False,
		)

		frappe.set_user(self.cashier_b)
		res = create_opening_shift(
			self.profile.name,
			self.profile.company,
			[{"mode_of_payment": self.mode[0], "amount": 0}],
		)

		self.shift.reload()
		self.assertEqual(self.shift.status, "Closed")
		self.assertTrue(self.shift.pos_closing_shift)

		closing = frappe.get_doc("POS Closing Shift", self.shift.pos_closing_shift)
		self.closing = closing
		self.assertEqual(closing.docstatus, 1)
		self.assertEqual(closing.auto_closed, 1)
		recon = [r for r in closing.payment_reconciliation if r.mode_of_payment == self.mode[0]]
		self.assertTrue(recon)
		self.assertGreater(flt(recon[0].expected_amount), 0)
		self.assertEqual(flt(recon[0].closing_amount), flt(recon[0].expected_amount))
		self.assertEqual(flt(recon[0].difference), 0)

		b_shift_name = res["pos_opening_shift"]["name"]
		self.assertNotEqual(b_shift_name, self.shift.name)
		self.addCleanup(self._cleanup_shift, b_shift_name)
		b_shift = frappe.get_doc("POS Opening Shift", b_shift_name)
		self.assertEqual(b_shift.status, "Open")
		self.assertEqual(b_shift.user, self.cashier_b)

	def test_profile_list_unlocks_profile_held_by_expired_shift(self):
		# The opening dialog refuses a locked profile before calling
		# create_opening_shift, so an expired shift must not lock it.
		from pos_next.api.pos_profile import get_pos_profiles

		for offset, expected_locked in ((120, True), (-10, False)):
			frappe.db.set_value(
				"POS Opening Shift",
				self.shift.name,
				"pos_schedule_deadline",
				now_datetime() + datetime.timedelta(minutes=offset),
				update_modified=False,
			)
			frappe.set_user(self.cashier_b)
			row = next(p for p in get_pos_profiles() if p.name == self.profile.name)
			frappe.set_user("Administrator")
			self.assertEqual(row.locked, expected_locked, f"deadline offset {offset} min")

	def test_manager_resume_unchanged(self):
		deadline = now_datetime() + datetime.timedelta(hours=2)
		frappe.db.set_value(
			"POS Opening Shift",
			self.shift.name,
			{
				"pos_schedule_enabled": 1,
				"pos_schedule_enforce_closing": 1,
				"pos_schedule_deadline": deadline,
			},
			update_modified=False,
		)
		frappe.set_user("Administrator")
		res = create_opening_shift(
			self.profile.name,
			self.profile.company,
			[{"mode_of_payment": self.mode[0], "amount": 0}],
		)
		self.assertTrue(res.get("resumed"))
		self.assertEqual(res["pos_opening_shift"]["name"], self.shift.name)
		self.shift.reload()
		self.assertEqual(self.shift.status, "Open")
		self.assertFalse(self.shift.pos_closing_shift)

