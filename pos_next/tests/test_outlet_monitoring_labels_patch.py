# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
import unittest
import uuid
from contextlib import contextmanager

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.outlet_monitoring_setup import DEFAULT_LABELS
from pos_next.tests.outlet_monitoring_test_utils import (
	assert_settings_state_equal,
	delete_owned_fixtures,
	owned_fixtures,
	restore_settings_state,
	snapshot_settings_state,
)


@contextmanager
def _rolled_back():
	"""Run destructive work inside a savepoint and undo it exactly.

	Schema work must happen before this: MySQL implicitly commits on
	ALTER/CREATE TABLE, which would silently discard the savepoint.
	"""
	name = f"om_patch_{uuid.uuid4().hex[:8]}"
	frappe.db.savepoint(name)
	try:
		yield
	finally:
		frappe.db.rollback(save_point=name)
		frappe.clear_document_cache("POS Monitoring Settings", "POS Monitoring Settings")


def _label_rows():
	return frappe.db.sql(
		"select name, color, disabled, creation, modified, owner from `tabPOS Monitoring Label` order by name",
		as_dict=True,
	)


_RUN_ID = uuid.uuid4().hex[:6]
# Run-unique fixtures: this suite only ever mutates records it created.
PATCH_CATEGORY = f"{_RUN_ID} Patch Category"
PATCH_CP = f"{_RUN_ID} Patch CP 1"
PATCH_TEMPLATE = f"{_RUN_ID} Patch Template"
PATCH_LABEL = f"{_RUN_ID} Patch Label"
# Same text, different case: exercises the case-insensitive dedupe.
PATCH_LABEL_LOWER = PATCH_LABEL.lower()
# Label used only to reach the Reviewed gate; run-unique like the rest.
GATE_LABEL = f"{_RUN_ID} Patch Gate Label"
# Text every legacy pass/fail/na cell in this suite starts with: the patch
# turns those texts into Label masters, so they must be run-unique too.
T = f"{_RUN_ID} Patch Text"


def _legacy_tables():
	"""True only where the pre-labels columns survived model sync."""
	return frappe.db.has_column("POS Monitoring Category", "pass_label") and frappe.db.has_column(
		"POS Monitoring Report Checkpoint", "pass_label"
	)


_FIXTURE_CHILDREN = {
	"POS Monitoring Report": ("POS Monitoring Report Checkpoint",),
	"POS Monitoring Template": (
		"POS Monitoring Template Checkpoint",
		"POS Monitoring Template Company",
		"POS Monitoring Template POS Profile",
		"POS Monitoring Template Shift Type",
	),
	"POS Monitoring Checkpoint": ("POS Monitoring Label Item",),
	"POS Monitoring Category": ("POS Monitoring Label Item",),
}
_ORDER = (
	"POS Monitoring Report",
	"POS Monitoring Template",
	"POS Monitoring Checkpoint",
	"POS Monitoring Category",
	"POS Monitoring Label",
)
# Label masters are named after the legacy cells the patch migrates, so the
# text prefix covers them; nothing outside this name space is ever deleted.
_FIXTURE_PREFIXES = {
	"POS Monitoring Label": (
		f"{_RUN_ID} Patch Label",
		f"{_RUN_ID} Patch Gate Label",
		f"{_RUN_ID} Patch Text",
	),
	"POS Monitoring Template": (f"{_RUN_ID} Patch Template",),
	"POS Monitoring Checkpoint": (f"{_RUN_ID} Patch CP",),
	"POS Monitoring Category": (f"{_RUN_ID} Patch Category",),
}
_FIXTURE_LINKS = {
	"POS Monitoring Report": (("template", "POS Monitoring Template"),),
}


def _set_legacy(doctype, name, mapping):
	row = frappe.get_doc(doctype, name)
	table = f"tab{doctype}"
	cols = ", ".join(f"`{k}` = %s" for k in mapping)
	frappe.db.sql(f"update `{table}` set {cols} where name = %s", (*mapping.values(), name))
	row.reload()
	return row


