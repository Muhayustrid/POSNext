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
from pos_next.tests.outlet_monitoring_test_utils import (
	delete_owned_files,
	delete_owned_fixtures,
	insert_owned_user,
	owned_file_names,
	owned_fixtures,
	restore_settings_state,
	snapshot_settings_state,
)

TINY_JPEG = (
	b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
	b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
	b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
	b"'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
	b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
	b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)

TINY_MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2mp41\x00\x00\x00\x08free"

_RUN_ID = uuid.uuid4().hex[:6]
# Run-unique fixtures: this suite only ever mutates records it created.
LABEL_A = f"{_RUN_ID} Rev Label A"
LABEL_B = f"{_RUN_ID} Rev Label B"
REV_CATEGORY = f"{_RUN_ID} Rev Category"
REV_CP_1 = f"{_RUN_ID} Rev CP 1"
REV_CP_2 = f"{_RUN_ID} Rev CP 2"
REV_TEMPLATE = f"{_RUN_ID} Rev Template"


def _create_user(role, prefix="review-test", company=None):
	email = f"{_RUN_ID}-{prefix}-{uuid.uuid4().hex[:8]}@example.com"
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
		insert_owned_user(user, _RUN_ID, role)
	finally:
		frappe.flags.in_import = was_in_import


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

	pname = f"{_RUN_ID}-Rev-Prof-{uuid.uuid4().hex[:6]}"
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


# Fixture doctypes this suite creates, with child tables for
# dependency-order deletion (parents last). Labels use DB-only deletes:
# POSMonitoringLabel.on_trash always rejects.
_FIXTURE_DOCTYPES = (
	"POS Monitoring Report",
	"POS Monitoring Template",
	"POS Monitoring Checkpoint",
	"POS Monitoring Category",
	"POS Monitoring Label",
	"POS Opening Shift",
	"POS Closing Shift",
	"POS Profile",
	"User Permission",
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
	"POS Opening Shift": ("POS Opening Shift Detail",),
	"POS Closing Shift": (
		"POS Closing Shift Spend",
		"Sales Invoice Reference",
		"POS Closing Shift Detail",
		"POS Closing Shift Taxes",
		"POS Payment Entry Reference",
	),
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
	"POS Monitoring Category": (f"{_RUN_ID} Rev Category",),
	"POS Monitoring Checkpoint": (f"{_RUN_ID} Rev CP",),
	"POS Monitoring Label": (f"{_RUN_ID} Rev Label",),
	"POS Monitoring Template": (
		f"{_RUN_ID} Rev Template",
		f"{_RUN_ID} Tmpl-Rev-",
		f"{_RUN_ID} Tmpl-Day-",
		f"{_RUN_ID} Tmpl-FakeMedia-",
	),
	"POS Profile": (f"{_RUN_ID}-Rev-Prof-",),
	"User": (f"{_RUN_ID}-rev-",),
}
_FIXTURE_LINKS = {
	# Shift names come from the site naming series, so they are claimed through
	# the POS Profile this suite created rather than by name.
	"POS Opening Shift": (("pos_profile", "POS Profile"),),
	"POS Closing Shift": (("pos_profile", "POS Profile"),),
	"POS Monitoring Report": (("template", "POS Monitoring Template"),),
	"User Permission": (("user", "User"),),
}
# Created once per class, so absent from a per-test snapshot but still ours.
_ANCHORS = {"POS Monitoring Template": (REV_TEMPLATE,)}


def _delete_new_fixtures(initial):
	"""Delete owned fixtures, innermost children first."""
	owned = owned_fixtures(initial, _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID)
	delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _FIXTURE_DOCTYPES)


def _restore_settings_exact(state):
	# Exact restore: every column of every Settings child row and the exact
	# tabSingles rows, not a partial name/label/idx rebuild.
	restore_settings_state(state)




