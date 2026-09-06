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
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pos_next.api.invoices import (
	_derive_item_offer_rules,
	_parse_relayed_offer_rules,
	_reapply_item_offer_attribution,
	_strip_server_managed_fields,
)
from pos_next.overrides.discount_code import invoice_has_manual_discount, validate_invoice_discounts

GATE_DB_PATCH = patch("pos_next.overrides.discount_code.frappe.db", new_callable=MagicMock)
GATE_GET_ALL_PATCH = patch(
	"pos_next.overrides.discount_code.frappe.get_all", new_callable=MagicMock, create=True
)


class FakeRow(dict):
	"""Mimics a frappe child Document: attribute writes are visible to .get()."""

	def __setattr__(self, key, value):
		self[key] = value


class FakeDoc(dict):
	"""Dict-based doc double: supports .get like a Document."""


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


class TestDeriveItemOfferRules(unittest.TestCase):
	def test_json_list_string(self):
		self.assertEqual(_derive_item_offer_rules('["PR-B", "PR-A"]'), ["PR-B", "PR-A"])

	def test_comma_separated_string(self):
		# standardized payloads join without spaces (standardize_pricing_rules)
		self.assertEqual(_derive_item_offer_rules("PR-A,PR-B"), ["PR-A", "PR-B"])

	def test_empty_values(self):
		self.assertEqual(_derive_item_offer_rules(""), [])
		self.assertEqual(_derive_item_offer_rules(None), [])


class TestSubmitLegAttribution(unittest.TestCase):
	"""Online submit leg (existing-draft branch of submit_invoice).

	Document.update() re-appends every child row from its (stripped) payload
	dict, so the server-derived pos_offer_item_rules must be snapshotted from
	the DB rows before the rebuild and restored/derived after it — otherwise
	validate_invoice_discounts gates a codeless offer cart.
	"""

	@staticmethod
	def _rebuild_rows(cleaned_items):
		"""Mimic BaseDocument.update: fresh rows solely from payload fields."""
		return [
			FakeRow(
				name=item.get("name"),
				pricing_rules=item.get("pricing_rules", ""),
				discount_percentage=item.get("discount_percentage", 0),
			)
			for item in cleaned_items
		]

	def test_online_submit_keeps_db_attribution_and_gate_passes(self):
		# DB draft rows as the preceding update_invoice save wrote them.
		db_attribution = {"row-1": '["PR-ITEM-1"]'}
		# Client echoes the draft doc (pricing_rules already cleared); the
		# strip removes pos_offer_item_rules from the payload items.
		payload = {
			"is_pos": 1,
			"company": "Company A",
			"items": [
				{
					"name": "row-1",
					"pricing_rules": "",
					"discount_percentage": 10,
					"pos_offer_item_rules": '["PR-ITEM-1"]',
				}
			],
		}
		cleaned = _strip_server_managed_fields(payload)
		self.assertNotIn("pos_offer_item_rules", cleaned["items"][0])

		rebuilt = self._rebuild_rows(cleaned["items"])
		doc = FakeDoc(is_pos=1, company="Company A", items=rebuilt)
		_reapply_item_offer_attribution(doc, cleaned.get("items"), db_attribution)

		self.assertEqual(rebuilt[0].get("pos_offer_item_rules"), '["PR-ITEM-1"]')

		with GATE_DB_PATCH as mock_db, GATE_GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [SimpleNamespace(name="PR-ITEM-1", apply_on="Item Code")]
			mock_db.get_value.return_value = None

			validate_invoice_discounts(doc, "validate")  # must not raise

			self.assertFalse(invoice_has_manual_discount(doc))

	def test_new_row_is_derived_from_pricing_rules(self):
		payload = {
			"is_pos": 1,
			"items": [{"pricing_rules": '["PR-ITEM-9"]', "discount_percentage": 5}],
		}
		cleaned = _strip_server_managed_fields(payload)

		rebuilt = self._rebuild_rows(cleaned["items"])
		doc = FakeDoc(is_pos=1, items=rebuilt)
		_reapply_item_offer_attribution(doc, cleaned.get("items"), {})

		self.assertEqual(rebuilt[0].get("pos_offer_item_rules"), '["PR-ITEM-9"]')

		with GATE_DB_PATCH as mock_db, GATE_GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [SimpleNamespace(name="PR-ITEM-9", apply_on="Item Code")]
			self.assertFalse(invoice_has_manual_discount(doc))

	def test_forged_payload_attribution_does_not_survive(self):
		payload = {
			"is_pos": 1,
			"company": "Company A",
			"items": [
				{
					"name": "row-1",
					"pricing_rules": "",
					"discount_percentage": 10,
					"pos_offer_item_rules": '["FORGED-1"]',
				}
			],
		}
		cleaned = _strip_server_managed_fields(payload)
		self.assertNotIn("pos_offer_item_rules", cleaned["items"][0])

		rebuilt = self._rebuild_rows(cleaned["items"])
		doc = FakeDoc(is_pos=1, company="Company A", items=rebuilt)
		# No DB snapshot for the row and no pricing_rules to derive from: the
		# forged value must not land and the gate must throw.
		_reapply_item_offer_attribution(doc, cleaned.get("items"), {})

		self.assertEqual(rebuilt[0].get("pos_offer_item_rules"), "")

		with GATE_DB_PATCH as mock_db, GATE_GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = []
			mock_db.get_value.return_value = None

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

	def test_manual_edit_without_attribution_still_gated(self):
		payload = {
			"is_pos": 1,
			"company": "Company A",
			"items": [{"name": "row-1", "pricing_rules": "", "discount_percentage": 10}],
		}
		cleaned = _strip_server_managed_fields(payload)

		rebuilt = self._rebuild_rows(cleaned["items"])
		doc = FakeDoc(is_pos=1, company="Company A", items=rebuilt)
		_reapply_item_offer_attribution(doc, cleaned.get("items"), {"row-1": ""})

		self.assertEqual(rebuilt[0].get("pos_offer_item_rules"), "")

		with GATE_DB_PATCH as mock_db, GATE_GET_ALL_PATCH as mock_get_all:
			mock_get_all.assert_not_called()
			mock_db.get_value.return_value = None

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")


if __name__ == "__main__":
	unittest.main()