def _make_report_row(
	label_options="", assessment=None, pass_text=None, fail_text=None, na_text=None, label=None
):
	import pos_next.patches.v2_36_0.outlet_monitoring_labels as patch

	cp = frappe.get_doc("POS Monitoring Checkpoint", PATCH_CP)
	options = __import__("pos_next.services.outlet_monitoring", fromlist=["resolve_label_options"])
	_ = (cp, options, patch)
	report = frappe.get_doc(
		{
			"doctype": "POS Monitoring Report",
			"template": PATCH_TEMPLATE,
			"cadence": "Per Shift",
			"company": "_Test Company",
			"operational_date": frappe.utils.today(),
			"due_at": frappe.utils.now_datetime(),
			"obligation_key": f"patch-{uuid.uuid4().hex[:8]}",
			"revision": 1,
			"status": "Draft",
			"checkpoints": [
				{
					"checkpoint": PATCH_CP,
					"checkpoint_name": PATCH_CP,
					"category": PATCH_CATEGORY,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
					"required": 1,
					"label_options": label_options,
					"cashier_can_label": 0,
					"label": label,
				}
			],
		}
	)
	report.flags.internal_report_build = True
	report.insert(ignore_permissions=True)
	row = report.checkpoints[0].name
	if _legacy_tables():
		frappe.db.sql(
			"update `tabPOS Monitoring Report Checkpoint` set pass_label = %s, "
			"fail_label = %s, na_label = %s, assessment = %s where name = %s",
			(pass_text or "", fail_text or "", na_text or "", assessment, row),
		)
	else:
		frappe.db.set_value("POS Monitoring Report Checkpoint", row, "assessment", assessment)
	return report


def _make_status(report, status):
	"""Drive a freshly inserted Draft report into a real Submitted/Reviewed/Waived.

	Reviewed can only be reached through the controller when the row already
	carries a label from a snapshot, so it is stepped through that gate first
	and then stripped back to the pre-patch legacy shape (see `_legacy_shape`).
	"""
	if status == "Draft":
		return None
	frappe.set_user("Administrator")
	doc = frappe.get_doc("POS Monitoring Report", report.name)
	doc.flags.ignore_permissions = True
	doc.flags.ignore_mandatory = True
	doc.status = "Submitted"
	doc.submit()
	if status == "Submitted":
		return doc
	if status == "Reviewed":
		# `label_options` is read-only and not allow_on_submit, so the gate is
		# satisfied with SQL; `_legacy_shape` strips it again afterwards.
		if not frappe.db.exists("POS Monitoring Label", GATE_LABEL):
			frappe.get_doc(
				{"doctype": "POS Monitoring Label", "label_name": GATE_LABEL, "color": "Green"}
			).insert(ignore_permissions=True)
		frappe.db.sql(
			"update `tabPOS Monitoring Report Checkpoint` set label_options = %s where parent = %s",
			(json.dumps([{"label": GATE_LABEL, "color": "Green"}]), report.name),
		)
	doc.reload()
	doc.flags.ignore_permissions = True
	doc.flags.internal_status_change = True
	doc.status = status
	if status == "Reviewed":
		doc.checkpoints[0].label = GATE_LABEL
	if status == "Waived":
		doc.waive_reason = "Patch fixture"
	doc.save()
	doc.reload()
	return doc


def _legacy_shape(report):
	"""Strip the snapshot back off so the doc looks exactly like pre-patch data.

	A report finalized before the labels migration has status/docstatus set,
	`assessment` set, and NO label_options and NO label. The patch must fill
	them; a doc that was only finalized after `execute()` would prove nothing.
	"""
	row = frappe.db.get_value(
		"POS Monitoring Report Checkpoint", {"parent": report}, "name", order_by="idx asc"
	)
	frappe.db.sql(
		"update `tabPOS Monitoring Report Checkpoint` set label_options = '', label = '' where name = %s",
		(row,),
	)
	state = frappe.db.get_value("POS Monitoring Report", report, ["status", "docstatus"], as_dict=True)
	assert state.status == "Reviewed" and state.docstatus == 1, state
	return row


