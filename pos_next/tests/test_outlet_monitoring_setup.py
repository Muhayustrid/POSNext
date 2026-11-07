# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import uuid
from unittest.mock import patch

import frappe
from frappe.core.api.file import get_max_file_size
from frappe.tests.utils import FrappeTestCase

from pos_next.outlet_monitoring_setup import REVIEWER_ROLE, ensure_outlet_monitoring_setup
from pos_next.tests.outlet_monitoring_test_utils import (
	assert_settings_state_equal,
	delete_owned_fixtures,
	owned_fixture_names,
	owned_fixtures,
	restore_settings_state,
	snapshot_settings_state,
)

# Fixture doctypes this suite creates, with their child tables for
# dependency-order deletion (parents last). Labels use DB-only deletes:
# POSMonitoringLabel.on_trash always rejects.
_FIXTURE_CHILDREN = {
	"POS Monitoring Template": (
		"POS Monitoring Template Checkpoint",
		"POS Monitoring Template Company",
		"POS Monitoring Template POS Profile",
		"POS Monitoring Template Shift Type",
	),
	"POS Monitoring Checkpoint": ("POS Monitoring Label Item",),
	"POS Monitoring Category": ("POS Monitoring Label Item",),
}


# Suite-unique names: this suite never deletes or overwrites a record it
# did not create. A run-unique suffix keeps us off any pre-existing row.
_RUN_ID = uuid.uuid4().hex[:6]
_SHARED_CATEGORY = f"{_RUN_ID} OMSetup Suite Cat"
_SHARED_CHECKPOINT = f"{_RUN_ID} OMSetup Suite CP"


def _ensure_shared_template_fixtures():
	"""Get-or-create the Category/Checkpoint rows shared by template tests.

	Never deletes or overwrites: if a record with the suite name already
	exists it is reused as is. Teardown deletes every fixture not present
	at setUp, so suite-created rows are still cleaned up.
	"""
	if not frappe.db.exists("POS Monitoring Category", _SHARED_CATEGORY):
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Category",
				"category_name": _SHARED_CATEGORY,
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("POS Monitoring Checkpoint", _SHARED_CHECKPOINT):
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": _SHARED_CHECKPOINT,
				"category": _SHARED_CATEGORY,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)


def _snapshot_fixtures():
	return {
		dt: set(frappe.get_all(dt, pluck="name"))
		for dt in (
			"POS Monitoring Category",
			"POS Monitoring Checkpoint",
			"POS Monitoring Template",
			"POS Monitoring Label",
		)
	}


_FIXTURE_PREFIXES = {
	"POS Monitoring Category": (f"{_RUN_ID} OMSetup Suite Cat", f"{_RUN_ID} Cat-Dup-"),
	"POS Monitoring Checkpoint": (
		f"{_RUN_ID} OMSetup Suite CP",
		f"{_RUN_ID} CP-Dup-",
		f"{_RUN_ID} Test CP Dup ",
	),
	"POS Monitoring Label": (
		f"{_RUN_ID} OMSetup Label ",
		f"{_RUN_ID} Label-Custom-A-",
		f"{_RUN_ID} Label-Custom-B-",
		f"{_RUN_ID} Label-Del-",
		f"{_RUN_ID} Label-Dup-",
	),
	"POS Monitoring Template": (
		f"{_RUN_ID} Scope-",
		f"{_RUN_ID} Window-",
		f"{_RUN_ID} Manual-Hours-",
		f"{_RUN_ID} Test Empty Checkpoints ",
		f"{_RUN_ID} Test Duplicate Checkpoints ",
		f"{_RUN_ID} Test Per Shift Due ",
		f"{_RUN_ID} Test Per Day Due ",
	),
}
# Templates reference checkpoints, checkpoints reference categories, and both
# reference labels: parents before labels, children handled per doctype.
_ORDER = (
	"POS Monitoring Template",
	"POS Monitoring Checkpoint",
	"POS Monitoring Category",
	"POS Monitoring Label",
)


