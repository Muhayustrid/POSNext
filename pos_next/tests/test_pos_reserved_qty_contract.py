# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Contract of the patched get_pos_reserved_qty_from_table against real SQL.

ERPNext version-16 (2026-11, 44c675875d) calls it as
(child_table, qty_column, item_codes, warehouses) and aggregates the returned
rows (item_code, warehouse, qty); older v16 calls (child_table, item_code,
warehouse) for a scalar. Rows are synthetic, written in this test's
transaction and rolled back. Run via
pos_next/_pn_run_tests.py pos_next.tests.test_pos_reserved_qty_contract
"""

import types
import uuid
from collections import defaultdict
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from pos_next.overrides.pos_reserved_qty import patch_pos_reserved_qty

TABLES = (("POS Invoice Item", "stock_qty"), ("Packed Item", "qty"))


def _native(child_table, *args):
	"""Stand-in for ERPNext's original: proves the fallback delegates."""
	return ("native", child_table, args)


def _patched_module():
	module = types.SimpleNamespace(get_pos_reserved_qty_from_table=_native)
	patch_pos_reserved_qty(module)
	return module


def _reserved_map(fn, item_codes, warehouses):
	# mirrors erpnext get_pos_reserved_qty_map (version-16, 2026-11)
	reserved = defaultdict(float)
	for child_table, qty_column in TABLES:
		for row in fn(child_table, qty_column, item_codes, warehouses):
			reserved[(row.item_code, row.warehouse)] += flt(row.qty)
	return reserved


class TestPOSReservedQtyContract(FrappeTestCase):
	def setUp(self):
		tag = uuid.uuid4().hex[:8]
		self.i1, self.i2 = f"_RQ-I1-{tag}", f"_RQ-I2-{tag}"
		self.w1, self.w2 = f"_RQ-W1-{tag}", f"_RQ-W2-{tag}"
		self.tag = tag
		self.fn = _patched_module().get_pos_reserved_qty_from_table

	def tearDown(self):
		frappe.db.rollback()

	def _invoice(self, rows, docstatus=1, consolidated="", shift=""):
		"""rows: (child_table, item_code, warehouse, qty)."""
		name = f"_RQ-PINV-{self.tag}-{uuid.uuid4().hex[:6]}"
		frappe.db.sql(
			"""insert into `tabPOS Invoice` (name, docstatus, consolidated_invoice, posa_pos_opening_shift)
			values (%s, %s, %s, %s)""",
			(name, docstatus, consolidated or None, shift or None),
		)
		for child_table, item_code, warehouse, qty in rows:
			qty_column = dict(TABLES)[child_table]
			frappe.db.sql(
				f"""insert into `tab{child_table}` (name, parent, parenttype, parentfield, docstatus,
				item_code, warehouse, `{qty_column}`) values (%s, %s, 'POS Invoice', 'items', %s, %s, %s, %s)""",
				(uuid.uuid4().hex, name, docstatus, item_code, warehouse, qty),
			)

	def test_four_arg_call_returns_grouped_rows_and_excludes_pos_next(self):
		self._invoice([("POS Invoice Item", self.i1, self.w1, 2), ("Packed Item", self.i1, self.w1, 3)])
		self._invoice([("POS Invoice Item", self.i1, self.w1, 1), ("POS Invoice Item", self.i2, self.w2, 4)])
		self._invoice([("Packed Item", self.i2, self.w1, 5)])
		# excluded: POS Next (own SLE), consolidated, draft, cancelled
		self._invoice(
			[("POS Invoice Item", self.i1, self.w1, 100), ("Packed Item", self.i1, self.w1, 100)],
			shift="SHIFT-X",
		)
		self._invoice([("POS Invoice Item", self.i1, self.w1, 100)], consolidated="SINV-X")
		self._invoice([("POS Invoice Item", self.i1, self.w1, 100)], docstatus=0)
		self._invoice([("Packed Item", self.i2, self.w2, 100)], docstatus=2)

		rows = self.fn("POS Invoice Item", "stock_qty", {self.i1, self.i2}, {self.w1, self.w2})
		self.assertEqual(
			sorted((r.item_code, r.warehouse, flt(r.qty)) for r in rows),
			[(self.i1, self.w1, 3.0), (self.i2, self.w2, 4.0)],
		)
		reserved = _reserved_map(self.fn, {self.i1, self.i2}, {self.w1, self.w2})
		self.assertEqual(
			dict(reserved), {(self.i1, self.w1): 6.0, (self.i2, self.w2): 4.0, (self.i2, self.w1): 5.0}
		)
		self.assertEqual(reserved[(self.i1, self.w2)], 0.0)

		# requested scope is respected: no cross-contamination from other keys
		self.assertEqual(dict(_reserved_map(self.fn, {self.i1}, {self.w2})), {})

	def test_legacy_three_arg_call_returns_scalar(self):
		self._invoice([("POS Invoice Item", self.i1, self.w1, 2), ("Packed Item", self.i1, self.w1, 3)])
		self._invoice([("POS Invoice Item", self.i1, self.w1, 100)], shift="SHIFT-X")
		self.assertEqual(self.fn("POS Invoice Item", self.i1, self.w1), 2.0)
		self.assertEqual(self.fn("Packed Item", self.i1, self.w1), 3.0)
		self.assertEqual(self.fn("Packed Item", self.i2, self.w1), 0)

	def test_no_reservations(self):
		self.assertEqual(self.fn("POS Invoice Item", "stock_qty", {self.i1}, {self.w1}), [])
		self.assertEqual(dict(_reserved_map(self.fn, {self.i1}, {self.w1})), {})

	def test_missing_custom_field_delegates_to_native(self):
		meta = mock.Mock()
		meta.has_field.return_value = False
		with mock.patch("frappe.get_meta", return_value=meta):
			self.assertEqual(
				self.fn("Packed Item", "qty", {self.i1}, {self.w1}),
				("native", "Packed Item", ("qty", {self.i1}, {self.w1})),
			)
			self.assertEqual(
				self.fn("Packed Item", self.i1, self.w1), ("native", "Packed Item", (self.i1, self.w1))
			)

	def test_patch_twice_is_noop(self):
		module = _patched_module()
		first = module.get_pos_reserved_qty_from_table
		patch_pos_reserved_qty(module)
		self.assertIs(module.get_pos_reserved_qty_from_table, first)
