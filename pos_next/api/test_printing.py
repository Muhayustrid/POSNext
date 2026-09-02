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

	def _has_fallback_column(self):
		# The Task 7 print fields may not be migrated on every site; the API
		# guards on meta, and tests that write those columns must too.
		meta = frappe.get_meta("POS Settings")
		return any(df.fieldname == "print_fallback_enabled" for df in meta.get("fields"))

	def test_fallback_enabled_defaults_to_true_when_unset(self):
		# print_fallback_enabled defaults to 1 on the doctype. When no POS
		# Settings row exists for the profile, or the column is NULL, the
		# transport must still get fallback_enabled=True — a False there
		# silently disables the whole fallback chain.
		settings_name = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile, "enabled": 1}, "name"
		)
		if settings_name and self._has_fallback_column():
			frappe.db.set_value("POS Settings", settings_name, "print_fallback_enabled", None)

		cfg = get_print_config(self.profile)
		self.assertTrue(cfg["fallback_enabled"])

	def test_fallback_enabled_false_only_when_explicit(self):
		# Only meaningful when the real column exists — on unmigrated sites
		# the meta guard makes the field unreachable (skipped, not failed).
		if not self._has_fallback_column():
			self.skipTest("print_fallback_enabled column not migrated on this site")

		settings_name = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile, "enabled": 1}, "name"
		)
		if not settings_name:
			settings_name = (
				frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.profile, "enabled": 1})
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value("POS Settings", settings_name, "print_fallback_enabled", 0)

		cfg = get_print_config(self.profile)
		self.assertFalse(cfg["fallback_enabled"])

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
	def test_tail_dots_defaults_sensibly(self):
		cfg = get_print_config(self.profile)
		self.assertIn("tail_dots", cfg)
		self.assertGreater(cfg["tail_dots"], 0)
		self.assertLessEqual(cfg["tail_dots"], 200)

	def test_tail_dots_clamps_large_values(self):
		if not self._has_feed_or_tail_column("imin_tail_dots"):
			self.skipTest("imin_tail_dots column not migrated on this site")
		settings_name = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile, "enabled": 1}, "name"
		)
		if not settings_name:
			settings_name = (
				frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.profile, "enabled": 1})
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value("POS Settings", settings_name, "imin_tail_dots", 999)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["tail_dots"], 200)

	def test_copy_labels_defaults_to_true(self):
		cfg = get_print_config(self.profile)
		self.assertIn("copy_labels", cfg)
		self.assertTrue(cfg["copy_labels"])

	def _has_feed_or_tail_column(self, fieldname):
		meta = frappe.get_meta("POS Settings")
		return any(df.fieldname == fieldname for df in meta.get("fields"))

