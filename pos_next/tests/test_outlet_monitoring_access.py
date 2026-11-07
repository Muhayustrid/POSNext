# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import os
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.outlet_monitoring_setup import REVIEWER_ROLE
from pos_next.services import outlet_monitoring_access as access
from pos_next.tests.outlet_monitoring_test_utils import (
	delete_owned_files,
	delete_owned_fixtures,
	insert_owned_user,
	owned_file_names,
	owned_fixtures,
	restore_settings_state,
	snapshot_settings_state,
)


def _create_test_user(role, name_prefix="om-user"):
	email = f"{_RUN_ID}-{name_prefix}-{uuid.uuid4().hex[:8]}@example.com"
	was_in_import = frappe.flags.in_import
	frappe.flags.in_import = True
	try:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": name_prefix,
				"enabled": 1,
				"send_welcome_email": 0,
			}
		)
		user.flags.no_welcome_mail = True
		insert_owned_user(user, _RUN_ID, role)
	finally:
		frappe.flags.in_import = was_in_import

	frappe.clear_cache(user=email)
	return email


def _ensure_profile_for_company(company, profile_name):
	if frappe.db.exists("POS Profile", profile_name):
		return profile_name

	# Find or create a warehouse for this company
	warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	if not warehouse:
		warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"{_RUN_ID} WH-{uuid.uuid4().hex[:6]}",
					"company": company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	profile = frappe.get_doc(
		{
			"doctype": "POS Profile",
			"name": profile_name,
			"company": company,
			"warehouse": warehouse,
			"currency": frappe.get_cached_value("Company", company, "default_currency") or "IDR",
			"write_off_account": frappe.db.get_value(
				"Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name"
			)
			or "Write Off - _TC",
			"write_off_cost_center": frappe.db.get_value(
				"Cost Center", {"company": company, "is_group": 0}, "name"
			)
			or "Main - _TC",
			"income_account": frappe.db.get_value(
				"Account", {"company": company, "root_type": "Income", "is_group": 0}, "name"
			)
			or "Sales - _TC",
			"payments": [{"mode_of_payment": "Cash", "default": 1}],
		}
	)
	profile.insert(ignore_permissions=True)
	return profile_name


_RUN_ID = uuid.uuid4().hex[:6]
# Run-unique fixtures: this suite only ever mutates records it created.
ACCESS_LABEL = f"{_RUN_ID} Access Label"
ACCESS_CATEGORY = f"{_RUN_ID} Access Category"
ACCESS_CP = f"{_RUN_ID} Access CP"
ACCESS_TEMPLATE = f"{_RUN_ID} Access Template"
ACCESS_LABEL_B = f"{_RUN_ID} Access Label B"


TINY_JPEG = (
	b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
	b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
	b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
	b"'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
	b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
	b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)


# Fixture doctypes this suite creates, with child tables for
# dependency-order deletion (parents last). Labels use DB-only deletes:
# POSMonitoringLabel.on_trash always rejects.
_FIXTURE_DOCTYPES = (
	"POS Monitoring Report",
	"POS Monitoring Template",
	"POS Monitoring Checkpoint",
	"POS Monitoring Category",
	"POS Monitoring Label",
	"POS Profile",
	"User",
)
_FIXTURE_CHILDREN = {
	"POS Monitoring Report": ("POS Monitoring Report Checkpoint", "POS Monitoring Report Media"),
	"POS Monitoring Template": (
		"POS Monitoring Template Checkpoint",
		"POS Monitoring Template Company",
		"POS Monitoring Template POS Profile",
		"POS Monitoring Template Shift Type",
	),
	"POS Monitoring Checkpoint": ("POS Monitoring Label Item",),
	"POS Monitoring Category": ("POS Monitoring Label Item",),
	"POS Profile": (
		"POS Profile User",
		"POS Payment Method",
		"POS Item Group",
		"POS Customer Group",
	),
	"User": ("Has Role",),
}


def _snapshot_fixtures():
	return {dt: set(frappe.get_all(dt, pluck="name")) for dt in _FIXTURE_DOCTYPES}


