# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import os
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, get_datetime, now_datetime

from pos_next.api import outlet_monitoring as api
from pos_next.services.outlet_monitoring import (
	build_report,
	ensure_obligations,
	get_settings,
	mark_missed,
	obligation_key,
)
from pos_next.services.outlet_monitoring_media import sniff_media
from pos_next.tests.outlet_monitoring_test_utils import (
	delete_owned_files,
	delete_owned_fixtures,
	insert_owned_user,
	owned_file_names,
	owned_fixtures,
	restore_settings_state,
	snapshot_settings_state,
)

_RUN_ID = uuid.uuid4().hex[:6]
# Run-unique fixtures: this suite only ever mutates records it created.
API_CATEGORY = f"{_RUN_ID} API Category"
API_PASS = f"{_RUN_ID} API Pass"
API_FAIL = f"{_RUN_ID} API Fail"
API_CP_PHOTO = f"{_RUN_ID} API CP Photo"
API_CP_VIDEO = f"{_RUN_ID} API CP Video"
API_CP_MIXED = f"{_RUN_ID} API CP Mixed"

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
	return email


def _setup_profile(company="_Test Company"):
	profile_name = f"{_RUN_ID} _Test API Prof {uuid.uuid4().hex[:6]}"
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
	"Delivery Note",
	"Purchase Receipt",
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
	"Delivery Note": ("Delivery Note Item",),
	"Purchase Receipt": ("Purchase Receipt Item",),
	"User": ("Has Role",),
}


def _snapshot_fixtures():
	return {dt: set(frappe.get_all(dt, pluck="name")) for dt in _FIXTURE_DOCTYPES}


# This suite's own name namespaces; a snapshot diff alone is not ownership.
_FIXTURE_PREFIXES = {
	"POS Monitoring Category": (f"{_RUN_ID} API Category",),
	"POS Monitoring Checkpoint": (f"{_RUN_ID} API CP",),
	"POS Monitoring Label": (f"{_RUN_ID} API Pass", f"{_RUN_ID} API Fail"),
	"POS Monitoring Template": (f"{_RUN_ID} Tmpl-API-", f"{_RUN_ID} Tmpl-Other-"),
	"POS Profile": (f"{_RUN_ID} _Test API Prof ",),
	"User": (f"{_RUN_ID}-api-cashier-", f"{_RUN_ID}-api-other-outlet-"),
}
_FIXTURE_LINKS = {
	# Shift names come from the site naming series, so they are claimed through
	# the POS Profile this suite created rather than by name.
	"POS Opening Shift": (("pos_profile", "POS Profile"),),
	"POS Closing Shift": (("pos_profile", "POS Profile"),),
	"POS Monitoring Report": (("template", "POS Monitoring Template"),),
}


def _delete_new_fixtures(initial):
	"""Delete owned fixtures, innermost children first."""
	owned = owned_fixtures(initial, _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID)
	delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _FIXTURE_DOCTYPES)


def _restore_settings_exact(state):
	# Exact restore: every column of every Settings child row and the exact
	# tabSingles rows, not a partial name/label/idx rebuild.
	restore_settings_state(state)




