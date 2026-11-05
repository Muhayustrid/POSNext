# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime, today

from pos_next.services.outlet_monitoring import (
	build_report,
	client_config,
	ensure_obligations,
	get_settings,
	mark_missed,
	obligation_key,
	purge_expired_media,
	recompute_evidence,
	resolve_labels,
)
from pos_next.tasks.outlet_monitoring import run_cron, run_daily

TINY_JPEG = (
	b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
	b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
	b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
	b"'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
	b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
	b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)


def _ensure_category(name="Service Test Category"):
	if not frappe.db.exists("POS Monitoring Category", name):
		frappe.get_doc({
			"doctype": "POS Monitoring Category",
			"category_name": name,
			"pass_label": "Cat Pass",
			"fail_label": "Cat Fail",
			"na_label": "Cat NA",
		}).insert(ignore_permissions=True)
	return name


def _ensure_checkpoint(name="Service Test CP", category="Service Test Category", min_count=1, max_count=2, media_type="Photo"):
	_ensure_category(category)
	if not frappe.db.exists("POS Monitoring Checkpoint", name):
		frappe.get_doc({
			"doctype": "POS Monitoring Checkpoint",
			"checkpoint_name": name,
			"category": category,
			"media_type": media_type,
			"min_count": min_count,
			"max_count": max_count,
		}).insert(ignore_permissions=True)
	return name


def _ensure_company():
	company = "_Test Company"
	if not frappe.db.exists("Company", company):
		frappe.get_doc({
			"doctype": "Company",
			"company_name": company,
			"default_currency": "IDR",
		}).insert(ignore_permissions=True)
	return company


def _ensure_pos_profile(company=None):
	company = company or _ensure_company()
	profile_name = f"_Test Service Profile {uuid.uuid4().hex[:6]}"
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


def _create_open_shift(pos_profile, user="Administrator", start_date=None, shift_slot=None):
	company = frappe.db.get_value("POS Profile", pos_profile, "company")
	shift = frappe.get_doc({
		"doctype": "POS Opening Shift",
		"pos_profile": pos_profile,
		"company": company,
		"user": user,
		"posting_date": getdate(start_date) if start_date else today(),
		"period_start_date": get_datetime(start_date) if start_date else now_datetime(),
		"shift_slot": shift_slot,
		"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 100000}],
	})
	shift.insert(ignore_permissions=True)
	shift.submit()
	return shift