def _delete_new_fixtures(initial):
	# Only rows inside this suite's name namespace, never a plain snapshot
	# diff: another writer's new row would land in the diff and be destroyed.
	owned = owned_fixtures(initial, _FIXTURE_PREFIXES, {}, _RUN_ID)
	delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _ORDER)


def _delete_created_rows(doctype, names):
	"""Cleanup for rows a test inserted itself, listed by exact name."""
	frappe.set_user("Administrator")
	frappe.db.delete(doctype, {"name": ["in", list(names)]})
	frappe.db.commit()


class TestOutletMonitoringSetup(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial = _snapshot_fixtures()
		cls._suite_state = snapshot_settings_state()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		_delete_new_fixtures(getattr(cls, "_suite_initial", {}))
		restore_settings_state(cls._suite_state)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		_ensure_shared_template_fixtures()
		self._original_state = snapshot_settings_state()
		self._initial_fixtures = _snapshot_fixtures()
		self.addCleanup(self._restore_settings)

	def _restore_settings(self):
		# Exact restore of the snapshot: every column of every child row plus
		# the exact tabSingles rows. No defaults are added and enabled is not
		# munged; a test that needs config sets it up after the snapshot.
		restore_settings_state(self._original_state)

	def tearDown(self):
		self._restore_settings()
		_delete_new_fixtures(getattr(self, "_initial_fixtures", {}))
		frappe.db.commit()
		super().tearDown()

	def test_owned_user_cleanup_preserves_shared_and_foreign_dependents(self):
		from pos_next.api import test_outlet_monitoring as fixtures

		token = uuid.uuid4().hex
		email = f"{fixtures._RUN_ID}-api-cashier-{token[:8]}@example.com"
		sp = f"user_cleanup_{token}"
		frappe.db.savepoint(sp)
		try:
			shared = frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": token,
					"user": email,
					"email_ids": [{"email_id": f"shared-{token}@example.com"}],
				}
			).insert(ignore_permissions=True, ignore_links=True)
			frappe.defaults.add_default("sentinel", token, email)
			tables = (
				"User",
				"Has Role",
				"Contact",
				"Contact Email",
				"Contact Phone",
				"Dynamic Link",
				"DefaultValue",
			)

			def snapshot():
				return {
					dt: frappe.db.sql(f"select * from `tab{dt}` order by name", as_dict=True) for dt in tables
				}

			before = snapshot()
			with (
				patch("frappe.in_test", True),
				patch.object(
					fixtures,
					"uuid",
					type("UUID", (), {"uuid4": staticmethod(lambda: type("Token", (), {"hex": token})())})(),
				),
			):
				self.assertEqual(fixtures._create_user("POSNext Cashier"), email)
			owned_contacts = set(
				frappe.get_all("Contact Email", filters={"email_id": email}, pluck="parent")
			) - {shared.name}
			self.assertTrue(owned_contacts, "native User insert must create its own Contact")
			foreign = frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": f"foreign-{token}",
					"user": email,
					"email_ids": [{"email_id": f"foreign-{token}@example.com"}],
				}
			).insert(ignore_permissions=True)
			frappe.defaults.add_default("foreign", token, email)
			foreign_rows = {dt: [r for r in rows if r not in before[dt]] for dt, rows in snapshot().items()}
			foreign_rows = {
				"Contact": [r for r in foreign_rows["Contact"] if r.name == foreign.name],
				"Contact Email": [r for r in foreign_rows["Contact Email"] if r.parent == foreign.name],
				"DefaultValue": [r for r in foreign_rows["DefaultValue"] if r.defkey == "foreign"],
			}
			delete_owned_fixtures({"User": {email}}, {"User": ("Has Role",)}, ("User",))
			self.assertFalse(frappe.db.exists("User", email))
			for name in owned_contacts:
				self.assertFalse(frappe.db.exists("Contact", name), "owned native Contact leaked")
			for dt, rows in snapshot().items():
				expected = sorted(before[dt] + foreign_rows.get(dt, []), key=lambda r: r.name)
				self.assertEqual(
					sorted(rows, key=lambda r: r.name),
					expected,
					f"{dt}: shared/pre-existing/foreign full rows changed",
				)
		finally:
			frappe.db.rollback(save_point=sp)
			frappe.clear_cache()

	def test_user_insert_rejects_existing_normalized_contact_email(self):
		from pos_next.tests import outlet_monitoring_test_utils as utils

		token = uuid.uuid4().hex
		email = f"{token}@example.com"
		sp = f"matching_contact_{token}"
		frappe.db.savepoint(sp)
		try:
			contact = frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": f"Existing-{token}",
					"last_name": "Preserve metadata",
					"email_ids": [{"email_id": email, "is_primary": 1}],
					"phone_nos": [{"phone": "081234567890", "is_primary_phone": 1}],
				}
			).insert(ignore_permissions=True)
			tables = (
				"Contact",
				"Contact Email",
				"Contact Phone",
				"Dynamic Link",
				"User",
				"Has Role",
				"DefaultValue",
			)
			before = {
				dt: frappe.db.sql(f"select * from `tab{dt}` order by name", as_dict=True) for dt in tables
			}
			ledger_before = dict(utils._USER_INSERTS)
			for supplied in (email, f"  {email.upper()}  "):
				with self.subTest(email=supplied):
					user = frappe.get_doc(
						{
							"doctype": "User",
							"email": supplied,
							"first_name": "Must not overwrite",
							"send_welcome_email": 0,
						}
					)
					with patch.object(user, "insert", wraps=user.insert) as insert:
						with self.assertRaises(AssertionError):
							utils.insert_owned_user(user, token, "POSNext Cashier")
						insert.assert_not_called()
					self.assertFalse(frappe.db.exists("User", email))
					self.assertEqual(utils._USER_INSERTS, ledger_before)
					for dt in tables:
						self.assertEqual(
							frappe.db.sql(f"select * from `tab{dt}` order by name", as_dict=True),
							before[dt],
							f"{dt} changed for matching Contact {contact.name}",
						)
		finally:
			frappe.db.rollback(save_point=sp)
			frappe.clear_cache()

	def test_failed_user_insert_does_not_register_dependent_ownership(self):
		from pos_next.tests import outlet_monitoring_test_utils as utils

		token = uuid.uuid4().hex
		email = f"{token}@example.com"
		sp = f"failed_user_{token}"
		frappe.db.savepoint(sp)
		try:
			frappe.defaults.add_default("sentinel", token, email)
			before = frappe.db.sql("select * from tabDefaultValue where parent=%s", email, as_dict=True)
			user = frappe.get_doc({"doctype": "User", "email": email, "first_name": token})
			with patch.object(user, "insert", side_effect=frappe.DuplicateEntryError):
				with self.assertRaises(frappe.DuplicateEntryError):
					utils.insert_owned_user(user, token, "POSNext Cashier")
			self.assertNotIn(email, utils._USER_INSERTS)
			self.assertEqual(
				frappe.db.sql("select * from tabDefaultValue where parent=%s", email, as_dict=True), before
			)
		finally:
			frappe.db.rollback(save_point=sp)
			frappe.clear_cache()

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
		from pos_next.outlet_monitoring_setup import DEFAULT_LABELS, ensure_default_labels

		ensure_default_labels()
		s = frappe.get_single("POS Monitoring Settings")
		self.assertEqual([r.label for r in s.default_labels], [name for name, _color in DEFAULT_LABELS])

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
		self.addCleanup(
			frappe.db.set_value, "POS Monitoring Settings", None, "gallery_page_size", original_size
		)

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
		cp = frappe.new_doc("POS Monitoring Checkpoint")
		cp.checkpoint_name = f"{_RUN_ID} Test CP Dup Max Below Min"
		cp.category = _SHARED_CATEGORY
		cp.media_type = "Photo"
		cp.min_count = 3
		cp.max_count = 2
		self.assertRaises(frappe.ValidationError, cp.save)

	def test_template_rejects_duplicate_checkpoint_and_empty_checkpoints(self):
		# Empty checkpoints
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Test Empty Checkpoints Template",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
			}
		)
		self.assertRaises(frappe.ValidationError, t.save)

		# Second checkpoint for the duplicate test (the first is the
		# shared fixture, already created in setUp). Run-unique: no leftover
		# row of another run can be adopted here.
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": f"{_RUN_ID} Test CP Dup 2",
				"category": _SHARED_CATEGORY,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)

		# Duplicate checkpoints
		t_dup = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Test Duplicate Checkpoints Template",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [
					{"checkpoint": _SHARED_CHECKPOINT, "required": 1},
					{"checkpoint": _SHARED_CHECKPOINT, "required": 0},
				],
			}
		)
		self.assertRaises(frappe.ValidationError, t_dup.save)

	def _ensure_label(self, name, color="Gray"):
		if not frappe.db.exists("POS Monitoring Label", name):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Label",
					"label_name": name,
					"color": color,
				}
			).insert(ignore_permissions=True)
		return name

	def _ensure_checkpoint_with_labels(self, cp_name, category, labels):
		if not frappe.db.exists("POS Monitoring Checkpoint", cp_name):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Checkpoint",
					"checkpoint_name": cp_name,
					"category": category,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"labels": [{"label": lbl} for lbl in labels],
				}
			).insert(ignore_permissions=True)
		return cp_name

	def test_label_delete_always_rejected(self):
		name = f"{_RUN_ID} Label-Del-{uuid.uuid4().hex[:6]}"
		self._ensure_label(name)
		self.assertTrue(frappe.db.exists("POS Monitoring Label", name))
		self.assertRaises(frappe.ValidationError, frappe.delete_doc, "POS Monitoring Label", name)
		self.assertTrue(frappe.db.exists("POS Monitoring Label", name))

	def test_label_rename_disabled(self):
		self.assertEqual(frappe.get_meta("POS Monitoring Label").allow_rename, 0)

	def test_disable_last_active_default_label_rejected_when_enabled(self):
		# Run-unique default labels: the pre-existing masters keep their
		# disabled flag and `modified` timestamp. The Settings child table is
		# restored exactly by `_restore_settings`.
		mine = [f"{_RUN_ID} OMSetup Label {suffix}" for suffix in ("A", "B")]
		for name in mine:
			self._ensure_label(name)
		s = frappe.get_single("POS Monitoring Settings")
		s.set("default_labels", [{"label": name} for name in mine])
		s.enabled = 1
		s.save(ignore_permissions=True)
		for name in mine[:-1]:
			label = frappe.get_doc("POS Monitoring Label", name)
			label.disabled = 1
			label.save(ignore_permissions=True)
		last = frappe.get_doc("POS Monitoring Label", mine[-1])
		last.disabled = 1
		with self.assertRaises(frappe.ValidationError):
			last.save()

	def test_settings_enabled_requires_active_default_label(self):
		from pos_next.outlet_monitoring_setup import ensure_default_labels

		ensure_default_labels()
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 0
		s.save(ignore_permissions=True)
		s.set("default_labels", [])
		s.enabled = 1
		self.assertRaises(frappe.ValidationError, s.save)

	def test_duplicate_label_in_table_rejected(self):
		for doctype, kwargs in (
			("POS Monitoring Category", {"category_name": f"{_RUN_ID} Cat-Dup-{uuid.uuid4().hex[:6]}"}),
			("POS Monitoring Checkpoint", None),
			("POS Monitoring Settings", None),
		):
			lbl = f"{_RUN_ID} Label-Dup-{uuid.uuid4().hex[:6]}"
			self._ensure_label(lbl)
			if doctype == "POS Monitoring Category":
				doc = frappe.new_doc(doctype)
				doc.category_name = kwargs["category_name"]
				doc.append("labels", {"label": lbl})
				doc.append("labels", {"label": lbl})
				with self.assertRaises(frappe.ValidationError, msg=doctype):
					doc.save()
			elif doctype == "POS Monitoring Checkpoint":
				doc = frappe.new_doc(doctype)
				doc.checkpoint_name = f"{_RUN_ID} CP-Dup-{uuid.uuid4().hex[:6]}"
				doc.category = _SHARED_CATEGORY
				doc.media_type = "Photo"
				doc.min_count = 1
				doc.max_count = 2
				doc.append("labels", {"label": lbl})
				doc.append("labels", {"label": lbl})
				with self.assertRaises(frappe.ValidationError, msg=doctype):
					doc.save()
			else:
				s = frappe.get_single("POS Monitoring Settings")
				s.append("default_labels", {"label": lbl})
				s.append("default_labels", {"label": lbl})
				with self.assertRaises(frappe.ValidationError, msg=doctype):
					s.save()
				s.reload()

	def test_settings_restore_helper_restores_custom_default_labels(self):
		from pos_next.outlet_monitoring_setup import DEFAULT_LABELS, ensure_default_labels

		ensure_default_labels()
		lbl_a = f"{_RUN_ID} Label-Custom-A-{uuid.uuid4().hex[:6]}"
		lbl_b = f"{_RUN_ID} Label-Custom-B-{uuid.uuid4().hex[:6]}"
		self._ensure_label(lbl_a, "Blue")
		self._ensure_label(lbl_b, "Yellow")
		s = frappe.get_single("POS Monitoring Settings")
		s.set("default_labels", [{"label": lbl_b}, {"label": lbl_a}])
		s.save(ignore_permissions=True)

		nested = type(self)("test_settings_restore_helper_restores_custom_default_labels")
		nested.setUp()
		try:
			nested._restore_settings()
			restored = frappe.get_single("POS Monitoring Settings")
			self.assertEqual([r.label for r in restored.default_labels], [lbl_b, lbl_a])
			self.assertEqual([r.idx for r in restored.default_labels], [1, 2])
			# Exact restore: no default labels are smuggled back in, and every
			# child column plus the tabSingles rows match the snapshot.
			default_names = {name for name, _color in DEFAULT_LABELS}
			self.assertFalse(default_names & {r.label for r in restored.default_labels})
			assert_settings_state_equal(nested._original_state, "setup nested restore")
		finally:
			nested.tearDown()
			nested.doCleanups()

	def test_ensure_default_labels_idempotent_and_case_insensitive(self):
		from pos_next.outlet_monitoring_setup import ensure_default_labels

		count_before = frappe.db.count("POS Monitoring Label")
		names_first = ensure_default_labels()
		names_second = ensure_default_labels()
		self.assertEqual(names_first, names_second)
		# Case-insensitive dedupe: only one label per default name whatever the case.
		for label_name, _color in (
			("Sesuai SOP", "Green"),
			("Tidak Sesuai SOP", "Red"),
			("Tidak Berlaku", "Gray"),
		):
			matching = frappe.get_all(
				"POS Monitoring Label", filters={"label_name": ["like", label_name]}, pluck="name"
			)
			self.assertEqual(len(matching), 1, label_name)
		self.assertEqual(frappe.db.count("POS Monitoring Label"), count_before)
		s = frappe.get_single("POS Monitoring Settings")
		self.assertEqual([r.label for r in s.default_labels], names_first)

	def test_template_scope_defaults(self):
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Scope-Defaults-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
			}
		)
		from pos_next.pos_next.doctype.pos_monitoring_template.pos_monitoring_template import (
			apply_scope_defaults,
		)

		apply_scope_defaults(t)
		t.save(ignore_permissions=True)
		self.assertEqual(t.company_scope, "Selected")
		self.assertEqual(t.profile_scope, "All")
		self.assertEqual(t.shift_type_scope, "All")

		# A Desk form applies new_doc defaults (first Select option: All)
		# before save; mirror that client path here.
		t2 = frappe.new_doc("POS Monitoring Template")
		t2.update(
			{
				"template_name": f"{_RUN_ID} Scope-Defaults-2-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
			}
		)
		t2.save(ignore_permissions=True)
		self.assertEqual(t2.company_scope, "All")
		self.assertEqual(t2.profile_scope, "All")
		self.assertEqual(t2.shift_type_scope, "All")

		t3 = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Scope-Defaults-3-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"company_scope": "All",
			}
		)
		t3.save(ignore_permissions=True)
		self.assertEqual(t3.company_scope, "All")
		self.assertEqual(t3.profile_scope, "All")
		self.assertEqual(t3.shift_type_scope, "All")

	def test_explicit_all_scope_with_filled_table_stays_all(self):
		# An explicit All is never rewritten, even with filled scope tables.
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Scope-All-Kept-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
				"company_scope": "All",
			}
		)
		t.save(ignore_permissions=True)
		self.assertEqual(t.company_scope, "All")
		self.assertEqual(t.profile_scope, "All")
		self.assertEqual(t.shift_type_scope, "All")

	def test_template_per_hari_selected_company_requires_rows(self):
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Scope-Day-Req-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Hari",
				"due_time": "12:00:00",
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"company_scope": "Selected",
			}
		)
		with self.assertRaises(frappe.ValidationError):
			t.save(ignore_permissions=True)

	def test_shared_fixtures_never_touch_preexisting_records(self):
		# The sentinel rows carry the HELPER'S OWN fixture names: if the helper
		# overwrote (or re-created) its fixture, this test would catch it.
		# setUp already ran the helper, so these rows pre-exist this call.
		self.assertTrue(frappe.db.exists("POS Monitoring Category", _SHARED_CATEGORY))
		self.assertTrue(frappe.db.exists("POS Monitoring Checkpoint", _SHARED_CHECKPOINT))
		original = (
			frappe.db.get_value("POS Monitoring Category", _SHARED_CATEGORY, "description"),
			frappe.db.get_value(
				"POS Monitoring Checkpoint", _SHARED_CHECKPOINT, ["min_count", "max_count"], as_dict=True
			),
		)
		# Later tests share these rows, so put the sentinel values back.
		self.addCleanup(
			frappe.db.set_value, "POS Monitoring Category", _SHARED_CATEGORY, "description", original[0]
		)
		self.addCleanup(
			frappe.db.set_value,
			"POS Monitoring Checkpoint",
			_SHARED_CHECKPOINT,
			{"min_count": original[1].min_count, "max_count": original[1].max_count},
		)
		frappe.db.set_value(
			"POS Monitoring Category", _SHARED_CATEGORY, "description", "sentinel must survive"
		)
		frappe.db.set_value(
			"POS Monitoring Checkpoint",
			_SHARED_CHECKPOINT,
			{"min_count": 2, "max_count": 4},
		)
		before_cat = frappe.db.get_value(
			"POS Monitoring Category", _SHARED_CATEGORY, ["description", "modified"], as_dict=True
		)
		before_cp = frappe.db.get_value(
			"POS Monitoring Checkpoint",
			_SHARED_CHECKPOINT,
			["category", "min_count", "max_count"],
			as_dict=True,
		)

		_ensure_shared_template_fixtures()

		after_cat = frappe.db.get_value(
			"POS Monitoring Category", _SHARED_CATEGORY, ["description", "modified"], as_dict=True
		)
		after_cp = frappe.db.get_value(
			"POS Monitoring Checkpoint",
			_SHARED_CHECKPOINT,
			["category", "min_count", "max_count"],
			as_dict=True,
		)
		self.assertEqual(after_cat, before_cat)
		self.assertEqual(after_cp, before_cp)
		# Shared fixtures still exist for the template tests.
		self.assertTrue(frappe.db.exists("POS Monitoring Category", _SHARED_CATEGORY))
		self.assertTrue(frappe.db.exists("POS Monitoring Checkpoint", _SHARED_CHECKPOINT))

	def test_ownership_predicate_requires_this_runs_token(self):
		# Deterministic: no DB, no timing. A row from the same suite namespace
		# but another run is NOT ours; our own run's row is.
		prefixes = {"POS Monitoring Category": (f"{_RUN_ID} OMSetup Suite Cat",)}
		foreign = "ffff00 OMSetup Suite Cat"
		mine = f"{_RUN_ID} OMSetup Suite Cat"
		self.assertEqual(
			owned_fixture_names(
				"POS Monitoring Category", [foreign, mine], [], prefixes, _RUN_ID
			),
			{mine},
			"a same-prefix row of another run must never be claimed",
		)
		# A prefix that does not carry the run token is a bug, not a wildcard.
		with self.assertRaises(AssertionError):
			owned_fixture_names(
				"POS Monitoring Category", [mine], [], {"POS Monitoring Category": ("OMSetup Suite Cat",)}, _RUN_ID
			)

	def _insert_owned_sentinel(self, kind, own=False, cp_name=None):
		"""Insert a Category+Checkpoint this test owns; clean up only what landed.

		`own=False` uses a fresh random token, i.e. a *foreign* run's namespace
		that the suite cleanup must never claim; `own=True` uses this suite's
		run id so the suite cleanup does own it. Each cleanup is registered
		right after its own insert succeeded, in reverse dependency order
		(LIFO: Category registered first, so the Checkpoint is deleted first).
		"""
		token = _RUN_ID if own else uuid.uuid4().hex[:6]
		if not own:
			self.assertNotIn(token, _RUN_ID)
		cat = f"{token} OMSetup Suite Cat {kind}"
		cp = cp_name or f"{token} OMSetup Suite CP {kind}"
		for doctype, name in (
			("POS Monitoring Category", cat),
			("POS Monitoring Checkpoint", cp),
		):
			if cp_name and doctype == "POS Monitoring Checkpoint":
				continue
			self.assertFalse(
				frappe.db.exists(doctype, name),
				f"sentinel {name} already exists; refusing to touch a pre-existing row",
			)
		frappe.get_doc(
			{"doctype": "POS Monitoring Category", "category_name": cat, "description": f"{kind} sentinel"}
		).insert(ignore_permissions=True)
		self.addCleanup(_delete_created_rows, "POS Monitoring Category", [cat])
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": cp,
				"category": cat,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(_delete_created_rows, "POS Monitoring Checkpoint", [cp])
		return cat, cp

	def test_cleanup_preserves_foreign_run_rows_in_the_same_namespace(self):
		# Same suite prefix, DIFFERENT run id: a parallel/older run's fixture.
		# Cleanup must leave it byte-identical while removing our own. This test
		# never deletes a row it did not insert itself: both sentinels use fresh
		# tokens, the test refuses to run over a pre-existing row, and each
		# cleanup is registered only after its own insert returned.
		foreign_cat, foreign_cp = self._insert_owned_sentinel("foreign")
		own_cat, own_cp = self._insert_owned_sentinel("own", own=True)
		frappe.db.commit()

		foreign_cat_row = frappe.db.get_value("POS Monitoring Category", foreign_cat, "*", as_dict=True)
		foreign_cp_row = frappe.db.get_value("POS Monitoring Checkpoint", foreign_cp, "*", as_dict=True)

		_delete_new_fixtures(self._initial_fixtures)

		self.assertEqual(
			frappe.db.get_value("POS Monitoring Category", foreign_cat, "*", as_dict=True),
			foreign_cat_row,
			"a foreign run's Category must survive cleanup with every column unchanged",
		)
		self.assertEqual(
			frappe.db.get_value("POS Monitoring Checkpoint", foreign_cp, "*", as_dict=True),
			foreign_cp_row,
			"a foreign run's Checkpoint must survive cleanup with every column unchanged",
		)
		self.assertFalse(frappe.db.exists("POS Monitoring Category", own_cat), "own fixture must be deleted")
		self.assertFalse(frappe.db.exists("POS Monitoring Checkpoint", own_cp), "own fixture must be deleted")

	def test_sentinel_insert_failure_never_deletes_a_preexisting_checkpoint(self):
		"""A sentinel insert that fails must not delete a pre-existing row.

		Simulates a race/collision where the Checkpoint name already exists,
		so its insert hits DuplicateEntryError. Cleanup registered only after
		a successful insert cannot touch the existing row; cleanup registered
		before insert would register the pre-existing name and delete it during
		teardown.
		"""
		preexisting_cat, preexisting_cp = self._insert_owned_sentinel("preexisting")
		frappe.db.commit()
		cp_before = frappe.db.get_value("POS Monitoring Checkpoint", preexisting_cp, "*", as_dict=True)
		cat_before = frappe.db.get_value("POS Monitoring Category", preexisting_cat, "*", as_dict=True)

		# The survival check runs after cleanups registered during the collision
		# attempt execute (LIFO: registered after the preexisting sentinels'
		# cleanups, but before any cleanup registered in the failing call).
		def assert_still_there():
			frappe.set_user("Administrator")
			self.assertEqual(
				frappe.db.get_value("POS Monitoring Checkpoint", preexisting_cp, "*", as_dict=True),
				cp_before,
				"a failed sentinel insert must not delete or modify a pre-existing Checkpoint",
			)
			self.assertEqual(
				frappe.db.get_value("POS Monitoring Category", preexisting_cat, "*", as_dict=True),
				cat_before,
				"a failed sentinel insert must not delete or modify a pre-existing Category",
			)

		self.addCleanup(assert_still_there)

		with self.assertRaises(frappe.DuplicateEntryError):
			self._insert_owned_sentinel("colliding", cp_name=preexisting_cp)
		frappe.db.rollback()

	def test_template_time_compare_after_reload(self):
		# Time fields reload as timedelta ("7:30:00"); a string compare would
		# read that as greater than "14:00:00" and wrongly reject the save.
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Window-Reload-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Hari",
				"due_time": "14:00:00",
				"open_time": "07:30:00",
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
			}
		)
		t.save(ignore_permissions=True)
		t.reload()
		t.save(ignore_permissions=True)

		t2 = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Window-Reload-Bad-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Hari",
				"due_time": "14:00:00",
				"open_time": "15:00:00",
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			t2.save(ignore_permissions=True)

	def test_template_negative_open_after_minutes_rejected(self):
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Window-Neg-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"open_after_minutes": -5,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			t.save(ignore_permissions=True)

	def test_template_selected_scope_requires_rows(self):
		for scope_field, _table in (("profile_scope", "pos_profiles"), ("shift_type_scope", "shift_types")):
			t = frappe.get_doc(
				{
					"doctype": "POS Monitoring Template",
					"template_name": f"{_RUN_ID} Scope-Req-{uuid.uuid4().hex[:6]}",
					"cadence": "Per Shift",
					"due_minutes_after_open": 30,
					"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
					"companies": [{"company": "_Test Company"}],
				}
			)
			setattr(t, scope_field, "Selected")
			with self.assertRaises(frappe.ValidationError, msg=f"{scope_field} empty Selected"):
				t.save()

	def test_template_per_hari_ignores_profile_shift_scope(self):
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Scope-Day-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Hari",
				"due_time": "12:00:00",
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
				"profile_scope": "Selected",
				"shift_type_scope": "Selected",
			}
		)
		t.save(ignore_permissions=True)
		self.assertEqual(t.profile_scope, "Selected")

	def test_template_manual_requires_submit_within_hours_gt_0(self):
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Manual-Hours-{uuid.uuid4().hex[:6]}",
				"cadence": "Manual",
				"submit_within_hours": 0,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
			}
		)
		self.assertRaises(frappe.ValidationError, t.save)

	def test_template_open_window_must_be_before_due(self):
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Window-Shift-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"open_after_minutes": 30,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
			}
		)
		self.assertRaises(frappe.ValidationError, t.save)

		t2 = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Window-Day-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Hari",
				"due_time": "12:00:00",
				"open_time": "12:00:00",
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
				"companies": [{"company": "_Test Company"}],
			}
		)
		self.assertRaises(frappe.ValidationError, t2.save)

	def test_template_per_shift_requires_due_minutes_per_day_requires_due_time(self):
		# Per Shift without due_minutes_after_open
		t_shift = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Test Per Shift Due Template",
				"cadence": "Per Shift",
				"due_minutes_after_open": 0,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
			}
		)
		self.assertRaises(frappe.ValidationError, t_shift.save)

		# Per Hari without due_time
		t_day = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Test Per Day Due Template",
				"cadence": "Per Hari",
				"due_time": None,
				"checkpoints": [{"checkpoint": _SHARED_CHECKPOINT, "required": 1}],
			}
		)
		self.assertRaises(frappe.ValidationError, t_day.save)
