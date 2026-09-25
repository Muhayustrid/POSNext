# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-07: period_scope must reuse the shift list it already plucked.

The old scope embedded a ``posa_pos_opening_shift IN (SELECT os.name ...)``
subquery on ``tabPOS Opening Shift`` even though the shift names were fetched
one statement earlier — the subquery re-probed the table inside every union
branch of every recap query. The scope filter must read the fetched list by
parameter instead.

Run via pos_next/_pn_run_tests.py pos_next.services.test_perf_be1_recap
"""

import unittest
from unittest import mock

import frappe

from pos_next.services.sales_recap import period_scope

PROFILE = "_PERF07 Profile"


class TestPeriodScope(unittest.TestCase):
	def test_scope_reuses_one_fetch_for_both_shift_lists(self):
		rows = [
			frappe._dict(name="_PERF07-OS-1", posting_date="2026-09-05"),
			frappe._dict(name="_PERF07-OS-2", posting_date="2026-09-20"),
		]
		with mock.patch("frappe.get_all", return_value=rows) as get_all:
			scope = period_scope(PROFILE, "2026-09-10", "2026-09-30")

		# one fetch feeds both lists
		get_all.assert_called_once()

		# drawer/count scope: only shifts opened inside the window
		self.assertEqual(scope.shifts, ["_PERF07-OS-2"])

		# the invoice filter reads every submitted shift of the profile by
		# parameter — a backdated invoice on a shift opened outside the window
		# still posts inside it (the old subquery's universe), no subquery on
		# tabPOS Opening Shift left in the fragment
		self.assertNotIn("tabPOS Opening Shift", scope.where)
		self.assertIn("si.posa_pos_opening_shift IN %(invoice_shifts)s", scope.where)
		self.assertEqual(
			scope.values.get("invoice_shifts"), ["_PERF07-OS-1", "_PERF07-OS-2"]
		)

	def test_scope_without_shifts_matches_nothing(self):
		with mock.patch("frappe.get_all", return_value=[]):
			scope = period_scope(PROFILE, "2026-09-01", "2026-09-30")

		self.assertEqual(scope.shifts, [])
		# an empty list would render `IN ()` (SQL syntax error); the scope
		# must exclude every invoice instead, like the empty subquery did
		self.assertNotIn("IN %(invoice_shifts)s", scope.where)
		self.assertNotIn("tabPOS Opening Shift", scope.where)