# This suite's own name namespaces; a snapshot diff alone is not ownership.
_FIXTURE_PREFIXES = {
	"POS Monitoring Category": (f"{_RUN_ID} Access Category",),
	"POS Monitoring Checkpoint": (f"{_RUN_ID} Access CP",),
	"POS Monitoring Label": (f"{_RUN_ID} Access Label",),
	"POS Monitoring Template": (f"{_RUN_ID} Access Template",),
	"POS Profile": (
		f"{_RUN_ID} _Test Prof A ",
		f"{_RUN_ID} _Test Prof B ",
		f"{_RUN_ID} _Test Prof C ",
	),
	"User": (
		f"{_RUN_ID}-cashier-a-",
		f"{_RUN_ID}-cashier-ab-",
		f"{_RUN_ID}-cashier-b-",
		f"{_RUN_ID}-reviewer-",
	),
}
_FIXTURE_LINKS = {
	"POS Monitoring Report": (("template", "POS Monitoring Template"),),
}
# Created once per class, so absent from a per-test snapshot but still ours.
_ANCHORS = {"POS Monitoring Template": (ACCESS_TEMPLATE,)}


def _delete_new_fixtures(initial):
	"""Delete owned fixtures, innermost children first."""
	owned = owned_fixtures(initial, _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID)
	delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _FIXTURE_DOCTYPES)


def _restore_settings_exact(state):
	# Exact restore: every column of every Settings child row and the exact
	# tabSingles rows, not a partial name/label/idx rebuild.
	restore_settings_state(state)




