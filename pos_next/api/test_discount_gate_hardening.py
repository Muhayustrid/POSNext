# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Task 4 hardening tests: SEC-04 (server-side manual-discount detection and
the max-discount cap) and SEC-10 (Pricing Rule claims verified for relevance
and discount reconciliation, relay claims filtered server-side).

Mocked-frappe style — no database needed; run via
pos_next/_pn_run_tests.py pos_next.api.test_discount_gate_hardening
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from pos_next.api.invoices import (
	_resolve_server_price_list_rate,
	_server_price_list_rates,
	calculate_price_list_rate,
	validate_manual_rate_edit,
)
from pos_next.overrides.discount_code import (
	_expected_rule_pct,
	_item_has_discount,
	_item_offer_matches,
	_rule_applies_to_item,
	_rule_in_doc_scope,
	_row_discount_pct,
	invoice_has_manual_discount,
	validate_invoice_discounts,
	verify_transaction_rule_names,
)


def _rule(**overrides):
	"""Pricing Rule row as the gate's verification query returns it."""
	row = {
		"name": "PR-1",
		"apply_on": "Item Code",
		"company": None,
		"valid_from": None,
		"valid_upto": None,
		"applicable_for": None,
		"customer": None,
		"customer_group": None,
		"territory": None,
		"price_or_product_discount": "Price",
		"rate_or_discount": "Discount Percentage",
		"rate": 0,
		"discount_percentage": 0,
		"discount_amount": 0,
		"min_qty": 0,
		"max_qty": 0,
		"min_amt": 0,
		"max_amt": 0,
		"apply_discount_on_price": None,
		"min_or_max_discount_qty_limit": 0,
		"pos_offer_max_discount": 0,
	}
	row.update(overrides)
	return frappe._dict(row)


def _settings(allow_edit=1, max_discount=20):
	return {"allow_user_to_edit_rate": allow_edit, "max_discount_allowed": max_discount}


class TestServerPriceListRate(unittest.TestCase):
	"""SEC-04: the server resolves its own list price per cart row."""

	def test_row_uom_price_wins(self):
		price_map = {"IT1": {"Box": 120000, "": 10000}}
		item = {"item_code": "IT1", "uom": "Box", "conversion_factor": 12}
		self.assertEqual(120000, _resolve_server_price_list_rate(price_map, item))

	def test_uomless_price_scales_by_conversion_factor(self):
		price_map = {"IT1": {"": 10000}}
		item = {"item_code": "IT1", "uom": "Box", "conversion_factor": 12}
		self.assertEqual(120000, _resolve_server_price_list_rate(price_map, item))

	def test_missing_price_yields_zero(self):
		self.assertEqual(0, _resolve_server_price_list_rate({}, {"item_code": "IT1"}))
		self.assertEqual(0, _resolve_server_price_list_rate({"IT1": {"Box": 5}}, {"item_code": "IT1"}))

	def test_map_is_built_for_all_row_items(self):
		items = [{"item_code": "IT1"}, {"item_code": "IT2"}, {"item_code": None}]
		with patch("pos_next.api.invoices._fetch_uom_prices_map") as fetch:
			fetch.return_value = {"IT1": {"": 100}}
			result = _server_price_list_rates(items, "Selling List", "2026-09-22")
		fetch.assert_called_once_with(["IT1", "IT2"], "Selling List", "2026-09-22")
		self.assertEqual({"IT1": {"": 100}}, result)

	def test_no_price_list_no_lookup(self):
		with patch("pos_next.api.invoices._fetch_uom_prices_map") as fetch:
			self.assertEqual({}, _server_price_list_rates([{"item_code": "IT1"}], None))
		fetch.assert_not_called()


