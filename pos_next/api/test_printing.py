# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.printing import get_print_config, get_print_logs, log_print_attempt


class TestPrintingAPI(FrappeTestCase):
	def setUp(self):
		self.profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.profile:
			self.skipTest("no POS Profile on this site")

	def test_get_print_config_returns_transport_keys(self):
		cfg = get_print_config(self.profile)
		# get_print_config returns mapped transport keys, not the raw POS
		# Settings field names.
		for key in ("driver", "paper", "custom_dots", "cut", "fallback_enabled"):
			self.assertIn(key, cfg)
		self.assertIn(cfg["driver"], ("imin", "qz", "browser"))
		self.assertIn(cfg["paper"], ("58mm", "80mm", "custom"))

	def test_get_print_config_requires_profile(self):
		with self.assertRaises(frappe.ValidationError):
			get_print_config(None)

	def test_log_print_attempt_creates_row(self):
		before = frappe.db.count("POS Print Log")
		name = log_print_attempt(
			reference_doctype="Sales Invoice",
			reference_name="ACC-SINV-TEST",
			driver="imin",
			status="Success",
			paper_width="58mm",
		)
		self.assertEqual(frappe.db.count("POS Print Log"), before + 1)

		row = frappe.db.get_value(
			"POS Print Log",
			name,
			["reference_doctype", "reference_name", "driver", "status", "paper_width"],
			as_dict=True,
		)
		self.assertEqual(row["reference_doctype"], "Sales Invoice")
		self.assertEqual(row["reference_name"], "ACC-SINV-TEST")
		self.assertEqual(row["driver"], "imin")
		self.assertEqual(row["status"], "Success")
		self.assertEqual(row["paper_width"], "58mm")

	def test_log_print_attempt_ignores_unmapped_keys(self):
		before = frappe.db.count("POS Print Log")
		log_print_attempt(
			reference_doctype="Sales Invoice",
			reference_name="ACC-SINV-TEST",
			driver="qz",
			status="Failed",
			some_unknown_kwarg="should-be-dropped",
		)
		# The unknown key must not reach the document / column set, and the
		# insert must still succeed with exactly one new row.
		self.assertEqual(frappe.db.count("POS Print Log"), before + 1)

	def test_get_print_logs_filters_by_reference(self):
		log_print_attempt(
			reference_doctype="Sales Invoice",
			reference_name="ACC-SINV-LIST",
			driver="browser",
			status="Success",
		)
		rows = get_print_logs(reference_name="ACC-SINV-LIST")
		self.assertTrue(any(r["reference_name"] == "ACC-SINV-LIST" for r in rows))
