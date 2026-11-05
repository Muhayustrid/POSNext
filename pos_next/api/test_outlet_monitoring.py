# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import os
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from pos_next.api import outlet_monitoring as api
from pos_next.services.outlet_monitoring import ensure_obligations, get_settings, mark_missed, obligation_key
from pos_next.services.outlet_monitoring_media import sniff_media

TINY_JPEG = (
	b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
	b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
	b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
	b"'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
	b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
	b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)

TINY_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41\x00\x00\x00\x08free"
TINY_MOV = b"\x00\x00\x00\x14ftypqt  \x00\x00\x02\x00qt  "
TINY_HEIC = b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic"
TINY_WEBM = b"\x1a\x45\xdf\xa3\x9f\x42\x86\x81\x01\x42\xf7\x81\x01"
TINY_GIF = b"GIF89a\x01\x00\x01\x00\x80\x00\x00\xff\xff\xff\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"


def _create_user(role, prefix="api-cashier"):
	email = f"{prefix}-{uuid.uuid4().hex[:8]}@example.com"
	was_in_import = frappe.flags.in_import
	frappe.flags.in_import = True
	try:
		user = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": prefix,
			"enabled": 1,
			"send_welcome_email": 0,
		})
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)
	finally:
		frappe.flags.in_import = was_in_import

	frappe.get_doc({
		"doctype": "Has Role",
		"parent": email,
		"parenttype": "User",
		"parentfield": "roles",
		"role": role,
	}).insert(ignore_permissions=True)
	frappe.clear_cache(user=email)
	return email


def _setup_profile(company="_Test Company"):
	profile_name = f"_Test API Prof {uuid.uuid4().hex[:6]}"
	warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	if not warehouse:
		warehouse = frappe.get_doc({
			"doctype": "Warehouse",
			"warehouse_name": f"WH-{uuid.uuid4().hex[:6]}",
			"company": company,
		}).insert(ignore_permissions=True).name

	profile = frappe.get_doc({
		"doctype": "POS Profile",
		"name": profile_name,
		"company": company,
		"warehouse": warehouse,
		"currency": "IDR",
		"write_off_account": frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name") or "Write Off - _TC",
		"write_off_cost_center": frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name") or "Main - _TC",
		"income_account": frappe.db.get_value("Account", {"company": company, "root_type": "Income", "is_group": 0}, "name") or "Sales - _TC",
		"payments": [{"mode_of_payment": "Cash", "default": 1}],
	})
	profile.insert(ignore_permissions=True)
	return profile_name


