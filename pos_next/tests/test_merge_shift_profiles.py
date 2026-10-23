# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Integration tests for the v2_26_0 Shift Group migration patch."""

import unittest
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.patches.v2_26_0.merge_shift_profiles import _format_time, merge_company
from pos_next.tests.price_group_helpers import make_test_company, make_test_pos_profile, make_test_warehouse

ADMIN = "Administrator"


class TestMergeShiftProfiles(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.suffix = uuid.uuid4().hex[:8]
		cls.company = make_test_company(f"ShiftMerge{cls.suffix}")
		cls.warehouse = make_test_warehouse(f"ShiftMerge{cls.suffix}", cls.company)
		cls.keep = make_test_pos_profile(f"ShiftKeep{cls.suffix}", cls.company, cls.warehouse)
		cls.disable = make_test_pos_profile(f"ShiftDisable{cls.suffix}", cls.company, cls.warehouse)
		cls.fallback = make_test_pos_profile(f"ShiftFallback{cls.suffix}", cls.company, cls.warehouse)
		cls.group_keep = f"Legacy Morning {cls.suffix}"
		cls.group_disable = f"Legacy Afternoon {cls.suffix}"
		cls._make_old_group(cls.group_keep, "05:00:00", "15:00:00")
		cls._make_old_group(cls.group_disable, "13:00:00", "21:00:00")
		cls._schedule(cls.keep, cls.group_keep, "05:00:00", "15:00:00")
		cls._schedule(cls.disable, cls.group_disable, "13:00:00", "21:00:00")
		cls._schedule(cls.fallback, None, "21:00:00", "05:00:00")
		cls._make_profile_user(cls.keep, ADMIN)
		cls._make_profile_user(cls.disable, ADMIN)
		cls.cashier = f"shift-merge.{cls.suffix}@example.com"
		frappe.get_doc(
			{"doctype": "User", "email": cls.cashier, "first_name": "Shift Merge Tester"}
		).insert(ignore_permissions=True)
		cls._make_profile_user(cls.disable, cls.cashier)
		cls._make_profile_user(cls.fallback, cls.cashier)
		cls._make_opening_shift_rows(cls.keep, 3)
		cls._make_opening_shift_rows(cls.disable, 1)

		cls.single_company = make_test_company(f"ShiftSingle{cls.suffix}")
		cls.single_warehouse = make_test_warehouse(f"ShiftSingle{cls.suffix}", cls.single_company)
		cls.single = make_test_pos_profile(
			f"ShiftSingle{cls.suffix}", cls.single_company, cls.single_warehouse
		)
		cls._schedule(cls.single, None, "05:00:00", "15:00:00")

		cls.open_company = make_test_company(f"ShiftOpen{cls.suffix}")
		cls.open_warehouse = make_test_warehouse(f"ShiftOpen{cls.suffix}", cls.open_company)
		cls.open_profiles = [
			make_test_pos_profile(f"ShiftOpen{i}{cls.suffix}", cls.open_company, cls.open_warehouse)
			for i in (1, 2)
		]
		for profile in cls.open_profiles:
			cls._schedule(profile, None, "05:00:00", "15:00:00")
		cls._make_opening_shift_rows(cls.open_profiles[0], 1, active=True)

		cls.warehouse_company = make_test_company(f"ShiftWarehouses{cls.suffix}")
		cls.warehouses = [
			make_test_warehouse(f"ShiftWarehouse{i}{cls.suffix}", cls.warehouse_company)
			for i in (1, 2)
		]
		cls.different_warehouse_profiles = [
			make_test_pos_profile(
				f"ShiftWarehouse{i}{cls.suffix}", cls.warehouse_company, warehouse
			)
			for i, warehouse in enumerate(cls.warehouses, 1)
		]
		for profile in cls.different_warehouse_profiles:
			cls._schedule(profile, None, "05:00:00", "15:00:00")
		frappe.db.commit()

	@classmethod
	def _make_old_group(cls, name, start, end):
		frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": name,
				"pos_schedule_enabled": 1,
				"pos_schedule_start": start,
				"pos_schedule_end": end,
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _schedule(cls, profile, group, start, end):
		frappe.db.set_value(
			"POS Profile",
			profile,
			{
				"pos_profile_group": group,
				"pos_schedule_enabled": 1,
				"pos_schedule_start": start,
				"pos_schedule_end": end,
				"pos_schedule_warning_minutes": 10,
				"pos_schedule_enforce_closing": 1,
			},
			update_modified=False,
		)

	@classmethod
	def _make_profile_user(cls, profile, user):
		parentfield = next(
			field.fieldname
			for field in frappe.get_meta("POS Profile").fields
			if field.fieldtype == "Table" and field.options == "POS Profile User"
		)
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": profile,
				"parenttype": "POS Profile",
				"parentfield": parentfield,
				"user": user,
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _make_opening_shift_rows(cls, profile, count, active=False):
		for index in range(count):
			name = f"_Test Shift Merge {uuid.uuid4().hex[:8]} {index}"
			frappe.db.sql(
				"""INSERT INTO `tabPOS Opening Shift`
				(name, owner, creation, modified, modified_by, docstatus, status,
				 pos_profile, company)
				VALUES (%s, %s, NOW(), NOW(), %s, 1, %s, %s, %s)""",
				(
					name,
				ADMIN,
				ADMIN,
				"Open" if active and index == 0 else "Closed",
				profile,
				frappe.db.get_value("POS Profile", profile, "company"),
				),
			)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		profiles = [
			getattr(cls, name, None)
			for name in ("keep", "disable", "fallback", "single", "open_profiles", "different_warehouse_profiles")
		]
		flat_profiles = []
		for item in profiles:
			if isinstance(item, list):
				flat_profiles.extend(item)
			elif item:
				flat_profiles.append(item)
		for profile in flat_profiles:
			frappe.db.delete("POS Opening Shift", {"pos_profile": profile, "name": ["like", "_Test Shift Merge %"]})
			frappe.db.delete("POS Profile User", {"parent": profile})
			frappe.db.delete("POS Profile Group Member", {"pos_profile": profile})
			frappe.db.set_value("POS Profile", profile, "pos_profile_group", "", update_modified=False)

		group_names = [
			getattr(cls, name, None)
			for name in ("group_keep", "group_disable")
		]
		group_names.extend(
			f"{company} Shifts"
			for company in (
				getattr(cls, "company", None),
				getattr(cls, "single_company", None),
				getattr(cls, "open_company", None),
				getattr(cls, "warehouse_company", None),
			)
			if company
		)
		for group in filter(None, group_names):
			frappe.db.delete("POS Profile Group Member", {"parent": group})
			frappe.db.delete("POS Shift Slot", {"parent": group})
			frappe.db.delete("POS Profile Group", {"name": group})

		def _safe(step):
			try:
				step()
			except Exception:
				pass

		if getattr(cls, "cashier", None) and frappe.db.exists("User", cls.cashier):
			_safe(lambda: frappe.delete_doc("User", cls.cashier, force=1, ignore_permissions=True))
			_safe(lambda: frappe.db.delete("User", {"name": cls.cashier}))
		for profile in flat_profiles:
			if frappe.db.exists("POS Profile", profile):
				_safe(lambda p=profile: frappe.delete_doc("POS Profile", p, force=1, ignore_permissions=True))
		for warehouse in getattr(cls, "warehouses", []):
			if frappe.db.exists("Warehouse", warehouse):
				_safe(lambda w=warehouse: frappe.delete_doc("Warehouse", w, force=1, ignore_permissions=True))
		for company in (
			getattr(cls, "company", None),
			getattr(cls, "single_company", None),
			getattr(cls, "open_company", None),
			getattr(cls, "warehouse_company", None),
		):
			if company and frappe.db.exists("Company", company):
				_safe(lambda c=company: frappe.delete_doc("Company", c, force=1, ignore_permissions=True))
		frappe.db.commit()
		super().tearDownClass()

	def test_merges_same_warehouse_profiles_and_is_idempotent(self):
		result = merge_company(self.company)

		self.assertEqual(result["status"], "merged")
		self.assertEqual(result["kept"], self.keep)
		self.assertCountEqual(result["disabled"], [self.disable, self.fallback])
		self.assertEqual(result["group"], f"{self.company} Shifts")
		self.assertEqual(frappe.db.get_value("POS Profile", self.keep, "disabled"), 0)
		for profile in (self.disable, self.fallback):
			self.assertEqual(frappe.db.get_value("POS Profile", profile, "disabled"), 1)
			self.assertFalse(frappe.db.get_value("POS Profile", profile, "pos_profile_group"))
		self.assertEqual(
			frappe.db.get_value("POS Profile", self.keep, "pos_profile_group"), result["group"]
		)

		group = frappe.get_doc("POS Profile Group", result["group"])
		self.assertEqual([row.pos_profile for row in group.profiles], [self.keep])
		slots = {
			row.shift_name: (_format_time(row.start_time), _format_time(row.end_time))
			for row in group.shifts
		}
		self.assertEqual(slots[self.group_keep], ("05:00:00", "15:00:00"))
		self.assertEqual(slots[self.group_disable], ("13:00:00", "21:00:00"))
		self.assertEqual(slots[self.fallback], ("21:00:00", "05:00:00"))

		users = frappe.get_all(
			"POS Profile User", filters={"parent": self.keep}, pluck="user", order_by="user asc"
		)
		self.assertCountEqual(users, [ADMIN, self.cashier])
		self.assertEqual(len(users), len(set(users)))
		self.assertEqual(merge_company(self.company)["status"], "noop")

	def test_single_scheduled_profile_gets_one_slot_group(self):
		result = merge_company(self.single_company)

		self.assertEqual(result["status"], "single")
		self.assertEqual(result["kept"], self.single)
		self.assertEqual(result["disabled"], [])
		self.assertEqual(result["group"], f"{self.single_company} Shifts")
		group = frappe.get_doc("POS Profile Group", result["group"])
		self.assertEqual(len(group.shifts), 1)
		self.assertEqual(group.shifts[0].shift_name, self.single)
		self.assertEqual(_format_time(group.shifts[0].start_time), "05:00:00")
		self.assertEqual(_format_time(group.shifts[0].end_time), "15:00:00")

	def test_open_shift_skips_company_and_logs_error(self):
		with patch("pos_next.patches.v2_26_0.merge_shift_profiles.frappe.log_error") as log_error:
			result = merge_company(self.open_company)

		self.assertEqual(result["status"], "skipped_open_shift")
		self.assertIsNone(result["kept"])
		self.assertEqual(result["disabled"], [])
		self.assertIsNone(result["group"])
		log_error.assert_called_once()
		self.assertEqual(
			frappe.db.get_value("POS Profile", self.open_profiles[0], "disabled"), 0
		)
		self.assertFalse(frappe.db.exists("POS Profile Group", f"{self.open_company} Shifts"))

	def test_profiles_with_different_warehouses_are_untouched(self):
		result = merge_company(self.warehouse_company)

		self.assertEqual(result["status"], "noop")
		for profile in self.different_warehouse_profiles:
			self.assertEqual(frappe.db.get_value("POS Profile", profile, "disabled"), 0)
			self.assertFalse(frappe.db.get_value("POS Profile", profile, "pos_profile_group"))
		self.assertFalse(frappe.db.exists("POS Profile Group", f"{self.warehouse_company} Shifts"))

	def test_rerun_merged_company_with_open_shift_returns_noop_and_does_not_log_error(self):
		merge_company(self.company)
		self._make_opening_shift_rows(self.keep, 1, active=True)
		self.addCleanup(
			frappe.db.delete,
			"POS Opening Shift",
			{"pos_profile": self.keep, "status": "Open"},
		)
		with patch("pos_next.patches.v2_26_0.merge_shift_profiles.frappe.log_error") as log_error:
			result = merge_company(self.company)

		self.assertEqual(result["status"], "noop")
		self.assertIsNone(result["kept"])
		self.assertEqual(result["disabled"], [])
		self.assertIsNone(result["group"])
		log_error.assert_not_called()


if __name__ == "__main__":
	unittest.main()
