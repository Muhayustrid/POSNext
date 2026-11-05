# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import os
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.outlet_monitoring_setup import REVIEWER_ROLE
from pos_next.services import outlet_monitoring_access as access


def _create_test_user(role, name_prefix="om-user"):
	email = f"{name_prefix}-{uuid.uuid4().hex[:8]}@example.com"
	was_in_import = frappe.flags.in_import
	frappe.flags.in_import = True
	try:
		user = frappe.get_doc({
			"doctype": "User",
			"email": email,
			"first_name": name_prefix,
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


def _ensure_profile_for_company(company, profile_name):
	if frappe.db.exists("POS Profile", profile_name):
		return profile_name

	# Find or create a warehouse for this company
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
		"currency": frappe.get_cached_value("Company", company, "default_currency") or "IDR",
		"write_off_account": frappe.db.get_value("Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name") or "Write Off - _TC",
		"write_off_cost_center": frappe.db.get_value("Cost Center", {"company": company, "is_group": 0}, "name") or "Main - _TC",
		"income_account": frappe.db.get_value("Account", {"company": company, "root_type": "Income", "is_group": 0}, "name") or "Sales - _TC",
		"payments": [{"mode_of_payment": "Cash", "default": 1}],
	})
	profile.insert(ignore_permissions=True)
	return profile_name


TINY_JPEG = (
	b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
	b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
	b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
	b"'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
	b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
	b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)