class TestValidateManualRateEdit(unittest.TestCase):
	"""SEC-04: the client flag can no longer switch the cap off — validation is
	caller-decided and always runs against the server-resolved basis."""

	def test_honest_edit_within_cap_passes_without_flag(self):
		item = {"item_code": "IT1", "rate": 90, "price_list_rate": 100, "is_rate_manually_edited": 0}
		self.assertTrue(validate_manual_rate_edit(item, "Profile 1", _settings())["valid"])

	def test_flagged_edit_still_passes(self):
		item = {"item_code": "IT1", "rate": 90, "price_list_rate": 100, "is_rate_manually_edited": 1}
		self.assertTrue(validate_manual_rate_edit(item, "Profile 1", _settings())["valid"])

	def test_unflagged_big_cut_is_capped(self):
		# The cheat: rate slashed far below list price, no flag sent.
		item = {"item_code": "IT1", "rate": 10, "price_list_rate": 100, "is_rate_manually_edited": 0}
		result = validate_manual_rate_edit(item, "Profile 1", _settings())
		self.assertFalse(result["valid"])
		self.assertIn("exceeds the maximum allowed discount of 20.0%", result["message"])

	def test_server_stamped_basis_wins_over_forged_original(self):
		# update_invoice stamps original_rate from the server's list price on
		# server-detected rows, so a forged client original cannot shrink the
		# measured cut (flag + original_rate=10 + rate=10 still reads 90%).
		item = {
			"item_code": "IT1",
			"rate": 10,
			"original_rate": 100,
			"price_list_rate": 100,
			"is_rate_manually_edited": 1,
		}
		result = validate_manual_rate_edit(item, "Profile 1", _settings())
		self.assertFalse(result["valid"])

	def test_editing_disallowed_blocks(self):
		item = {"item_code": "IT1", "rate": 90, "price_list_rate": 100}
		self.assertFalse(validate_manual_rate_edit(item, "Profile 1", _settings(allow_edit=0))["valid"])

	def test_zero_rate_blocks(self):
		item = {"item_code": "IT1", "rate": 0, "price_list_rate": 100}
		self.assertFalse(validate_manual_rate_edit(item, "Profile 1", _settings())["valid"])

	def test_no_cap_configured_allows_any_edit(self):
		item = {"item_code": "IT1", "rate": 5, "price_list_rate": 100}
		self.assertTrue(validate_manual_rate_edit(item, "Profile 1", _settings(max_discount=0))["valid"])


class TestItemDiscountDetection(unittest.TestCase):
	"""SEC-04: the gate reads the price gap, not the client flag."""

	def test_rate_below_price_list_counts_without_flag(self):
		item = {"rate": 9000, "price_list_rate": 10000}
		self.assertTrue(_item_has_discount(item))

	def test_rate_at_price_list_does_not_count(self):
		item = {"rate": 10000, "price_list_rate": 10000}
		self.assertFalse(_item_has_discount(item))

	def test_free_row_is_not_a_discount(self):
		item = {"rate": 0, "price_list_rate": 0, "is_free_item": 1}
		self.assertFalse(_item_has_discount(item))

	def test_row_without_price_is_not_a_discount(self):
		item = {"rate": 5000}
		self.assertFalse(_item_has_discount(item))

	def test_percentage_discount_counts(self):
		self.assertTrue(_item_has_discount({"discount_percentage": 10}))

	def test_calculate_price_list_rate_reverse_engineers_the_base(self):
		self.assertEqual(100, calculate_price_list_rate(90, 10, 0))


class TestRuleScope(unittest.TestCase):
	"""SEC-10 (a): a claimed rule must be in scope for THIS document."""

	doc = {"company": "Company A", "customer": "CUST-1", "posting_date": "2026-09-22"}

	def test_company_mismatch_is_out_of_scope(self):
		self.assertFalse(_rule_in_doc_scope(_rule(company="Company B"), self.doc))

	def test_blank_company_is_in_scope(self):
		self.assertTrue(_rule_in_doc_scope(_rule(company=None), self.doc))

	def test_matching_company_is_in_scope(self):
		self.assertTrue(_rule_in_doc_scope(_rule(company="Company A"), self.doc))

	def test_expired_rule_is_out_of_scope(self):
		self.assertFalse(_rule_in_doc_scope(_rule(valid_upto="2000-01-01"), self.doc))

	def test_not_yet_valid_rule_is_out_of_scope(self):
		self.assertFalse(_rule_in_doc_scope(_rule(valid_from="2999-01-01"), self.doc))

	def test_validity_window_open_is_in_scope(self):
		rule = _rule(valid_from="2000-01-01", valid_upto="2999-12-31")
		self.assertTrue(_rule_in_doc_scope(rule, self.doc))

	def test_other_customer_is_out_of_scope(self):
		rule = _rule(applicable_for="Customer", customer="CUST-OTHER")
		self.assertFalse(_rule_in_doc_scope(rule, self.doc))

	def test_claimed_customer_matching_is_in_scope(self):
		rule = _rule(applicable_for="Customer", customer="CUST-1")
		self.assertTrue(_rule_in_doc_scope(rule, self.doc))

	def test_missing_posting_date_falls_back_to_today(self):
		self.assertTrue(_rule_in_doc_scope(_rule(), {"company": "Company A"}))


