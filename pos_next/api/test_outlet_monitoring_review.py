# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, now_datetime, today

from pos_next.api import outlet_monitoring as cashier_api
from pos_next.api import outlet_monitoring_review as review_api
from pos_next.services.outlet_monitoring import ensure_obligations, get_settings, obligation_key
from pos_next.services.outlet_monitoring_access import REVIEWER_ROLE

TINY_JPEG = (
	b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
	b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
	b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
	b"'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
	b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
	b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)


def _create_user(role, prefix="review-test", company=None):
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

	if company:
		frappe.get_doc({
			"doctype": "User Permission",
			"user": email,
			"allow": "Company",
			"for_value": company,
		}).insert(ignore_permissions=True)

	return email


def _setup_profile(company):
	wh = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	if not wh:
		wh = frappe.get_doc({
			"doctype": "Warehouse",
			"warehouse_name": f"WH-Rev-{uuid.uuid4().hex[:6]}",
			"company": company,
		}).insert(ignore_permissions=True).name

	pname = f"Rev-Prof-{uuid.uuid4().hex[:6]}"
	prof = frappe.get_doc({
		"doctype": "POS Profile",
		"name": pname,
		"company": company,
		"warehouse": wh,
		"currency": "IDR",
		"write_off_account": frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name") or "Write Off - _TC",
		"write_off_cost_center": frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name") or "Main - _TC",
		"income_account": frappe.db.get_value("Account", {"company": company, "root_type": "Income", "is_group": 0}, "name") or "Sales - _TC",
		"payments": [{"mode_of_payment": "Cash", "default": 1}],
	})
	prof.insert(ignore_permissions=True)
	return pname


def _get_second_db():
	from frappe.database.mariadb.mysqlclient import MariaDBDatabase
	c = frappe.conf
	db2 = MariaDBDatabase(
		user=c.db_name,
		password=c.db_password,
		host=c.db_host,
		port=c.db_port,
		cur_db_name=c.db_name,
		socket=c.get("db_socket"),
	)
	db2.connect()
	return db2


