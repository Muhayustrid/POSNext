# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestProductionDoctypes(FrappeTestCase):
	def test_doctypes_and_key_fields_exist(self):
		for doctype, fields in [
			("POS Production Recipe", ["recipe_name", "production_item", "output_qty", "disabled", "items", "companies"]),
			("POS Production Recipe Item", ["item_code", "qty"]),
			("POS Production Recipe Company", ["company", "enabled"]),
			("POS Production Log", ["recipe", "production_item", "qty", "items_used", "stock_entry", "pos_profile", "company"]),
		]:
			meta = frappe.get_meta(doctype)
			for fieldname in fields:
				self.assertTrue(meta.has_field(fieldname), f"{doctype} missing {fieldname}")

	def test_log_is_submittable(self):
		self.assertTrue(frappe.get_meta("POS Production Log").is_submittable)