class TestRuleItemApplicability(unittest.TestCase):
	"""SEC-10 (a): the rule must apply to the row it is claimed on."""

	scope_map = {"PR-1": {"IT1"}}

	def test_item_code_scope(self):
		rule = _rule(apply_on="Item Code")
		self.assertTrue(_rule_applies_to_item(rule, {"item_code": "IT1"}, self.scope_map))
		self.assertFalse(_rule_applies_to_item(rule, {"item_code": "IT2"}, self.scope_map))

	def test_transaction_rule_never_attributes_an_item(self):
		rule = _rule(apply_on="Transaction")
		self.assertFalse(_rule_applies_to_item(rule, {"item_code": "IT1"}, {}))

	def test_qty_window(self):
		rule = _rule(min_qty=2)
		self.assertFalse(_rule_applies_to_item(rule, {"item_code": "IT1", "qty": 1}, self.scope_map))
		self.assertTrue(_rule_applies_to_item(rule, {"item_code": "IT1", "qty": 3}, self.scope_map))

	def test_amount_window(self):
		rule = _rule(min_amt=500)
		self.assertFalse(
			_rule_applies_to_item(rule, {"item_code": "IT1", "qty": 1, "rate": 100}, self.scope_map)
		)
		self.assertTrue(
			_rule_applies_to_item(rule, {"item_code": "IT1", "qty": 6, "rate": 100}, self.scope_map)
		)


class TestDiscountReconciliation(unittest.TestCase):
	"""SEC-10 (b): the rule's configured discount must match the row's."""

	item = {"item_code": "IT1", "price_list_rate": 100, "rate": 90, "qty": 1}

	def test_row_discount_pct_reads_the_money_gap(self):
		self.assertAlmostEqual(10.0, _row_discount_pct(self.item))
		self.assertIsNone(_row_discount_pct({"rate": 90}))
		self.assertIsNone(_row_discount_pct({"price_list_rate": 0, "rate": 0}))

	def test_percentage_rule_reconciles(self):
		self.assertAlmostEqual(10.0, _expected_rule_pct(_rule(discount_percentage=10), self.item))

	def test_percentage_rule_mismatch(self):
		self.assertAlmostEqual(50.0, _expected_rule_pct(_rule(discount_percentage=50), self.item))

	def test_capped_offer_percentage(self):
		# 50% rule with a 20/unit cap on a 100 list price → 20% effective.
		rule = _rule(discount_percentage=50, pos_offer_max_discount=20)
		self.assertAlmostEqual(20.0, _expected_rule_pct(rule, self.item))

	def test_amount_rule_reconciles_as_pct(self):
		self.assertAlmostEqual(10.0, _expected_rule_pct(_rule(rate_or_discount="Discount Amount", discount_amount=10), self.item))

	def test_rate_rule_reconciles_as_pct(self):
		rule = _rule(rate_or_discount="Rate", rate=80)
		self.assertAlmostEqual(20.0, _expected_rule_pct(rule, self.item))

	def test_min_max_blend_across_line_qty(self):
		# Cheapest-item rule: 50% on up to 2 units of a 4-unit line → 25% blend.
		rule = _rule(
			discount_percentage=50,
			apply_discount_on_price="Min",
			min_or_max_discount_qty_limit=2,
		)
		self.assertAlmostEqual(25.0, _expected_rule_pct(rule, {**self.item, "qty": 4}))

	def test_unknown_rate_or_discount_reconciles_to_nothing(self):
		self.assertIsNone(_expected_rule_pct(_rule(rate_or_discount="Something Else"), self.item))

	def test_matching_rule_exempts_row(self):
		rules = {"PR-1": _rule(discount_percentage=10)}
		scope_map = {"PR-1": {"IT1"}}
		item = {**self.item, "pos_offer_item_rules": '["PR-1"]'}
		self.assertTrue(_item_offer_matches(item, rules, scope_map))

	def test_mismatched_rule_does_not_exempt_row(self):
		rules = {"PR-1": _rule(discount_percentage=5)}
		scope_map = {"PR-1": {"IT1"}}
		item = {**self.item, "pos_offer_item_rules": '["PR-1"]'}
		self.assertFalse(_item_offer_matches(item, rules, scope_map))

	def test_stacked_rules_sum_to_the_row_discount(self):
		rules = {
			"PR-1": _rule(discount_percentage=6),
			"PR-2": _rule(discount_percentage=4),
		}
		scope_map = {"PR-1": {"IT1"}, "PR-2": {"IT1"}}
		item = {**self.item, "pos_offer_item_rules": '["PR-1", "PR-2"]'}
		self.assertTrue(_item_offer_matches(item, rules, scope_map))

	def test_row_without_price_is_never_exempt(self):
		rules = {"PR-1": _rule(discount_percentage=10)}
		scope_map = {"PR-1": {"IT1"}}
		item = {"item_code": "IT1", "discount_percentage": 10, "pos_offer_item_rules": '["PR-1"]'}
		self.assertFalse(_item_offer_matches(item, rules, scope_map))