class TestOutletMonitoringApi(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial = _snapshot_fixtures()
		cls._suite_state = snapshot_settings_state()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		# Full-suite safety net (setup-suite pattern): catches everything the
		# per-test snapshot teardown may have missed — e.g. Test 1 creating a
		# shared Category/Checkpoint/Label that later tests reuse (so their own
		# snapshots never contain it), or a wholesale per-test-teardown failure
		# under fix-stack cron contention (01:44 run leaked 60 templates etc.).
		_delete_new_fixtures(getattr(cls, "_suite_initial", {}))
		_restore_settings_exact(cls._suite_state)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		# Request context for worker threads (same pattern as
		# test_outlet_monitoring_review.py): threads re-init frappe,
		# which leaves frappe.local.request unbound.
		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		builder = EnvironBuilder(path="/")
		frappe.local.request = Request(builder.get_environ())
		if hasattr(frappe.local, "session") and not getattr(frappe.local.session, "data", None):
			frappe.local.session.data = frappe._dict()
		frappe.set_user("Administrator")
		self._original_state = snapshot_settings_state()
		self._test_initial = _snapshot_fixtures()
		self.addCleanup(self._restore_settings)
		self.company = "_Test Company"
		self.profile = _setup_profile(self.company)
		self.cashier = _create_user("POSNext Cashier", "api-cashier")

		# Assign cashier to profile
		pdoc = frappe.get_doc("POS Profile", self.profile)
		pdoc.append("applicable_for_users", {"user": self.cashier})
		pdoc.save(ignore_permissions=True)

		# Ensure labels, category & checkpoints (labels resolved per §4.1)
		for lbl, color in ((API_PASS, "Green"), (API_FAIL, "Red")):
			if not frappe.db.exists("POS Monitoring Label", lbl):
				frappe.get_doc({
					"doctype": "POS Monitoring Label",
					"label_name": lbl,
					"color": color,
				}).insert(ignore_permissions=True)
		if not frappe.db.exists("POS Monitoring Category", API_CATEGORY):
			frappe.get_doc({
				"doctype": "POS Monitoring Category",
				"category_name": API_CATEGORY,
				"cashier_can_label": 1,
				"labels": [{"label": API_PASS}, {"label": API_FAIL}],
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", API_CP_PHOTO):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": API_CP_PHOTO,
				"category": API_CATEGORY,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		if not frappe.db.exists("POS Monitoring Checkpoint", API_CP_VIDEO):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": API_CP_VIDEO,
				"category": API_CATEGORY,
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
		existing = {r.label for r in s.default_labels}
		for lbl in (API_PASS, API_FAIL):
			if lbl not in existing:
				s.append("default_labels", {"label": lbl})
		s.save(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		# Reports and their files are claimed through the templates this suite
		# owns, so no other writer's rows can be swept up here.
		owned = owned_fixtures(
			getattr(self, "_test_initial", {}), _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID
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

	def _create_template(self, cadence="Per Shift", checkpoints=None):
		cps = checkpoints or [API_CP_PHOTO]
		t = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"{_RUN_ID} Tmpl-API-{uuid.uuid4().hex[:6]}",
			"cadence": cadence,
			"enabled": 1,
			"due_minutes_after_open": 30,
			"due_time": "12:00:00" if cadence == "Per Hari" else None,
			"submit_within_hours": 24 if cadence == "Manual" else None,
			"checkpoints": [{"checkpoint": cp, "required": 1} for cp in cps],
			"company_scope": "Selected",
			"companies": [{"company": self.company}],
		})
		t.insert(ignore_permissions=True)
		return t

	def _open_shift(self, user=None, profile=None):
		target_profile = profile or self.profile
		open_shift = frappe.db.get_value(
			"POS Opening Shift", {"pos_profile": target_profile, "status": "Open"}, "name"
		)
		if open_shift:
			return frappe.get_doc("POS Opening Shift", open_shift)
		shift = frappe.get_doc({
			"doctype": "POS Opening Shift",
			"pos_profile": target_profile,
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

		t = self._create_template(checkpoints=[API_CP_PHOTO, API_CP_VIDEO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO, API_CP_VIDEO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])  # max_count 2
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

		# File attached to a different doc. It hangs off a name no report
		# ownership can claim, so the test removes it itself.
		f = self._attach_file(f"SomeOtherReportName-{uuid.uuid4().hex[:6]}", TINY_JPEG, file_name="other.jpg")
		self.addCleanup(frappe.delete_doc, "File", f.name, force=True, ignore_permissions=True)
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.register_media(rep.name, cp_row, f.name)

	def test_register_mixed_checkpoint_accepts_photo_and_video(self):
		if not frappe.db.exists("POS Monitoring Checkpoint", API_CP_MIXED):
			frappe.get_doc({
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": API_CP_MIXED,
				"category": API_CATEGORY,
				"media_type": "Photo or Video",
				"min_count": 1,
				"max_count": 2,
			}).insert(ignore_permissions=True)

		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_MIXED])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_VIDEO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
			"template_name": f"{_RUN_ID} Tmpl-Other-{uuid.uuid4().hex[:6]}",
			"cadence": "Per Shift",
			"enabled": 1,
			"due_minutes_after_open": 30,
			"checkpoints": [{"checkpoint": API_CP_PHOTO, "required": 1}],
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

	def _create_draft_report(self, checkpoints=None, profile=None):
		tmpl = self._create_template("Per Shift", checkpoints=checkpoints or [API_CP_PHOTO])
		shift = self._open_shift(profile=profile)
		ensure_obligations(pos_profile=profile or self.profile)
		return tmpl, self._get_shift_report(tmpl.name, shift.name)

	def _get_report_row(self, report_name):
		doc = frappe.get_doc("POS Monitoring Report", report_name)
		return doc, doc.checkpoints[0]

	def test_set_cashier_label_set_and_clear(self):
		_, rep = self._create_draft_report()
		cp_row = rep.checkpoints[0].name

		frappe.set_user(self.cashier)
		res = api.set_cashier_label(rep.name, cp_row, API_PASS)
		self.assertEqual(res["checkpoints"][0]["cashier_label"], API_PASS)

		# Reload from DB: value persisted
		_, row = self._get_report_row(rep.name)
		self.assertEqual(row.cashier_label, API_PASS)

		# Clear
		res = api.set_cashier_label(rep.name, cp_row, None)
		self.assertIsNone(res["checkpoints"][0]["cashier_label"])
		_, row = self._get_report_row(rep.name)
		self.assertIsNone(row.cashier_label)

	def test_set_cashier_label_same_value_idempotent(self):
		_, rep = self._create_draft_report()
		cp_row = rep.checkpoints[0].name

		frappe.set_user(self.cashier)
		res = api.set_cashier_label(rep.name, cp_row, API_PASS)
		self.assertEqual(res["checkpoints"][0]["cashier_label"], API_PASS)

		# Same value twice: no error, no double change
		res = api.set_cashier_label(rep.name, cp_row, API_PASS)
		self.assertEqual(res["checkpoints"][0]["cashier_label"], API_PASS)
		_, row = self._get_report_row(rep.name)
		self.assertEqual(row.cashier_label, API_PASS)

	def test_set_cashier_label_rejections(self):
		_, rep = self._create_draft_report()
		cp_row = rep.checkpoints[0].name

		frappe.set_user(self.cashier)
		# Label outside label_options
		with self.assertRaises(frappe.ValidationError):
			api.set_cashier_label(rep.name, cp_row, "No Such Label Ever")

		# Row of another report (separate profile: one open shift per profile)
		_, rep2 = self._create_draft_report(profile=_setup_profile(self.company))
		with self.assertRaises(frappe.ValidationError):
			api.set_cashier_label(rep.name, rep2.checkpoints[0].name, API_PASS)

		# Other outlet user
		_setup_profile("_Test Company 1")
		other = _create_user("POSNext Cashier", "api-other-outlet")
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			api.set_cashier_label(rep.name, cp_row, API_PASS)

		# Toggle off: category disallows cashier labels
		frappe.set_user("Administrator")
		cat = frappe.get_doc("POS Monitoring Category", API_CATEGORY)
		cat.cashier_can_label = 0
		cat.save(ignore_permissions=True)
		# New report snapshots the toggle off
		tmpl = self._create_template("Per Shift")
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		off_rep = self._get_shift_report(tmpl.name, shift.name)
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.set_cashier_label(off_rep.name, off_rep.checkpoints[0].name, API_PASS)

		# Restore toggle for other tests
		frappe.set_user("Administrator")
		cat.reload()
		cat.cashier_can_label = 1
		cat.save(ignore_permissions=True)

	def test_native_save_cannot_change_cashier_label_or_label(self):
		from frappe.client import save as client_save

		_, rep = self._create_draft_report()

		frappe.set_user(self.cashier)
		payload = rep.as_dict()
		payload["checkpoints"][0]["cashier_label"] = API_PASS
		with self.assertRaises(frappe.PermissionError):
			client_save(frappe.as_json(payload))

		rep.reload()
		self.assertIsNone(rep.checkpoints[0].cashier_label)

	def test_native_save_silently_drops_label_tampering(self):
		# Framework resets permlevel-1 child values to stored BEFORE the
		# controller validate() runs (validate_higher_perm_levels in
		# _save), so the reviewer guard never fires on native-save
		# updates: the tampered value is silently discarded instead.
		# Tampering is still safe (never persisted). A throwing guard
		# would need a validate_higher_perm_levels override or an
		# access-layer check, both in non-owned files; see B1.md.
		from frappe.client import save as client_save

		_, rep = self._create_draft_report()

		frappe.set_user(self.cashier)
		payload = rep.as_dict()
		payload["checkpoints"][0]["label"] = API_PASS
		client_save(frappe.as_json(payload))

		rep.reload()
		self.assertIsNone(rep.checkpoints[0].label)

	def test_set_cashier_label_after_submit_or_missed_rejected(self):
		_, rep = self._create_draft_report()
		cp_row = rep.checkpoints[0].name
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		api.set_cashier_label(rep.name, cp_row, API_PASS)
		api.register_media(rep.name, cp_row, f.name)
		api.submit_report(rep.name)

		# Terminal reports revoke write access, so the permission layer
		# rejects before the Draft-state check: accept either error.
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			api.set_cashier_label(rep.name, cp_row, API_FAIL)

		_, row = self._get_report_row(rep.name)
		self.assertEqual(row.cashier_label, API_PASS)

		# Missed path (separate profile: one open shift per profile).
		# The cashier must be assigned to the second profile to write there.
		profile2 = _setup_profile(self.company)
		pdoc2 = frappe.get_doc("POS Profile", profile2)
		pdoc2.append("applicable_for_users", {"user": self.cashier})
		pdoc2.save(ignore_permissions=True)
		_, rep2 = self._create_draft_report(profile=profile2)
		cp2 = rep2.checkpoints[0].name
		frappe.set_user(self.cashier)
		api.set_cashier_label(rep2.name, cp2, API_PASS)
		frappe.set_user("Administrator")
		rep2.reload()
		rep2.status = "Missed"
		rep2.flags.ignore_mandatory = True
		rep2.flags.ignore_permissions = True
		rep2.submit()
		frappe.set_user(self.cashier)
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			api.set_cashier_label(rep2.name, cp2, API_FAIL)
		_, row = self._get_report_row(rep2.name)
		self.assertEqual(row.cashier_label, API_PASS)

	def test_set_cashier_label_race_terminal_first(self):
		"""Real overlap: A holds the row lock while B attempts the label write.

		A opens a transaction, takes the parent row lock (the same lock the
		setter takes) and only then lets B run. B must block on that lock until
		A commits the terminal transition, then see the post-terminal state and
		reject the write. The mark_missed branch of the plan (§7 concurrency) is
		covered here too: the terminal transition is mark_missed, not submit.
		"""
		import threading

		_, rep = self._create_draft_report()
		rep_name = rep.name
		cp_row = rep.checkpoints[0].name
		f = self._attach_file(rep_name, TINY_JPEG, file_name="valid.jpg")
		frappe.set_user(self.cashier)
		api.set_cashier_label(rep_name, cp_row, API_PASS)
		api.register_media(rep_name, cp_row, f.name)
		frappe.set_user("Administrator")
		# Expire the draft so mark_missed has a real reason to close it.
		frappe.db.set_value(
			"POS Monitoring Report", rep_name, "due_at", add_to_date(now_datetime(), hours=-48)
		)
		frappe.db.commit()
		cashier = self.cashier
		settings = get_settings()
		self.assertTrue(settings["enabled"])
		self.assertEqual(get_settings()["shift_missed_grace_hours"] or 12, 12)

		site = frappe.local.site
		sites_path = getattr(frappe.local, "sites_path", None) or "/home/frappe/frappe-bench/sites"
		lock_taken = threading.Event()
		release = threading.Event()
		b_errors = []
		b_done = threading.Event()

		def worker_a_terminal_first():
			from werkzeug.test import EnvironBuilder
			from werkzeug.wrappers import Request

			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			builder = EnvironBuilder(path="/api/method/pos_next.api.outlet_monitoring.mark_missed")
			frappe.local.request = Request(builder.get_environ())
			frappe.local.form_dict = frappe._dict(cmd="mark_missed")
			frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
			try:
				# Take the very lock set_cashier_label takes, then hold it until
				# B has actually started waiting on it (real overlap).
				frappe.db.get_value("POS Monitoring Report", rep_name, "name", for_update=True)
				lock_taken.set()
				self.assertTrue(release.wait(timeout=30), "worker B never started")
				missed = mark_missed(names=[rep_name])
				frappe.db.commit()
				self.assertEqual(missed, [rep_name], "mark_missed must close the expired draft")
			except Exception as exc:  # surfaced in the main thread below
				b_errors.append(("A", exc))
				frappe.db.rollback()
			finally:
				frappe.db.close()

		def worker_b_setter():
			from werkzeug.test import EnvironBuilder
			from werkzeug.wrappers import Request

			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			builder = EnvironBuilder(path="/api/method/pos_next.api.outlet_monitoring.set_cashier_label")
			frappe.local.request = Request(builder.get_environ())
			frappe.local.form_dict = frappe._dict(cmd="set_cashier_label")
			frappe.local.session = frappe._dict(user=cashier, data=frappe._dict())
			try:
				self.assertTrue(lock_taken.wait(timeout=30), "worker A never took the lock")
				# B blocks here on A's row lock; release it while B is waiting.
				threading.Timer(0.5, release.set).start()
				api.set_cashier_label(rep_name, cp_row, API_FAIL)
			except Exception as exc:
				b_errors.append(("B", exc))
			finally:
				frappe.db.rollback()
				frappe.db.close()
				b_done.set()

		ta = threading.Thread(target=worker_a_terminal_first)
		tb = threading.Thread(target=worker_b_setter)
		ta.start()
		tb.start()
		self.assertTrue(b_done.wait(timeout=60), "setter worker did not finish")
		ta.join(timeout=60)
		tb.join(timeout=60)
		release.set()

		self.assertEqual([tag for tag, _e in b_errors], ["B"], f"unexpected worker errors: {b_errors}")
		# B must lose on APPLICATION state (it read the row after A committed the
		# terminal transition), never on a raw DB deadlock. Accepting
		# QueryDeadlockError here would let the test pass without the row lock:
		# unlocked, B hits "record has changed since last read" instead.
		self.assertIsInstance(
			b_errors[0][1],
			(frappe.ValidationError, frappe.PermissionError),
			f"B must be rejected by post-terminal state, not a DB race: {b_errors[0][1]!r}",
		)
		frappe.db.rollback()
		frappe.set_user("Administrator")
		doc = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual((doc.status, doc.docstatus), ("Missed", 1), "A's terminal transition must win")
		self.assertEqual(doc.checkpoints[0].cashier_label, API_PASS, "B's write must not land")

	def test_set_cashier_label_race_setter_first(self):
		"""Two setters overlap on the same row; the second must wait, not interleave.

		A takes the parent row lock inside `set_cashier_label` and blocks there
		until B is provably blocked behind it on that same lock. Only then does
		A commit. Without the lock B commits first (proven by the mutant run
		below), so the two writes interleave and the last writer wins — the
		exact lost-update the lock exists to prevent.
		"""

		def overlap(use_lock):
			import threading

			_, rep = self._create_draft_report()
			name, cp_row = rep.name, rep.checkpoints[0].name
			# The workers use their own connections: release this thread's row
			# locks or A blocks on OUR transaction instead of on B.
			frappe.db.commit()
			site = frappe.local.site
			sites_path = getattr(frappe.local, "sites_path", None) or "/home/frappe/frappe-bench/sites"
			a_locked, release, b_done = threading.Event(), threading.Event(), threading.Event()
			state = {}

			def worker_a():
				frappe.init(site=site, sites_path=sites_path)
				frappe.connect()
				frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
				orig = frappe.db.get_value

				def wrapped(*args, **kwargs):
					if not kwargs.get("for_update"):
						return orig(*args, **kwargs)
					if not use_lock:
						kwargs.pop("for_update")
						value = orig(*args, **kwargs)
					else:
						value = orig(*args, **kwargs)
					# Both variants stop here; only the locked one holds the row.
					a_locked.set()
					release.wait(30)
					return value

				frappe.db.get_value = wrapped
				try:
					api.set_cashier_label(name, cp_row, API_FAIL)
					frappe.db.commit()
				except Exception as exc:
					state["a_error"] = exc
					frappe.db.rollback()
				finally:
					frappe.db.close()

			def worker_b():
				frappe.init(site=site, sites_path=sites_path)
				frappe.connect()
				frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
				try:
					self.assertTrue(a_locked.wait(timeout=30), "A never reached its lock")
					api.set_cashier_label(name, cp_row, API_PASS)
					frappe.db.commit()
					state["b_committed"] = True
				except Exception as exc:
					state["b_error"] = exc
					frappe.db.rollback()
				finally:
					b_done.set()
					frappe.db.close()

			ta, tb = threading.Thread(target=worker_a), threading.Thread(target=worker_b)
			ta.start()
			tb.start()
			self.assertTrue(a_locked.wait(timeout=30), f"A never reached its lock: {state}")
			# Give B a full second to (wrongly) commit while A still holds the lock.
			state["b_finished_first"] = b_done.wait(timeout=1.0)
			release.set()
			ta.join(timeout=60)
			tb.join(timeout=60)
			self.assertTrue(b_done.is_set(), "B never finished")
			frappe.db.rollback()
			stored = frappe.db.get_value(
				"POS Monitoring Report Checkpoint", cp_row, ["label", "cashier_label"], as_dict=True
			)
			return state, stored

		with_lock, stored = overlap(use_lock=True)
		# B was genuinely blocked on A's row lock instead of finishing.
		self.assertFalse(with_lock.get("b_finished_first"), f"B did not block on the lock: {with_lock}")
		self.assertNotIn("b_error", with_lock, f"B must queue, not fail: {with_lock}")
		self.assertNotIn("a_error", with_lock, f"A must commit cleanly: {with_lock}")
		self.assertEqual(stored.cashier_label, API_PASS, "the later, unblocked write wins")

		frappe.db.rollback()
		frappe.set_user("Administrator")
		without_lock, mutant = overlap(use_lock=False)
		# Proof the lock is what serialises them: unlocked, B commits while A is
		# still mid-write, so the transaction breaks (deadlock) or A's stale
		# value overwrites B's. Either way the pair is no longer clean.
		self.assertTrue(
			without_lock.get("b_finished_first"),
			f"mutant run did not overlap, so it proves nothing: {without_lock}",
		)
		self.assertTrue(
			"a_error" in without_lock or mutant.cashier_label != API_PASS,
			f"without the lock the writes must collide, but the run was clean: {without_lock} / {mutant}",
		)

	def _setter_then_terminal(self, terminal, use_lock):
		"""Setter commits B first, terminal transition overlaps it, not after it.

		Worker A runs the real `set_cashier_label`, so A holds the same parent
		row lock the setter takes, and keeps its transaction open until B is
		provably inside its own call. B then runs the terminal transition
		(`submit_report` or `mark_missed`) and must queue behind that lock and
		read the committed label B. `use_lock=False` drops `for_update` inside
		B's path only, to show the lock is what makes the locked run clean.
		"""
		import threading

		_, rep = self._create_draft_report()
		name, cp_row = rep.name, rep.checkpoints[0].name
		f = self._attach_file(name, TINY_JPEG, file_name="valid.jpg")
		frappe.set_user(self.cashier)
		api.register_media(name, cp_row, f.name)
		frappe.set_user("Administrator")
		if terminal == "missed":
			# A real reason for mark_missed to close the draft.
			frappe.db.set_value(
				"POS Monitoring Report", name, "due_at", add_to_date(now_datetime(), hours=-48)
			)
		# The workers use their own connections: release this thread's row locks
		# or A would block on OUR transaction instead of holding its own.
		frappe.db.commit()

		site = frappe.local.site
		sites_path = getattr(frappe.local, "sites_path", None) or "/home/frappe/frappe-bench/sites"
		a_locked = threading.Event()
		b_started = threading.Event()
		b_done = threading.Event()
		release_a = threading.Event()
		state = {}

		def worker_a_setter():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
			try:
				api.set_cashier_label(name, cp_row, API_PASS)
				a_locked.set()
				self.assertTrue(b_started.wait(timeout=30), "worker B never started")
				self.assertTrue(release_a.wait(timeout=30), "main thread never released A")
				frappe.db.commit()
			except Exception as exc:
				state["a_error"] = exc
				frappe.db.rollback()
			finally:
				frappe.db.close()

		def worker_b_terminal():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
			real_get_doc, real_get_values = frappe.get_doc, frappe.db.get_values

			def unlocked(fn):
				def wrapped(*args, **kwargs):
					kwargs.pop("for_update", None)
					return fn(*args, **kwargs)

				return wrapped

			try:
				self.assertTrue(a_locked.wait(timeout=30), "worker A never took the lock")
				b_started.set()

				def run_terminal():
					if terminal == "submit":
						api.submit_report(name)
					else:
						state["missed"] = mark_missed(names=[name])

				if use_lock:
					run_terminal()
				else:
					with (
						patch("frappe.get_doc", unlocked(real_get_doc)),
						patch.object(frappe.db, "get_values", unlocked(real_get_values)),
					):
						run_terminal()
				frappe.db.commit()
				state["b_committed"] = True
			except Exception as exc:
				state["b_error"] = exc
				frappe.db.rollback()
			finally:
				b_done.set()
				frappe.db.close()

		ta = threading.Thread(target=worker_a_setter)
		tb = threading.Thread(target=worker_b_terminal)
		ta.start()
		tb.start()
		self.assertTrue(a_locked.wait(timeout=30), f"A never took the lock: {state}")
		self.assertTrue(b_started.wait(timeout=30), f"B never started: {state}")
		# B must be queued on A's row lock, not finished: a sequential run
		# proves nothing about the overlap.
		state["b_finished_first"] = b_done.wait(timeout=1.0)
		release_a.set()
		ta.join(timeout=60)
		tb.join(timeout=60)
		self.assertTrue(b_done.is_set(), "B never finished")

		frappe.db.rollback()
		frappe.set_user("Administrator")
		state["stored"] = frappe.db.get_value(
			"POS Monitoring Report Checkpoint", cp_row, ["label", "cashier_label"], as_dict=True
		)
		state["status"] = frappe.db.get_value("POS Monitoring Report", name, ["status", "docstatus"])
		return state

	def test_set_cashier_label_race_setter_first_submit_keeps_locked_label(self):
		"""Setter commits B, then submit prefills label B from the locked read.

		Contract (plan §7): setter first, submit after -> label == B. Under a
		real overlap the submit transaction must wait for the setter's row lock
		and read B, not a pre-setter snapshot.
		"""
		state = self._setter_then_terminal("submit", use_lock=True)
		self.assertFalse(state.get("b_finished_first"), f"B did not queue on the lock: {state}")
		self.assertNotIn("a_error", state, f"A must commit cleanly: {state}")
		self.assertNotIn("b_error", state, f"B must queue, not fail: {state}")
		self.assertEqual(state["stored"].cashier_label, API_PASS, "the setter's label must survive submit")
		self.assertEqual(state["stored"].label, API_PASS, "submit must prefill from the committed label")
		self.assertEqual(state["status"][0], "Submitted")

		mutant = self._setter_then_terminal("submit", use_lock=False)
		self.assertTrue(
			"b_error" in mutant or mutant["stored"].label != API_PASS,
			f"without the lock submit must read a stale label, but it was clean: {mutant}",
		)

	def test_set_cashier_label_race_setter_first_mark_missed_keeps_locked_label(self):
		"""Setter commits B, then mark_missed closes the draft without losing it.

		Contract (plan §7): setter first, mark_missed after -> cashier_label B
		kept, status Missed. mark_missed rewrites the child rows, so a stale
		read would silently overwrite B with NULL.
		"""
		state = self._setter_then_terminal("missed", use_lock=True)
		self.assertFalse(state.get("b_finished_first"), f"B did not queue on the lock: {state}")
		self.assertNotIn("a_error", state, f"A must commit cleanly: {state}")
		self.assertNotIn("b_error", state, f"B must queue, not fail: {state}")
		self.assertEqual(state["stored"].cashier_label, API_PASS, "mark_missed must keep the setter's label")
		self.assertEqual(state["status"][0], "Missed")

		mutant = self._setter_then_terminal("missed", use_lock=False)
		self.assertTrue(
			"b_error" in mutant or mutant["stored"].cashier_label != API_PASS,
			f"without the lock mark_missed must overwrite the label, but it was clean: {mutant}",
		)

	def test_submit_prefills_label_from_cashier_label(self):
		_, rep = self._create_draft_report()
		cp_row = rep.checkpoints[0].name
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		api.set_cashier_label(rep.name, cp_row, API_PASS)
		api.register_media(rep.name, cp_row, f.name)
		res = api.submit_report(rep.name)

		self.assertEqual(res["status"], "Submitted")
		_, row = self._get_report_row(rep.name)
		self.assertEqual(row.label, API_PASS)

	def test_submit_without_cashier_label_allowed(self):
		_, rep = self._create_draft_report()
		cp_row = rep.checkpoints[0].name
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		api.register_media(rep.name, cp_row, f.name)
		res = api.submit_report(rep.name)

		self.assertEqual(res["status"], "Submitted")
		_, row = self._get_report_row(rep.name)
		self.assertIsNone(row.label)

	def test_before_opens_at_upload_label_submit_rejected(self):
		"""Everything else valid; the ONLY reason to reject is the time window.

		The report is given a complete, otherwise-submittable draft (media
		registered, cashier label chosen) at a stubbed instant past `opens_at`,
		then the clock is moved back before `opens_at`. If the time guard were
		removed the submit would succeed, so the test cannot pass for the wrong
		reason. The rejection message must be the opens_at one.
		"""
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
		frappe.set_user("Administrator")
		tmpl.reload()
		tmpl.due_minutes_after_open = 240
		tmpl.open_after_minutes = 120
		tmpl.save(ignore_permissions=True)
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")
		self.assertIsNotNone(rep.opens_at)
		opens_at = get_datetime(rep.opens_at)
		before = add_to_date(opens_at, minutes=-1)

		frappe.set_user(self.cashier)
		# Build a fully valid, submittable draft while the window is open.
		with patch("pos_next.services.outlet_monitoring.now_datetime", return_value=opens_at):
			api.set_cashier_label(rep.name, cp_row, API_PASS)
			api.register_media(rep.name, cp_row, f.name)
		ready = frappe.get_doc("POS Monitoring Report", rep.name)
		self.assertEqual(ready.checkpoints[0].cashier_label, API_PASS)
		self.assertEqual(len(ready.media), 1)
		self.assertEqual(ready.checkpoints[0].evidence_complete, 1)

		with patch("pos_next.services.outlet_monitoring.now_datetime", return_value=before):
			for call in (
				lambda: api.set_cashier_label(rep.name, cp_row, API_FAIL),
				lambda: api.submit_report(rep.name),
			):
				with self.assertRaises(frappe.ValidationError) as caught:
					call()
				self.assertIn(
					"Report can be filled from",
					str(caught.exception),
					f"rejected for the wrong reason: {caught.exception}",
				)
			f2 = self._attach_file(rep.name, TINY_JPEG, file_name="early.jpg")
			with self.assertRaises(frappe.ValidationError) as caught:
				api.register_media(rep.name, cp_row, f2.name)
			self.assertIn("Report can be filled from", str(caught.exception))

		# Exactly at opens_at the very same calls succeed.
		with patch("pos_next.services.outlet_monitoring.now_datetime", return_value=opens_at):
			api.set_cashier_label(rep.name, cp_row, API_FAIL)
			res = api.submit_report(rep.name)
		self.assertEqual(res["status"], "Submitted")

	def test_open_window_boundary_inclusive(self):
		# now == opens_at must be allowed: stub now_datetime at the opens_at instant
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
		frappe.set_user("Administrator")
		tmpl.reload()
		tmpl.due_minutes_after_open = 120
		tmpl.open_after_minutes = 60
		tmpl.save(ignore_permissions=True)
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)

		self.assertIsNotNone(rep.opens_at)
		opens_at = get_datetime(rep.opens_at)
		cp_row = rep.checkpoints[0].name
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		# assert_report_open lives in services.outlet_monitoring, so the stub
		# must target that module's now_datetime, not the api re-export.
		with patch("pos_next.services.outlet_monitoring.now_datetime", return_value=opens_at):
			res = api.set_cashier_label(rep.name, cp_row, API_PASS)
			self.assertEqual(res["checkpoints"][0]["cashier_label"], API_PASS)
			res = api.register_media(rep.name, cp_row, f.name)
			self.assertEqual(len(res["media"]), 1)

	def test_revision_copies_snapshot_and_reference_clears_labels_no_opens_at(self):
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		api.set_cashier_label(rep.name, rep.checkpoints[0].name, API_PASS)
		api.register_media(rep.name, rep.checkpoints[0].name, f.name)
		api.submit_report(rep.name)

		frappe.set_user("Administrator")
		rep.reload()
		snapshot = rep.checkpoints[0].label_options
		rep.checkpoints[0].label = API_FAIL
		rep.status = "Reviewed"
		rep.correction_requested = 1
		rep.save()

		frappe.set_user(self.cashier)
		rev = api.create_revision(rep.name)
		rev_doc = frappe.get_doc("POS Monitoring Report", rev["name"])
		self.assertEqual(rev_doc.checkpoints[0].label_options, snapshot)
		self.assertEqual(rev_doc.checkpoints[0].cashier_can_label, rep.checkpoints[0].cashier_can_label)
		self.assertIsNone(rev_doc.checkpoints[0].cashier_label)
		self.assertIsNone(rev_doc.checkpoints[0].label)
		self.assertIsNone(rev_doc.checkpoints[0].assessment_note)
		self.assertIsNone(rev_doc.opens_at)

	def test_get_report_hides_label_until_reviewed(self):
		_, rep = self._create_draft_report()
		cp_row = rep.checkpoints[0].name
		f = self._attach_file(rep.name, TINY_JPEG, file_name="valid.jpg")

		frappe.set_user(self.cashier)
		api.set_cashier_label(rep.name, cp_row, API_PASS)
		res = api.get_report(rep.name)
		self.assertIn("label_options", res["checkpoints"][0])
		self.assertIn("cashier_can_label", res["checkpoints"][0])
		self.assertEqual(res["checkpoints"][0]["cashier_label"], API_PASS)
		self.assertNotIn("label", res["checkpoints"][0])
		self.assertNotIn("pass_label", res["checkpoints"][0])
		self.assertNotIn("assessment", res["checkpoints"][0])

		api.register_media(rep.name, cp_row, f.name)
		api.submit_report(rep.name)
		res = api.get_report(rep.name)
		self.assertNotIn("label", res["checkpoints"][0])

		# After review: label + color visible
		frappe.set_user("Administrator")
		rev_doc = frappe.get_doc("POS Monitoring Report", rep.name)
		rev_doc.checkpoints[0].label = API_PASS
		rev_doc.status = "Reviewed"
		rev_doc.save()
		frappe.set_user(self.cashier)
		res = api.get_report(rep.name)
		self.assertEqual(res["checkpoints"][0]["label"], API_PASS)
		self.assertEqual(res["checkpoints"][0]["label_color"], "Green")

	def test_revision_only_when_requested(self):
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		rep.checkpoints[0].label = API_PASS
		rep.status = "Reviewed"
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
		self.assertNotIn("label", rev["checkpoints"][0])
		self.assertEqual(len(rev["media"]), 0)

		# Verify DB state has cleared labels and copied snapshot/reference
		rev_doc = frappe.get_doc("POS Monitoring Report", rev["name"])
		self.assertIsNone(rev_doc.checkpoints[0].label)
		self.assertIsNone(rev_doc.checkpoints[0].cashier_label)
		self.assertTrue(rev_doc.checkpoints[0].label_options)
		self.assertIsNone(rev_doc.opens_at)

	def test_register_foreign_file_rejected_and_not_deleted(self):
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
		shift = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep = self._get_shift_report(tmpl.name, shift.name)
		cp_row = rep.checkpoints[0].name

		f = self._attach_file(rep.name, TINY_JPEG, file_name="first.jpg")
		frappe.set_user(self.cashier)
		api.register_media(rep.name, cp_row, f.name)
		api.submit_report(rep.name)

		# Reviewer sets label + correction_requested (Task 5 owns finalize_review)
		frappe.set_user("Administrator")
		rep.reload()
		rep.checkpoints[0].label = API_FAIL
		rep.status = "Reviewed"
		rep.correction_requested = 1
		rep.save()

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

		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
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

	def test_suite_cleanup_preserves_reports_of_foreign_templates(self):
		# A report is only ours through its template. Another writer's template
		# (created after our snapshot, outside our name space) keeps its report.
		foreign = frappe.get_doc({
			"doctype": "POS Monitoring Template",
			"template_name": f"Foreign Template {uuid.uuid4().hex[:6]}",
			"cadence": "Per Shift",
			"due_minutes_after_open": 30,
			"checkpoints": [{"checkpoint": API_CP_PHOTO, "required": 1}],
		}).insert(ignore_permissions=True)
		foreign_report = build_report(
			foreign,
			"Per Shift",
			company=self.company,
			operational_date=frappe.utils.today(),
			due_at=frappe.utils.now_datetime(),
			obligation_key=f"foreign-{uuid.uuid4().hex}",
		)
		foreign_report.insert(ignore_permissions=True)

		mine = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
		own_report = build_report(
			mine,
			"Per Shift",
			company=self.company,
			operational_date=frappe.utils.today(),
			due_at=frappe.utils.now_datetime(),
			obligation_key=f"sentinel-{uuid.uuid4().hex}",
		)
		own_report.insert(ignore_permissions=True)
		frappe.db.commit()
		self.addCleanup(
			frappe.delete_doc, "POS Monitoring Template", foreign.name, force=True, ignore_permissions=True
		)
		self.addCleanup(
			frappe.delete_doc, "POS Monitoring Report", foreign_report.name, force=True, ignore_permissions=True
		)

		# The same safety-net helper tearDownClass uses (NOT tearDownClass
		# itself: that also restores Settings/commits/calls super(), must not
		# run mid-suite).
		_delete_new_fixtures(self.__class__._suite_initial)

		self.assertTrue(
			frappe.db.exists("POS Monitoring Report", foreign_report.name),
			"Suite cleanup must not delete reports of a template it did not create",
		)
		self.assertFalse(
			frappe.db.exists("POS Monitoring Report", own_report.name),
			"Suite cleanup must delete reports of its own templates",
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
		tmpl = self._create_template("Per Shift", checkpoints=[API_CP_PHOTO])
		shift1 = self._open_shift()
		ensure_obligations(pos_profile=self.profile)
		rep1 = self._get_shift_report(tmpl.name, shift1.name)

		# Create a second report
		rep2 = build_report(
			tmpl,
			"Per Shift",
			company=self.company,
			pos_profile=self.profile,
			operational_date=frappe.utils.today(),
			due_at=frappe.utils.now_datetime(),
			obligation_key=f"shared-{uuid.uuid4().hex}",
		)
		rep2.insert(ignore_permissions=True)

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
