# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.core.api.file import get_max_file_size
from frappe.tests.utils import FrappeTestCase

from pos_next.outlet_monitoring_setup import REVIEWER_ROLE, ensure_outlet_monitoring_setup


class TestOutletMonitoringSetup(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self._original_settings = frappe.db.get_singles_dict("POS Monitoring Settings")
		self.addCleanup(self._restore_settings)

	def _restore_settings(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": "POS Monitoring Settings"})
		frappe.db.set_single_value("POS Monitoring Settings", self._original_settings, update_modified=False)

	def tearDown(self):
		self._restore_settings()
		frappe.db.commit()
		super().tearDown()

	def test_reviewer_role_created_idempotently(self):
		ensure_outlet_monitoring_setup()
		ensure_outlet_monitoring_setup()
		self.assertEqual(frappe.db.count("Role", {"name": REVIEWER_ROLE}), 1)

	def test_settings_defaults(self):
		meta = frappe.get_meta("POS Monitoring Settings")
		field_defaults = {df.fieldname: df.default for df in meta.fields if df.fieldname}
		self.assertEqual(int(field_defaults.get("enabled", 0)), 0)
		self.assertEqual(int(field_defaults.get("max_revisions", 0)), 3)
		self.assertEqual(int(field_defaults.get("shift_missed_grace_hours", 0)), 12)
		self.assertEqual(int(field_defaults.get("video_max_mb", 0)), 20)
		self.assertEqual(int(field_defaults.get("video_max_seconds", 0)), 30)
		self.assertEqual(int(field_defaults.get("media_retention_days", 0)), 90)
		self.assertEqual(int(field_defaults.get("gallery_page_size", 0)), 40)
		self.assertIn(str(field_defaults.get("day_missed_next_day_time", "")), ("2:00:00", "02:00:00"))
		self.assertEqual(field_defaults.get("default_pass_label"), "Sesuai SOP")

	def test_settings_rejects_video_above_site_limit(self):
		s = frappe.get_single("POS Monitoring Settings")
		original_mb = s.video_max_mb
		self.addCleanup(frappe.db.set_value, "POS Monitoring Settings", None, "video_max_mb", original_mb)

		max_bytes = get_max_file_size()
		too_large_mb = (max_bytes / (1024 * 1024)) + 10
		s.video_max_mb = too_large_mb
		self.assertRaises(frappe.ValidationError, s.save)

	def test_settings_rejects_thumbnail_not_smaller_than_photo(self):
		s = frappe.get_single("POS Monitoring Settings")
		original_thumb = s.thumbnail_max_edge
		original_photo = s.photo_max_edge
		self.addCleanup(
			frappe.db.set_value,
			"POS Monitoring Settings",
			None,
			{"thumbnail_max_edge": original_thumb, "photo_max_edge": original_photo},
		)

		s.photo_max_edge = 800
		s.thumbnail_max_edge = 800
		self.assertRaises(frappe.ValidationError, s.save)

		s.thumbnail_max_edge = 900
		self.assertRaises(frappe.ValidationError, s.save)

	def test_settings_rejects_gallery_page_size_out_of_10_200(self):
		s = frappe.get_single("POS Monitoring Settings")
		original_size = s.gallery_page_size
		self.addCleanup(frappe.db.set_value, "POS Monitoring Settings", None, "gallery_page_size", original_size)

		s.gallery_page_size = 5
		self.assertRaises(frappe.ValidationError, s.save)

		s.gallery_page_size = 205
		self.assertRaises(frappe.ValidationError, s.save)

	def test_settings_rejects_zero_or_negative_operational_limits(self):
		fields_requiring_positive = (
			"photo_max_edge",
			"thumbnail_max_edge",
			"photo_max_mb",
			"video_max_mb",
			"video_max_seconds",
			"max_revisions",
			"shift_missed_grace_hours",
		)
		for field in fields_requiring_positive:
			s = frappe.get_single("POS Monitoring Settings")
			setattr(s, field, 0)
			with self.assertRaises(frappe.ValidationError, msg=f"{field}=0 should be rejected"):
				s.save()

	def test_settings_rejects_photo_quality_out_of_range(self):
		s = frappe.get_single("POS Monitoring Settings")
		orig_val = s.photo_quality
		self.addCleanup(frappe.db.set_value, "POS Monitoring Settings", None, "photo_quality", orig_val)

		s.photo_quality = 0
		self.assertRaises(frappe.ValidationError, s.save)

		s.photo_quality = 101
		self.assertRaises(frappe.ValidationError, s.save)

		s.photo_quality = -5
		self.assertRaises(frappe.ValidationError, s.save)

	def test_checkpoint_rejects_max_below_min(self):
		# Ensure test category
		if not frappe.db.exists("POS Monitoring Category", "Test Category"):
			frappe.get_doc({
				"doctype": "POS Monitoring Category",
				"category_name": "Test Category",
			}).insert(ignore_permissions=True)

		cp = frappe.new_doc("POS Monitoring Checkpoint")
		cp.checkpoint_name = "Test CP Max Below Min"
		cp.category = "Test Category"
		cp.media_type = "Photo"
		cp.min_count = 3
		cp.max_count = 2
		self.assertRaises(frappe.ValidationError, cp.save)

	def test_template_rejects_duplicate_checkpoint_and_empty_checkpoints(self):
		# Empty checkpoints
		t = frappe.new_doc("POS Monitoring Template")
		t.template_name = "Test Empty Checkpoints Template"
		t.cadence = "Per Shift"
		t.due_minutes_after_open = 30
		self.assertRaises(frappe.ValidationError, t.save)

		# Ensure test checkpoints
		for cp_name in ("CP 1", "CP 2"):
			if not frappe.db.exists("POS Monitoring Checkpoint", cp_name):
				if not frappe.db.exists("POS Monitoring Category", "Test Category"):
					frappe.get_doc({
						"doctype": "POS Monitoring Category",
						"category_name": "Test Category",
					}).insert(ignore_permissions=True)
				frappe.get_doc({
					"doctype": "POS Monitoring Checkpoint",
					"checkpoint_name": cp_name,
					"category": "Test Category",
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
				}).insert(ignore_permissions=True)

		# Duplicate checkpoints
		t_dup = frappe.new_doc("POS Monitoring Template")
		t_dup.template_name = "Test Duplicate Checkpoints Template"
		t_dup.cadence = "Per Shift"
		t_dup.due_minutes_after_open = 30
		t_dup.append("checkpoints", {"checkpoint": "CP 1", "required": 1})
		t_dup.append("checkpoints", {"checkpoint": "CP 1", "required": 0})
		self.assertRaises(frappe.ValidationError, t_dup.save)

	def test_template_per_shift_requires_due_minutes_per_day_requires_due_time(self):
		if not frappe.db.exists("POS Monitoring Checkpoint", "CP 1"):
			if not frappe.db.exists("POS Monitoring Category", "Test Category"):
				frappe.get_doc({
					"doctype": "POS Monitoring Category",
					"category_name": "Test Category",
				}).insert(ignore_permissions=True)
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": "CP 1",
				"category": "Test Category",
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		# Per Shift without due_minutes_after_open
		t_shift = frappe.new_doc("POS Monitoring Template")
		t_shift.template_name = "Test Per Shift Due Template"
		t_shift.cadence = "Per Shift"
		t_shift.due_minutes_after_open = 0
		t_shift.append("checkpoints", {"checkpoint": "CP 1", "required": 1})
		self.assertRaises(frappe.ValidationError, t_shift.save)

		# Per Hari without due_time
		t_day = frappe.new_doc("POS Monitoring Template")
		t_day.template_name = "Test Per Day Due Template"
		t_day.cadence = "Per Hari"
		t_day.due_time = None
		t_day.append("checkpoints", {"checkpoint": "CP 1", "required": 1})
		self.assertRaises(frappe.ValidationError, t_day.save)
