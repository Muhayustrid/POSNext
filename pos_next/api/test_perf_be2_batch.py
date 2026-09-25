# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-14 acceptance tests: get_batch_serial_data_for_items must answer with a
single batched sweep plus one Batch metadata fetch, never
get_batch_qty(warehouse=..., item_code=...) + get_cached_doc("Batch") per item.

Run inside the container (serial only):

    ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_perf_be2_batch
"""

import unittest
from unittest import mock

import frappe
from frappe.utils import flt

import pos_next.api.items as items_mod
from pos_next.api.items import get_batch_serial_data_for_items

COMPANY = "_Test Company"
ITEM_A = "_PNXT_PERF14_BATCH_A"
ITEM_B = "_PNXT_PERF14_BATCH_B"
BATCH_A_ID = "PNXT-PERF14-BATCH-A"
BATCH_B_ID = "PNXT-PERF14-BATCH-B"
QTY_A = 4
QTY_B = 2


def _warehouse():
	return frappe.db.get_value("Warehouse", {"company": COMPANY, "is_group": 0}, "name")


def _ensure_batch_item(item_code):
	if frappe.db.exists("Item", item_code):
		return
	item = frappe.get_doc(
		{
			"doctype": "Item",
			"item_code": item_code,
			"item_name": item_code,
			"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
			"stock_uom": "Nos",
			"is_stock_item": 1,
			"is_sales_item": 1,
			"has_batch_no": 1,
		}
	).insert(ignore_permissions=True)
	if item.name != item_code:
		frappe.rename_doc("Item", item.name, item_code, force=True)


def _ensure_batch(batch_id, item_code):
	if frappe.db.exists("Batch", {"batch_id": batch_id}):
		return frappe.db.get_value("Batch", {"batch_id": batch_id}, "name")
	return frappe.get_doc(
		{"doctype": "Batch", "batch_id": batch_id, "item": item_code}
	).insert(ignore_permissions=True).name


def _receipt(item_code, batch_name, qty):
	from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry

	make_stock_entry(
		item_code=item_code,
		qty=qty,
		to_warehouse=_warehouse(),
		batch_no=batch_name,
		rate=10,
		do_not_submit=False,
	)


def _ensure_fixtures():
	_ensure_batch_item(ITEM_A)
	_ensure_batch_item(ITEM_B)
	batch_a = _ensure_batch(BATCH_A_ID, ITEM_A)
	batch_b = _ensure_batch(BATCH_B_ID, ITEM_B)
	frappe.db.commit()

	# Stock receipts are the expensive part; only run them when the batch is
	# still empty at the test warehouse.
	if not flt(frappe.db.get_value("Batch", batch_a, "batch_qty")):
		_receipt(ITEM_A, batch_a, QTY_A)
	if not flt(frappe.db.get_value("Batch", batch_b, "batch_qty")):
		_receipt(ITEM_B, batch_b, QTY_B)
	return batch_a, batch_b


class TestBatchDataForItemsIsBulk(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.batch_a, cls.batch_b = _ensure_fixtures()
		frappe.db.commit()

	def test_batch_qty_and_metadata_are_correct(self):
		result = get_batch_serial_data_for_items([ITEM_A, ITEM_B], _warehouse())

		self.assertIn(ITEM_A, result)
		self.assertIn(ITEM_B, result)
		rows_a = {row["batch_no"]: row for row in result[ITEM_A]["batch_no_data"]}
		rows_b = {row["batch_no"]: row for row in result[ITEM_B]["batch_no_data"]}

		self.assertIn(self.batch_a, rows_a)
		self.assertEqual(flt(rows_a[self.batch_a]["batch_qty"]), float(QTY_A))
		self.assertIsNone(rows_a[self.batch_a]["expiry_date"])
		# contract mirrors the endpoint: str(doc value) or None
		mfg = frappe.db.get_value("Batch", self.batch_a, "manufacturing_date")
		self.assertEqual(rows_a[self.batch_a]["manufacturing_date"], str(mfg) if mfg else None)

		self.assertIn(self.batch_b, rows_b)
		self.assertEqual(flt(rows_b[self.batch_b]["batch_qty"]), float(QTY_B))

		self.assertEqual(result[ITEM_A]["serial_no_data"], [])
		self.assertEqual(result[ITEM_B]["serial_no_data"], [])

	def test_no_per_item_batch_queries(self):
		calls = []
		original_get_batch_qty = items_mod.get_batch_qty
		original_get_cached_doc = frappe.get_cached_doc

		def counting_get_batch_qty(*args, **kwargs):
			calls.append("get_batch_qty")
			return original_get_batch_qty(*args, **kwargs)

		def counting_get_cached_doc(doctype, name, *args, **kwargs):
			if doctype == "Batch":
				calls.append("get_cached_doc:Batch")
			return original_get_cached_doc(doctype, name, *args, **kwargs)

		with (
			mock.patch.object(items_mod, "get_batch_qty", side_effect=counting_get_batch_qty),
			mock.patch("frappe.get_cached_doc", side_effect=counting_get_cached_doc),
		):
			result = get_batch_serial_data_for_items([ITEM_A, ITEM_B], _warehouse())

		self.assertEqual(calls, [])
		rows_a = {row["batch_no"]: row for row in result[ITEM_A]["batch_no_data"]}
		rows_b = {row["batch_no"]: row for row in result[ITEM_B]["batch_no_data"]}
		self.assertIn(self.batch_a, rows_a)
		self.assertIn(self.batch_b, rows_b)