class TestRelayVerification(unittest.TestCase):
	"""SEC-10 (c): only relayed names the server can confirm join the stash."""

	doc = {
		"company": "Company A",
		"posting_date": "2026-09-22",
		"additional_discount_percentage": 10,
		"total": 900,
	}

	@staticmethod
	def _patch_get_all(rows):
		def _get_all(doctype, filters=None, fields=None, **kwargs):
			return rows

		return patch("pos_next.overrides.discount_code.frappe.get_all", side_effect=_get_all, create=True)

	def test_matching_transaction_rule_is_kept(self):
		rule = _rule(apply_on="Transaction", discount_percentage=10)
		with self._patch_get_all([rule]):
			self.assertEqual(["PR-1"], verify_transaction_rule_names(["PR-1"], self.doc))

	def test_discount_mismatch_is_dropped(self):
		rule = _rule(apply_on="Transaction", discount_percentage=25)
		with self._patch_get_all([rule]):
			self.assertEqual([], verify_transaction_rule_names(["PR-1"], self.doc))

	def test_item_level_rule_is_dropped(self):
		# The relay exists for transaction-scope rules; an Item Code rule named
		# in the relay filters out (query only asks for apply_on="Transaction").
		with self._patch_get_all([]):
			self.assertEqual([], verify_transaction_rule_names(["PR-1"], self.doc))

	def test_empty_relay_yields_nothing(self):
		with self._patch_get_all([]) as mock_get_all:
			self.assertEqual([], verify_transaction_rule_names([], self.doc))
			self.assertEqual([], verify_transaction_rule_names([None, ""], self.doc))
		mock_get_all.assert_not_called()


class TestHonestCashierFlows(unittest.TestCase):
	"""Acceptance: the honest cashier lane keeps working end to end."""

	def test_manual_edit_with_valid_code_still_passes(self):
		# Cashier edited 100 → 90 with the code — allowed, gated, code checks out.
		with patch(
			"pos_next.overrides.discount_code.frappe.db", new_callable=MagicMock
		) as mock_db:
			mock_db.get_value.return_value = frappe._dict(
				{
					"name": "CODE-1",
					"code": "ABCD2345",
					"status": "Active",
					"valid_from": None,
					"valid_upto": None,
					"company_scope": "All Outlets",
				}
			)
			doc = {
				"is_pos": 1,
				"company": "Company A",
				"discount_confirmation_code": "ABCD2345",
				"items": [{"item_code": "IT1", "price_list_rate": 100, "rate": 90}],
			}
			self.assertTrue(invoice_has_manual_discount(doc))  # a code IS required

			validate_invoice_discounts(doc, "validate")  # and the valid code passes

	def test_full_price_cart_needs_no_code(self):
		doc = {
			"is_pos": 1,
			"company": "Company A",
			"items": [{"item_code": "IT1", "price_list_rate": 100, "rate": 100}],
		}
		self.assertFalse(invoice_has_manual_discount(doc))

	def test_free_item_rows_need_no_code(self):
		doc = {
			"is_pos": 1,
			"company": "Company A",
			"items": [
				{"item_code": "IT1", "price_list_rate": 100, "rate": 100},
				{"item_code": "IT1", "price_list_rate": 0, "rate": 0, "is_free_item": 1},
			],
		}
		self.assertFalse(invoice_has_manual_discount(doc))


if __name__ == "__main__":
	unittest.main()
