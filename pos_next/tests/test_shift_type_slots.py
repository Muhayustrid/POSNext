# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Shift slots pick an HRMS Shift Type; the slot hours follow the Shift Type."""

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.patches.v2_33_0.shift_slots_to_shift_type import execute as migrate_slots
from pos_next.shift_schedule import _group_slots


def _shift_type(name, start, end):
	if not frappe.db.exists("Shift Type", name):
		frappe.get_doc(
			{
				"doctype": "Shift Type",
				"name": name,
				"start_time": start,
				"end_time": end,
				"begin_check_in_before_shift_start_time": 0,
				"allow_check_out_after_shift_end_time": 0,
			}
		).insert(ignore_permissions=True)
	return name


def _legacy_group(name, slots):
	"""A group as the old free-text slots left it (no Shift Type behind the names)."""
	group = frappe.get_doc(
		{
			"doctype": "POS Profile Group",
			"group_name": name,
			"shifts": [{"shift_name": n, "start_time": s, "end_time": e} for n, s, e in slots],
		}
	)
	group.flags.ignore_links = True
	return group


class TestShiftTypeSlots(FrappeTestCase):
	def setUp(self):
		self.suffix = frappe.generate_hash(length=6)

	def _insert(self, group):
		# the patch reads every slot on the site: leave none behind
		group.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "POS Profile Group", group.name, force=1, ignore_permissions=True)
		return group

	def test_slot_times_are_fetched_from_shift_type(self):
		st = _shift_type(f"_T Pagi {self.suffix}", "05:00:00", "13:00:00")
		group = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": f"_T ST Group {self.suffix}",
				"shifts": [{"shift_name": st}],
			}
		)
		row = self._insert(group).shifts[0]
		self.assertEqual(str(row.start_time), "5:00:00")
		self.assertEqual(str(row.end_time), "13:00:00")

	def test_group_slots_follow_later_shift_type_change(self):
		st = _shift_type(f"_T Siang {self.suffix}", "13:00:00", "21:00:00")
		group = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": f"_T ST Live {self.suffix}",
				"shifts": [{"shift_name": st}],
			}
		)
		self._insert(group)
		frappe.db.set_value("Shift Type", st, {"start_time": "14:00:00", "end_time": "22:00:00"})

		slot = _group_slots(group.name)[0]
		self.assertEqual((slot["start_time"], slot["end_time"]), ("14:00:00", "22:00:00"))

	def test_unknown_shift_type_refused(self):
		group = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": f"_T ST Bad {self.suffix}",
				"shifts": [{"shift_name": f"_T Missing {self.suffix}"}],
			}
		)
		with self.assertRaises(frappe.LinkValidationError):
			group.insert(ignore_permissions=True)

	def test_patch_creates_shift_types_from_legacy_slots(self):
		day = f"_T Day {self.suffix}"
		all_time = f"_T All {self.suffix}"
		self._insert(
			_legacy_group(
				f"_T Legacy {self.suffix}",
				[(day, "05:00:00", "22:00:00"), (all_time, "00:00:00", "23:59:59")],
			)
		)

		migrate_slots()

		st = frappe.get_doc("Shift Type", day)
		self.assertEqual((str(st.start_time), str(st.end_time)), ("5:00:00", "22:00:00"))
		self.assertEqual(st.begin_check_in_before_shift_start_time, 0)
		self.assertEqual(st.allow_check_out_after_shift_end_time, 0)
		# HRMS refuses a 24h Shift Type: a full-day slot ends one minute early
		self.assertEqual(str(frappe.db.get_value("Shift Type", all_time, "end_time")), "23:59:00")
		migrate_slots()  # re-run is a no-op

	def test_patch_stops_on_same_name_with_other_times(self):
		name = _shift_type(f"_T Clash {self.suffix}", "06:00:00", "14:00:00")
		self._insert(_legacy_group(f"_T Legacy Clash {self.suffix}", [(name, "05:00:00", "13:00:00")]))

		with self.assertRaises(frappe.ValidationError) as ctx:
			migrate_slots()
		self.assertIn(name, str(ctx.exception))
