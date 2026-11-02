import unittest

import frappe


class TestSpendSetup(unittest.TestCase):
	def test_spend_fields_exist(self):
		fields = (
			"posa_client_request_id",
			"posa_spend_template",
			"posa_pos_profile",
			"posa_pos_opening_shift",
			"posa_shift_account",
			"posa_vendor_name",
			"posa_spend_status",
			"posa_correction_of",
			"posa_correction_of_type",
		)
		for dt in ("Purchase Invoice", "Journal Entry", "Payment Entry"):
			for field in fields:
				with self.subTest(doctype=dt, field=field):
					df = frappe.get_meta(dt).get_field(field)
					self.assertIsNotNone(df)
					self.assertEqual(df.no_copy, 1)
					self.assertEqual(df.read_only, 1)

	def test_spend_fields_follow_collapsible_section(self):
		for dt in ("Purchase Invoice", "Journal Entry", "Payment Entry"):
			meta = frappe.get_meta(dt)
			fields = [df.fieldname for df in meta.fields]
			section = meta.get_field("posa_spend_section")
			self.assertEqual(section.collapsible, 1)
			self.assertLess(fields.index(section.fieldname), fields.index("posa_client_request_id"))
			self.assertEqual(meta.get_field("posa_client_request_id").length, 64)

	def test_request_id_unique_index(self):
		for dt in ("Purchase Invoice", "Journal Entry", "Payment Entry"):
			with self.subTest(doctype=dt):
				self.assertTrue(
					frappe.db.sql(
						f"SHOW INDEX FROM `tab{dt}` WHERE Column_name='posa_client_request_id' AND Non_unique=0"
					)
				)

	def test_walk_in_flag_on_supplier(self):
		df = frappe.get_meta("Supplier").get_field("posa_is_walk_in_supplier")
		self.assertIsNotNone(df)
		self.assertEqual(df.fieldtype, "Check")
		self.assertEqual(df.no_copy, 1)