class TestOutletMonitoringAccess(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial = _snapshot_fixtures()
		cls._suite_state = snapshot_settings_state()
		frappe.set_user("Administrator")

		cls.company_a = "_Test Company"
		cls.company_b = "_Test Company 1"
		cls.company_c = "_Test Company 2"

		cls.profile_a = _ensure_profile_for_company(cls.company_a, f"{_RUN_ID} _Test Prof A {uuid.uuid4().hex[:6]}")
		cls.profile_b = _ensure_profile_for_company(cls.company_b, f"{_RUN_ID} _Test Prof B {uuid.uuid4().hex[:6]}")
		cls.profile_c = _ensure_profile_for_company(cls.company_c, f"{_RUN_ID} _Test Prof C {uuid.uuid4().hex[:6]}")

		cls.cashier_a = _create_test_user("POSNext Cashier", "cashier-a")
		cls.cashier_ab = _create_test_user("POSNext Cashier", "cashier-ab")
		cls.reviewer = _create_test_user(REVIEWER_ROLE, "reviewer")

		# Assign profile users
		frappe.get_doc("POS Profile", cls.profile_a).append(
			"applicable_for_users", {"user": cls.cashier_a}
		).save(ignore_permissions=True)
		frappe.get_doc("POS Profile", cls.profile_a).append(
			"applicable_for_users", {"user": cls.cashier_ab}
		).save(ignore_permissions=True)
		frappe.get_doc("POS Profile", cls.profile_b).append(
			"applicable_for_users", {"user": cls.cashier_ab}
		).save(ignore_permissions=True)

		# Ensure test label, category & checkpoint & template
		if not frappe.db.exists("POS Monitoring Label", ACCESS_LABEL):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Label",
					"label_name": ACCESS_LABEL,
					"color": "Blue",
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Category", ACCESS_CATEGORY):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Category",
					"category_name": ACCESS_CATEGORY,
					"labels": [{"label": ACCESS_LABEL}],
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", ACCESS_CP):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Checkpoint",
					"checkpoint_name": ACCESS_CP,
					"category": ACCESS_CATEGORY,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
				}
			).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Template", ACCESS_TEMPLATE):
			t = frappe.get_doc(
				{
					"doctype": "POS Monitoring Template",
					"template_name": ACCESS_TEMPLATE,
					"cadence": "Per Shift",
					"due_minutes_after_open": 30,
					"checkpoints": [{"checkpoint": ACCESS_CP, "required": 1}],
				}
			)
			t.insert(ignore_permissions=True)

		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		_delete_new_fixtures(getattr(cls, "_suite_initial", {}))
		_restore_settings_exact(cls._suite_state)
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

		self._original_state = snapshot_settings_state()
		self.addCleanup(self._restore_settings)
		self._initial_fixtures = _snapshot_fixtures()

	def tearDown(self):
		frappe.set_user("Administrator")
		# Reports and their files are claimed through the template this suite
		# owns, so no other writer's rows can be swept up here.
		owned = owned_fixtures(
			getattr(self, "_initial_fixtures", {}),
			_FIXTURE_PREFIXES,
			_FIXTURE_LINKS,
			_RUN_ID,
			_ANCHORS,
		)
		# Reports first: a File still referenced by a report media row is
		# protected by the monitoring File guard and would leak as an orphan.
		delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _FIXTURE_DOCTYPES)
		delete_owned_files(owned_file_names(owned.get("POS Monitoring Report")))
		self._restore_settings()
		frappe.db.commit()
		super().tearDown()

	def _restore_settings(self):
		_restore_settings_exact(self._original_state)

	def _make_report(
		self,
		company,
		pos_profile=None,
		cadence="Per Shift",
		docstatus=0,
		status="Draft",
		revision=1,
		key_suffix=None,
		label=None,
	):
		import json

		from pos_next.services.outlet_monitoring import resolve_label_options

		key = f"acc-key-{key_suffix or uuid.uuid4().hex[:8]}"
		cp = frappe.get_doc("POS Monitoring Checkpoint", ACCESS_CP)
		options = resolve_label_options(cp)
		doc = frappe.get_doc(
			{
				"doctype": "POS Monitoring Report",
				"template": ACCESS_TEMPLATE,
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
						"checkpoint": ACCESS_CP,
						"checkpoint_name": ACCESS_CP,
						"category": ACCESS_CATEGORY,
						"media_type": "Photo",
						"min_count": 1,
						"max_count": 2,
						"required": 1,
						"label_options": json.dumps(options),
						"cashier_can_label": 0,
						"label": label,
					}
				],
			}
		)
		doc.flags.internal_report_build = True
		doc.insert(ignore_permissions=True)
		return doc

	def test_unique_obligation_revision(self):
		key = f"uniq-{uuid.uuid4().hex[:8]}"
		r1 = self._make_report(self.company_a, self.profile_a, key_suffix=key)
		self.assertTrue(r1.name)

		# Second insert with same obligation_key and revision must fail unique constraint.
		# §3.5 server-only creation guard fires first on native saves (validate runs
		# before the DB constraint), so use the internal build flag to reach it.
		r2 = frappe.get_doc(
			{
				"doctype": "POS Monitoring Report",
				"template": ACCESS_TEMPLATE,
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
						"checkpoint": ACCESS_CP,
						"checkpoint_name": ACCESS_CP,
						"category": ACCESS_CATEGORY,
						"media_type": "Photo",
						"min_count": 1,
						"max_count": 2,
						"required": 1,
					}
				],
			}
		)
		r2.flags.internal_report_build = True
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
		doc.checkpoints[0].label = ACCESS_LABEL
		doc.checkpoints[0].assessment_note = "cashier note"
		doc.save()

		# Check reloaded from DB
		doc.reload()
		self.assertNotEqual(doc.checkpoints[0].label, ACCESS_LABEL)
		self.assertNotEqual(doc.checkpoints[0].assessment_note, "cashier note")

	def test_reviewer_scoped_by_user_permission(self):
		rep_a = self._make_report(self.company_a, self.profile_a)
		rep_b = self._make_report(self.company_b, self.profile_b)

		# Add user permission for reviewer on Company A
		up = frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": self.reviewer,
				"allow": "Company",
				"for_value": self.company_a,
			}
		)
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

		file_b = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "test_b.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep_b.name,
				"is_private": 1,
				"content": TINY_JPEG,
			}
		)
		file_b.insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", file_b.name, force=True, ignore_permissions=True)

		file_a = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "test_a.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep_a.name,
				"is_private": 1,
				"content": TINY_JPEG,
			}
		)
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

		bench_sites = (
			frappe.utils.get_bench_path() + "/sites"
			if hasattr(frappe.utils, "get_bench_path")
			else "/home/frappe/frappe-bench/sites"
		)
		sites_path = getattr(frappe.local, "sites_path", None) or bench_sites
		site = frappe.local.site
		frappe.app._site = site
		frappe.app._sites_path = sites_path

		rep_a = self._make_report(self.company_a, self.profile_a)
		rep_b = self._make_report(self.company_b, self.profile_b)

		file_a = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"dl_a_{uuid.uuid4().hex[:6]}.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep_a.name,
				"is_private": 1,
				"content": TINY_JPEG + uuid.uuid4().bytes,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", file_a.name, force=True, ignore_permissions=True)

		file_b = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"dl_b_{uuid.uuid4().hex[:6]}.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep_b.name,
				"is_private": 1,
				"content": TINY_JPEG + uuid.uuid4().bytes,
			}
		).insert(ignore_permissions=True)
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
			"template": ACCESS_TEMPLATE,
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
		f1 = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "valid.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep.name,
				"is_private": 1,
				"content": TINY_JPEG,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", f1.name, force=True, ignore_permissions=True)

		from pos_next.api import outlet_monitoring as api

		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.save(ignore_permissions=True)

		frappe.set_user(self.cashier_a)
		api.register_media(rep.name, rep.checkpoints[0].name, f1.name)

		f_gif = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "fake.gif",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep.name,
				"is_private": 1,
				"content": b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
			}
		).insert(ignore_permissions=True)
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
			"template": ACCESS_TEMPLATE,
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
					"checkpoint": ACCESS_CP,
					"checkpoint_name": ACCESS_CP,
					"category": ACCESS_CATEGORY,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"required": 1,
					"label": ACCESS_LABEL,
					"cashier_label": ACCESS_LABEL,
				}
			],
		}
		try:
			res = frappe.client.insert(payload)
			# If insert succeeded without error, labels must NOT be set
			created = frappe.get_doc("POS Monitoring Report", res.get("name"))
			self.assertNotEqual(created.checkpoints[0].label, ACCESS_LABEL)
			self.assertNotEqual(created.checkpoints[0].cashier_label, ACCESS_LABEL)
		except frappe.PermissionError:
			pass

	def test_reviewer_can_change_label_on_submitted_report(self):
		# A second label must be in the category so the snapshot carries both.
		if not frappe.db.exists("POS Monitoring Label", ACCESS_LABEL_B):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Label",
					"label_name": ACCESS_LABEL_B,
					"color": "Green",
				}
			).insert(ignore_permissions=True)
		cat = frappe.get_doc("POS Monitoring Category", ACCESS_CATEGORY)
		if ACCESS_LABEL_B not in [r.label for r in cat.labels]:
			cat.append("labels", {"label": ACCESS_LABEL_B})
			cat.save(ignore_permissions=True)

		rep = self._make_report(
			self.company_a, self.profile_a, docstatus=1, status="Submitted", label=ACCESS_LABEL
		)
		frappe.set_user(self.reviewer)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].label = ACCESS_LABEL_B
		doc.save()
		self.assertEqual(doc.status, "Submitted")
		doc.reload()
		self.assertEqual(doc.checkpoints[0].label, ACCESS_LABEL_B)
		frappe.set_user("Administrator")

	def test_finalized_report_lock_blocks_label_change(self):
		for final_status in ("Reviewed", "Waived"):
			rep = self._make_report(self.company_a, self.profile_a, docstatus=1, status=final_status)
			frappe.set_user(self.reviewer)
			doc = frappe.get_doc("POS Monitoring Report", rep.name)
			doc.checkpoints[0].label = ACCESS_LABEL
			with self.assertRaises(frappe.PermissionError):
				doc.save()
			frappe.set_user("Administrator")
			rep.reload()
			self.assertNotEqual(rep.checkpoints[0].label, ACCESS_LABEL)

	def test_status_transition_after_submit(self):
		rep = self._make_report(
			self.company_a, self.profile_a, docstatus=1, status="Submitted", label=ACCESS_LABEL
		)
		frappe.set_user(self.reviewer)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		# Reload label_options through the same doc: native save of a Label
		# link needs the snapshot's label; set both from the stored row.
		doc.checkpoints[0].label = doc.checkpoints[0].label or ACCESS_LABEL
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
		f1 = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"guard_del_{uuid.uuid4().hex[:6]}.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep.name,
				"is_private": 1,
				"content": TINY_JPEG + uuid.uuid4().bytes,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("File", f1.name, "owner", self.cashier_a, update_modified=False)

		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.save(ignore_permissions=True)

		frappe.set_user(self.cashier_a)
		api.register_media(rep.name, rep.checkpoints[0].name, f1.name)

		# Mark report Reviewed
		frappe.set_user("Administrator")
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].label = ACCESS_LABEL
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
		f1 = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"guard_save_{uuid.uuid4().hex[:6]}.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep.name,
				"is_private": 1,
				"content": TINY_JPEG + uuid.uuid4().bytes,
			}
		).insert(ignore_permissions=True)
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

	# ---- Task 2: server-only creation, immutable fields, Manual access ----

	def test_native_insert_by_cashier_rejected(self):
		frappe.set_user(self.cashier_a)
		for cadence, extra in (
			("Per Hari", {}),
			("Manual", {}),
			("Per Hari", {"obligation_key": f"forged-{uuid.uuid4().hex[:8]}"}),
		):
			payload = {
				"doctype": "POS Monitoring Report",
				"template": ACCESS_TEMPLATE,
				"cadence": cadence,
				"company": self.company_a,
				"pos_profile": self.profile_a,
				"operational_date": frappe.utils.today(),
				"due_at": frappe.utils.now_datetime(),
				"obligation_key": extra.get("obligation_key", f"fake-{uuid.uuid4().hex[:8]}"),
				"revision": 1,
				"status": "Draft",
			}
			with self.assertRaises(frappe.PermissionError, msg=cadence):
				frappe.client.insert(payload)
		frappe.set_user("Administrator")

	def test_cashier_cannot_insert_with_label_or_cashier_label(self):
		frappe.set_user(self.cashier_a)
		payload = {
			"doctype": "POS Monitoring Report",
			"template": ACCESS_TEMPLATE,
			"cadence": "Per Hari",
			"company": self.company_a,
			"pos_profile": self.profile_a,
			"operational_date": frappe.utils.today(),
			"due_at": frappe.utils.now_datetime(),
			"obligation_key": f"fake-{uuid.uuid4().hex[:8]}",
			"revision": 1,
			"status": "Draft",
			"flags": {"internal_report_build": True},
			"checkpoints": [
				{
					"checkpoint": ACCESS_CP,
					"checkpoint_name": ACCESS_CP,
					"category": ACCESS_CATEGORY,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"required": 1,
					"label": ACCESS_LABEL,
				}
			],
		}
		with self.assertRaises(frappe.PermissionError):
			frappe.client.insert(payload)
		frappe.set_user("Administrator")

	def test_new_immutable_fields_locked(self):
		rep = self._make_report(self.company_a, self.profile_a)
		frappe.set_user(self.reviewer)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.opens_at = "2099-01-01 00:00:00"
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		frappe.set_user("Administrator")
		rep.reload()
		self.assertNotEqual(str(rep.opens_at), "2099-01-01 00:00:00")

	def test_cashier_write_manual_only_own_profile(self):
		# cashier_ab holds both profile_a and profile_b, so a profile only
		# cashier_ab lacks is needed for the negative case.
		cashier_b = _create_test_user("POSNext Cashier", "cashier-b")
		frappe.get_doc("POS Profile", self.profile_b).append(
			"applicable_for_users", {"user": cashier_b}
		).save(ignore_permissions=True)
		rep = self._make_report(self.company_a, None, cadence="Manual")
		frappe.db.set_value("POS Monitoring Report", rep.name, "pos_profile", self.profile_a)
		rep.reload()
		self.assertTrue(access.has_permission(rep, ptype="write", user=self.cashier_a))
		self.assertFalse(access.has_permission(rep, ptype="write", user=cashier_b))
		frappe.set_user("Administrator")
