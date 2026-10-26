# Copyright (c) 2026, Youssef Restom and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint


class TestPOSNextGlobalSettings(FrappeTestCase):
	def test_allow_negative_stock_bridges_to_stock_settings(self):
		original = cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock") or 0)
		try:
			for value in (1, 0):
				doc = frappe.get_doc("POS Next Global Settings")
				doc.allow_negative_stock = value
				doc.save(ignore_permissions=True)
				self.assertEqual(
					cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock") or 0),
					value,
				)
		finally:
			if original != cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock") or 0):
				frappe.db.set_single_value("Stock Settings", "allow_negative_stock", original)
		frappe.db.commit()

	def test_block_out_of_stock_items_in_pos_settings_payload(self):
		from pos_next.api.pos_profile import get_pos_settings as get_profile_settings
		from pos_next.pos_next.doctype.pos_settings.pos_settings import get_pos_settings

		profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name") or "test_profile"
		original = cint(frappe.db.get_single_value("POS Next Global Settings", "block_out_of_stock_items"))
		try:
			for value in (0, 1):
				frappe.db.set_single_value("POS Next Global Settings", "block_out_of_stock_items", value)
				for fetcher in (get_pos_settings, get_profile_settings):
					settings = fetcher(profile)
					self.assertIn("block_out_of_stock_items", settings)
					self.assertEqual(settings["block_out_of_stock_items"], value)
		finally:
			frappe.db.set_single_value("POS Next Global Settings", "block_out_of_stock_items", original)
		frappe.db.commit()