class TestOutletMonitoringReview(FrappeTestCase):
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
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		builder = EnvironBuilder(path="/")
		frappe.local.request = Request(builder.get_environ())
		if hasattr(frappe.local, "session") and not getattr(frappe.local.session, "data", None):
			frappe.local.session.data = frappe._dict()

		frappe.set_user("Administrator")
		self._original_settings = frappe.db.get_singles_dict("POS Monitoring Settings")
		self.addCleanup(self._restore_settings)
		self._test_initial_reports = set(frappe.get_all("POS Monitoring Report", pluck="name"))
		self._test_initial_files = set(frappe.get_all("File", pluck="name"))
		self.company = "_Test Company"
		if not frappe.db.exists("Company", self.company):
			frappe.get_doc({
				"doctype": "Company",
				"company_name": self.company,
				"default_currency": "IDR",
			}).insert(ignore_permissions=True)

		self.profile = _setup_profile(self.company)
		self.cashier = _create_user("POSNext Cashier", "rev-cashier")
		self.reviewer = _create_user(REVIEWER_ROLE, "rev-reviewer", company=self.company)

		# Assign cashier to profile
		pdoc = frappe.get_doc("POS Profile", self.profile)
		pdoc.append("applicable_for_users", {"user": self.cashier})
		pdoc.save(ignore_permissions=True)

		# Setup checkpoints
		if not frappe.db.exists("POS Monitoring Category", "Rev Category"):
			frappe.get_doc({
				"doctype": "POS Monitoring Category",
				"category_name": "Rev Category",
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", "Rev CP 1"):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": "Rev CP 1",
				"category": "Rev Category",
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", "Rev CP 2"):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": "Rev CP 2",
				"category": "Rev Category",
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		# Enable settings
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.allow_na = 1
		s.require_note_on_fail = 1
		s.require_note_on_na = 1
		s.prevent_self_review = 1
		s.notify_reviewers_on_submit = 1
		s.notify_reporter_on_review = 1
		s.save(ignore_permissions=True)

	def _restore_settings(self):
		frappe.set_user("Administrator")
		frappe.db.delete("Singles", {"doctype": "POS Monitoring Settings"})
		frappe.db.set_single_value("POS Monitoring Settings", self._original_settings, update_modified=False)

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

	def _create_submitted_report(self, checkpoints=None, cadence="Per Shift", submitter=None, pos_profile=None):
		cps = checkpoints or ["Rev CP 1"]
		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"Tmpl-Rev-{uuid.uuid4().hex[:6]}",
			"cadence": cadence,
			"enabled": 1,
			"due_minutes_after_open": 30,
			"due_time": "12:00:00" if cadence == "Per Hari" else None,
			"checkpoints": [{"checkpoint": cp, "required": 1} for cp in cps],
			"companies": [{"company": self.company}],
		}).insert(ignore_permissions=True)

		user = submitter or self.cashier
		profile = pos_profile or _setup_profile(self.company)
		pdoc = frappe.get_doc("POS Profile", profile)
		if not any(r.user == user for r in pdoc.applicable_for_users):
			pdoc.append("applicable_for_users", {"user": user})
			pdoc.save(ignore_permissions=True)

		shift = frappe.get_doc({
			"doctype": "POS Opening Shift",
			"pos_profile": profile,
			"company": self.company,
			"user": user,
			"posting_date": today(),
			"period_start_date": now_datetime(),
			"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 100000}],
		})
		shift.insert(ignore_permissions=True)
		shift.submit()

		ensure_obligations(pos_profile=profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"template": t.name, "opening_shift": shift.name},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)

		# Attach evidence as submitter
		frappe.set_user(user)
		for cp_row in rep.checkpoints:
			f = frappe.get_doc({
				"doctype": "File",
				"file_name": f"f-{uuid.uuid4().hex[:6]}.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep.name,
				"is_private": 1,
				"content": TINY_JPEG + uuid.uuid4().bytes,
			}).insert(ignore_permissions=True)
			cashier_api.register_media(rep.name, cp_row.name, f.name)

		cashier_api.submit_report(rep.name)
		frappe.set_user("Administrator")
		rep.reload()
		return rep

	def test_reviewer_json_roundtrip_assessment_save(self):
		from frappe.client import save

		rep = self._create_submitted_report()
		frappe.db.set_value(rep.doctype, rep.name, "operational_date", today())
		rep.reload()
		payload = rep.as_dict()
		payload["checkpoints"][0]["assessment"] = "Pass"
		frappe.set_user(self.reviewer)
		save(frappe.as_json(payload))
		rep.reload()
		self.assertEqual(rep.checkpoints[0].assessment, "Pass")
		from frappe.desk.form.save import savedocs

		payload = rep.as_dict()
		payload["checkpoints"][0]["assessment_note"] = "Saved through Desk"
		savedocs(frappe.as_json(payload), "Update")
		rep.reload()
		self.assertEqual(rep.checkpoints[0].assessment_note, "Saved through Desk")

	def test_native_finalize_ignores_client_review_attribution(self):
		from frappe.client import save

		for status in ("Reviewed", "Waived"):
			with self.subTest(status=status):
				rep = self._create_submitted_report()
				frappe.set_user("Administrator")
				rep.reload()
				payload = rep.as_dict()
				payload["status"] = status
				payload["waive_reason"] = "Renovation"
				payload["checkpoints"][0]["assessment"] = "Pass"
				payload["reviewed_by"] = "Administrator"
				payload["reviewed_at"] = "2020-01-01 00:00:00"
				frappe.set_user(self.reviewer)
				save(frappe.as_json(payload))
				frappe.set_user("Administrator")
				rep.reload()
				self.assertEqual(rep.status, status)
				self.assertEqual(rep.reviewed_by, self.reviewer)
				self.assertNotEqual(str(rep.reviewed_at)[:4], "2020")

	def test_waived_report_locks_all_review_fields(self):
		rep = self._create_submitted_report()
		frappe.set_user(self.reviewer)
		review_api.waive(rep.name, "Renovation")
		for field, value in (("review_result", "Fail"), ("review_note", "changed"), ("correction_requested", 1), ("assessment", "Fail"), ("assessment_note", "changed")):
			with self.subTest(field=field):
				rep.reload()
				if field.startswith("assessment"):
					rep.checkpoints[0].set(field, value)
				else:
					rep.set(field, value)
				with self.assertRaises(frappe.PermissionError):
					rep.save()

	def test_finalize_rejects_pending(self):
		rep = self._create_submitted_report(["Rev CP 1", "Rev CP 2"])
		# Checkpoints are currently Pending assessment
		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)

	def test_finalize_requires_note_for_fail_and_na(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Fail"
		rep.checkpoints[0].assessment_note = ""  # empty note
		rep.save()

		frappe.set_user(self.reviewer)
		# Fail with empty note should throw
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)

		# NA with empty note should throw
		frappe.set_user("Administrator")
		rep.reload()
		rep.checkpoints[0].assessment = "N/A"
		rep.checkpoints[0].assessment_note = ""
		rep.save()

		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)

		# Note provided -> succeeds
		frappe.set_user("Administrator")
		rep.reload()
		rep.checkpoints[0].assessment = "Fail"
		rep.checkpoints[0].assessment_note = "Item out of standard"
		rep.save()

		frappe.set_user(self.reviewer)
		res = review_api.finalize_review(rep.name)
		self.assertEqual(res["status"], "Reviewed")
		self.assertEqual(res["review_result"], "Fail")

	def test_finalize_rejects_na_when_disallowed(self):
		frappe.set_user("Administrator")
		s = frappe.get_single("POS Monitoring Settings")
		orig_allow_na = s.allow_na
		self.addCleanup(frappe.db.set_value, "POS Monitoring Settings", None, "allow_na", orig_allow_na)
		s.allow_na = 0
		s.save(ignore_permissions=True)

		rep = self._create_submitted_report(["Rev CP 1"])
		rep.checkpoints[0].assessment = "N/A"
		rep.checkpoints[0].assessment_note = "Not applicable"
		rep.save()

		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)

	def test_finalize_result_fail_if_any_fail(self):
		rep = self._create_submitted_report(["Rev CP 1", "Rev CP 2"])
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Pass"
		rep.checkpoints[1].assessment = "Fail"
		rep.checkpoints[1].assessment_note = "Broken"
		rep.save()

		frappe.set_user(self.reviewer)
		res = review_api.finalize_review(rep.name)
		self.assertEqual(res["review_result"], "Fail")

		# If both pass -> Pass
		rep2 = self._create_submitted_report(["Rev CP 1", "Rev CP 2"])
		frappe.set_user("Administrator")
		rep2.checkpoints[0].assessment = "Pass"
		rep2.checkpoints[1].assessment = "Pass"
		rep2.save()

		frappe.set_user(self.reviewer)
		res2 = review_api.finalize_review(rep2.name)
		self.assertEqual(res2["review_result"], "Pass")

	def test_reviewed_report_is_locked_against_native_edits(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Pass"
		rep.save()

		frappe.set_user(self.reviewer)
		review_api.finalize_review(rep.name)

		# Now report is Reviewed. Attempting to change assessment to Fail via doc.save must fail!
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].assessment = "Fail"
		doc.checkpoints[0].assessment_note = "Changed after review"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

		# Changing review_result directly must fail
		doc.reload()
		doc.review_result = "Fail"
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_prevent_self_review(self):
		# Create submitted report where submitter is self.reviewer
		pdoc = frappe.get_doc("POS Profile", self.profile)
		pdoc.append("applicable_for_users", {"user": self.reviewer})
		pdoc.save(ignore_permissions=True)

		rep = self._create_submitted_report(["Rev CP 1"], submitter=self.reviewer)
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Pass"
		rep.save()

		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.PermissionError):
			review_api.finalize_review(rep.name)

	def test_finalize_twice_second_fails(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Pass"
		rep.save()

		frappe.set_user(self.reviewer)
		review_api.finalize_review(rep.name)

		# Second call: status is already Reviewed -> ValidationError
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)

	def test_finalize_review_takes_row_lock(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Pass"
		rep.save()

		seen = []
		orig_get_value = frappe.db.get_value

		def spy_get_value(*args, **kwargs):
			seen.append((args, kwargs))
			return orig_get_value(*args, **kwargs)

		frappe.set_user(self.reviewer)
		with patch.object(frappe.db, "get_value", side_effect=spy_get_value):
			review_api.finalize_review(rep.name)

		lock_calls = [
			(a, k) for a, k in seen
			if a and a[0] == "POS Monitoring Report" and k.get("for_update")
		]
		self.assertTrue(lock_calls, "finalize_review must acquire row lock with for_update=True")
		self.assertEqual(lock_calls[0][0][2], "status")

	def test_concurrent_finalize_second_sees_reviewed_via_separate_connection(self):
		import threading

		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Pass"
		rep.save()
		frappe.db.commit()

		site = frappe.local.site
		bench_sites = frappe.utils.get_bench_path() + "/sites" if hasattr(frappe.utils, "get_bench_path") else "/home/frappe/frappe-bench/sites"
		sites_path = getattr(frappe.local, "sites_path", None) or bench_sites
		reviewer = self.reviewer
		rep_name = rep.name

		barrier = threading.Barrier(2)
		results = []
		errors = []

		def worker(tid):
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			builder = EnvironBuilder(path="/api/method/pos_next.api.outlet_monitoring_review.finalize_review")
			frappe.local.request = Request(builder.get_environ())
			frappe.local.form_dict = frappe._dict(cmd="finalize_review")
			frappe.local.session = frappe._dict(user=reviewer, data=frappe._dict())
			try:
				barrier.wait(timeout=10)
				res = review_api.finalize_review(rep_name)
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

		# Refresh ambient transaction state in main test thread
		frappe.db.rollback()
		frappe.set_user("Administrator")

		# Exactly one finalize operation must succeed
		self.assertEqual(len(results), 1, f"Expected exactly 1 success, got {len(results)}. Errors: {errors}")
		self.assertEqual(results[0][1]["status"], "Reviewed")

		# Exactly one operation must fail (either rejected by row check or MariaDB concurrency conflict)
		self.assertEqual(len(errors), 1, f"Expected exactly 1 error, got {len(errors)}")
		self.assertTrue(
			isinstance(errors[0][1], (frappe.ValidationError, frappe.QueryDeadlockError, frappe.QueryTimeoutError)),
			f"Expected ValidationError or QueryDeadlockError, got {type(errors[0][1])}: {errors[0][1]}",
		)

		# DB state is clean
		rep.reload()
		self.assertEqual(rep.status, "Reviewed")
		self.assertEqual(rep.review_result, "Pass")

	def test_cashier_cannot_finalize_or_waive(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.PermissionError):
			review_api.finalize_review(rep.name)

		with self.assertRaises(frappe.PermissionError):
			review_api.waive(rep.name, "test reason")

	def test_waive_requires_reason_and_state(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user(self.reviewer)

		# Empty reason -> ValidationError
		with self.assertRaises(frappe.ValidationError):
			review_api.waive(rep.name, "   ")

		# Valid reason -> Waived
		res = review_api.waive(rep.name, "Exempted due to renovation")
		self.assertEqual(res["status"], "Waived")
		self.assertEqual(res["waive_reason"], "Exempted due to renovation")

		# Waiving already waived -> ValidationError
		with self.assertRaises(frappe.ValidationError):
			review_api.waive(rep.name, "Again")

	def test_notify_reviewer_in_scope_only(self):
		other_company = "_Test Company 1"
		reviewer_other = _create_user(REVIEWER_ROLE, "rev-other", company=other_company)

		# Clear past notification logs for these users
		frappe.db.delete("Notification Log", {"for_user": ["in", [self.reviewer, reviewer_other]]})

		rep = self._create_submitted_report(["Rev CP 1"])
		# On submission, review_api.on_report_submitted was called
		# self.reviewer has permission for self.company -> should have log
		logs_self = frappe.get_all("Notification Log", filters={"for_user": self.reviewer, "document_name": rep.name})
		self.assertGreaterEqual(len(logs_self), 1)

		# reviewer_other is scoped only to other_company -> must NOT have log
		logs_other = frappe.get_all("Notification Log", filters={"for_user": reviewer_other, "document_name": rep.name})
		self.assertEqual(len(logs_other), 0)

	def test_publish_per_day_hits_all_company_profiles(self):
		# Create second profile in self.company
		prof2 = _setup_profile(self.company)

		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"Tmpl-Day-{uuid.uuid4().hex[:6]}",
			"cadence": "Per Hari",
			"enabled": 1,
			"due_time": "18:00:00",
			"checkpoints": [{"checkpoint": "Rev CP 1", "required": 1}],
			"companies": [{"company": self.company}],
		}).insert(ignore_permissions=True)

		now_dt = get_datetime(f"{today()} 08:00:00")
		ensure_obligations(pos_profile=self.profile, now=now_dt)
		day_key = obligation_key(t.name, "Per Hari", company=self.company, operational_date=today())
		rep_name = frappe.db.get_value("POS Monitoring Report", {"obligation_key": day_key})
		rep = frappe.get_doc("POS Monitoring Report", rep_name)

		with patch("pos_next.api.outlet_monitoring_review.publish_to_profile") as mock_pub:
			review_api._publish(rep)
			called_profiles = [call.args[2] for call in mock_pub.call_args_list]
			self.assertIn(self.profile, called_profiles)
			self.assertIn(prof2, called_profiles)

	def test_gallery_scope_and_filters(self):
		other_company = "_Test Company 1"
		reviewer_other = _create_user(REVIEWER_ROLE, "rev-other-gal", company=other_company)

		rep = self._create_submitted_report(["Rev CP 1"])

		# reviewer_other has scope on other_company -> cannot see items of self.company
		frappe.set_user(reviewer_other)
		res_other = review_api.get_gallery()
		report_names = [i["report"] for i in res_other["items"]]
		self.assertNotIn(rep.name, report_names)

		# Forged company filter raises PermissionError
		with self.assertRaises(frappe.PermissionError):
			review_api.get_gallery(filters={"company": self.company})

		# self.reviewer sees the media item
		frappe.set_user(self.reviewer)
		res_self = review_api.get_gallery()
		self.assertIn(rep.name, [i["report"] for i in res_self["items"]])

	def test_gallery_pagination_has_more(self):
		self._create_submitted_report(["Rev CP 1"])
		frappe.set_user(self.reviewer)
		# Request with page_length 10
		res = review_api.get_gallery(start=0, page_length=10)
		self.assertIn("has_more", res)
		self.assertIn("items", res)

	def test_gallery_skips_purged(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user("Administrator")
		# Mark media as purged
		frappe.db.set_value("POS Monitoring Report Media", rep.media[0].name, "purged", 1)

		frappe.set_user(self.reviewer)
		res = review_api.get_gallery()
		purged_rows = [i["media_row"] for i in res["items"] if i["media_row"] == rep.media[0].name]
		self.assertEqual(len(purged_rows), 0)

	def test_native_save_cannot_bypass_register_media_fake_files(self):
		from pos_next.services.outlet_monitoring import build_report

		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"Tmpl-FakeMedia-{uuid.uuid4().hex[:6]}",
			"cadence": "Per Shift",
			"enabled": 1,
			"due_minutes_after_open": 30,
			"checkpoints": [{"checkpoint": "Rev CP 1", "required": 1}],
			"companies": [{"company": self.company}],
		}).insert(ignore_permissions=True)

		shift = frappe.get_doc({
			"doctype": "POS Opening Shift",
			"pos_profile": self.profile,
			"company": self.company,
			"user": self.cashier,
			"posting_date": today(),
			"period_start_date": now_datetime(),
			"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 100000}],
		}).insert(ignore_permissions=True)
		shift.submit()

		draft_rep = build_report(
			t,
			"Per Shift",
			company=self.company,
			pos_profile=self.profile,
			opening_shift=shift.name,
			operational_date=today(),
			due_at=now_datetime(),
		)
		draft_rep.insert(ignore_permissions=True)

		frappe.set_user(self.cashier)
		draft_rep.reload()
		draft_rep.append("media", {
			"checkpoint_row": draft_rep.checkpoints[0].name,
			"file_url": "/files/not-real.jpg",
			"media_type": "Photo",
			"file_size": 1234,
			"purged": 0,
		})
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			draft_rep.save()

	def test_native_save_submitted_report_cannot_mark_reviewed(self):
		rev_as_submitter = self.reviewer
		rep = self._create_submitted_report(["Rev CP 1"], submitter=rev_as_submitter)
		self.assertEqual(rep.status, "Submitted")

		frappe.set_user(rev_as_submitter)
		rep.reload()
		rep.status = "Reviewed"
		rep.review_result = "Pass"
		with self.assertRaises(frappe.PermissionError):
			rep.save()

		rep.reload()
		self.assertEqual(rep.status, "Submitted")
		self.assertFalse(rep.review_result)

	def test_native_save_cannot_bypass_pending_checkpoints(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user(self.reviewer)
		rep.reload()
		self.assertEqual(rep.checkpoints[0].assessment, "Pending")
		rep.status = "Reviewed"
		with self.assertRaises(frappe.PermissionError):
			rep.save()

	def test_native_save_reviewed_report_cannot_modify_review_decision_or_metadata(self):
		rep = self._create_submitted_report(["Rev CP 1"])
		frappe.set_user("Administrator")
		rep.checkpoints[0].assessment = "Pass"
		rep.save()

		frappe.set_user(self.reviewer)
		res = review_api.finalize_review(rep.name)
		self.assertEqual(res["status"], "Reviewed")
		self.assertEqual(res["review_result"], "Pass")
		self.assertEqual(res["correction_requested"], 0)

		# Reviewer attempts native save to change correction_requested
		rep.reload()
		payload = rep.as_dict()
		payload["correction_requested"] = 1
		with self.assertRaises(frappe.PermissionError):
			frappe.client.save(payload)

		# Reviewer attempts native save to change review_note
		payload = rep.as_dict()
		payload["review_note"] = "Tampered note after review"
		with self.assertRaises(frappe.PermissionError):
			frappe.client.save(payload)

		# Reviewer attempts native save to change reviewed_by
		payload = rep.as_dict()
		payload["reviewed_by"] = self.cashier
		with self.assertRaises(frappe.PermissionError):
			frappe.client.save(payload)

	def test_native_transition_to_reviewed_rejects_correction_when_disabled(self):
		s = frappe.get_doc("POS Monitoring Settings")
		s.allow_correction = 0
		s.save(ignore_permissions=True)

		rep = self._create_submitted_report(["Rev CP 1"])
		rep.reload()
		payload = rep.as_dict()
		payload["status"] = "Reviewed"
		payload["correction_requested"] = 1
		payload["checkpoints"][0]["assessment"] = "Pass"

		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.client.save(payload)
		self.assertIn("Correction is not allowed by settings", str(cm.exception))
