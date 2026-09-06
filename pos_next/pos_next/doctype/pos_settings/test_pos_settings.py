# Copyright (c) 2025, Youssef Restom and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.pos_next.doctype.pos_settings.pos_settings import get_pos_settings


class TestPOSSettings(FrappeTestCase):
	def test_get_pos_settings_includes_queue_enabled(self):
		profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not profile:
			self.skipTest("no POS Profile on this site")
		settings = get_pos_settings(profile)
		self.assertIn("queue_enabled", settings)
		self.assertIsInstance(settings["queue_enabled"], bool)
