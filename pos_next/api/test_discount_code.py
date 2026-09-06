# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the discount code gate (multi-use HQ codes).

Mocked-frappe style (same as test_offers.py) — no database needed; run via
pos_next/_pn_run_tests.py pos_next.api.test_discount_code

Only individual frappe attributes (db, get_all, get_doc, session,
has_permission) are patched so frappe.throw still raises real ValidationErrors.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pos_next.api.discount_code import check_code, get_status, validate_confirmation_code
from pos_next.overrides.discount_code import (
	invoice_has_manual_discount,
	record_code_usage_on_submit,
	validate_code,
	validate_invoice_discounts,
)
from pos_next.pos_next.doctype.pos_discount_confirmation_code.pos_discount_confirmation_code import (
	CODE_ALPHABET,
	CODE_LENGTH,
	POSDiscountConfirmationCode,
	generate_codes,
)

DB_PATCH = patch("pos_next.overrides.discount_code.frappe.db", new_callable=MagicMock)
GET_ALL_PATCH = patch(
	"pos_next.overrides.discount_code.frappe.get_all", new_callable=MagicMock, create=True
)
SESSION_PATCH = patch(
	"pos_next.overrides.discount_code.frappe.session", MagicMock(user="cashier@example.com"), create=True
)

CTRL_PATH = "pos_next.pos_next.doctype.pos_discount_confirmation_code.pos_discount_confirmation_code"
CTRL_DB_PATCH = patch(f"{CTRL_PATH}.frappe.db", new_callable=MagicMock)
CTRL_GET_DOC_PATCH = patch(f"{CTRL_PATH}.frappe.get_doc")
CTRL_PERM_PATCH = patch(f"{CTRL_PATH}.frappe.has_permission")


class FakeItem(dict):
	pass


class FakeDoc(dict):
	"""Dict-based doc/payload double: supports .get like a Document."""

	def set(self, key, value):
		self.setdefault("_set_calls", []).append((key, value))
		self[key] = value


def _code_row(**overrides):
	"""Parent row as returned by db.get_value(as_dict=True)."""
	row = {
		"name": "CODE-1",
		"code": "ABCD2345",
		"status": "Active",
		"valid_from": None,
		"valid_upto": None,
		"company_scope": "All Outlets",
	}
	row.update(overrides)
	return frappe._dict(row)