class TestOutletMonitoringReview(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial = _snapshot_fixtures()
		cls._suite_state = snapshot_settings_state()
		for lbl, color in ((LABEL_A, "Blue"), (LABEL_B, "Green")):
			if not frappe.db.exists("POS Monitoring Label", lbl):
				frappe.get_doc({
					"doctype": "POS Monitoring Label",
					"label_name": lbl,
					"color": color,
				}).insert(ignore_permissions=True)
		if not frappe.db.exists("POS Monitoring Category", REV_CATEGORY):
			frappe.get_doc({
				"doctype": "POS Monitoring Category",
				"category_name": REV_CATEGORY,
				"labels": [{"label": LABEL_A}, {"label": LABEL_B}],
			}).insert(ignore_permissions=True)
		else:
			cat = frappe.get_doc("POS Monitoring Category", REV_CATEGORY)
			existing = {r.label for r in cat.labels}
			changed = False
			for lbl in (LABEL_A, LABEL_B):
				if lbl not in existing:
					cat.append("labels", {"label": lbl})
					changed = True
			if changed:
				cat.save(ignore_permissions=True)
		for cp in (REV_CP_1, REV_CP_2):
			if not frappe.db.exists("POS Monitoring Checkpoint", cp):
				frappe.get_doc({
					"doctype": "POS Monitoring Checkpoint",
					"checkpoint_name": cp,
					"category": REV_CATEGORY,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
				}).insert(ignore_permissions=True)
		if not frappe.db.exists("POS Monitoring Template", REV_TEMPLATE):
			frappe.get_doc({
				"doctype": "POS Monitoring Template",
				"template_name": REV_TEMPLATE,
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [
					{"checkpoint": REV_CP_1, "required": 1},
					{"checkpoint": REV_CP_2, "required": 1},
				],
			}).insert(ignore_permissions=True)
		cls.company = "_Test Company"
		if not frappe.db.exists("Company", cls.company):
			frappe.get_doc({
				"doctype": "Company",
				"company_name": cls.company,
				"default_currency": "IDR",
			}).insert(ignore_permissions=True)
		cls.profile = _setup_profile(cls.company)
		cls.cashier = _create_user("POSNext Cashier", "rev-cashier")
		cls.reviewer = _create_user(REVIEWER_ROLE, "rev-reviewer", company=cls.company)
		pdoc = frappe.get_doc("POS Profile", cls.profile)
		pdoc.append("applicable_for_users", {"user": cls.cashier})
		pdoc.save(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		# Full-suite safety net: catches everything the per-test snapshot
		# teardown may have missed.
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

		frappe.set_user("Administrator")
		self._original_state = snapshot_settings_state()
		self.addCleanup(self._restore_settings)
		self._test_initial = _snapshot_fixtures()
		self.company = self.__class__.company
		self.profile = self.__class__.profile
		self.cashier = self.__class__.cashier
		self.reviewer = self.__class__.reviewer

		# Enable settings
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.prevent_self_review = 1
		s.notify_reviewers_on_submit = 1
		s.notify_reporter_on_review = 1
		s.save(ignore_permissions=True)

	def _restore_settings(self):
		_restore_settings_exact(self._original_state)

	def tearDown(self):
		frappe.set_user("Administrator")
		# Reports and their files are claimed through the templates this suite
		# owns, so no other writer's rows can be swept up here.
		owned = owned_fixtures(
			getattr(self, "_test_initial", {}), _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID, _ANCHORS
		)
		# Reports first: a File still referenced by a report media row is
		# protected by the monitoring File guard and would leak as an orphan.
		delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _FIXTURE_DOCTYPES)
		delete_owned_files(owned_file_names(owned.get("POS Monitoring Report")))
		self._restore_settings()
		frappe.db.commit()
		super().tearDown()

	def _make_report(self, docstatus=0, status="Draft", label=None, submitted_by=None):
		"""Light report with a real label snapshot, no media/submit machinery."""
		import json

		from pos_next.services.outlet_monitoring import resolve_label_options

		cp = frappe.get_doc("POS Monitoring Checkpoint", REV_CP_1)
		options = resolve_label_options(cp)
		doc = frappe.get_doc({
			"doctype": "POS Monitoring Report",
			"template": REV_TEMPLATE,
			"cadence": "Per Shift",
			"company": self.company,
			"pos_profile": self.profile,
			"operational_date": today(),
			"due_at": now_datetime(),
			"obligation_key": f"rev-key-{uuid.uuid4().hex[:8]}",
			"revision": 1,
			"status": status,
			"docstatus": docstatus,
			"submitted_by": submitted_by,
			"checkpoints": [
				{
					"checkpoint": REV_CP_1,
					"checkpoint_name": REV_CP_1,
					"category": REV_CATEGORY,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"required": 1,
					"label_options": json.dumps(options),
					"cashier_can_label": 0,
					"label": label,
				}
			],
		})
		doc.flags.internal_report_build = True
		doc.insert(ignore_permissions=True)
		return doc

	def _label_all_rows(self, rep, label=LABEL_A):
		frappe.set_user("Administrator")
		rep.reload()
		for cp_row in rep.checkpoints:
			cp_row.label = label
		rep.save()
		rep.reload()

	def _create_submitted_report(self, checkpoints=None, cadence="Per Shift", submitter=None, pos_profile=None):
		cps = checkpoints or [REV_CP_1]
		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"{_RUN_ID} Tmpl-Rev-{uuid.uuid4().hex[:6]}",
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

	def test_reviewer_partial_label_save_on_submitted(self):
		rep = self._make_report(docstatus=1, status="Submitted")

		# Partial save: one label set, the other row untouched.
		frappe.set_user(self.reviewer)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].label = LABEL_A
		doc.save()
		self.assertEqual(doc.status, "Submitted")
		doc.reload()
		self.assertEqual(doc.checkpoints[0].label, LABEL_A)

		# Clearing the label again is allowed.
		doc.checkpoints[0].label = None
		doc.save()
		doc.reload()
		self.assertFalse(doc.checkpoints[0].label)
		frappe.set_user("Administrator")

	def test_label_outside_options_rejected(self):
		rep = self._make_report(docstatus=1, status="Submitted")

		# Native save with an existing label that is not in this row's
		# snapshot must fail.
		frappe.set_user(self.reviewer)
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].label = "Sesuai SOP"
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		frappe.set_user("Administrator")

		# finalize_review must fail the same way when the stored label is
		# outside the snapshot (e.g. edited directly in the database).
		rep.reload()
		frappe.db.set_value(
			"POS Monitoring Report Checkpoint", rep.checkpoints[0].name, "label", "Sesuai SOP"
		)
		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)
		frappe.set_user("Administrator")

	def test_reviewed_transition_requires_all_labels_native_and_finalize(self):
		# finalize_review without labels.
		rep = self._make_report(docstatus=1, status="Submitted")
		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)

		# Native transition without labels.
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.status = "Reviewed"
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		frappe.set_user("Administrator")

		# Both paths succeed once every row carries a label.
		rep2 = self._make_report(docstatus=1, status="Submitted")
		self._label_all_rows(rep2)
		frappe.set_user(self.reviewer)
		res = review_api.finalize_review(rep2.name)
		self.assertEqual(res["status"], "Reviewed")
		frappe.set_user("Administrator")

		rep3 = self._make_report(docstatus=1, status="Submitted")
		self._label_all_rows(rep3)
		frappe.set_user(self.reviewer)
		doc3 = frappe.get_doc("POS Monitoring Report", rep3.name)
		doc3.status = "Reviewed"
		doc3.save()
		self.assertEqual(doc3.status, "Reviewed")
		frappe.set_user("Administrator")

	def test_reviewed_and_waived_lock_label(self):
		for final_status in ("Reviewed", "Waived"):
			with self.subTest(final_status=final_status):
				if final_status == "Reviewed":
					rep = self._make_report(docstatus=1, status="Submitted")
					self._label_all_rows(rep)
					frappe.set_user(self.reviewer)
					review_api.finalize_review(rep.name)
				else:
					rep = self._make_report(docstatus=1, status="Submitted")
					frappe.set_user(self.reviewer)
					review_api.waive(rep.name, "Renovation")
				rep.reload()
				self.assertEqual(rep.status, final_status)

				doc = frappe.get_doc("POS Monitoring Report", rep.name)
				doc.checkpoints[0].label = LABEL_B
				with self.assertRaises(frappe.PermissionError):
					doc.save()
				doc.reload()
				doc.checkpoints[0].assessment_note = "Changed after finalize"
				with self.assertRaises(frappe.PermissionError):
					doc.save()
				frappe.set_user("Administrator")
				rep.reload()
				self.assertNotEqual(rep.checkpoints[0].label, LABEL_B)

	def test_disabled_label_in_snapshot_still_selectable(self):
		rep = self._make_report(docstatus=1, status="Submitted")
		row = rep.checkpoints[0].name

		frappe.set_user("Administrator")
		frappe.db.set_value("POS Monitoring Label", LABEL_A, "disabled", 1)
		self.addCleanup(frappe.db.set_value, "POS Monitoring Label", LABEL_A, "disabled", 0)

		# The custom Link query still returns the disabled snapshot label.
		frappe.set_user(self.reviewer)
		res = review_api.label_options_query(
			"POS Monitoring Report Checkpoint",
			"",
			"label",
			0,
			20,
			filters={"report": rep.name, "checkpoint_row": row},
		)
		labels = [r[0] for r in res]
		self.assertIn(LABEL_A, labels)

		# And a reviewer can still save it natively.
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.checkpoints[0].label = LABEL_A
		doc.save()
		doc.reload()
		self.assertEqual(doc.checkpoints[0].label, LABEL_A)
		frappe.set_user("Administrator")

	def test_label_options_query_auth(self):
		rep = self._make_report(docstatus=1, status="Submitted")
		other = self._make_report(docstatus=1, status="Submitted")
		row = rep.checkpoints[0].name

		# Cashier is not a reviewer.
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.PermissionError):
			review_api.label_options_query(
				"POS Monitoring Report Checkpoint",
				"",
				"label",
				0,
				20,
				filters={"report": rep.name, "checkpoint_row": row},
			)

		# Reviewer scoped to another company cannot query this report.
		other_company = "_Test Company 1"
		reviewer_other = _create_user(REVIEWER_ROLE, "rev-other-q", company=other_company)
		frappe.set_user(reviewer_other)
		with self.assertRaises(frappe.PermissionError):
			review_api.label_options_query(
				"POS Monitoring Report Checkpoint",
				"",
				"label",
				0,
				20,
				filters={"report": rep.name, "checkpoint_row": row},
			)

		# Row of another report.
		frappe.set_user(self.reviewer)
		self.assertEqual(
			review_api.label_options_query(
				"POS Monitoring Report Checkpoint",
				"",
				"label",
				0,
				20,
				filters={"report": rep.name, "checkpoint_row": other.checkpoints[0].name},
			),
			[],
		)

		# Unsaved Desk row.
		self.assertEqual(
			review_api.label_options_query(
				"POS Monitoring Report Checkpoint",
				"",
				"label",
				0,
				20,
				filters={"report": rep.name, "checkpoint_row": "new-pos-monitoring-report-checkpoint-1"},
			),
			[],
		)
		frappe.set_user("Administrator")

	def test_finalize_notification_subject_without_result(self):
		rep = self._make_report(
			docstatus=1, status="Submitted", submitted_by=self.cashier
		)
		self._label_all_rows(rep)
		frappe.db.delete("Notification Log", {"document_name": rep.name})

		frappe.set_user(self.reviewer)
		review_api.finalize_review(rep.name)
		frappe.set_user("Administrator")

		logs = frappe.get_all(
			"Notification Log",
			filters={"for_user": self.cashier, "document_name": rep.name},
			fields=["subject"],
		)
		self.assertTrue(logs)
		for log in logs:
			self.assertEqual(log.subject, f"POS Monitoring Report reviewed: {rep.name}")
			self.assertNotIn("(", log.subject)

	def test_gallery_label_filter_and_color(self):
		rep = self._create_submitted_report([REV_CP_1, REV_CP_2])
		frappe.set_user("Administrator")
		rep.reload()
		rep.checkpoints[0].label = LABEL_A
		rep.save()
		rep.reload()

		frappe.set_user(self.reviewer)
		res = review_api.get_gallery()
		items = [i for i in res["items"] if i["report"] == rep.name]
		self.assertTrue(items)
		first = [i for i in items if i["checkpoint_name"] == REV_CP_1]
		second = [i for i in items if i["checkpoint_name"] == REV_CP_2]
		self.assertTrue(first and second)
		self.assertEqual(first[0]["label"], LABEL_A)
		self.assertEqual(first[0]["color"], "Blue")
		self.assertIsNone(second[0]["label"])
		self.assertIsNone(second[0]["color"])

		# Label filter replaces the old assessment filter.
		res_a = review_api.get_gallery(filters={"label": LABEL_A})
		self.assertIn(rep.name, [i["report"] for i in res_a["items"]])
		res_b = review_api.get_gallery(filters={"label": LABEL_B})
		self.assertNotIn(rep.name, [i["report"] for i in res_b["items"]])
		frappe.set_user("Administrator")

	def test_reviewer_json_roundtrip_label_save(self):
		from frappe.client import save

		rep = self._make_report(docstatus=1, status="Submitted")
		frappe.db.set_value(rep.doctype, rep.name, "operational_date", today())
		rep.reload()
		payload = rep.as_dict()
		payload["checkpoints"][0]["label"] = LABEL_A
		frappe.set_user(self.reviewer)
		save(frappe.as_json(payload))
		rep.reload()
		self.assertEqual(rep.checkpoints[0].label, LABEL_A)
		from frappe.desk.form.save import savedocs

		payload = rep.as_dict()
		payload["checkpoints"][0]["assessment_note"] = "Saved through Desk"
		savedocs(frappe.as_json(payload), "Update")
		rep.reload()
		self.assertEqual(rep.checkpoints[0].assessment_note, "Saved through Desk")
		frappe.set_user("Administrator")

	def test_native_finalize_ignores_client_review_attribution(self):
		from frappe.client import save

		for status in ("Reviewed", "Waived"):
			with self.subTest(status=status):
				rep = self._make_report(docstatus=1, status="Submitted")
				frappe.set_user("Administrator")
				rep.reload()
				payload = rep.as_dict()
				payload["status"] = status
				payload["waive_reason"] = "Renovation"
				payload["checkpoints"][0]["label"] = LABEL_A
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
		rep = self._make_report(docstatus=1, status="Submitted")
		frappe.set_user(self.reviewer)
		review_api.waive(rep.name, "Renovation")
		for field, value in (("correction_requested", 1), ("review_note", "changed"), ("label", LABEL_A), ("assessment_note", "changed")):
			with self.subTest(field=field):
				# Fresh object: the insert-time object carries
				# flags.internal_report_build / ignore_permissions.
				doc = frappe.get_doc("POS Monitoring Report", rep.name)
				if field in ("label", "assessment_note"):
					doc.checkpoints[0].set(field, value)
				else:
					doc.set(field, value)
				with self.assertRaises(frappe.PermissionError):
					doc.save()

	def test_finalize_twice_second_fails(self):
		rep = self._make_report(docstatus=1, status="Submitted")
		self._label_all_rows(rep)

		frappe.set_user(self.reviewer)
		review_api.finalize_review(rep.name)

		# Second call: status is already Reviewed -> ValidationError
		with self.assertRaises(frappe.ValidationError):
			review_api.finalize_review(rep.name)

	def test_finalize_review_takes_row_lock(self):
		rep = self._make_report(docstatus=1, status="Submitted")
		self._label_all_rows(rep)

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

		rep = self._make_report(docstatus=1, status="Submitted")
		self._label_all_rows(rep)
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
		self.assertEqual(rep.checkpoints[0].label, LABEL_A)

	def test_cashier_cannot_finalize_or_waive(self):
		rep = self._make_report(docstatus=1, status="Submitted")
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.PermissionError):
			review_api.finalize_review(rep.name)

		with self.assertRaises(frappe.PermissionError):
			review_api.waive(rep.name, "test reason")

	def test_waive_requires_reason_and_state(self):
		rep = self._make_report(docstatus=1, status="Submitted")
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

		rep = self._create_submitted_report([REV_CP_1])
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
			"template_name": f"{_RUN_ID} Tmpl-Day-{uuid.uuid4().hex[:6]}",
			"cadence": "Per Hari",
			"enabled": 1,
			"due_time": "18:00:00",
			"checkpoints": [{"checkpoint": REV_CP_1, "required": 1}],
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

		rep = self._create_submitted_report([REV_CP_1])

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
		self._create_submitted_report([REV_CP_1])
		frappe.set_user(self.reviewer)
		# Request with page_length 10
		res = review_api.get_gallery(start=0, page_length=10)
		self.assertIn("has_more", res)
		self.assertIn("items", res)

	def test_gallery_skips_purged(self):
		rep = self._create_submitted_report([REV_CP_1])
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
			"template_name": f"{_RUN_ID} Tmpl-FakeMedia-{uuid.uuid4().hex[:6]}",
			"cadence": "Per Shift",
			"enabled": 1,
			"due_minutes_after_open": 30,
			"checkpoints": [{"checkpoint": REV_CP_1, "required": 1}],
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
		rep = self._create_submitted_report([REV_CP_1], submitter=self.cashier)
		self.assertEqual(rep.status, "Submitted")

		# Native transition without labels must fail review completeness,
		# not silently succeed.
		frappe.set_user(self.reviewer)
		rep.reload()
		rep.status = "Reviewed"
		with self.assertRaises(frappe.ValidationError):
			rep.save()

		rep.reload()
		self.assertEqual(rep.status, "Submitted")

	def test_native_save_reviewed_report_cannot_modify_review_decision_or_metadata(self):
		rep = self._make_report(docstatus=1, status="Submitted")
		self._label_all_rows(rep)

		frappe.set_user(self.reviewer)
		res = review_api.finalize_review(rep.name)
		self.assertEqual(res["status"], "Reviewed")
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

		rep = self._create_submitted_report([REV_CP_1])
		rep.reload()
		payload = rep.as_dict()
		payload["status"] = "Reviewed"
		payload["correction_requested"] = 1
		payload["checkpoints"][0]["label"] = LABEL_A

		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.ValidationError) as cm:
			frappe.client.save(payload)
		self.assertIn("Correction is not allowed by settings", str(cm.exception))

	def test_prevent_self_review(self):
		# Create submitted report where submitter is self.reviewer
		pdoc = frappe.get_doc("POS Profile", self.profile)
		pdoc.append("applicable_for_users", {"user": self.reviewer})
		pdoc.save(ignore_permissions=True)

		rep = self._create_submitted_report([REV_CP_1], submitter=self.reviewer)
		self._label_all_rows(rep)

		frappe.set_user(self.reviewer)
		with self.assertRaises(frappe.PermissionError):
			review_api.finalize_review(rep.name)
