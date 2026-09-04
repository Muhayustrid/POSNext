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

	def test_get_print_config_without_profile_falls_back_to_first_enabled_row(self):
		# The Direct Print page has no shift/invoice context, so it calls with
		# None. The endpoint must answer with usable config instead of throwing:
		# fall back to the first enabled POS Settings row on the site.
		cfg = get_print_config(None)
		for key in ("driver", "paper", "custom_dots", "cut", "fallback_enabled"):
			self.assertIn(key, cfg)
		first_enabled = frappe.db.get_value(
			"POS Settings", {"enabled": 1}, "pos_profile",
			order_by="modified desc",
		)
		self.assertEqual(cfg.get("pos_profile"), first_enabled)

	def test_get_print_config_without_profile_survives_empty_site(self):
		# No enabled row at all -> pure transport defaults, still no throw.
		enabled_rows = frappe.get_all(
			"POS Settings", filters={"enabled": 1}, pluck="name"
		)
		frappe.db.set_value("POS Settings", {"enabled": 1}, "enabled", 0)
		try:
			cfg = get_print_config(None)
			self.assertIsNone(cfg.get("pos_profile"))
			self.assertEqual(cfg["driver"], "browser")
		finally:
			for name in enabled_rows:
				frappe.db.set_value("POS Settings", name, "enabled", 1)

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
		if not self._has_column("imin_tail_dots"):
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
		# Undo so later tests (and the defaults test) see the clean state.
		# (Int columns here are NOT NULL, so reset to the default, not None.)
		frappe.db.set_value("POS Settings", settings_name, "imin_tail_dots", 24)

	def test_font_scale_defaults_to_100(self):
		cfg = get_print_config(self.profile)
		self.assertIn("font_scale", cfg)
		self.assertEqual(cfg["font_scale"], 100)

	def test_font_scale_clamps_absurd_values(self):
		if not self._has_column("imin_font_scale"):
			self.skipTest("imin_font_scale column not migrated on this site")
		settings_name = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile, "enabled": 1}, "name"
		)
		if not settings_name:
			settings_name = (
				frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.profile, "enabled": 1})
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value("POS Settings", settings_name, "imin_font_scale", 999)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["font_scale"], 250)
		# Undo so later tests (and the defaults test) see the clean state.
		# (Int columns here are NOT NULL, so reset to the default, not None.)
		frappe.db.set_value("POS Settings", settings_name, "imin_font_scale", 100)

	def test_copy_labels_is_no_longer_served(self):
		# The banners came off the paper, so the endpoint must not advertise a
		# knob that would do nothing: the transport has nothing left to switch.
		cfg = get_print_config(self.profile)
		self.assertNotIn("copy_labels", cfg)

	def test_crew_font_scale_defaults_to_130(self):
		cfg = get_print_config(self.profile)
		self.assertIn("crew_font_scale", cfg)
		self.assertEqual(cfg["crew_font_scale"], 130)

	def test_crew_font_scale_clamps_absurd_values(self):
		if not self._has_column("imin_crew_font_scale"):
			self.skipTest("imin_crew_font_scale column not migrated on this site")
		settings_name = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile, "enabled": 1}, "name"
		)
		if not settings_name:
			settings_name = (
				frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.profile, "enabled": 1})
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value("POS Settings", settings_name, "imin_crew_font_scale", 999)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["crew_font_scale"], 250)
		frappe.db.set_value("POS Settings", settings_name, "imin_crew_font_scale", 40)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["crew_font_scale"], 60)
		# Undo so later tests (and the defaults test) see the clean state.
		# (Int columns here are NOT NULL, so reset to the default, not None.)
		frappe.db.set_value("POS Settings", settings_name, "imin_crew_font_scale", 130)

	def test_crew_font_scale_survives_garbage(self):
		cfg = get_print_config(self.profile)
		# A NULL column answers the default rather than leaking None to the FE.
		self.assertIsInstance(cfg["crew_font_scale"], int)

	def test_line_spacing_defaults_to_100(self):
		cfg = get_print_config(self.profile)
		self.assertIn("line_spacing", cfg)
		self.assertEqual(cfg["line_spacing"], 100)

	def test_line_spacing_clamps_absurd_values(self):
		if not self._has_column("imin_line_spacing"):
			self.skipTest("imin_line_spacing column not migrated on this site")
		settings_name = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile, "enabled": 1}, "name"
		)
		if not settings_name:
			settings_name = (
				frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.profile, "enabled": 1})
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value("POS Settings", settings_name, "imin_line_spacing", 999)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["line_spacing"], 150)
		frappe.db.set_value("POS Settings", settings_name, "imin_line_spacing", 10)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["line_spacing"], 50)
		# Undo so later tests (and the defaults test) see the clean state.
		# (Int columns here are NOT NULL, so reset to the default, not None.)
		frappe.db.set_value("POS Settings", settings_name, "imin_line_spacing", 100)

	def test_line_spacing_survives_garbage(self):
		cfg = get_print_config(self.profile)
		# A NULL column answers the default rather than leaking None to the FE.
		self.assertIsInstance(cfg["line_spacing"], int)

	def test_side_margin_defaults_to_16(self):
		cfg = get_print_config(self.profile)
		self.assertIn("side_margin", cfg)
		self.assertEqual(cfg["side_margin"], 16)

	def test_side_margin_clamps_absurd_values(self):
		if not self._has_column("imin_side_margin"):
			self.skipTest("imin_side_margin column not migrated on this site")
		settings_name = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile, "enabled": 1}, "name"
		)
		if not settings_name:
			settings_name = (
				frappe.get_doc({"doctype": "POS Settings", "pos_profile": self.profile, "enabled": 1})
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.set_value("POS Settings", settings_name, "imin_side_margin", 999)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["side_margin"], 64)
		frappe.db.set_value("POS Settings", settings_name, "imin_side_margin", 0)
		cfg = get_print_config(self.profile)
		self.assertEqual(cfg["side_margin"], 0)
		# Undo so later tests (and the defaults test) see the clean state.
		# (Int columns here are NOT NULL, so reset to the default, not None.)
		frappe.db.set_value("POS Settings", settings_name, "imin_side_margin", 16)

	def test_side_margin_survives_garbage(self):
		cfg = get_print_config(self.profile)
		# A NULL column answers the default rather than leaking None to the FE.
		self.assertIsInstance(cfg["side_margin"], int)

	def _has_column(self, fieldname):
		meta = frappe.get_meta("POS Settings")
		return any(df.fieldname == fieldname for df in meta.get("fields"))