class TestValidateCode(unittest.TestCase):
	def test_missing_code_raises(self):
		with DB_PATCH as mock_db:
			with self.assertRaises(frappe.ValidationError):
				validate_code("", "Company A")
			mock_db.get_value.assert_not_called()

	def test_unknown_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = None
			with self.assertRaises(frappe.ValidationError):
				validate_code("ABCD2345", "Company A")

	def test_disabled_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = _code_row(status="Disabled")
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_code("ABCD2345", "Company A")
			self.assertIn("disabled", str(ctx.exception))

	def test_scope_all_outlets_valid_for_any_company(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_db.get_value.return_value = _code_row(company_scope="All Outlets")

			self.assertEqual(validate_code("ABCD2345", "Company A"), "CODE-1")
			self.assertEqual(validate_code("ABCD2345", "Company B"), "CODE-1")
			mock_get_all.assert_not_called()

	def test_empty_scope_valid_for_any_company(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_db.get_value.return_value = _code_row(company_scope="")

			self.assertEqual(validate_code("ABCD2345", "Company A"), "CODE-1")
			self.assertEqual(validate_code("ABCD2345", "Company B"), "CODE-1")
			mock_get_all.assert_not_called()

	def test_selected_outlets_passes_for_listed_company(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_db.get_value.return_value = _code_row(company_scope="Selected Outlets")
			mock_get_all.return_value = ["Company A"]

			self.assertEqual(validate_code("ABCD2345", "Company A"), "CODE-1")

			# pin the exact child query: frappe.get_all takes `pluck` (singular)
			mock_get_all.assert_called_once_with(
				"POS Discount Code Company",
				filters={"parenttype": "POS Discount Confirmation Code", "parent": "CODE-1"},
				pluck="company",
			)

	def test_selected_outlets_raises_for_other_company(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_db.get_value.return_value = _code_row(company_scope="Selected Outlets")
			mock_get_all.return_value = ["Company B"]

			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_code("ABCD2345", "Company A")

			self.assertIn("not valid for company Company A", str(ctx.exception))

	def test_all_outlets_except_passes_for_unlisted_company(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_db.get_value.return_value = _code_row(company_scope="All Outlets Except")
			mock_get_all.return_value = ["Company B"]

			self.assertEqual(validate_code("ABCD2345", "Company A"), "CODE-1")

	def test_all_outlets_except_raises_for_listed_company(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_db.get_value.return_value = _code_row(company_scope="All Outlets Except")
			mock_get_all.return_value = ["Company A"]

			with self.assertRaises(frappe.ValidationError):
				validate_code("ABCD2345", "Company A")

	def test_empty_company_argument_skips_scope_check(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_db.get_value.return_value = _code_row(company_scope="Selected Outlets")

			self.assertEqual(validate_code("ABCD2345", None), "CODE-1")
			self.assertEqual(validate_code("ABCD2345", ""), "CODE-1")
			mock_get_all.assert_not_called()

	def test_not_yet_valid_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = _code_row(valid_from="2999-01-01")

			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_code("ABCD2345", "Company A")

			self.assertIn("not valid yet", str(ctx.exception))

	def test_expired_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = _code_row(valid_upto="2000-01-01")

			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_code("ABCD2345", "Company A")

			self.assertIn("expired", str(ctx.exception))

	def test_validity_window_open_today_passes(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = _code_row(valid_from="2000-01-01", valid_upto="2999-12-31")

			self.assertEqual(validate_code("ABCD2345", "Company A"), "CODE-1")

	def test_valid_code_normalizes_case(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = _code_row()

			self.assertEqual(validate_code("abcd2345", "Company A"), "CODE-1")
			self.assertEqual(mock_db.get_value.call_args[0][1], {"code": "ABCD2345"})


class TestInvoiceHasManualDiscount(unittest.TestCase):
	def test_no_discount(self):
		doc = FakeDoc(items=[FakeItem(item_code="IT1")], discount_amount=0)
		self.assertFalse(invoice_has_manual_discount(doc))

	def test_item_percentage_discount(self):
		doc = FakeDoc(items=[FakeItem(discount_percentage=10)], discount_amount=0)
		self.assertTrue(invoice_has_manual_discount(doc))

	def test_item_amount_discount(self):
		doc = FakeDoc(items=[FakeItem(discount_amount=5000)], discount_amount=0)
		self.assertTrue(invoice_has_manual_discount(doc))

	def test_manual_rate_edit_below_price_list_rate_counts(self):
		item = FakeItem(is_rate_manually_edited=1, rate=9000, price_list_rate=10000)
		doc = FakeDoc(items=[item], discount_amount=0)
		self.assertTrue(invoice_has_manual_discount(doc))

	def test_manual_rate_edit_at_price_list_rate_does_not_count(self):
		item = FakeItem(is_rate_manually_edited=1, rate=10000, price_list_rate=10000)
		doc = FakeDoc(items=[item], discount_amount=0)
		self.assertFalse(invoice_has_manual_discount(doc))

	def test_header_additional_discount_counts(self):
		doc = FakeDoc(items=[FakeItem()], discount_amount=25000)
		self.assertTrue(invoice_has_manual_discount(doc))

	def test_header_additional_discount_percentage_counts(self):
		doc = FakeDoc(items=[FakeItem()], additional_discount_percentage=5)
		self.assertTrue(invoice_has_manual_discount(doc))


class TestOfferExemption(unittest.TestCase):
	"""Offer-attributed discounts (server-stashed pricing rules) are exempt."""

	def test_item_with_verified_rule_passes_without_code(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [SimpleNamespace(name="PR-OFFER-1", apply_on="Item Code")]
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				items=[
					FakeItem(
						item_code="IT1",
						discount_percentage=10,
						pos_offer_item_rules='["PR-OFFER-1"]',
					)
				],
				discount_confirmation_code="",
			)

			validate_invoice_discounts(doc, "validate")  # must not raise

			mock_db.get_value.assert_not_called()
			mock_get_all.assert_called_once_with(
				"Pricing Rule",
				filters={"name": ["in", ["PR-OFFER-1"]], "disable": 0},
				fields=["name", "apply_on"],
			)

	def test_item_exempt_when_any_claimed_rule_is_verified(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [SimpleNamespace(name="PR-1", apply_on="Item Code")]
			doc = FakeDoc(
				items=[
					FakeItem(
						discount_percentage=10,
						pos_offer_item_rules='["PR-1", "PR-FAKE"]',
					)
				],
				discount_amount=0,
			)

			self.assertFalse(invoice_has_manual_discount(doc))

	def test_item_with_unknown_claimed_rule_is_gated(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			# Claimed rule does not exist (or is disabled) — no exemption.
			mock_get_all.return_value = []
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				items=[
					FakeItem(
						item_code="IT1",
						discount_percentage=10,
						pos_offer_item_rules='["PR-GONE"]',
					)
				],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

	def test_item_with_empty_stash_is_gated(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				items=[FakeItem(item_code="IT1", discount_percentage=10, pos_offer_item_rules="")],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

			mock_get_all.assert_not_called()

	def test_malformed_item_stash_is_gated(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				items=[
					FakeItem(
						item_code="IT1",
						discount_percentage=10,
						pos_offer_item_rules="not json",
					)
				],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

			mock_get_all.assert_not_called()

	def test_header_discount_exempt_with_verified_transaction_rule(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [SimpleNamespace(name="PR-TRANS", apply_on="Transaction")]
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				discount_amount=25000,
				# In production this stash is item-derived rules plus the
				# client-relayed transaction rule names, merged by update_invoice.
				pos_applied_offer_rules='["PR-TRANS"]',
				items=[FakeItem(item_code="IT1")],
				discount_confirmation_code="",
			)

			validate_invoice_discounts(doc, "validate")  # must not raise

			mock_db.get_value.assert_not_called()

	def test_item_claimed_transaction_rule_does_not_exempt_header(self):
		# R3 keys the header exemption on the INVOICE-level stash only — an
		# item claiming a Transaction rule must not free the header discount.
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [SimpleNamespace(name="PR-TRANS", apply_on="Transaction")]
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				discount_amount=25000,
				pos_applied_offer_rules="",
				items=[
					FakeItem(
						item_code="IT1",
						discount_percentage=10,
						pos_offer_item_rules='["PR-TRANS"]',
					)
				],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

	def test_relay_merged_stash_exempts_header_and_items(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [
				SimpleNamespace(name="PR-ITEM-1", apply_on="Item Code"),
				SimpleNamespace(name="PR-TRANS", apply_on="Transaction"),
			]
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				discount_amount=25000,
				pos_applied_offer_rules='["PR-ITEM-1", "PR-TRANS"]',
				items=[
					FakeItem(
						item_code="IT1",
						discount_percentage=10,
						pos_offer_item_rules='["PR-ITEM-1"]',
					)
				],
				discount_confirmation_code="",
			)

			validate_invoice_discounts(doc, "validate")  # must not raise

			mock_db.get_value.assert_not_called()

	def test_header_discount_gated_when_no_verified_rule_is_transaction(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			# Verified rules exist, but none with apply_on == "Transaction".
			mock_get_all.return_value = [SimpleNamespace(name="PR-ITEM", apply_on="Item Code")]
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				discount_amount=25000,
				pos_applied_offer_rules='["PR-ITEM"]',
				items=[FakeItem(item_code="IT1")],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

	def test_header_discount_gated_when_stash_empty(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				discount_amount=25000,
				pos_applied_offer_rules="",
				items=[FakeItem(item_code="IT1")],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

			mock_get_all.assert_not_called()

	def test_manual_item_discount_still_gated_when_header_exempt(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			mock_get_all.return_value = [SimpleNamespace(name="PR-TRANS", apply_on="Transaction")]
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				discount_amount=25000,
				pos_applied_offer_rules='["PR-TRANS"]',
				items=[FakeItem(item_code="IT1", discount_percentage=10)],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")


class TestValidateInvoiceDiscounts(unittest.TestCase):
	def test_non_pos_invoice_skipped(self):
		with DB_PATCH as mock_db:
			validate_invoice_discounts(FakeDoc(is_pos=0, items=[FakeItem(discount_percentage=10)]))
			mock_db.get_value.assert_not_called()

	def test_return_invoice_skipped(self):
		with DB_PATCH as mock_db:
			validate_invoice_discounts(
				FakeDoc(is_pos=1, is_return=1, items=[FakeItem(discount_percentage=10)])
			)
			mock_db.get_value.assert_not_called()

	def test_undiscounted_invoice_skipped(self):
		with DB_PATCH as mock_db:
			validate_invoice_discounts(FakeDoc(is_pos=1, items=[FakeItem(item_code="IT1")]))
			mock_db.get_value.assert_not_called()

	def test_discount_without_code_blocks(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = None
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				items=[FakeItem(item_code="IT1", discount_percentage=10)],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_invoice_discounts(doc, "validate")

			self.assertIn("required", str(ctx.exception))

	def test_discount_with_valid_code_passes(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = _code_row()
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				items=[FakeItem(item_code="IT1", discount_percentage=10)],
				discount_confirmation_code="abcd2345",
			)

			validate_invoice_discounts(doc, "validate")  # must not raise

			self.assertEqual(mock_db.get_value.call_args[0][1], {"code": "ABCD2345"})


class TestRecordCodeUsageOnSubmit(unittest.TestCase):
	@staticmethod
	def _code_lookup(validate_result=None, lock_result=None):
		def lookup(doctype, filters=None, fieldname=None, as_dict=False, for_update=False):
			if for_update:
				return lock_result
			return validate_result

		return lookup

	@staticmethod
	def _lock_row(**overrides):
		"""Locked re-read of the code row (for_update)."""
		row = {
			"name": "CODE-1",
			"code": "ABCD2345",
			"status": "Active",
			"valid_from": None,
			"valid_upto": None,
			"company_scope": "All Outlets",
			"used_count": 3,
		}
		row.update(overrides)
		return frappe._dict(row)

	def test_non_pos_invoice_skipped(self):
		with DB_PATCH as mock_db:
			record_code_usage_on_submit(FakeDoc(is_pos=0, discount_confirmation_code="ABCD2345"))
			mock_db.set_value.assert_not_called()

	def test_invoice_without_code_records_nothing(self):
		with DB_PATCH as mock_db:
			doc = FakeDoc(is_pos=1, items=[FakeItem(discount_percentage=10)], discount_confirmation_code="")
			record_code_usage_on_submit(doc, "on_submit")
			mock_db.set_value.assert_not_called()

	def test_undiscounted_invoice_records_nothing(self):
		with DB_PATCH as mock_db:
			doc = FakeDoc(is_pos=1, items=[FakeItem(item_code="IT1")], discount_confirmation_code="ABCD2345")
			record_code_usage_on_submit(doc, "on_submit")
			mock_db.set_value.assert_not_called()

	def test_submit_increments_usage_audit(self):
		with (
			DB_PATCH as mock_db,
			SESSION_PATCH,
			patch("pos_next.overrides.discount_code.now_datetime") as mock_now,
		):
			mock_now.return_value = "2026-09-06 10:00:00"
			mock_db.get_value.side_effect = self._code_lookup(
				validate_result=_code_row(),
				lock_result=self._lock_row(used_count=3),
			)
			doc = FakeDoc(
				is_pos=1,
				name="ACC-SINV-0001",
				company="Company A",
				items=[FakeItem(discount_percentage=10)],
				discount_confirmation_code="ABCD2345",
			)

			record_code_usage_on_submit(doc, "on_submit")

			# usage audit written under the submit transaction, code row locked;
			# the lock re-read carries status/validity/scope so usability is
			# re-verified before the stamp
			mock_db.get_value.assert_called_with(
				"POS Discount Confirmation Code",
				"CODE-1",
				["name", "code", "status", "valid_from", "valid_upto", "company_scope", "used_count"],
				as_dict=True,
				for_update=True,
			)
			set_value_args = mock_db.set_value.call_args
			self.assertEqual(set_value_args[0][0], "POS Discount Confirmation Code")
			self.assertEqual(set_value_args[0][1], "CODE-1")
			values = set_value_args[0][2]
			self.assertEqual(values["used_count"], 4)
			self.assertEqual(values["last_used_by"], "cashier@example.com")
			self.assertEqual(values["last_used_in_invoice"], "ACC-SINV-0001")
			self.assertEqual(values["last_used_on"], "2026-09-06 10:00:00")

	def test_code_disabled_between_draft_and_submit_blocks(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.side_effect = self._code_lookup(
				validate_result=_code_row(),
				lock_result=self._lock_row(status="Disabled"),
			)
			doc = FakeDoc(
				is_pos=1,
				name="ACC-SINV-0002",
				items=[FakeItem(discount_percentage=10)],
				discount_confirmation_code="ABCD2345",
			)

			with self.assertRaises(frappe.ValidationError):
				record_code_usage_on_submit(doc, "on_submit")

			mock_db.set_value.assert_not_called()

	def test_code_expired_between_draft_and_submit_blocks(self):
		with DB_PATCH as mock_db:
			# Valid at draft time, valid_upto passed before the submit.
			mock_db.get_value.side_effect = self._code_lookup(
				validate_result=_code_row(),
				lock_result=self._lock_row(valid_upto="2000-01-01"),
			)
			doc = FakeDoc(
				is_pos=1,
				name="ACC-SINV-0005",
				company="Company A",
				items=[FakeItem(discount_percentage=10)],
				discount_confirmation_code="ABCD2345",
			)

			with self.assertRaises(frappe.ValidationError) as ctx:
				record_code_usage_on_submit(doc, "on_submit")

			self.assertIn("expired", str(ctx.exception))
			mock_db.set_value.assert_not_called()

	def test_scope_rebinding_under_lock_blocks(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all:
			# The code was re-scoped to other outlets after the draft passed.
			mock_db.get_value.side_effect = self._code_lookup(
				validate_result=_code_row(),
				lock_result=self._lock_row(company_scope="Selected Outlets"),
			)
			mock_get_all.return_value = ["Company B"]
			doc = FakeDoc(
				is_pos=1,
				name="ACC-SINV-0003",
				company="Company A",
				items=[FakeItem(discount_percentage=10)],
				discount_confirmation_code="ABCD2345",
			)

			with self.assertRaises(frappe.ValidationError) as ctx:
				record_code_usage_on_submit(doc, "on_submit")

			self.assertIn("not valid for company", str(ctx.exception))
			mock_db.set_value.assert_not_called()

	def test_code_scope_still_covers_company_under_lock_passes(self):
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all, SESSION_PATCH:
			mock_db.get_value.side_effect = self._code_lookup(
				validate_result=_code_row(),
				lock_result=self._lock_row(company_scope="Selected Outlets", used_count=1),
			)
			mock_get_all.return_value = ["Company A"]
			doc = FakeDoc(
				is_pos=1,
				name="ACC-SINV-0004",
				company="Company A",
				items=[FakeItem(discount_percentage=10)],
				discount_confirmation_code="ABCD2345",
			)

			record_code_usage_on_submit(doc, "on_submit")  # must not raise

			mock_db.set_value.assert_called_once()


class TestGetStatusAPI(unittest.TestCase):
	def test_gate_is_always_enabled(self):
		self.assertEqual(get_status(company="Company A"), {"enabled": True})


class TestCheckCodeAPI(unittest.TestCase):
	"""check_code validates the code VALUE alone (locked-fields UX: no cart
	context yet, so the cart-aware endpoint would skip the check)."""

	@patch("pos_next.api.discount_code.validate_code")
	def test_valid_code_returns_valid(self, mock_validate):
		mock_validate.return_value = "CODE-1"

		result = check_code(code="ABCD2345", company="Company A")

		self.assertEqual(result, {"valid": True})

	@patch("pos_next.api.discount_code.validate_code")
	def test_validate_code_receives_raw_code_and_company(self, mock_validate):
		mock_validate.return_value = "CODE-1"

		check_code(code="abcd2345", company="Company A")

		# The API passes the value through raw; validate_code normalizes it.
		mock_validate.assert_called_once_with("abcd2345", "Company A")

	@patch("pos_next.api.discount_code.validate_code")
	def test_invalid_code_returns_message_without_raising(self, mock_validate):
		mock_validate.side_effect = frappe.ValidationError("Discount code WRONG1 is not valid.")

		result = check_code(code="WRONG1", company="Company A")

		self.assertFalse(result["valid"])
		self.assertIn("not valid", result["message"])


class TestValidateConfirmationCodeAPI(unittest.TestCase):
	@patch("pos_next.api.discount_code.validate_code")
	def test_undiscounted_cart_needs_no_code(self, mock_validate):
		result = validate_confirmation_code(code="ABCD2345", company="Company A", items="[]")

		self.assertTrue(result["valid"])
		self.assertFalse(result["requires_code"])
		mock_validate.assert_not_called()

	@patch("pos_next.api.discount_code.validate_code")
	def test_additional_discount_requires_code(self, mock_validate):
		mock_validate.return_value = "CODE-1"

		result = validate_confirmation_code(
			code="ABCD2345", company="Company A", items=None, additional_discount=5000
		)

		self.assertTrue(result["valid"])
		self.assertTrue(result["requires_code"])

	@patch("pos_next.api.discount_code.validate_code")
	def test_item_discount_requires_code(self, mock_validate):
		mock_validate.return_value = "CODE-1"

		result = validate_confirmation_code(
			code="ABCD2345",
			company="Company A",
			items='[{"item_code": "IT1", "discount_percentage": 10}]',
		)

		self.assertTrue(result["valid"])
		self.assertTrue(result["requires_code"])

	@patch("pos_next.api.discount_code.validate_code")
	def test_invalid_code_returns_message(self, mock_validate):
		mock_validate.side_effect = frappe.ValidationError("Discount code WRONG1 is not valid.")

		result = validate_confirmation_code(
			code="WRONG1",
			company="Company A",
			items='[{"item_code": "IT1", "discount_percentage": 10}]',
		)

		self.assertFalse(result["valid"])
		self.assertTrue(result["requires_code"])
		self.assertIn("not valid", result["message"])

	@patch("pos_next.api.discount_code.validate_code")
	def test_malformed_items_payload_is_rejected_without_raising(self, mock_validate):
		# Entries that are not dicts used to 500 on .get(); the payload must be
		# answered defensively instead.
		result = validate_confirmation_code(code="ABCD2345", company="Company A", items='["garbage", 5]')

		self.assertFalse(result["valid"])
		self.assertTrue(result["requires_code"])
		self.assertTrue(result.get("message"))
		mock_validate.assert_not_called()


class TestGenerateCodes(unittest.TestCase):
	def test_requires_create_permission(self):
		with CTRL_PERM_PATCH as mock_perm:
			mock_perm.side_effect = frappe.PermissionError("No permission")
			with self.assertRaises(frappe.PermissionError):
				generate_codes(count=1)

	def test_count_bounds_are_enforced(self):
		with CTRL_PERM_PATCH:
			for bad in (0, -1, 501):
				with self.assertRaises(frappe.ValidationError):
					generate_codes(count=bad)

	def test_unknown_company_is_rejected(self):
		with CTRL_PERM_PATCH, CTRL_DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			with self.assertRaises(frappe.ValidationError) as ctx:
				generate_codes(count=1, company="Company Z")

			self.assertIn("Company Z", str(ctx.exception))

	def test_generates_active_codes_with_company_and_notes(self):
		created = []

		def fake_get_doc(payload):
			return SimpleNamespace(**payload, insert=lambda **kwargs: created.append(payload))

		with (
			CTRL_PERM_PATCH,
			CTRL_DB_PATCH as mock_db,
			CTRL_GET_DOC_PATCH as mock_get_doc,
		):
			mock_db.exists.return_value = True
			mock_get_doc.side_effect = fake_get_doc

			result = generate_codes(count=3, company=" Company A ", notes=" Lebaran promo ")

			self.assertEqual(len(result["codes"]), 3)
			self.assertEqual(len(created), 3)
			for payload in created:
				self.assertEqual(payload["status"], "Active")
				self.assertEqual(payload["notes"], "Lebaran promo")
				# legacy single-company field is gone: scope + child row instead
				self.assertNotIn("company", payload)
				self.assertEqual(payload["company_scope"], "Selected Outlets")
				self.assertEqual(payload["companies"], [{"company": "Company A"}])
			for code in result["codes"]:
				self.assertEqual(len(code), CODE_LENGTH)
				self.assertTrue(set(code) <= set(CODE_ALPHABET))

	def test_duplicate_code_is_regenerated(self):
		created = []

		def get_doc(payload):
			def insert(**kwargs):
				if payload["code"] == "AAABBB22":
					raise frappe.DuplicateEntryError
				created.append(payload)

			return SimpleNamespace(**payload, insert=insert)

		with (
			patch(f"{CTRL_PATH}.secrets.choice") as mock_choice,
			CTRL_PERM_PATCH,
			CTRL_DB_PATCH as mock_db,
			CTRL_GET_DOC_PATCH as mock_get_doc,
		):
			# deterministic sequence: a colliding code, then a fresh one
			mock_choice.side_effect = list("AAABBB22") + list("AAABBB23")
			mock_db.exists.return_value = True
			mock_get_doc.side_effect = get_doc

			result = generate_codes(count=1)

			self.assertEqual(result["codes"], ["AAABBB23"])
			self.assertEqual(len(created), 1)


class TestControllerValidate(unittest.TestCase):
	@staticmethod
	def _doc(**fields):
		doc = object.__new__(POSDiscountConfirmationCode)
		doc.__dict__.update(fields)
		doc.doctype = "POS Discount Confirmation Code"
		# defaults matching the doctype (explicit values win)
		doc.valid_from = fields.get("valid_from")
		doc.valid_upto = fields.get("valid_upto")
		doc.company_scope = fields.get("company_scope", "All Outlets")
		doc.companies = fields.get("companies", [])
		return doc

	def test_code_is_normalized_and_alphabet_checked(self):
		doc = self._doc(code="abcd2345", used_count=0, __islocal=1)
		doc.validate()
		self.assertEqual(doc.code, "ABCD2345")

	def test_blank_code_is_auto_generated(self):
		with patch(f"{CTRL_PATH}.secrets.choice") as mock_choice, CTRL_DB_PATCH as mock_db:
			# deterministic sequence: a colliding code, then a fresh one
			mock_choice.side_effect = list("AAABBB22") + list("AAABBB23")
			mock_db.exists.side_effect = [True, False]

			doc = self._doc(code="", used_count=0, __islocal=1)
			doc.validate()

			self.assertEqual(doc.code, "AAABBB23")
			# first candidate collided, generation retried once
			self.assertEqual(mock_db.exists.call_count, 2)

	def test_ambiguous_characters_rejected(self):
		doc = self._doc(code="AB1", used_count=0, __islocal=1)
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_used_code_value_cannot_change(self):
		with CTRL_DB_PATCH as mock_db:
			mock_db.get_value.return_value = "ABCD2345"
			doc = self._doc(code="ZZZZ9999", used_count=2, name="CODE-1")
			with self.assertRaises(frappe.ValidationError):
				doc.validate()

	def test_used_code_can_be_disabled_without_value_change(self):
		with CTRL_DB_PATCH as mock_db:
			mock_db.get_value.return_value = "ABCD2345"
			doc = self._doc(code="ABCD2345", used_count=2, name="CODE-1", status="Disabled")
			doc.validate()  # must not raise

	def test_valid_upto_before_valid_from_raises(self):
		doc = self._doc(
			code="ABCD2345", used_count=0, __islocal=1, valid_from="2026-09-10", valid_upto="2026-09-01"
		)
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_validity_window_in_order_passes(self):
		doc = self._doc(
			code="ABCD2345",
			used_count=0,
			__islocal=1,
			valid_from="2026-09-01",
			valid_upto="2026-09-30",
		)
		doc.validate()  # must not raise

	def test_scope_without_outlets_raises(self):
		for scope in ("Selected Outlets", "All Outlets Except"):
			doc = self._doc(code="ABCD2345", used_count=0, __islocal=1, company_scope=scope, companies=[])
			with self.assertRaises(frappe.ValidationError):
				doc.validate()

	def test_scope_with_outlet_passes(self):
		doc = self._doc(
			code="ABCD2345",
			used_count=0,
			__islocal=1,
			company_scope="Selected Outlets",
			companies=[{"company": "Company A"}],
		)
		doc.validate()  # must not raise


if __name__ == "__main__":
	unittest.main()
