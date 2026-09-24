# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""REL-01: guard + backup for the destructive POS Settings reclaim.

Drift is simulated by monkeypatching `_pos_settings_meta_drift`; the
destructive step is replaced by a recorder, so the real dev table is
never dropped. Read-only against the shared dev site otherwise.

Run via pos_next/_pn_run_tests.py pos_next.api.test_settings_reclaim
"""

import json
import os
import unittest.mock
from glob import glob

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next import install

DRIFT = frappe._dict(module="Accounts", issingle=1)
CONF_KEY = "allow_settings_reclaim"


def _backup_files():
	backup_dir = frappe.get_site_path("private", "backups")
	return set(glob(os.path.join(backup_dir, "pos_settings_reclaim_*.json")))


class TestSettingsReclaimGuard(FrappeTestCase):
	def setUp(self):
		self.drift = unittest.mock.patch.object(
			install, "_pos_settings_meta_drift", return_value=DRIFT
		)
		self.drift.start()
		self.destroy = unittest.mock.patch.object(
			install, "_destroy_pos_settings_schema", return_value=None
		)
		self.mock_destroy = self.destroy.start()
		# The real reclaim reloads pos_next doctype meta + commits after
		# destroy; with destroy mocked the reload would touch shared site
		# meta for nothing. Mock it so the test is purely about the
		# backup-before-destroy ordering.
		self.reload = unittest.mock.patch.object(frappe, "reload_doc", return_value=None)
		self.reload.start()
		self.addCleanup(self.drift.stop)
		self.addCleanup(self.destroy.stop)
		self.addCleanup(self.reload.stop)
		# Keep the in-memory site config clean before and after each test.
		self.addCleanup(frappe.conf.pop, CONF_KEY, None)
		frappe.conf.pop(CONF_KEY, None)

		def _purge_new_backups():
			for path in _backup_files() - self._files_before:
				os.remove(path)

		self._files_before = _backup_files()
		self.addCleanup(_purge_new_backups)

	def test_guard_off_blocks_destructive_reclaim(self):
		rows_before = frappe.db.sql("SELECT * FROM `tabPOS Settings` ORDER BY `name`", as_dict=True)
		files_before = _backup_files()

		install.reclaim_pos_settings_doctype(quiet=True)

		self.mock_destroy.assert_not_called()
		rows_after = frappe.db.sql("SELECT * FROM `tabPOS Settings` ORDER BY `name`", as_dict=True)
		self.assertEqual(rows_after, rows_before)
		self.assertTrue(rows_after)
		self.assertEqual(_backup_files(), files_before)

	def test_guard_on_backs_up_before_destroy(self):
		rows = frappe.db.sql("SELECT * FROM `tabPOS Settings`", as_dict=True)
		files_before = _backup_files()
		frappe.conf[CONF_KEY] = 1

		def fake_destroy():
			# Runs inside reclaim: the backup must already exist on disk and
			# contain the rows that are about to be destroyed.
			new = _backup_files() - files_before
			self.assertEqual(len(new), 1, "backup file must be created before destroy")
			with open(new.pop()) as f:
				payload = json.load(f)
			self.assertTrue(payload["tabPOS Settings"])
			self.assertEqual(len(payload["tabPOS Settings"]), len(rows))

		self.mock_destroy.side_effect = fake_destroy

		install.reclaim_pos_settings_doctype(quiet=True)

		self.mock_destroy.assert_called_once()
