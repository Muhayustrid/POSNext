# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.install import ensure_site_indexes
from pos_next.patches.v2_36_0 import outlet_monitoring_sweep_day_index as day_patch


def _index_exists(table_name: str, index_name: str) -> bool:
	return bool(
		frappe.db.sql(
			"""
			SELECT 1 FROM information_schema.STATISTICS
			WHERE table_schema = DATABASE()
			AND table_name = %s
			AND index_name = %s
			LIMIT 1
			""",
			(table_name, index_name),
		)
	)


class TestOutletMonitoringSweepIndexes(FrappeTestCase):
	def test_sweep_day_index_emits_ddl_when_missing_and_skips_when_present(self):
		"""When om_sweep_day_idx is missing, patch emits ALTER TABLE once; when present, it skips."""
		with (
			patch.object(frappe.db, "table_exists", return_value=True),
			patch.object(frappe.db, "sql_ddl") as mock_ddl,
		):
			# Branch 1: index missing in statistics -> executes ALTER TABLE
			with patch.object(frappe.db, "sql", return_value=[]):
				day_patch.execute()
				mock_ddl.assert_called_once_with(
					"ALTER TABLE `tabPOS Monitoring Report` ADD INDEX om_sweep_day_idx "
					"(docstatus, status, cadence, operational_date)"
				)

			mock_ddl.reset_mock()

			# Branch 2: index already present -> skips DDL entirely
			with patch.object(frappe.db, "sql", return_value=[(1,)]):
				day_patch.execute()
				mock_ddl.assert_not_called()

	def test_sweep_day_index_skips_when_table_missing(self):
		"""Patch returns early without executing DDL if table does not exist."""
		with (
			patch.object(frappe.db, "table_exists", return_value=False),
			patch.object(frappe.db, "sql_ddl") as mock_ddl,
		):
			day_patch.execute()
			mock_ddl.assert_not_called()

	def test_sweep_day_index_leaves_old_patch_log_untouched(self):
		"""Executing the new day index patch never mutates the old sweep indexes Patch Log."""
		old_patch_name = "pos_next.patches.v2_36_0.outlet_monitoring_sweep_indexes"
		old_log = frappe.db.get_value(
			"Patch Log",
			{"patch": old_patch_name},
			["name", "patch"],
			as_dict=True,
		)

		with (
			patch.object(frappe.db, "table_exists", return_value=True),
			patch.object(frappe.db, "sql", return_value=[(1,)]),
			patch.object(frappe.db, "sql_ddl"),
		):
			day_patch.execute()

		current_log = frappe.db.get_value(
			"Patch Log",
			{"patch": old_patch_name},
			["name", "patch"],
			as_dict=True,
		)
		self.assertEqual(old_log, current_log, "Old Patch Log entry must remain completely untouched")

	def test_ensure_site_indexes_invokes_all_patch_callables(self):
		"""ensure_site_indexes() invokes all four site index patch callables."""
		mock_item = MagicMock()
		mock_per = MagicMock()
		mock_sweep = MagicMock()
		mock_day = MagicMock()

		with (
			patch("pos_next.patches.v2_12_0.add_item_pos_search_index.execute", mock_item),
			patch("pos_next.patches.v2_12_0.add_payment_entry_reference_index.execute", mock_per),
			patch("pos_next.patches.v2_36_0.outlet_monitoring_sweep_indexes.execute", mock_sweep),
			patch("pos_next.patches.v2_36_0.outlet_monitoring_sweep_day_index.execute", mock_day),
		):
			ensure_site_indexes()

		mock_item.assert_called_once()
		mock_per.assert_called_once()
		mock_sweep.assert_called_once()
		mock_day.assert_called_once()

	def test_ensure_site_indexes_handles_patch_failure_gracefully(self):
		"""One failing DDL patch is logged and does not crash install/migrate."""
		with (
			patch(
				"pos_next.patches.v2_36_0.outlet_monitoring_sweep_day_index.execute",
				side_effect=Exception("Simulated DDL failure"),
			),
			patch("frappe.log_error") as mock_log,
		):
			ensure_site_indexes()
			mock_log.assert_called()

	def test_live_site_indexes_present_in_statistics(self):
		"""Read-only check that all three sweep indexes exist on tabPOS Monitoring Report."""
		for idx in ("om_sweep_due_idx", "om_sweep_shift_idx", "om_sweep_day_idx"):
			self.assertTrue(
				_index_exists("tabPOS Monitoring Report", idx),
				f"{idx} must exist on tabPOS Monitoring Report",
			)