class TestOutletMonitoringApi(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial_reports = set(frappe.get_all("POS Monitoring Report", pluck="name"))
		cls._suite_settings = frappe.db.get_singles_dict("POS Monitoring Settings")

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		current_reports = set(frappe.get_all("POS Monitoring Report", pluck="name"))
		suite_created = current_reports - getattr(cls, "_suite_initial_reports", set())
		if suite_created:
			frappe.db.delete("POS Monitoring Report Media", {"parent": ["in", list(suite_created)]})
			frappe.db.delete("POS Monitoring Report Checkpoint", {"parent": ["in", list(suite_created)]})
			frappe.db.delete("POS Monitoring Report", {"name": ["in", list(suite_created)]})

		frappe.db.delete("Singles", {"doctype": "POS Monitoring Settings"})
		frappe.db.set_single_value("POS Monitoring Settings", cls._suite_settings, update_modified=False)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._original_settings = frappe.db.get_singles_dict("POS Monitoring Settings")
		self.addCleanup(self._restore_settings)
		self._test_initial_reports = set(frappe.get_all("POS Monitoring Report", pluck="name"))
		self._test_initial_files = set(frappe.get_all("File", pluck="name"))
		self.company = "_Test Company"
		self.profile = _setup_profile(self.company)
		self.cashier = _create_user("POSNext Cashier", "cashier")

		# Assign cashier to profile
		pdoc = frappe.get_doc("POS Profile", self.profile)
		pdoc.append("applicable_for_users", {"user": self.cashier})
		pdoc.save(ignore_permissions=True)

		# Ensure category & checkpoints
		if not frappe.db.exists("POS Monitoring Category", "API Category"):
			frappe.get_doc({
				"doctype": "POS Monitoring Category",
				"category_name": "API Category",
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", "API CP Photo"):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": "API CP Photo",
				"category": "API Category",
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", "API CP Video"):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": "API CP Video",
				"category": "API Category",
				"media_type": "Video",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		# Enable settings
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.allow_late_submission = 1
		s.allow_webm = 0
		s.photo_max_mb = 5.0
		s.save(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		current_reports = set(frappe.get_all("POS Monitoring Report", pluck="name"))
		new_reports = current_reports - getattr(self, "_test_initial_reports", set())
		if new_reports:
			frappe.db.delete("POS Monitoring Report Media", {"parent": ["in", list(new_reports)]})
			frappe.db.delete("POS Monitoring Report Checkpoint", {"parent": ["in", list(new_reports)]})
			frappe.db.delete("POS Monitoring Report", {"name": ["in", list(new_reports)]})

		current_files = set(frappe.get_all("File", pluck="name"))
		new_files = current_files - getattr(self, "_test_initial_files", set())
		for fn in new_files:
			try:
				frappe.flags.internal_monitoring_delete = True
				frappe.delete_doc("File", fn, force=True, ignore_permissions=True)
			except Exception:
				pass
			finally:
				frappe.flags.internal_monitoring_delete = False
		self._restore_settings()
		frappe.db.commit()
		super().tearDown()

	def _restore_settings(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": "POS Monitoring Settings"})
		frappe.db.set_single_value("POS Monitoring Settings", self._original_settings, update_modified=False)

	def _create_template(self, cadence="Per Shift", checkpoints=None):
		cps = checkpoints or ["API CP Photo"]
		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"Tmpl-API-{uuid.uuid4().hex[:6]}",
			"cadence": cadence,
			"enabled": 1,
			"due_minutes_after_open": 30,
			"due_time": "12:00:00" if cadence == "Per Hari" else None,
			"checkpoints": [{"checkpoint": cp, "required": 1} for cp in cps],
			"companies": [{"company": self.company}],
		})
		t.insert(ignore_permissions=True)
		return t

	def _open_shift(self, user=None):
		shift = frappe.get_doc({
			"doctype": "POS Opening Shift",
			"pos_profile": self.profile,
			"company": self.company,
			"user": user or self.cashier,
			"posting_date": frappe.utils.today(),
			"period_start_date": frappe.utils.now_datetime(),
			"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 100000}],
		})
		shift.insert(ignore_permissions=True)
		shift.submit()
		return shift

	def _attach_file(self, report_name, content, file_name="sample.jpg", is_private=1):
		if isinstance(content, (bytes, bytearray)) and content.startswith(b"\xff\xd8\xff"):
			content = bytes(content) + uuid.uuid4().bytes
		f = frappe.get_doc({
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": report_name,
			"is_private": is_private,
			"content": content,
		})
		f.insert(ignore_permissions=True)
		if hasattr(self, "cashier"):
			frappe.db.set_value("File", f.name, "owner", self.cashier, update_modified=False)
			f.owner = self.cashier
		return f

	def _get_shift_report(self, template_name, shift_name):
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"template": template_name, "opening_shift": shift_name},
		)
		return frappe.get_doc("POS Monitoring Report", rep_name)

	def test_native_save_rejects_duplicate_checkpoint_identities(self):
		from frappe.client import set_value

		t = self._create_template(checkpoints=["API CP Photo", "API CP Video"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(t.name, shift.name)
		original_names = {cp.name for cp in rep.checkpoints}
		frappe.set_user(self.cashier)
		row = rep.checkpoints[0].as_dict()
		with self.assertRaises(frappe.PermissionError):
			set_value(rep.doctype, rep.name, frappe.as_json({"checkpoints": [row, row]}))
		rep.reload()
		self.assertEqual({cp.name for cp in rep.checkpoints}, original_names)
		with self.assertRaises(frappe.ValidationError):
			api.submit_report(rep.name)

	def test_native_save_rejects_duplicate_media_identities(self):
		t = self._create_template()
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(t.name, shift.name)
		frappe.set_user(self.cashier)
		for _ in range(2):
			f = self._attach_file(rep.name, TINY_JPEG)
			api.register_media(rep.name, rep.checkpoints[0].name, f.name)
		rep.reload()
		rep.set("media", [rep.media[0].as_dict(), rep.media[0].as_dict()])
		with self.assertRaises(frappe.PermissionError):
			rep.save()

	def test_native_save_cannot_forge_checkpoint_evidence(self):
		t = self._create_template()
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(t.name, shift.name)
		frappe.set_user(self.cashier)
		rep.checkpoints[0].evidence_complete = 1
		with self.assertRaises(frappe.PermissionError):
			rep.save()

	def test_submit_checks_stored_snapshot_not_mutated_memory(self):
		t = self._create_template()
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(t.name, shift.name)
		frappe.set_user(self.cashier)
		with patch.object(api, "recompute_evidence", side_effect=lambda doc: doc.set("checkpoints", [])):
			with self.assertRaises(frappe.ValidationError):
				api.submit_report(rep.name)

	def test_cashier_json_roundtrip_noop_save(self):
		from frappe.client import save

		t = self._create_template(cadence="Per Hari")
		ensure_obligations(pos_profile=self.profile)
		name = frappe.db.get_value("POS Monitoring Report", {"template": t.name})
		rep = frappe.get_doc("POS Monitoring Report", name)
		frappe.set_user(self.cashier)
		self.assertEqual(save(frappe.as_json(rep.as_dict()))["name"], name)

	def test_sniff_media(self):
		self.assertEqual(sniff_media(TINY_JPEG[:16]), "jpeg")
		self.assertEqual(sniff_media(TINY_MP4[:16]), "mp4")
		self.assertEqual(sniff_media(TINY_WEBM[:16]), "webm")
		self.assertIsNone(sniff_media(TINY_MOV[:16]))
		self.assertIsNone(sniff_media(TINY_HEIC[:16]))
		self.assertIsNone(sniff_media(b"GIF89a1234567890"))

	def test_my_tasks_counts_obligations_not_checkpoints(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo", "API CP Video"])
		self._open_shift()
		frappe.set_user(self.cashier)
		res = api.get_my_tasks(self.profile)
		self.assertTrue(res["enabled"])
		self.assertEqual(res["count"], len(res["tasks"]))
		task = next(t for t in res["tasks"] if t["template"] == tmpl.name)
		self.assertEqual(task["required_total"], 2)
		self.assertEqual(task["required_done"], 0)

	def test_my_tasks_disabled_returns_flag(self):
		frappe.set_user("Administrator")
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 0
		s.save(ignore_permissions=True)

		frappe.set_user(self.cashier)
		res = api.get_my_tasks(self.profile)
		self.assertFalse(res["enabled"])
		self.assertEqual(res["count"], 0)
		self.assertEqual(res["tasks"], [])

	def test_my_tasks_excludes_missed(self):
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)

		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"template": tmpl.name, "opening_shift": shift.name},
		)
		# Mark Missed
		doc = frappe.get_doc("POS Monitoring Report", rep_name)
		doc.status = "Missed"
		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		doc.submit()

		frappe.set_user(self.cashier)
		res = api.get_my_tasks(self.profile)
		task_names = [t["name"] for t in res["tasks"]]
		self.assertNotIn(rep_name, task_names)

	def test_register_rejects_fake_jpeg(self):
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)

		# Create file with GIF content named .gif
		f = self._attach_file(rep.name, TINY_GIF, file_name="fake.gif")
		cp_row = rep.checkpoints[0].name

		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f.name)
		# Request rollback triggers after_rollback orphan file cleanup
		frappe.db.rollback()
		self.assertFalse(frappe.db.exists("File", f.name))

	def test_register_rejects_heic_and_mov(self):
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f_heic = self._attach_file(rep.name, TINY_HEIC, file_name="test.heic")
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f_heic.name)

		f_mov = self._attach_file(rep.name, TINY_MOV, file_name="test.mov")
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f_mov.name)

	def test_register_rejects_oversize(self):
		frappe.set_user("Administrator")
		s = frappe.get_single("POS Monitoring Settings")
		s.photo_max_mb = 0.0001
		s.save(ignore_permissions=True)

		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_JPEG, file_name="oversize.jpg")
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f.name)

	def test_register_rejects_wrong_type_for_checkpoint(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		# Upload MP4 video to Photo checkpoint
		f = self._attach_file(rep.name, TINY_MP4, file_name="video.mp4")
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f.name)

	def test_register_rejects_over_max_count(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])  # max_count 2
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f1 = self._attach_file(rep.name, TINY_JPEG, file_name="p1.jpg")
		f2 = self._attach_file(rep.name, TINY_JPEG, file_name="p2.jpg")
		f3 = self._attach_file(rep.name, TINY_JPEG, file_name="p3.jpg")

		frappe.set_user(self.cashier)
		api.register_media(rep.name, cp_row, f1.name)
		api.register_media(rep.name, cp_row, f2.name)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f3.name)

	def test_register_rejects_file_of_other_doc(self):
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		# File attached to a different doc
		f = self._attach_file("SomeOtherReportName", TINY_JPEG, file_name="other.jpg")
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f.name)

	def test_register_mixed_checkpoint_accepts_photo_and_video(self):
		if not frappe.db.exists("POS Monitoring Checkpoint", "API CP Mixed"):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": "API CP Mixed",
				"category": "API Category",
				"media_type": "Photo or Video",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		tmpl = self._create_template("Per Shift", checkpoints=["API CP Mixed"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f_photo = self._attach_file(rep.name, TINY_JPEG, file_name="mixed_p.jpg")
		f_video = self._attach_file(rep.name, TINY_MP4, file_name="mixed_v.mp4")

		frappe.set_user(self.cashier)
		res1 = api.register_media(rep.name, cp_row, f_photo.name)
		self.assertEqual(len(res1["media"]), 1)
		self.assertEqual(res1["media"][0]["media_type"], "Photo")

		res2 = api.register_media(rep.name, cp_row, f_video.name)
		self.assertEqual(len(res2["media"]), 2)
		self.assertEqual(res2["media"][1]["media_type"], "Video")

	def test_register_failure_deletes_file(self):
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_GIF, file_name="invalid.gif")
		file_id = f.name
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, file_id)
		frappe.db.rollback()
		self.assertFalse(frappe.db.exists("File", file_id))

	def test_register_failure_does_not_commit_and_rolls_back_partial_changes(self):
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_GIF, file_name="invalid_probe.gif")
		file_id = f.name
		frappe.db.commit()
		frappe.set_user(self.cashier)

		commit_calls = []
		orig_commit = frappe.db.commit

		def spy_commit(*args, **kwargs):
			commit_calls.append(True)
			return orig_commit(*args, **kwargs)

		with patch.object(frappe.db, "commit", side_effect=spy_commit):
			with self.assertRaises(frappe.ValidationError):
				api.register_media(rep.name, cp_row, file_id)

		# commit must NOT have been called during failed register_media request
		self.assertEqual(len(commit_calls), 0)

		# Now when request handler rolls back, after_rollback cleans up the orphan file
		frappe.db.rollback()
		self.assertFalse(frappe.db.exists("File", file_id))

	def test_register_crash_after_append_rolls_back_partial_changes(self):
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_JPEG, file_name="crash_test.jpg")
		file_id = f.name
		frappe.db.commit()
		frappe.set_user(self.cashier)

		commit_calls = []
		orig_commit = frappe.db.commit

		def spy_commit(*args, **kwargs):
			commit_calls.append(True)
			return orig_commit(*args, **kwargs)

		with patch.object(frappe.db, "commit", side_effect=spy_commit):
			with patch("pos_next.api.outlet_monitoring.get_report", side_effect=RuntimeError("Simulated post-save crash")):
				with self.assertRaises(RuntimeError):
					api.register_media(rep.name, cp_row, file_id)

		self.assertEqual(len(commit_calls), 0)

		frappe.db.rollback()
		rep.reload()
		self.assertEqual(len(getattr(rep, "media", [])), 0)
		self.assertFalse(frappe.db.exists("File", file_id))

	def test_webm_only_when_allowed(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Video"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_WEBM, file_name="sample.webm")
		frappe.set_user(self.cashier)
		# allow_webm is 0 -> should reject
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f.name)

		# Allow webm
		frappe.set_user("Administrator")
		s = frappe.get_single("POS Monitoring Settings")
		s.allow_webm = 1
		s.save(ignore_permissions=True)

		f2 = self._attach_file(rep.name, TINY_WEBM, file_name="sample2.webm")
		frappe.set_user(self.cashier)
		res = api.register_media(rep.name, cp_row, f2.name)
		self.assertEqual(len(res["media"]), 1)

	def test_submit_requires_required_evidence(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)

		frappe.set_user(self.cashier)
		# No media registered yet -> submit must throw
		with self.assertRaises(frappe.ValidationError):
			api.submit_report(rep.name)

		# Attach valid media
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")
		api.register_media(rep.name, rep.checkpoints[0].name, f.name)

		# Submit must succeed
		res = api.submit_report(rep.name)
		self.assertEqual(res["status"], "Submitted")

	def test_submit_after_due_is_late(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		api.register_media(rep.name, rep.checkpoints[0].name, f.name)

		# Patch now_datetime to be after due_at
		due_time = rep.due_at
		past_due = add_to_date(due_time, minutes=10)
		with patch("pos_next.api.outlet_monitoring.now_datetime", return_value=past_due):
			res = api.submit_report(rep.name)
			self.assertEqual(res["timeliness"], "Late")

	def test_cashier_other_company_cannot_get_report(self):
		other_company = "_Test Company 1"
		other_profile = _setup_profile(other_company)
		self._create_template("Per Shift", checkpoints=["API CP Photo"])
		# Open shift in other company
		other_shift = frappe.get_doc({
			"doctype": "POS Opening Shift",
			"pos_profile": other_profile,
			"company": other_company,
			"user": "Administrator",
			"posting_date": frappe.utils.today(),
			"period_start_date": frappe.utils.now_datetime(),
			"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 100000}],
		})
		other_shift.insert(ignore_permissions=True)
		other_shift.submit()

		# Create template for other company
		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"Tmpl-Other-{uuid.uuid4().hex[:6]}",
			"cadence": "Per Shift",
			"enabled": 1,
			"due_minutes_after_open": 30,
			"checkpoints": [{"checkpoint": "API CP Photo", "required": 1}],
			"companies": [{"company": other_company}],
		}).insert(ignore_permissions=True)

		ensure_obligations(pos_profile=other_profile)
		other_rep = frappe.get_doc(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(t.name, "Per Shift", opening_shift=other_shift.name)},
		)

		# cashier from self.company cannot get report
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.PermissionError):
			api.get_report(other_rep.name)

	def test_revision_only_when_requested(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		api.register_media(rep.name, rep.checkpoints[0].name, f.name)
		api.submit_report(rep.name)

		# Try revision when Reviewed but correction_requested is False -> should fail
		frappe.set_user("Administrator")
		rep.reload()
		rep.checkpoints[0].assessment = "Pass"
		rep.status = "Reviewed"
		rep.review_result = "Pass"
		rep.correction_requested = 0
		rep.save()

		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.create_revision(rep.name)

		# Now set correction_requested = 1
		frappe.set_user("Administrator")
		rep.reload()
		rep.correction_requested = 1
		rep.save()

		frappe.set_user(self.cashier)
		rev = api.create_revision(rep.name)
		self.assertEqual(rev["revision"], 2)
		self.assertEqual(rev["status"], "Draft")
		self.assertEqual(rev["correction_of"], rep.name)
		self.assertNotIn("assessment", rev["checkpoints"][0])
		self.assertEqual(len(rev["media"]), 0)

		# Verify DB state has Pending assessment
		rev_doc = frappe.get_doc("POS Monitoring Report", rev["name"])
		self.assertEqual(rev_doc.checkpoints[0].assessment, "Pending")

	def test_register_foreign_file_rejected_and_not_deleted(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		# Administrator creates a private File attached to User Administrator
		frappe.set_user("Administrator")
		admin_file = frappe.get_doc({
			"doctype": "File",
			"file_name": "admin_private.jpg",
			"content": TINY_JPEG,
			"is_private": 1,
			"attached_to_doctype": "User",
			"attached_to_name": "Administrator",
		}).insert(ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, admin_file.name)

		# Foreign file must NOT be deleted by cleanup
		self.assertTrue(frappe.db.exists("File", admin_file.name))

		# Cleanup
		frappe.set_user("Administrator")
		frappe.delete_doc("File", admin_file.name, ignore_permissions=True, force=True)
		frappe.db.commit()

	def test_register_already_registered_file_not_deleted_on_second_failure(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")
		frappe.set_user(self.cashier)
		api.register_media(rep.name, cp_row, f.name)

		# Try to register the same file to a non-existent checkpoint
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, "nonexistent-row", f.name)

		# Already registered file must NOT be deleted
		self.assertTrue(frappe.db.exists("File", f.name))

	def test_register_failure_cleanup_persists_after_request_rollback(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_GIF, file_name="invalid.gif")
		full_path = f.get_full_path()
		fname = f.name
		frappe.db.commit()

		frappe.set_user(self.cashier)
		try:
			api.register_media(rep.name, cp_row, fname)
		except frappe.ValidationError:
			# Simulating request handler rollback on exception
			frappe.db.rollback()

		# After request rollback, file must NOT exist in DB and disk file must be deleted
		self.assertFalse(frappe.db.exists("File", fname))
		if full_path:
			self.assertFalse(os.path.exists(full_path))

	def test_revision_on_closed_shift_workflow(self):
		from pos_next.api.outlet_monitoring_review import finalize_review
		from pos_next.services.outlet_monitoring_access import REVIEWER_ROLE

		reviewer = _create_user(REVIEWER_ROLE, "rev-shift-closed")

		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_JPEG, file_name="first.jpg")
		frappe.set_user(self.cashier)
		api.register_media(rep.name, cp_row, f.name)
		api.submit_report(rep.name)

		# Finalize review with Fail and correction_requested
		frappe.set_user(reviewer)
		rep.reload()
		rep.checkpoints[0].assessment = "Fail"
		rep.checkpoints[0].assessment_note = "Needs retake"
		rep.correction_requested = 1
		rep.flags.ignore_permissions = True
		rep.save()
		finalize_review(rep.name)

		# Close the shift!
		frappe.set_user("Administrator")
		shift.reload()
		shift.status = "Closed"
		shift.save(ignore_permissions=True)

		# Disallow late submission in settings
		s = frappe.get_single("POS Monitoring Settings")
		s.allow_late_submission = 0
		s.save(ignore_permissions=True)

		# Cashier creates revision
		frappe.set_user(self.cashier)
		rev = api.create_revision(rep.name)
		rev_name = rev["name"]

		# 1. due_at must be in the future, giving a real working window
		rev_doc = frappe.get_doc("POS Monitoring Report", rev_name)
		self.assertGreater(get_datetime(rev_doc.due_at), now_datetime())

		# 2. get_my_tasks must include the revision despite closed shift
		tasks_resp = api.get_my_tasks(self.profile)
		task_names = [t["name"] for t in tasks_resp["tasks"]]
		self.assertIn(rev_name, task_names)

		# 3. mark_missed must NOT mark the revision as missed
		missed = mark_missed()
		self.assertNotIn(rev_name, missed)
		rev_doc.reload()
		self.assertEqual(rev_doc.status, "Draft")

		# 4. Upload evidence and submit report
		f2 = self._attach_file(rev_name, TINY_JPEG, file_name="retake.jpg")
		api.register_media(rev_name, rev_doc.checkpoints[0].name, f2.name)
		sub_res = api.submit_report(rev_name)
		self.assertEqual(sub_res["status"], "Submitted")
		self.assertEqual(sub_res["timeliness"], "On Time")

	def test_revision_limit(self):
		frappe.set_user("Administrator")
		s = frappe.get_single("POS Monitoring Settings")
		orig_revisions = s.max_revisions
		self.addCleanup(frappe.db.set_value, "POS Monitoring Settings", None, "max_revisions", orig_revisions)
		s.max_revisions = 1
		s.save(ignore_permissions=True)

		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		rep.revision = 2  # already rev 2
		rep.status = "Reviewed"
		rep.correction_requested = 1
		rep.docstatus = 1
		rep.flags.ignore_permissions = True
		rep.save()

		frappe.set_user(self.cashier)
		# max_revisions is 1, so revision >= 1 + 1 = 2 cannot create rev 3
		with self.assertRaises(frappe.ValidationError):
			api.create_revision(rep.name)

	def test_remove_orphan_file_only_own_unreferenced(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)

		# Cashier creates unreferenced file attached to draft report
		frappe.set_user(self.cashier)
		unique_name = f"orphan-{uuid.uuid4().hex[:8]}.jpg"
		f = frappe.get_doc({
			"doctype": "File",
			"file_name": unique_name,
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep.name,
			"is_private": 1,
			"content": TINY_JPEG + uuid.uuid4().bytes,
		}).insert(ignore_permissions=True)

		api.remove_orphan_file(f.name)
		self.assertFalse(frappe.db.exists("File", f.name))

	def test_native_save_cannot_tamper_uploaded_at(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")
		frappe.set_user(self.cashier)
		api.register_media(rep.name, cp_row, f.name)
		rep.reload()

		orig_uploaded_at = str(rep.media[0].uploaded_at)
		payload = rep.as_dict()
		payload["media"][0]["uploaded_at"] = "2099-01-01 00:00:00"

		with self.assertRaises(frappe.PermissionError):
			frappe.client.save(payload)

		rep.reload()
		self.assertEqual(str(rep.media[0].uploaded_at), orig_uploaded_at)

	def test_native_save_cannot_empty_media_table(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")
		frappe.set_user(self.cashier)
		api.register_media(rep.name, cp_row, f.name)
		rep.reload()
		self.assertEqual(len(rep.media), 1)

		payload = rep.as_dict()
		payload["media"] = []

		with self.assertRaises(frappe.PermissionError):
			frappe.client.save(payload)

		rep.reload()
		self.assertEqual(len(rep.media), 1)

	def test_suite_cleanup_preserves_external_sentinel_reports(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		sentinel = frappe.get_doc({
			"doctype": "POS Monitoring Report",
			"company": self.company,
			"template": tmpl.name,
			"operational_date": frappe.utils.today(),
			"due_at": frappe.utils.now_datetime(),
			"cadence": "Per Shift",
			"status": "Draft",
			"obligation_key": f"sentinel-{uuid.uuid4().hex}",
			"revision": 1,
		})
		sentinel.flags.ignore_mandatory = True
		sentinel.flags.ignore_permissions = True
		sentinel.insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(frappe.delete_doc, "POS Monitoring Report", sentinel.name, force=True, ignore_permissions=True)

		# Simulate that sentinel existed before this suite was run
		self.__class__._suite_initial_reports.add(sentinel.name)

		# Execute suite cleanup
		self.__class__.tearDownClass()

		# Sentinel report must STILL exist!
		self.assertTrue(
			frappe.db.exists("POS Monitoring Report", sentinel.name),
			"Suite cleanup must not delete reports outside of test fixture"
		)

	def test_disabled_endpoints_reject(self):
		frappe.set_user("Administrator")
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 0
		s.save(ignore_permissions=True)

		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.get_history(self.profile)

	def test_safe_cleanup_file_preserves_shared_blob(self):
		tmpl = self._create_template("Per Shift", checkpoints=["API CP Photo"])
		shift1 = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep1 = self._get_shift_report(tmpl.name, shift1.name)

		# Create a second report
		rep2 = frappe.get_doc({
			"doctype": "POS Monitoring Report",
			"company": self.company,
			"template": tmpl.name,
			"pos_profile": self.profile,
			"operational_date": frappe.utils.today(),
			"due_at": frappe.utils.now_datetime(),
			"cadence": "Per Shift",
			"status": "Draft",
			"obligation_key": f"shared-{uuid.uuid4().hex}",
			"revision": 1,
		}).insert(ignore_permissions=True)

		# Create two File documents with identical content
		identical_bytes = TINY_JPEG
		f1 = frappe.get_doc({
			"doctype": "File",
			"file_name": "shared1.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep1.name,
			"is_private": 1,
			"content": identical_bytes,
		}).insert(ignore_permissions=True)
		frappe.db.set_value("File", f1.name, "owner", self.cashier, update_modified=False)
		f1.owner = self.cashier

		f2 = frappe.get_doc({
			"doctype": "File",
			"file_name": "shared2.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep2.name,
			"is_private": 1,
			"content": identical_bytes,
		}).insert(ignore_permissions=True)
		frappe.db.set_value("File", f2.name, "owner", self.cashier, update_modified=False)
		f2.owner = self.cashier
		frappe.db.commit()

		# Both files share the same file_url and disk path
		self.assertEqual(f1.file_url, f2.file_url)
		disk_path = f1.get_full_path()
		self.assertTrue(os.path.exists(disk_path))

		# Now simulate failed register_media cleanup on rep2
		from pos_next.api.outlet_monitoring import _safe_cleanup_file
		_safe_cleanup_file(f2.name, self.cashier, rep2.name)

		# f2 record was deleted from tabFile
		self.assertFalse(frappe.db.exists("File", f2.name))
		# BUT f1 record still exists AND shared blob on disk MUST STILL EXIST!
		self.assertTrue(frappe.db.exists("File", f1.name))
		self.assertTrue(os.path.exists(disk_path), "Shared blob on disk must not be deleted when cleaned up for another File")