class TestOutletMonitoringService(FrappeTestCase):
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
		self.company = _ensure_company()
		self.profile = _ensure_pos_profile(self.company)
		self.category = _ensure_category()
		self.checkpoint = _ensure_checkpoint()

		# Enable settings for test
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
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

	def test_cleanup_preserves_nondefault_settings(self):
		s = frappe.get_single("POS Monitoring Settings")
		s.max_revisions = 7
		s.default_pass_label = "Preserved original label"
		s.save(ignore_permissions=True)
		nested = type(self)("test_cleanup_preserves_nondefault_settings")
		nested.setUp()
		try:
			nested._restore_settings()
			restored = frappe.get_single("POS Monitoring Settings")
			self.assertEqual(restored.max_revisions, 7)
			self.assertEqual(restored.default_pass_label, "Preserved original label")
			self.assertEqual(frappe.db.get_singles_dict("POS Monitoring Settings"), nested._original_settings)
		finally:
			nested.tearDown()
			nested.doCleanups()

	def _create_template(self, cadence="Per Shift", checkpoints=None, companies=None, pos_profiles=None, shift_types=None, enabled=1, due_minutes=30, due_time="10:00:00"):
		name = f"Tmpl-{uuid.uuid4().hex[:8]}"
		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": name,
			"cadence": cadence,
			"enabled": enabled,
			"due_minutes_after_open": due_minutes if cadence == "Per Shift" else 0,
			"due_time": due_time if cadence == "Per Hari" else None,
			"checkpoints": [{"checkpoint": cp, "required": 1} for cp in (checkpoints or [self.checkpoint])],
			"companies": [{"company": c} for c in (companies or [self.company])],
			"pos_profiles": [{"pos_profile": p} for p in (pos_profiles or [])],
			"shift_types": [{"shift_type": st} for st in (shift_types or [])],
		})
		t.insert(ignore_permissions=True)
		return t

	def test_ensure_twice_creates_one_report_per_key(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")

		res1 = ensure_obligations(pos_profile=self.profile)
		self.assertGreaterEqual(len(res1), 1)

		# Second run must not create duplicate report for same shift
		res2 = ensure_obligations(pos_profile=self.profile)
		created_for_key = [r for r in res2 if r in res1]
		self.assertEqual(len(created_for_key), 0)

		# Verify exactly 1 report exists for this key
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		count = frappe.db.count("POS Monitoring Report", {"obligation_key": key})
		self.assertEqual(count, 1)

	def test_concurrent_ensure_two_connections_unique_obligation(self):
		import threading

		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		frappe.db.commit()

		site = frappe.local.site
		bench_sites = frappe.utils.get_bench_path() + "/sites" if hasattr(frappe.utils, "get_bench_path") else "/home/frappe/frappe-bench/sites"
		sites_path = getattr(frappe.local, "sites_path", None) or bench_sites
		profile = self.profile

		barrier = threading.Barrier(2)
		results = []
		errors = []

		def worker(tid):
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			builder = EnvironBuilder(path="/api/method/pos_next.api.outlet_monitoring.ensure_obligations")
			frappe.local.request = Request(builder.get_environ())
			frappe.local.form_dict = frappe._dict(cmd="ensure_obligations")
			frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
			try:
				barrier.wait(timeout=10)
				res = ensure_obligations(pos_profile=profile)
				frappe.db.commit()
				results.append((tid, res))
			except Exception as e:
				frappe.db.rollback()
				errors.append((tid, e))
			finally:
				frappe.db.close()

		t1 = threading.Thread(target=worker, args=(1,))
		t2 = threading.Thread(target=worker, args=(2,))
		t1.start()
		t2.start()
		t1.join(timeout=15)
		t2.join(timeout=15)

		frappe.db.rollback()
		frappe.set_user("Administrator")

		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		count = frappe.db.count("POS Monitoring Report", {"obligation_key": key, "revision": 1})
		self.assertEqual(count, 1, f"Expected exactly 1 report, found {count}. Results: {results}, Errors: {errors}")

		# Total reports returned across both threads for this key should be 1
		all_created = [r for tid, res in results for r in res]
		created_for_key = frappe.get_all("POS Monitoring Report", filters={"name": ["in", all_created], "obligation_key": key}, pluck="name")
		self.assertEqual(len(created_for_key), 1)

	def test_per_day_not_duplicated_across_shifts(self):
		tmpl_day = self._create_template("Per Hari", due_time="12:00:00")
		tmpl_shift = self._create_template("Per Shift")

		shift1 = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)

		# Close shift 1
		frappe.db.set_value("POS Opening Shift", shift1.name, "status", "Closed")

		# Open shift 2
		shift2 = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)

		# 1 day report for this company & date, but 2 shift reports
		day_key = obligation_key(tmpl_day.name, "Per Hari", company=self.company, operational_date=today())
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": day_key}), 1)

		shift1_key = obligation_key(tmpl_shift.name, "Per Shift", opening_shift=shift1.name)
		shift2_key = obligation_key(tmpl_shift.name, "Per Shift", opening_shift=shift2.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": shift1_key}), 1)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": shift2_key}), 1)

	def test_shift_type_filter(self):
		# Create a dummy Shift Type if needed
		st_name = "Morning Slot"
		if not frappe.db.exists("Shift Type", st_name):
			frappe.get_doc({
				"doctype": "Shift Type",
				"name": st_name,
				"start_time": "08:00:00",
				"end_time": "16:00:00",
			}).insert(ignore_permissions=True)

		shift = _create_open_shift(self.profile, shift_slot="Evening Slot")
		tmpl = self._create_template("Per Shift", shift_types=[st_name])

		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": key}), 0)

	def test_empty_companies_creates_nothing(self):
		shift = _create_open_shift(self.profile)
		# Template with no companies
		name = f"Tmpl-NoComp-{uuid.uuid4().hex[:8]}"
		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": name,
			"cadence": "Per Shift",
			"enabled": 1,
			"due_minutes_after_open": 30,
			"checkpoints": [{"checkpoint": self.checkpoint, "required": 1}],
			"companies": [],
		}).insert(ignore_permissions=True)

		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(t.name, "Per Shift", opening_shift=shift.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": key}), 0)

	def test_disabled_flag_creates_nothing(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift", enabled=0)

		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": key}), 0)

	def test_due_at_per_shift_and_per_day(self):
		start_time = "2026-11-05 08:00:00"
		shift = _create_open_shift(self.profile, start_date=start_time)
		tmpl_shift = self._create_template("Per Shift", due_minutes=45)
		tmpl_day = self._create_template("Per Hari", due_time="14:30:00")

		with patch("pos_next.services.outlet_monitoring.now_datetime", return_value=get_datetime(start_time)):
			ensure_obligations(pos_profile=self.profile, now=get_datetime(start_time))

		rep_shift_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl_shift.name, "Per Shift", opening_shift=shift.name)},
		)
		rep_shift = frappe.get_doc("POS Monitoring Report", rep_shift_name)
		self.assertEqual(str(rep_shift.due_at), "2026-11-05 08:45:00")

		rep_day_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl_day.name, "Per Hari", company=self.company, operational_date="2026-11-05")},
		)
		rep_day = frappe.get_doc("POS Monitoring Report", rep_day_name)
		self.assertEqual(str(rep_day.due_at), "2026-11-05 14:30:00")

	def test_snapshot_survives_template_edit(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")

		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.checkpoints[0].min_count, 1)

		# Modify checkpoint master
		cp = frappe.get_doc("POS Monitoring Checkpoint", self.checkpoint)
		cp.max_count = 6
		cp.min_count = 5
		cp.disabled = 1
		cp.save(ignore_permissions=True)
		self.addCleanup(frappe.db.set_value, "POS Monitoring Checkpoint", self.checkpoint, {"min_count": 1, "max_count": 2, "disabled": 0})

		# Report checkpoint snapshot must be unchanged
		rep.reload()
		self.assertEqual(rep.checkpoints[0].min_count, 1)

	def test_closed_shift_draft_becomes_missed(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")

		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)

		# Close the shift
		frappe.db.set_value("POS Opening Shift", shift.name, "status", "Closed")

		missed = mark_missed(names=[rep_name])
		self.assertIn(rep_name, missed)

		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Missed")
		self.assertEqual(rep.docstatus, 1)

	def test_per_day_missed_next_day(self):
		tmpl_day = self._create_template("Per Hari", due_time="10:00:00")
		op_date = "2026-11-05"
		now_create = get_datetime(f"{op_date} 09:00:00")
		ensure_obligations(pos_profile=self.profile, now=now_create)

		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl_day.name, "Per Hari", company=self.company, operational_date=op_date)},
		)

		# H+1 at 01:59:00 (before 02:00 cutoff) -> not missed
		before_cutoff = get_datetime("2026-11-06 01:59:00")
		missed = mark_missed(now=before_cutoff, names=[rep_name])
		self.assertNotIn(rep_name, missed)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Draft")

		# H+1 at 02:01:00 (after 02:00 cutoff) -> missed
		after_cutoff = get_datetime("2026-11-06 02:01:00")
		missed = mark_missed(now=after_cutoff, names=[rep_name])
		self.assertIn(rep_name, missed)
		rep.reload()
		self.assertEqual(rep.status, "Missed")
		self.assertEqual(rep.docstatus, 1)

	def test_shift_missed_after_grace(self):
		shift = _create_open_shift(self.profile, start_date="2026-11-05 08:00:00")
		tmpl = self._create_template("Per Shift", due_minutes=30)  # due 08:30

		ensure_obligations(pos_profile=self.profile, now=get_datetime("2026-11-05 08:00:00"))
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)

		# Grace is 12 hours -> cutoff is 20:30
		# At 20:29 -> still Draft
		missed = mark_missed(now=get_datetime("2026-11-05 20:29:00"), names=[rep_name])
		self.assertNotIn(rep_name, missed)

		# At 20:31 -> Missed
		missed = mark_missed(now=get_datetime("2026-11-05 20:31:00"), names=[rep_name])
		self.assertIn(rep_name, missed)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Missed")
		self.assertEqual(rep.docstatus, 1)

	def test_per_day_revision_window_follows_revision_due_at(self):
		tmpl_day = self._create_template("Per Hari", due_time="10:00:00")
		op_date = "2026-11-01"
		key = obligation_key(tmpl_day.name, "Per Hari", company=self.company, operational_date=op_date)
		doc = build_report(
			tmpl_day,
			"Per Hari",
			company=self.company,
			operational_date=op_date,
			due_at=get_datetime("2026-11-01 10:00:00"),
			obligation_key=key,
		)
		doc.insert(ignore_permissions=True)
		doc.status = "Reviewed"
		doc.review_result = "Fail"
		doc.correction_requested = 1
		doc.docstatus = 1
		doc.flags.ignore_permissions = True
		doc.save()

		with patch("pos_next.api.outlet_monitoring.now_datetime", return_value=get_datetime("2026-11-05 08:00:00")):
			from pos_next.api.outlet_monitoring import create_revision
			rev = create_revision(doc.name)
			rev_name = rev["name"]

		rev_doc = frappe.get_doc("POS Monitoring Report", rev_name)
		self.assertEqual(rev_doc.revision, 2)
		self.assertEqual(str(rev_doc.operational_date), "2026-11-01")
		self.assertEqual(str(rev_doc.due_at), "2026-11-05 20:00:00")

		# Check mark_missed at 2026-11-05 08:30:00 (after operational_date + 1 day, but before revision due_at + grace)
		missed = mark_missed(now=get_datetime("2026-11-05 08:30:00"), names=[rev_name])
		self.assertNotIn(rev_name, missed)
		rev_doc.reload()
		self.assertEqual(rev_doc.status, "Draft")

		# After revision due_at + grace_hours (2026-11-05 20:00 + 12h = 2026-11-06 08:00) -> 2026-11-06 08:01:00
		missed = mark_missed(now=get_datetime("2026-11-06 08:01:00"), names=[rev_name])
		self.assertIn(rev_name, missed)
		rev_doc.reload()
		self.assertEqual(rev_doc.status, "Missed")

	def test_purge_keeps_report_deletes_file(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)

		# Attach a file
		file_doc = frappe.get_doc({
			"doctype": "File",
			"file_name": "retention_test.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep.name,
			"is_private": 1,
			"content": TINY_JPEG,
		}).insert(ignore_permissions=True)

		# Add media row
		rep.append("media", {
			"checkpoint_row": rep.checkpoints[0].name,
			"file_url": file_doc.file_url,
			"media_type": "Photo",
			"file_size": 100,
			"uploaded_at": add_days(today(), -95),  # 95 days ago
			"purged": 0,
		})
		# Mark report reviewed & submit
		rep.status = "Reviewed"
		rep.docstatus = 1
		rep.flags.ignore_permissions = True
		rep.save()

		# Run purge with retention 90 days
		deleted = purge_expired_media(today=today())
		self.assertGreaterEqual(deleted, 1)

		# File should be deleted
		self.assertFalse(frappe.db.exists("File", file_doc.name))
		# Report exists and media row purged=1
		rep.reload()
		self.assertEqual(rep.media[0].purged, 1)

	def test_retention_zero_keeps_forever(self):
		s = frappe.get_single("POS Monitoring Settings")
		s.media_retention_days = 0
		s.save(ignore_permissions=True)

		deleted = purge_expired_media(today=today())
		self.assertEqual(deleted, 0)

	def test_task_module_imports_cold(self):
		import subprocess
		import sys

		result = subprocess.run(
			[sys.executable, "-c", "import pos_next.tasks.outlet_monitoring"],
			capture_output=True,
			text=True,
			cwd=frappe.utils.get_bench_path() + "/sites",
		)
		self.assertEqual(result.returncode, 0, result.stderr[-500:])

	def test_cron_disabled_does_not_mutate_drafts(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		self.assertTrue(rep_name)

		# Close shift so draft would be eligible for Missed
		frappe.db.set_value("POS Opening Shift", shift.name, "status", "Closed")

		# Disable monitoring
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 0
		s.save(ignore_permissions=True)

		run_cron()
		missed = mark_missed()
		self.assertEqual(missed, [])

		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Draft")
		self.assertEqual(rep.docstatus, 0)

	def test_retention_failure_keeps_media_retryable(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)

		file_doc = frappe.get_doc({
			"doctype": "File",
			"file_name": "retention_fail.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep.name,
			"is_private": 1,
			"content": TINY_JPEG,
		}).insert(ignore_permissions=True)

		rep.append("media", {
			"checkpoint_row": rep.checkpoints[0].name,
			"file_url": file_doc.file_url,
			"media_type": "Photo",
			"file_size": 100,
			"uploaded_at": add_days(today(), -95),
			"purged": 0,
		})
		rep.status = "Reviewed"
		rep.docstatus = 1
		rep.flags.ignore_permissions = True
		rep.save()

		# Simulate deletion failure (e.g. delete_doc raising an exception)
		with patch("frappe.delete_doc", side_effect=OSError("Disk read-only")):
			deleted = purge_expired_media(today=today())
			self.assertEqual(deleted, 0)

		# Row must NOT be purged=1 and must remain retryable
		rep.reload()
		self.assertEqual(rep.media[0].purged, 0)
		self.assertTrue(frappe.db.exists("File", file_doc.name))
