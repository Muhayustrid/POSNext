# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Tests for the copy_global_settings_to_single patch's locale migration.

The allowed_locales field was dropped from the POS Settings meta while this
patch runs in [post_model_sync], so the old child rows must be read straight
from the `tabPOS Allowed Locale` table. These tests insert a dummy legacy row
and check the migration picks its languages up (and stays idempotent).

Run via
pos_next/_pn_run_tests.py pos_next.api.test_copy_global_settings_patch
"""

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.patches.v2_11_0.copy_global_settings_to_single import (
	GLOBAL_DOCTYPE,
	_migrate_allowed_locales,
)

DUMMY_PARENT = "_REL04-test-settings"


class TestMigrateAllowedLocales(FrappeTestCase):
	def setUp(self):
		# make the dummy the only enabled row so get_value({enabled: 1})
		# deterministically lands on it; restore the others afterwards
		self.enabled_rows = frappe.db.get_all("POS Settings", filters={"enabled": 1}, pluck="name")
		for name in self.enabled_rows:
			frappe.db.set_value("POS Settings", name, "enabled", 0, update_modified=False)
		frappe.get_doc(
			{"doctype": "POS Settings", "name": DUMMY_PARENT, "enabled": 1}
		).db_insert()
		for idx, language in enumerate(("en", "id"), 1):
			frappe.get_doc(
				{
					"doctype": "POS Allowed Locale",
					"name": f"_REL04-{uuid.uuid4().hex[:8]}",
					"language": language,
					"parent": DUMMY_PARENT,
					"parentfield": "allowed_locales",
					"parenttype": "POS Settings",
					"idx": idx,
				}
			).db_insert()

	def tearDown(self):
		frappe.db.delete("POS Allowed Locale", {"parent": DUMMY_PARENT})
		frappe.db.delete("POS Settings", {"name": DUMMY_PARENT})
		for name in self.enabled_rows:
			frappe.db.set_value("POS Settings", name, "enabled", 1, update_modified=False)

	def test_migrates_locales_from_legacy_child_table(self):
		single = frappe.get_doc(GLOBAL_DOCTYPE)
		changed = _migrate_allowed_locales(single)
		self.assertTrue(changed)
		self.assertEqual(
			[row.language for row in single.get("allowed_locales")], ["en", "id"]
		)

	def test_idempotent(self):
		single = frappe.get_doc(GLOBAL_DOCTYPE)
		_migrate_allowed_locales(single)
		changed = _migrate_allowed_locales(single)
		self.assertFalse(changed)
		self.assertEqual(len(single.get("allowed_locales")), 2)
