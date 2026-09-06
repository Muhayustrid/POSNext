# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.queue import get_next_queue_number


class TestQueueAPI(FrappeTestCase):
	def setUp(self):
		profile = frappe.db.get_value(
			"POS Profile", {"disabled": 0}, ["name", "company"], as_dict=True
		)
		if not profile:
			self.skipTest("no POS Profile on this site")
		self.profile = profile.name
		self.company = profile.company
		self._original_enabled = frappe.db.get_value(
			"Company", self.company, "enable_pos_queue"
		)
		frappe.db.set_value("Company", self.company, "enable_pos_queue", 1)
		frappe.db.delete("POS Queue Counter", {"company": self.company})
		self.other_company = None

	def tearDown(self):
		# The API commits on purpose, so the counter rows and the enabled flag
		# are already durable: undo uncommitted test mutations first, then
		# clean up in its own transaction (the framework rolls back after
		# tearDown, so an uncommitted cleanup would itself be rolled back).
		frappe.db.rollback()
		frappe.db.delete("POS Queue Counter", {"company": self.company})
		frappe.db.set_value(
			"Company", self.company, "enable_pos_queue", self._original_enabled or 0
		)
		if self.other_company:
			frappe.db.delete("POS Queue Counter", {"company": self.other_company})
			frappe.db.set_value(
				"Company",
				self.other_company,
				"enable_pos_queue",
				self._other_original_enabled or 0,
			)
		frappe.db.commit()

	def test_disabled_company_gets_no_number(self):
		frappe.db.set_value("Company", self.company, "enable_pos_queue", 0)
		out = get_next_queue_number(self.profile)
		self.assertFalse(out["enabled"])
		self.assertNotIn("queue_number", out)

	def test_missing_profile_throws(self):
		self.assertRaises(frappe.exceptions.ValidationError, get_next_queue_number, "NOPE")

	def test_numbers_increment_per_day(self):
		a = get_next_queue_number(self.profile)
		b = get_next_queue_number(self.profile)
		self.assertTrue(a["enabled"])
		self.assertEqual(a["company"], self.company)
		self.assertEqual(a["date"], nowdate())
		self.assertEqual(b["queue_number"], a["queue_number"] + 1)
		self.assertEqual(a["queue_number"], 1)  # setUp cleared the counter

	def test_separate_companies_do_not_share(self):
		other = frappe.db.get_value(
			"POS Profile",
			{"disabled": 0, "company": ("!=", self.company)},
			["name", "company"],
			as_dict=True,
		)
		if not other:
			self.skipTest("site has no second POS Profile on another company")
		self.other_company = other.company
		self._other_original_enabled = frappe.db.get_value(
			"Company", other.company, "enable_pos_queue"
		)
		frappe.db.set_value("Company", other.company, "enable_pos_queue", 1)
		frappe.db.delete("POS Queue Counter", {"company": other.company})

		a = get_next_queue_number(self.profile)
		b = get_next_queue_number(other.name)
		self.assertTrue(b["enabled"])
		self.assertEqual(b["company"], other.company)
		# Each company's counter starts from its own row.
		self.assertEqual(a["queue_number"], 1)
		self.assertEqual(b["queue_number"], 1)

	def test_submit_bumps_counter_to_printed_number(self):
		from pos_next.overrides.queue_counter import bump_queue_counter

		# struk offline menyimpan nomor 7 tanpa server tahu
		frappe.get_doc({
			"doctype": "POS Queue Counter",
			"company": self.company,
			"date": nowdate(),
			"current_number": 2,
		}).insert(ignore_permissions=True)
		bump_queue_counter(
			frappe._dict(
				company=self.company,
				pos_queue_number=7,
				pos_queue_date=nowdate(),
			),
			None,
		)
		out = get_next_queue_number(self.profile)
		self.assertEqual(out["queue_number"], 8)

	def test_submit_lower_number_does_not_lower_counter(self):
		from pos_next.overrides.queue_counter import bump_queue_counter

		# counter 9, invoice sync bernomor 3 -> next tetap 10
		frappe.get_doc({
			"doctype": "POS Queue Counter",
			"company": self.company,
			"date": nowdate(),
			"current_number": 9,
		}).insert(ignore_permissions=True)
		bump_queue_counter(
			frappe._dict(
				company=self.company,
				pos_queue_number=3,
				pos_queue_date=nowdate(),
			),
			None,
		)
		out = get_next_queue_number(self.profile)
		self.assertEqual(out["queue_number"], 10)

	def test_bootstrap_settings_include_queue_enabled(self):
		from pos_next.api.bootstrap import _get_pos_settings

		profile = frappe.get_doc("POS Profile", self.profile)
		settings = _get_pos_settings(profile)
		self.assertIn("queue_enabled", settings)
		self.assertIsInstance(settings["queue_enabled"], bool)

	def test_lost_insert_race_retries(self):
		# First-day insert race: the first insert hits the DB unique index and
		# frappe's insert path surfaces it as DuplicateEntryError. The retry
		# must still allocate the day's first number instead of 500ing.
		# (Call counting is fragile — the insert path itself resolves docs via
		# frappe.get_doc — so assert the failure fired exactly once.)
		from unittest.mock import patch

		real_get_doc = frappe.get_doc
		simulated_failures = []

		def losing_first_insert(*args, **kwargs):
			if not simulated_failures:
				simulated_failures.append(1)
				raise frappe.exceptions.DuplicateEntryError
			return real_get_doc(*args, **kwargs)

		with patch("frappe.get_doc", side_effect=losing_first_insert):
			out = get_next_queue_number(self.profile)

		self.assertEqual(len(simulated_failures), 1)
		self.assertEqual(out["queue_number"], 1)

	def test_counter_rolls_to_new_date(self):
		get_next_queue_number(self.profile)
		# Backdate the counter row; the next number for today starts at 1 again.
		name = frappe.db.get_value("POS Queue Counter", {"company": self.company}, "name")
		frappe.db.set_value("POS Queue Counter", name, "date", "2000-01-01")
		c = get_next_queue_number(self.profile)
		self.assertEqual(c["queue_number"], 1)