class TestOutletMonitoringAccess(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial_reports = set(frappe.get_all("POS Monitoring Report", pluck="name"))
		cls._suite_settings = frappe.db.get_singles_dict("POS Monitoring Settings")
		frappe.set_user("Administrator")

		cls.company_a = "_Test Company"
		cls.company_b = "_Test Company 1"
		cls.company_c = "_Test Company 2"

		cls.profile_a = _ensure_profile_for_company(cls.company_a, f"_Test Prof A {uuid.uuid4().hex[:6]}")
		cls.profile_b = _ensure_profile_for_company(cls.company_b, f"_Test Prof B {uuid.uuid4().hex[:6]}")
		cls.profile_c = _ensure_profile_for_company(cls.company_c, f"_Test Prof C {uuid.uuid4().hex[:6]}")

		cls.cashier_a = _create_test_user("POSNext Cashier", "cashier-a")
		cls.cashier_ab = _create_test_user("POSNext Cashier", "cashier-ab")
		cls.reviewer = _create_test_user(REVIEWER_ROLE, "reviewer")

		# Assign profile users
		frappe.get_doc("POS Profile", cls.profile_a).append("applicable_for_users", {"user": cls.cashier_a}).save(ignore_permissions=True)
		frappe.get_doc("POS Profile", cls.profile_a).append("applicable_for_users", {"user": cls.cashier_ab}).save(ignore_permissions=True)
		frappe.get_doc("POS Profile", cls.profile_b).append("applicable_for_users", {"user": cls.cashier_ab}).save(ignore_permissions=True)

		# Ensure test category & checkpoint & template
		if not frappe.db.exists("POS Monitoring Category", "Access Category"):
			frappe.get_doc({
				"doctype": "POS Monitoring Category",
				"category_name": "Access Category",
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", "Access CP"):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": "Access CP",
				"category": "Access Category",
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Template", "Access Template"):
			t = frappe.get_doc({
				"doctype": "POS Monitoring Template",
				"template_name": "Access Template",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [{"checkpoint": "Access CP", "required": 1}],
			})
			t.insert(ignore_permissions=True)

		frappe.db.commit()

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

		self._original_settings = frappe.db.get_singles_dict("POS Monitoring Settings")
		self.addCleanup(self._restore_settings)
		self._test_initial_reports = set(frappe.get_all("POS Monitoring Report", pluck="name"))
		self._test_initial_files = set(frappe.get_all("File", pluck="name"))

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

	def _make_report(self, company, pos_profile=None, cadence="Per Shift", docstatus=0, status="Draft", revision=1, key_suffix=None):
		key = f"acc-key-{key_suffix or uuid.uuid4().hex[:8]}"
		doc = frappe.get_doc({
			"doctype": "POS Monitoring Report",
			"template": "Access Template",
			"cadence": cadence,
			"company": company,
			"pos_profile": pos_profile,
			"operational_date": frappe.utils.today(),
			"due_at": frappe.utils.now_datetime(),
			"obligation_key": key,
			"revision": revision,
			"status": status,
			"docstatus": docstatus,
			"checkpoints": [
				{
					"checkpoint": "Access CP",
					"checkpoint_name": "Access CP",
					"category": "Access Category",
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"required": 1,
					"assessment": "Pending",
				}
			],
		})
		doc.insert(ignore_permissions=True)
		return doc

	def test_unique_obligation_revision(self):
		key = f"uniq-{uuid.uuid4().hex[:8]}"
		r1 = self._make_report(self.company_a, self.profile_a, key_suffix=key)
		self.assertTrue(r1.name)

		# Second insert with same obligation_key and revision must fail unique constraint
		r2 = frappe.get_doc({
			"doctype": "POS Monitoring Report",
			"template": "Access Template",
			"cadence": "Per Shift",
			"company": self.company_a,
			"pos_profile": self.profile_a,
			"operational_date": frappe.utils.today(),
			"due_at": frappe.utils.now_datetime(),
			"obligation_key": r1.obligation_key,
			"revision": 1,
			"status": "Draft",
			"checkpoints": [
				{
					"checkpoint": "Access CP",
					"checkpoint_name": "Access CP",
					"category": "Access Category",
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"required": 1,
				}
			],
		})
		with self.assertRaises((frappe.UniqueValidationError, frappe.DuplicateEntryError)):
			r2.insert(ignore_permissions=True)

	def test_cashier_reads_own_company_only(self):
		rep_a = self._make_report(self.company_a, self.profile_a)
		rep_b = self._make_report(self.company_b, self.profile_b)

		self.assertTrue(access.has_permission(rep_a, ptype="read", user=self.cashier_a))
		self.assertFalse(access.has_permission(rep_b, ptype="read", user=self.cashier_a))

		frappe.set_user(self.cashier_a)
		visible = frappe.get_list("POS Monitoring Report", pluck="name")
		self.assertIn(rep_a.name, visible)
		self.assertNotIn(rep_b.name, visible)

	def test_cashier_two_profiles_scope(self):
		rep_a = self._make_report(self.company_a, self.profile_a)
		rep_b = self._make_report(self.company_b, self.profile_b)
		rep_c = self._make_report(self.company_c, self.profile_c)

		self.assertTrue(access.has_permission(rep_a, ptype="read", user=self.cashier_ab))
		self.assertTrue(access.has_permission(rep_b, ptype="read", user=self.cashier_ab))
		self.assertFalse(access.has_permission(rep_c, ptype="read", user=self.cashier_ab))

	def test_cashier_cannot_write_submitted(self):
		rep = self._make_report(self.company_a, self.profile_a, docstatus=1, status="Submitted")
		self.assertFalse(access.has_permission(rep, ptype="write", user=self.cashier_a))

	def test_cashier_cannot_set_permlevel1(self):
		rep = self._make_report(self.company_a, self.profile_a)

		frappe.set_user(self.cashier_a)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.review_result = "Pass"
		doc.checkpoints[0].assessment = "Pass"
		doc.save()

		# Check reloaded from DB
		doc.reload()
		self.assertNotEqual(doc.review_result, "Pass")
		self.assertEqual(doc.checkpoints[0].assessment, "Pending")

	def test_reviewer_scoped_by_user_permission(self):
		rep_a = self._make_report(self.company_a, self.profile_a)
		rep_b = self._make_report(self.company_b, self.profile_b)

		# Add user permission for reviewer on Company A
		up = frappe.get_doc({
			"doctype": "User Permission",
			"user": self.reviewer,
			"allow": "Company",
			"for_value": self.company_a,
		})
		up.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "User Permission", up.name, force=True, ignore_permissions=True)
		frappe.clear_cache(user=self.reviewer)

		self.assertTrue(access.has_permission(rep_a, ptype="read", user=self.reviewer))
		self.assertFalse(access.has_permission(rep_b, ptype="read", user=self.reviewer))

		frappe.set_user(self.reviewer)
		visible = frappe.get_list("POS Monitoring Report", pluck="name")
		self.assertIn(rep_a.name, visible)
		self.assertNotIn(rep_b.name, visible)

	def test_private_file_follows_report(self):
		rep_a = self._make_report(self.company_a, self.profile_a)
		rep_b = self._make_report(self.company_b, self.profile_b)

		file_b = frappe.get_doc({
			"doctype": "File",
			"file_name": "test_b.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep_b.name,
			"is_private": 1,
			"content": TINY_JPEG,
		})
		file_b.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", file_b.name, force=True, ignore_permissions=True)

		file_a = frappe.get_doc({
			"doctype": "File",
			"file_name": "test_a.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep_a.name,
			"is_private": 1,
			"content": TINY_JPEG,
		})
		file_a.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", file_a.name, force=True, ignore_permissions=True)

		self.assertFalse(frappe.has_permission("File", doc=file_b, user=self.cashier_a))
		self.assertTrue(frappe.has_permission("File", doc=file_a, user=self.cashier_a))

	def test_private_file_download_endpoint_cross_company_denied(self):
		import frappe.app
		from frappe.app import application
		from frappe.core.doctype.user.user import generate_keys
		from werkzeug.test import Client
		from werkzeug.wrappers import Response

		orig_site = getattr(frappe.app, "_site", None)
		orig_sites_path = getattr(frappe.app, "_sites_path", None)

		bench_sites = frappe.utils.get_bench_path() + "/sites" if hasattr(frappe.utils, "get_bench_path") else "/home/frappe/frappe-bench/sites"
		sites_path = getattr(frappe.local, "sites_path", None) or bench_sites
		site = frappe.local.site
		frappe.app._site = site
		frappe.app._sites_path = sites_path

		rep_a = self._make_report(self.company_a, self.profile_a)
		rep_b = self._make_report(self.company_b, self.profile_b)

		file_a = frappe.get_doc({
			"doctype": "File",
			"file_name": f"dl_a_{uuid.uuid4().hex[:6]}.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep_a.name,
			"is_private": 1,
			"content": TINY_JPEG + uuid.uuid4().bytes,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", file_a.name, force=True, ignore_permissions=True)

		file_b = frappe.get_doc({
			"doctype": "File",
			"file_name": f"dl_b_{uuid.uuid4().hex[:6]}.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep_b.name,
			"is_private": 1,
			"content": TINY_JPEG + uuid.uuid4().bytes,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", file_b.name, force=True, ignore_permissions=True)
		frappe.db.commit()

		frappe.set_user("Administrator")
		keys = generate_keys(self.cashier_a)
		frappe.db.commit()

		c = Client(application, Response)
		headers = {
			"Host": site,
			"Authorization": f"token {keys['api_key']}:{keys['api_secret']}",
		}

		try:
			# Positive control: In-scope file download succeeds with 200
			resp_a = c.get(file_a.file_url, headers=headers)
			self.assertEqual(resp_a.status_code, 200)

			# Cross-company file download is denied with 403 Forbidden
			resp_b = c.get(file_b.file_url, headers=headers)
			self.assertEqual(resp_b.status_code, 403)
		finally:
			frappe.app._site = orig_site
			frappe.app._sites_path = orig_sites_path
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.set_user("Administrator")

	def test_cashier_cannot_take_over_other_company_report(self):
		rep_b = self._make_report(self.company_b, self.profile_b)
		frappe.set_user(self.cashier_a)
		payload = rep_b.as_dict()
		payload["company"] = self.company_a
		payload["pos_profile"] = self.profile_a
		with self.assertRaises(frappe.PermissionError):
			frappe.client.save(payload)
		self.assertEqual(frappe.db.get_value("POS Monitoring Report", rep_b.name, "company"), self.company_b)

	def test_cashier_cannot_modify_checkpoint_rules_or_delete_checkpoints(self):
		rep_a = self._make_report(self.company_a, self.profile_a)
		frappe.set_user(self.cashier_a)
		doc = frappe.get_doc("POS Monitoring Report", rep_a.name)
		doc.checkpoints[0].min_count = 0
		doc.checkpoints[0].required = 0
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			doc.save()
		rep_a.reload()
		self.assertEqual(rep_a.checkpoints[0].min_count, 1)
		self.assertEqual(rep_a.checkpoints[0].required, 1)

	def test_cashier_cannot_change_draft_status_via_native_save(self):
		rep = self._make_report(self.company_a, self.profile_a, status="Draft", docstatus=0)
		frappe.set_user(self.cashier_a)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.status = "Reviewed"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		rep.reload()
		self.assertEqual(rep.status, "Draft")

	def test_cashier_cannot_change_due_at_or_submission_metadata(self):
		rep = self._make_report(self.company_a, self.profile_a, status="Draft", docstatus=0)
		orig_due = str(rep.due_at)
		frappe.set_user(self.cashier_a)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.due_at = "2099-01-01 00:00:00"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		rep.reload()
		self.assertEqual(str(rep.due_at), orig_due)

	def test_cashier_cannot_set_due_at_on_new_report(self):
		frappe.set_user(self.cashier_a)
		key = f"fake-{uuid.uuid4().hex[:8]}"
		payload = {
			"doctype": "POS Monitoring Report",
			"template": "Access Template",
			"cadence": "Per Shift",
			"company": self.company_a,
			"pos_profile": self.profile_a,
			"operational_date": frappe.utils.today(),
			"due_at": "2099-01-01 00:00:00",
			"obligation_key": key,
			"revision": 1,
			"status": "Draft",
		}
		with self.assertRaises(frappe.PermissionError):
			frappe.client.insert(payload)

	def test_cashier_cannot_replace_media_row_content_via_native_save(self):
		rep = self._make_report(self.company_a, self.profile_a, status="Draft", docstatus=0)
		f1 = frappe.get_doc({
			"doctype": "File",
			"file_name": "valid.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep.name,
			"is_private": 1,
			"content": TINY_JPEG,
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", f1.name, force=True, ignore_permissions=True)

		from pos_next.api import outlet_monitoring as api

		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.save(ignore_permissions=True)

		frappe.set_user(self.cashier_a)
		api.register_media(rep.name, rep.checkpoints[0].name, f1.name)

		f_gif = frappe.get_doc({
			"doctype": "File",
			"file_name": "fake.gif",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep.name,
			"is_private": 1,
			"content": b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
		}).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", f_gif.name, force=True, ignore_permissions=True)

		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.media[0].file_url = f_gif.file_url
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_cashier_cannot_insert_assessment_on_new_report(self):
		frappe.set_user(self.cashier_a)
		key = f"fake-{uuid.uuid4().hex[:8]}"
		payload = {
			"doctype": "POS Monitoring Report",
			"template": "Access Template",
			"cadence": "Per Shift",
			"company": self.company_a,
			"pos_profile": self.profile_a,
			"operational_date": frappe.utils.today(),
			"due_at": frappe.utils.now_datetime(),
			"obligation_key": key,
			"revision": 1,
			"status": "Draft",
			"checkpoints": [
				{
					"checkpoint": "Access CP",
					"checkpoint_name": "Access CP",
					"category": "Access Category",
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"required": 1,
					"assessment": "Pass",
				}
			],
		}
		try:
			res = frappe.client.insert(payload)
			# If insert succeeded without error, assessment must NOT be Pass
			created = frappe.get_doc("POS Monitoring Report", res.get("name"))
			self.assertNotEqual(created.checkpoints[0].assessment, "Pass")
		except frappe.PermissionError:
			pass

	def test_status_transition_after_submit(self):
		rep = self._make_report(self.company_a, self.profile_a, docstatus=1, status="Submitted")
		frappe.set_user(self.reviewer)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].assessment = "Pass"
		doc.status = "Reviewed"
		doc.save()
		self.assertEqual(doc.status, "Reviewed")
		self.assertEqual(doc.docstatus, 1)

		# From Submitted to Waived
		rep2 = self._make_report(self.company_a, self.profile_a, docstatus=1, status="Submitted")
		doc2 = frappe.get_doc("POS Monitoring Report", rep2.name)
		doc2.status = "Waived"
		doc2.waive_reason = "Excluded by management"
		doc2.save()
		self.assertEqual(doc2.status, "Waived")
		self.assertEqual(doc2.docstatus, 1)

		# From Missed to Waived
		rep3 = self._make_report(self.company_a, self.profile_a, docstatus=1, status="Missed")
		doc3 = frappe.get_doc("POS Monitoring Report", rep3.name)
		doc3.status = "Waived"
		doc3.waive_reason = "Excluded due to technical issue"
		doc3.save()
		self.assertEqual(doc3.status, "Waived")
		self.assertEqual(doc3.docstatus, 1)

	def test_reviewer_cannot_cancel_report(self):
		rep = self._make_report(self.company_a, self.profile_a, docstatus=1, status="Submitted")
		frappe.set_user(self.reviewer)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
			doc.cancel()
		rep.reload()
		self.assertEqual(rep.docstatus, 1)

	def test_cashier_cannot_delete_file_of_reviewed_report(self):
		from pos_next.api import outlet_monitoring as api

		rep = self._make_report(self.company_a, self.profile_a, status="Draft", docstatus=0)
		f1 = frappe.get_doc({
			"doctype": "File",
			"file_name": f"guard_del_{uuid.uuid4().hex[:6]}.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep.name,
			"is_private": 1,
			"content": TINY_JPEG + uuid.uuid4().bytes,
		}).insert(ignore_permissions=True)
		frappe.db.set_value("File", f1.name, "owner", self.cashier_a, update_modified=False)

		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.save(ignore_permissions=True)

		frappe.set_user(self.cashier_a)
		api.register_media(rep.name, rep.checkpoints[0].name, f1.name)

		# Mark report Reviewed
		frappe.set_user("Administrator")
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].assessment = "Pass"
		doc.status = "Reviewed"
		doc.docstatus = 1
		doc.flags.ignore_permissions = True
		doc.save()

		# Cashier attempts native delete of File
		frappe.set_user(self.cashier_a)
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			frappe.client.delete("File", f1.name)
		self.assertTrue(frappe.db.exists("File", f1.name))
		# A rejected delete must not remove the blob: File.on_trash unlinks it from
		# disk before the guard would run, so the guard has to run first.
		self.assertTrue(os.path.exists(f1.get_full_path()))

	def test_cashier_cannot_detach_or_deprivatize_file_of_submitted_report(self):
		from pos_next.api import outlet_monitoring as api

		rep = self._make_report(self.company_a, self.profile_a, status="Draft", docstatus=0)
		f1 = frappe.get_doc({
			"doctype": "File",
			"file_name": f"guard_save_{uuid.uuid4().hex[:6]}.jpg",
			"attached_to_doctype": "POS Monitoring Report",
			"attached_to_name": rep.name,
			"is_private": 1,
			"content": TINY_JPEG + uuid.uuid4().bytes,
		}).insert(ignore_permissions=True)
		frappe.db.set_value("File", f1.name, "owner", self.cashier_a, update_modified=False)

		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.save(ignore_permissions=True)

		frappe.set_user(self.cashier_a)
		api.register_media(rep.name, rep.checkpoints[0].name, f1.name)
		api.submit_report(rep.name)

		# Cashier attempts native save to detach and de-privatize the file
		frappe.set_user(self.cashier_a)
		file_doc = frappe.get_doc("File", f1.name)
		payload = file_doc.as_dict()
		payload["attached_to_doctype"] = None
		payload["attached_to_name"] = None
		payload["is_private"] = 0

		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			frappe.client.save(payload)

		stored = frappe.db.get_value("File", f1.name, ["attached_to_doctype", "is_private"], as_dict=True)
		self.assertEqual(stored.attached_to_doctype, "POS Monitoring Report")
		self.assertEqual(stored.is_private, 1)
		# A rejected save must not have moved the blob: File.validate relocates the
		# file on disk before the guard would run, so the guard has to run first.
		self.assertTrue(os.path.exists(f1.get_full_path()))
		self.assertFalse(
			os.path.exists(frappe.get_site_path("public", "files", os.path.basename(f1.file_url)))
		)
