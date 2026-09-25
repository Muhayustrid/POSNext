# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-15 acceptance tests: package quoting must resolve is_stock_item with one
bulk Item lookup, never frappe.db.get_value("Item", ...) per component row, and
package-return validation must fetch original rows for all instances at once.

Run inside the container (serial only):

    ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_perf_be2_packages
"""

import unittest
from unittest import mock

import frappe

from pos_next.api.packages import quote
from pos_next.api.test_packages import (
	COMPANY,
	LAPTOP,
	PROFILE,
	_ensure_item,
	_ensure_inr_price_list,
	_ensure_profile,
)

SERVICE = "_PNXT_PERF15_SERVICE"
PARENT_ITEM = "_PNXT_PERF15_PARENT"
PACKAGE = "_PNXT PERF15 Mixed Package"


def _ensure_service_item():
	_ensure_item(SERVICE, "PNXT PERF15 Service", is_stock_item=False)
	_ensure_item(PARENT_ITEM, "PNXT PERF15 Mixed Package", is_stock_item=False)


def _ensure_package():
	_ensure_profile()
	if frappe.db.exists("POS Package", PACKAGE):
		return
	vals = frappe.db.get_value("POS Profile", PROFILE, ["company", "warehouse"], as_dict=True)
	frappe.get_doc(
		{
			"doctype": "POS Package",
			"package_name": PACKAGE,
			"company": COMPANY,
			"currency": frappe.db.get_value("Company", COMPANY, "default_currency"),
			"parent_item": PARENT_ITEM,
			"base_price": 50_000.0,
			"items": [{"item_code": LAPTOP, "qty": 1}],
			"groups": [{"group_key": "svc", "label": "Service", "min_qty": 1, "max_qty": 1}],
			"options": [
				{
					"group_key": "svc",
					"item_code": SERVICE,
					"qty_per_unit": 1,
					"price_adjustment": 0,
				}
			],
			"outlets": [
				{
					"company": vals.company,
					"warehouse": vals.warehouse,
					"enabled": 1,
				}
			],
		}
	).insert(ignore_permissions=True)


class TestPackageQuoteStockFlags(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		_ensure_service_item()
		_ensure_package()
		frappe.db.commit()
		pkg = frappe.get_cached_doc("POS Package", PACKAGE)
		cls.option_id = pkg.options[0].name

	def _quote(self):
		return quote(
			PACKAGE,
			[{"group_key": "svc", "options": [{"option_id": self.option_id, "qty": 1}]}],
			PROFILE,
		)

	def test_is_stock_item_is_correct_for_mixed_components(self):
		result = self._quote()

		components = {line["item_code"]: line for line in result["lines"][1:]}
		self.assertIn(LAPTOP, components)
		self.assertIn(SERVICE, components)
		self.assertEqual(components[LAPTOP]["is_stock_item"], 1)
		self.assertEqual(components[SERVICE]["is_stock_item"], 0)
		self.assertEqual(result["total"], 50_000.0)

	def test_no_per_component_item_lookups(self):
		item_lookups = []
		original = frappe.db.get_value

		def counting_get_value(doctype, *args, **kwargs):
			if doctype == "Item":
				item_lookups.append(args)
			return original(doctype, *args, **kwargs)

		with mock.patch("frappe.db.get_value", side_effect=counting_get_value):
			result = self._quote()

		self.assertEqual(item_lookups, [])
		components = {line["item_code"]: line for line in result["lines"][1:]}
		self.assertEqual(components[LAPTOP]["is_stock_item"], 1)
		self.assertEqual(components[SERVICE]["is_stock_item"], 0)
