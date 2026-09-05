# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the client-relayed applied-rule list (transaction offers).

Mocked-frappe style — no database needed; run via
pos_next/_pn_run_tests.py pos_next.api.test_invoices_offer_relay

Transaction-scope pricing rules never ride item rows, so their names reach
update_invoice only via the invoice payload's `pos_relayed_offer_rules`
(the client's copy of the apply_offers response's applied_pricing_rules).
update_invoice merges them into the invoice-level pos_applied_offer_rules
stash; every consumer re-verifies the names before granting anything.
"""

import unittest

from pos_next.api.invoices import _parse_relayed_offer_rules, _strip_server_managed_fields


class TestParseRelayedOfferRules(unittest.TestCase):
	def test_parses_json_list_string(self):
		self.assertEqual(_parse_relayed_offer_rules('["PR-TRANS-1", "PR-ITEM-2"]'), ["PR-ITEM-2", "PR-TRANS-1"])

	def test_accepts_raw_list(self):
		self.assertEqual(_parse_relayed_offer_rules(["PR-B", "PR-A"]), ["PR-A", "PR-B"])

	def test_deduplicates_and_sorts(self):
		self.assertEqual(_parse_relayed_offer_rules(["PR-B", "PR-A", "PR-B", ""]), ["PR-A", "PR-B"])

	def test_garbage_string_yields_empty(self):
		self.assertEqual(_parse_relayed_offer_rules("not json"), [])
		self.assertEqual(_parse_relayed_offer_rules('{"a": 1}'), [])

	def test_non_list_yields_empty(self):
		self.assertEqual(_parse_relayed_offer_rules({"PR-A": True}), [])
		self.assertEqual(_parse_relayed_offer_rules(5), [])

	def test_empty_yields_empty(self):
		self.assertEqual(_parse_relayed_offer_rules(None), [])
		self.assertEqual(_parse_relayed_offer_rules(""), [])
		self.assertEqual(_parse_relayed_offer_rules([]), [])


class TestRelayKeyIsStripped(unittest.TestCase):
	def test_relayed_rules_never_reach_the_document(self):
		payload = {
			"doctype": "Sales Invoice",
			"customer": "CUST-1",
			"pos_relayed_offer_rules": ["PR-TRANS-1"],
			"pos_applied_offer_rules": '["PR-A"]',
		}
		cleaned = _strip_server_managed_fields(payload)
		self.assertNotIn("pos_relayed_offer_rules", cleaned)
		self.assertNotIn("pos_applied_offer_rules", cleaned)
		self.assertEqual("CUST-1", cleaned["customer"])

	def test_stripping_does_not_mutate_original_payload(self):
		payload = {"pos_relayed_offer_rules": ["PR-TRANS-1"]}
		_strip_server_managed_fields(payload)
		self.assertIn("pos_relayed_offer_rules", payload)


if __name__ == "__main__":
	unittest.main()
