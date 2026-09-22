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
