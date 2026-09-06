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

	def test_counter_rolls_to_new_date(self):
		get_next_queue_number(self.profile)
		# Backdate the counter row; the next number for today starts at 1 again.
		name = frappe.db.get_value("POS Queue Counter", {"company": self.company}, "name")
		frappe.db.set_value("POS Queue Counter", name, "date", "2000-01-01")
		c = get_next_queue_number(self.profile)
		self.assertEqual(c["queue_number"], 1)
