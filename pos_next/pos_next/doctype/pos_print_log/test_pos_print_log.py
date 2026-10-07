# Copyright (c) 2026, POS Next and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from pos_next.install import register_default_log_clearing
from pos_next.pos_next.doctype.pos_print_log.pos_print_log import POSPrintLog


class TestPOSPrintLog(FrappeTestCase):
	def setUp(self):
		self.created_logs = []

	def tearDown(self):
		for name in self.created_logs:
			if frappe.db.exists("POS Print Log", name):
				frappe.delete_doc("POS Print Log", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _create_log(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Print Log",
				"driver": "browser",
				"status": "Success",
			}
		).insert(ignore_permissions=True)
		self.created_logs.append(doc.name)
		return doc

	def test_clear_old_logs(self):
		old_log = self._create_log()
		new_log = self._create_log()

		past_date = add_to_date(now_datetime(), days=-100)
		frappe.db.set_value("POS Print Log", old_log.name, "creation", past_date, update_modified=False)
		frappe.db.commit()

		POSPrintLog.clear_old_logs(90)

		self.assertFalse(frappe.db.exists("POS Print Log", old_log.name))
		self.assertTrue(frappe.db.exists("POS Print Log", new_log.name))

	def test_log_settings_registration(self):
		register_default_log_clearing(quiet=True)
		log_settings = frappe.get_single("Log Settings")
		entries = [d for d in log_settings.logs_to_clear if d.ref_doctype == "POS Print Log"]
		self.assertEqual(len(entries), 1)
		self.assertEqual(entries[0].days, 90)

	def test_log_settings_preserves_custom_days(self):
		register_default_log_clearing(quiet=True)
		log_settings = frappe.get_single("Log Settings")
		for d in log_settings.logs_to_clear:
			if d.ref_doctype == "POS Print Log":
				d.days = 45
				break
		log_settings.save(ignore_permissions=True)
		frappe.db.commit()

		try:
			register_default_log_clearing(quiet=True)
			log_settings.reload()
			entries = [d for d in log_settings.logs_to_clear if d.ref_doctype == "POS Print Log"]
			self.assertEqual(len(entries), 1)
			self.assertEqual(entries[0].days, 45)
		finally:
			log_settings.reload()
			for d in log_settings.logs_to_clear:
				if d.ref_doctype == "POS Print Log":
					d.days = 90
					break
			log_settings.save(ignore_permissions=True)
			frappe.db.commit()
