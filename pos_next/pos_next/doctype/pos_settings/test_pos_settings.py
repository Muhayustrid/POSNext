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

	def test_non_manager_cannot_change_sales_management_fields(self):
		from unittest.mock import patch

		from pos_next.pos_next.doctype.pos_settings import pos_settings as mod

		name = frappe.db.get_value("POS Settings", {"enabled": 1}, "name")
		if not name:
			self.skipTest("no POS Settings on this site")
		doc = frappe.get_doc("POS Settings", name)
		before = doc.max_discount_allowed
		with patch.object(mod, "is_management_user", return_value=False):
			mod.update_pos_settings(doc.pos_profile, {"max_discount_allowed": before + 7})
		self.assertEqual(frappe.db.get_value("POS Settings", name, "max_discount_allowed"), before)
		frappe.db.rollback()

	def test_get_pos_settings_includes_automatic_stock_sync(self):
		profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not profile:
			self.skipTest("no POS Profile on this site")
		self.assertIn(get_pos_settings(profile).get("enable_automatic_stock_sync"), (0, 1))