class TestOutletMonitoringLabelsPatch(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._initial = {
			dt: set(frappe.get_all(dt, pluck="name"))
			for dt in (
				"POS Monitoring Report",
				"POS Monitoring Template",
				"POS Monitoring Checkpoint",
				"POS Monitoring Category",
				"POS Monitoring Label",
			)
		}
		cls._settings_state = snapshot_settings_state()
		cls._label_state = _label_rows()
		for lbl, color in ((PATCH_LABEL, "Blue"), (PATCH_LABEL_LOWER, "Red")):
			if not frappe.db.exists("POS Monitoring Label", lbl):
				frappe.get_doc(
					{
						"doctype": "POS Monitoring Label",
						"label_name": lbl,
						"color": color,
					}
				).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Category",
				"category_name": PATCH_CATEGORY,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": PATCH_CP,
				"category": PATCH_CATEGORY,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": PATCH_TEMPLATE,
				"cadence": "Per Shift",
				"due_minutes_after_open": 30,
				"checkpoints": [{"checkpoint": PATCH_CP, "required": 1}],
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		restore_settings_state(cls._settings_state)
		# Only rows in this suite's own name space: a plain snapshot diff would
		# also match a row another writer created while the suite ran.
		owned = owned_fixtures(cls._initial, _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID)
		delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _ORDER)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")

	def test_patch_builds_labels_and_snapshots_all_statuses(self):
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		self.assertTrue(hasattr(patch, "execute"))
		if not _legacy_tables():
			self.skipTest("legacy pass/fail columns already dropped on this site")
		# Each status gets a REAL doc in that status, its own legacy text, and
		# a snapshot that differs from the master (which stays "Patch Label").
		_set_legacy(
			"POS Monitoring Category",
			PATCH_CATEGORY,
			{
				"pass_label": f"{T} Kategori",
				"fail_label": "",
				"na_label": "",
			},
		)
		statuses = ("Draft", "Submitted", "Reviewed", "Waived")
		reports = {}
		for status in statuses:
			rep = _make_report_row(pass_text=f"{T} Baris {status}", label=None)
			reports[status] = rep.name
			if status != "Draft":
				_make_status(rep, status)
			stored = frappe.db.get_value(
				"POS Monitoring Report",
				rep.name,
				["status", "docstatus"],
				as_dict=True,
			)
			self.assertEqual(stored.status, status, f"fixture {status} must already BE {status}")
			self.assertEqual(stored.docstatus, 0 if status == "Draft" else 1)

		# Pre-patch legacy shape: the Reviewed doc keeps its finalized status
		# but loses the snapshot the controller needed to reach it.
		_legacy_shape(reports["Reviewed"])
		pre = frappe.db.get_value(
			"POS Monitoring Report Checkpoint",
			{"parent": reports["Reviewed"]},
			["label", "label_options"],
			as_dict=True,
		)
		self.assertEqual((pre.label, pre.label_options), ("", ""))

		patch.execute()

		final = frappe.db.get_value(
			"POS Monitoring Report", reports["Reviewed"], ["status", "docstatus"], as_dict=True
		)
		self.assertEqual((final.status, final.docstatus), ("Reviewed", 1))

		names = frappe.get_all("POS Monitoring Label", pluck="name")
		for status, name in reports.items():
			row = frappe.get_doc("POS Monitoring Report", name).checkpoints[0]
			options = json.loads(row.label_options or "[]")
			self.assertTrue(options, f"{status} row must get a snapshot")
			# Snapshot comes from the row's own legacy text, not the master list.
			self.assertEqual([o["label"] for o in options], [f"{T} Baris {status}"])
			self.assertEqual(options[0]["color"], "Green")
			self.assertIn(f"{T} Baris {status}", names)

	def test_patch_reads_legacy_settings_texts_from_tab_singles(self):
		# Settings is a Single with no table and no meta fields left, so the
		# legacy default_*_label texts only exist as tabSingles rows. The whole
		# body runs in a savepoint: the exact pre-test tabSingles rows come back
		# even if an assertion below fails.
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		singles_before = frappe.db.sql(
			"select field, value from tabSingles where doctype = 'POS Monitoring Settings' order by field",
			as_dict=True,
		)
		labels_before = _label_rows()
		with _rolled_back():
			legacy = {
				"default_pass_label": "Lolos Lama",
				"default_fail_label": "Gagal Lama",
				"default_na_label": "Tak Berlaku Lama",
			}
			frappe.db.sql(
				"delete from tabSingles where doctype = 'POS Monitoring Settings' "
				"and field in ('default_pass_label', 'default_fail_label', 'default_na_label')"
			)
			for field, value in legacy.items():
				frappe.db.sql(
					"insert into tabSingles (doctype, field, value) values (%s, %s, %s)",
					("POS Monitoring Settings", field, value),
				)
			# Clear defaults so the patch has to fill them from the legacy texts.
			frappe.db.delete("POS Monitoring Label Item", {"parenttype": "POS Monitoring Settings"})
			frappe.clear_document_cache("POS Monitoring Settings", "POS Monitoring Settings")

			patch.execute()

			rows = frappe.get_all(
				"POS Monitoring Label Item",
				filters={"parenttype": "POS Monitoring Settings"},
				fields=["label"],
				order_by="idx",
			)
			self.assertEqual(
				[r.label for r in rows],
				["Lolos Lama", "Gagal Lama", "Tak Berlaku Lama"],
				"legacy Settings texts must land in pass -> fail -> na order",
			)
			colors = {
				r.name: r.color
				for r in frappe.get_all(
					"POS Monitoring Label",
					filters={"name": ["in", ["Lolos Lama", "Gagal Lama", "Tak Berlaku Lama"]]},
					fields=["name", "color"],
				)
			}
			self.assertEqual(colors, {"Lolos Lama": "Green", "Gagal Lama": "Red", "Tak Berlaku Lama": "Gray"})

		self.assertEqual(
			frappe.db.sql(
				"select field, value from tabSingles where doctype = 'POS Monitoring Settings' order by field",
				as_dict=True,
			),
			singles_before,
			"legacy tabSingles rows must be gone again after the savepoint rollback",
		)
		self.assertEqual(_label_rows(), labels_before)

	def test_patch_fills_category_children_in_pass_fail_na_order(self):
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		if not _legacy_tables():
			self.skipTest("legacy pass/fail columns already dropped on this site")
		_set_legacy(
			"POS Monitoring Category",
			PATCH_CATEGORY,
			{
				"pass_label": f"{T} Urut Lulus",
				"fail_label": f"{T} Urut Gagal",
				"na_label": f"{T} Urut NA",
			},
		)
		frappe.db.delete(
			"POS Monitoring Label Item",
			{"parenttype": "POS Monitoring Category", "parent": PATCH_CATEGORY},
		)
		patch.execute()
		# Reload from DB in display order.
		rows = frappe.get_all(
			"POS Monitoring Label Item",
			filters={"parenttype": "POS Monitoring Category", "parent": PATCH_CATEGORY},
			fields=["label", "idx"],
			order_by="idx",
		)
		self.assertEqual([r.label for r in rows], [f"{T} Urut Lulus", f"{T} Urut Gagal", f"{T} Urut NA"])
		self.assertEqual([r.idx for r in rows], [1, 2, 3])

	def test_patch_assessment_maps_to_label_by_slot(self):
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		if not _legacy_tables():
			self.skipTest("legacy pass/fail columns already dropped on this site")
		rep = _make_report_row(assessment="Fail", fail_text=f"{T} Slot Hijau")
		patch.execute()
		row = frappe.get_doc("POS Monitoring Report", rep.name).checkpoints[0]
		self.assertEqual(row.label, f"{T} Slot Hijau")

	def test_patch_dedupes_same_text_two_slots(self):
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		if not _legacy_tables():
			self.skipTest("legacy pass/fail columns already dropped on this site")
		_set_legacy(
			"POS Monitoring Category",
			PATCH_CATEGORY,
			{
				"pass_label": f"{T} Sama",
				"fail_label": f"{T} Sama",
				"na_label": "",
			},
		)
		rep = _make_report_row(pass_text=f"{T} Sama")
		patch.execute()
		labels = [
			n
			for n in frappe.get_all("POS Monitoring Label", pluck="name")
			if n.strip().lower() == f"{T} sama".lower()
		]
		self.assertEqual(len(labels), 1)
		row = frappe.get_doc("POS Monitoring Report", rep.name).checkpoints[0]
		options = json.loads(row.label_options or "[]")
		self.assertEqual([o["label"] for o in options].count(f"{T} Sama"), 1)

	def test_patch_case_insensitive_dedupe(self):
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		if not _legacy_tables():
			self.skipTest("legacy pass/fail columns already dropped on this site")
		_set_legacy(
			"POS Monitoring Category",
			PATCH_CATEGORY,
			{
				"pass_label": f" {T} BAIK ",
				"fail_label": f"{T} baik",
				"na_label": f"{T} Baik",
			},
		)
		rep = _make_report_row(pass_text=f"{T} BAIK")
		patch.execute()
		labels = [
			n
			for n in frappe.get_all("POS Monitoring Label", pluck="name")
			if n.strip().lower() == f"{T} baik".lower()
		]
		self.assertEqual(len(labels), 1)
		row = frappe.get_doc("POS Monitoring Report", rep.name).checkpoints[0]
		options = json.loads(row.label_options or "[]")
		self.assertEqual([o["label"] for o in options].count(labels[0]), 1)

	def test_patch_template_scope_from_null_columns(self):
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		t = frappe.get_doc("POS Monitoring Template", PATCH_TEMPLATE)
		frappe.db.sql(
			"update `tabPOS Monitoring Template` set company_scope = NULL, "
			"profile_scope = NULL, shift_type_scope = NULL where name = %s",
			t.name,
		)
		frappe.db.sql("delete from `tabPOS Monitoring Template POS Profile` where parent = %s", t.name)
		patch.execute()
		t.reload()
		self.assertEqual(t.company_scope, "Selected")
		self.assertEqual(t.profile_scope, "All")
		self.assertEqual(t.shift_type_scope, "All")

	def test_patch_rerun_does_not_overwrite(self):
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		if not _legacy_tables():
			self.skipTest("legacy pass/fail columns already dropped on this site")
		_set_legacy(
			"POS Monitoring Category",
			PATCH_CATEGORY,
			{
				"pass_label": f"{T} Tetap",
				"fail_label": "",
				"na_label": "",
			},
		)
		rep = _make_report_row(pass_text=f"{T} Tetap")
		patch.execute()
		# A reviewer picks a DIFFERENT label from the same snapshot.
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Label",
				"label_name": f"{T} Pilihan Reviewer",
				"color": "Red",
			}
		).insert(ignore_permissions=True)
		rep.reload()
		row = rep.checkpoints[0]
		row.label_options = json.dumps(
			[{"label": f"{T} Tetap", "color": "Green"}, {"label": f"{T} Pilihan Reviewer", "color": "Red"}]
		)
		row.label = f"{T} Pilihan Reviewer"
		rep.flags.ignore_permissions = True
		rep.save()
		frappe.db.set_value("POS Monitoring Template", PATCH_TEMPLATE, "company_scope", "All")
		before_options = frappe.db.get_value("POS Monitoring Report Checkpoint", row.name, "label_options")

		patch.execute()

		after_options = frappe.db.get_value("POS Monitoring Report Checkpoint", row.name, "label_options")
		self.assertEqual(before_options, after_options)
		reloaded = frappe.get_doc("POS Monitoring Report", rep.name).checkpoints[0]
		self.assertEqual(reloaded.label, f"{T} Pilihan Reviewer", "rerun must not reset the review label")
		self.assertEqual(
			frappe.db.get_value("POS Monitoring Template", PATCH_TEMPLATE, "company_scope"), "All"
		)

	def test_patch_fresh_site_only_fills_settings(self):
		"""A site with no monitoring data: the patch must only fill Settings.

		Runs entirely inside a savepoint — the labels it deletes are restored
		by the rollback, never recreated (a recreate cannot bring back the
		original names, colours, disabled flags, `creation` or the Category /
		Checkpoint rows that reference them). `execute()` must not commit or
		release the savepoint, or the rollback would be a no-op.
		"""
		from pos_next.patches.v2_36_0 import outlet_monitoring_labels as patch

		state_before = snapshot_settings_state()
		labels_before = _label_rows()
		category_refs_before = frappe.db.sql(
			"select parent, label from `tabPOS Monitoring Label Item` "
			"where parenttype = 'POS Monitoring Category' order by parent, idx"
		)

		with _rolled_back():
			# Simulated fresh site: no default labels, no default masters.
			frappe.db.delete("POS Monitoring Label Item", {"parenttype": "POS Monitoring Settings"})
			for label_name, _color in DEFAULT_LABELS:
				if frappe.db.exists("POS Monitoring Label", label_name):
					frappe.db.delete("POS Monitoring Label Item", {"label": label_name})
					frappe.db.delete("POS Monitoring Label", label_name)
			frappe.clear_document_cache("POS Monitoring Settings", "POS Monitoring Settings")
			self.assertEqual(
				frappe.get_all(
					"POS Monitoring Label Item", filters={"parenttype": "POS Monitoring Settings"}
				),
				[],
			)

			patch.execute()

			rows = frappe.get_all(
				"POS Monitoring Label Item",
				filters={"parenttype": "POS Monitoring Settings"},
				fields=["label"],
				order_by="idx",
			)
			self.assertEqual([r.label for r in rows], [name for name, _ in DEFAULT_LABELS])

		# Exact restoration: every label column and every Settings child row.
		self.assertEqual(_label_rows(), labels_before)
		assert_settings_state_equal(state_before, "fresh-site rollback")
		self.assertEqual(
			frappe.db.sql(
				"select parent, label from `tabPOS Monitoring Label Item` "
				"where parenttype = 'POS Monitoring Category' order by parent, idx"
			),
			category_refs_before,
		)

	def test_savepoint_cleanup_survives_a_failing_assertion(self):
		"""Cleanup is in `finally`, so a red assertion still leaves no trace."""
		state_before = snapshot_settings_state()
		labels_before = _label_rows()
		with self.assertRaises(AssertionError):
			with _rolled_back():
				frappe.db.delete("POS Monitoring Label Item", {"parenttype": "POS Monitoring Settings"})
				frappe.db.sql("delete from `tabPOS Monitoring Label` where name = %s", (PATCH_LABEL,))
				self.assertFalse(frappe.db.exists("POS Monitoring Label", PATCH_LABEL))
				raise AssertionError("simulated failing assertion after destructive work")
		self.assertTrue(frappe.db.exists("POS Monitoring Label", PATCH_LABEL))
		self.assertEqual(_label_rows(), labels_before)
		assert_settings_state_equal(state_before, "failed-assertion rollback")
