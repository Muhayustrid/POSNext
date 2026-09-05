# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for stripping server-managed fields from client payloads.

Mocked-frappe style — run via
pos_next/_pn_run_tests.py pos_next.api.test_invoices_strip_fields
"""

import unittest

from pos_next.api.invoices import _strip_server_managed_fields


class TestStripServerManagedFields(unittest.TestCase):
	def test_strips_packed_items_and_offer_stashes(self):
		payload = {
			"doctype": "Sales Invoice",
			"customer": "CUST-1",
			"packed_items": [{"doctype": "Packed Item", "item_code": "ITEM-1"}],
			"pos_applied_offer_rules": '["PR-A"]',
			"pos_applied_one_time_rules": '["PR-B"]',
		}
		cleaned = _strip_server_managed_fields(payload)
		self.assertNotIn("packed_items", cleaned)
		self.assertNotIn("pos_applied_offer_rules", cleaned)
		self.assertNotIn("pos_applied_one_time_rules", cleaned)
		self.assertEqual("CUST-1", cleaned["customer"])
		self.assertEqual("Sales Invoice", cleaned["doctype"])

	def test_leaves_unrelated_keys_untouched(self):
		payload = {
			"customer": "CUST-1",
			"is_pos": 1,
			"items": [{"item_code": "ITEM-1", "qty": 2}],
			"payments": [{"mode_of_payment": "Cash"}],
		}
		self.assertEqual(payload, _strip_server_managed_fields(payload))

	def test_does_not_mutate_original_payload(self):
		payload = {"pos_applied_offer_rules": '["PR-A"]', "packed_items": [{"item_code": "ITEM-1"}]}
		_strip_server_managed_fields(payload)
		self.assertIn("pos_applied_offer_rules", payload)
		self.assertIn("packed_items", payload)

	def test_non_dict_passthrough(self):
		self.assertIsNone(_strip_server_managed_fields(None))
		self.assertEqual("invoice", _strip_server_managed_fields("invoice"))
		items = [{"item_code": "ITEM-1"}]
		self.assertEqual(items, _strip_server_managed_fields(items))
