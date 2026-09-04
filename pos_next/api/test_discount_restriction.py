# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for POS Discount Restriction enforcement.

Mocked-frappe style (same as test_offers.py) — no database needed; run via
pos_next/_pn_run_tests.py pos_next.api.test_discount_restriction

Only individual frappe attributes (db, get_all, get_doc, session) are patched
so frappe.throw still raises real ValidationErrors.
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pos_next.api.discount_restriction import (
	get_status,
	validate_confirmation_code,
)
from pos_next.overrides.discount_restriction import (
	check_quota,
	claim_code,
	get_applicable_restriction,
	get_quota_info,
	invoice_has_manual_discount,
	invoice_requires_code,
	release_usage_on_cancel,
	record_usage_on_submit,
	validate_invoice_discounts,
	_validate_code_value,
)

DB_PATCH = patch("pos_next.overrides.discount_restriction.frappe.db", new_callable=MagicMock)
GET_ALL_PATCH = patch("pos_next.overrides.discount_restriction.frappe.get_all")
GET_DOC_PATCH = patch("pos_next.overrides.discount_restriction.frappe.get_doc")
SESSION_PATCH = patch(
	"pos_next.overrides.discount_restriction.frappe.session", MagicMock(user="cashier@example.com"), create=True
)


class FakeItem(dict):
	pass


class FakeDoc(dict):
	"""Dict-based doc/payload double: supports .get/.set like a Document."""

	def set(self, key, value):
		self.setdefault("_set_calls", []).append((key, value))
		self[key] = value


class FakeRule:
	"""Doc-like double for POS Discount Restriction with sane defaults."""

	def __init__(self, name="RULE-1", title="Rule One", **kwargs):
		self.name = name
		self.title = title
		self.enforce_usage_quota = kwargs.get("enforce_usage_quota", 0)
		self.quota_mode = kwargs.get("quota_mode", "Global")
		self.global_max_usage = kwargs.get("global_max_usage", 0)
		self.require_confirmation_code = kwargs.get("require_confirmation_code", 0)
		self._children = kwargs.get("children", {})

	def get(self, key, *args, **kwargs):
		return self._children.get(key, [])


def restriction_row(name, title):
	return SimpleNamespace(name=name, title=title)


def rule_lookup_side_effect(rules, matched_companies):
	"""Side effect for frappe.get_all covering both lookups in
	get_applicable_restriction: rules in window, then enabled company rows
	(returned plucked, i.e. a list of parent names)."""

	def get_all(doctype, filters=None, fields=None, **kwargs):
		if doctype == "POS Discount Restriction":
			return rules
		if doctype == "POS Discount Restriction Company":
			return list(matched_companies)
		raise AssertionError(f"unexpected get_all on {doctype}")

	return get_all


class TestGetApplicableRestriction(unittest.TestCase):
	def test_no_company_returns_none(self):
		with GET_ALL_PATCH as mock_get_all:
			self.assertIsNone(get_applicable_restriction(None))
			mock_get_all.assert_not_called()

	def test_no_rules_returns_none(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.side_effect = rule_lookup_side_effect([], set())
			self.assertIsNone(get_applicable_restriction("Company A", "2026-09-04"))

	def test_company_not_listed_returns_none(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.side_effect = rule_lookup_side_effect([restriction_row("R1", "Rule One")], set())
			self.assertIsNone(get_applicable_restriction("Company A", "2026-09-04"))

	def test_single_match_returns_rule_doc(self):
		rule = FakeRule(name="R1")
		with GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_get_all.side_effect = rule_lookup_side_effect(
				[restriction_row("R1", "Rule One")], {"R1"}
			)
			mock_get_doc.return_value = rule

			result = get_applicable_restriction("Company A", "2026-09-04")

			self.assertIs(result, rule)
			mock_get_doc.assert_called_once_with("POS Discount Restriction", "R1")

	def test_multiple_matches_raise_conflict(self):
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.side_effect = rule_lookup_side_effect(
				[restriction_row("R1", "Rule One"), restriction_row("R2", "Rule Two")], {"R1", "R2"}
			)

			with self.assertRaises(frappe.ValidationError) as ctx:
				get_applicable_restriction("Company A", "2026-09-04")

			self.assertIn("Rule One", str(ctx.exception))
			self.assertIn("Rule Two", str(ctx.exception))


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


class TestInvoiceRequiresCode(unittest.TestCase):
	def test_rule_without_code_requirement(self):
		rule = FakeRule(require_confirmation_code=0)
		doc = FakeDoc(items=[FakeItem(item_code="IT1", discount_percentage=10)])
		self.assertFalse(invoice_requires_code(rule, doc))

	def test_listed_item_discount_requires_code(self):
		rule = FakeRule(
			require_confirmation_code=1, children={"code_items": [SimpleNamespace(item="IT1")]}
		)
		doc = FakeDoc(items=[FakeItem(item_code="IT1", discount_percentage=10)])
		self.assertTrue(invoice_requires_code(rule, doc))

	def test_unlisted_item_discount_does_not_require_code(self):
		rule = FakeRule(
			require_confirmation_code=1, children={"code_items": [SimpleNamespace(item="IT1")]}
		)
		doc = FakeDoc(items=[FakeItem(item_code="OTHER", discount_percentage=10)])
		self.assertFalse(invoice_requires_code(rule, doc))

	def test_empty_item_list_means_any_item(self):
		rule = FakeRule(require_confirmation_code=1)
		doc = FakeDoc(items=[FakeItem(item_code="ANY", discount_percentage=5)])
		self.assertTrue(invoice_requires_code(rule, doc))

	def test_additional_discount_requires_code(self):
		rule = FakeRule(
			require_confirmation_code=1, children={"code_items": [SimpleNamespace(item="IT1")]}
		)
		doc = FakeDoc(items=[FakeItem(item_code="OTHER")], discount_amount=10000)
		self.assertTrue(invoice_requires_code(rule, doc))

	def test_full_price_item_does_not_require_code(self):
		rule = FakeRule(require_confirmation_code=1)
		doc = FakeDoc(items=[FakeItem(item_code="IT1")], discount_amount=0)
		self.assertFalse(invoice_requires_code(rule, doc))


class TestQuota(unittest.TestCase):
	def test_quota_not_enforced_returns_none(self):
		with DB_PATCH as mock_db:
			rule = FakeRule(enforce_usage_quota=0)
			self.assertIsNone(get_quota_info(rule, "Company A"))
			mock_db.count.assert_not_called()

	def test_global_quota_counts_all_companies(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 30
			rule = FakeRule(enforce_usage_quota=1, quota_mode="Global", global_max_usage=50)

			info = get_quota_info(rule, "Company A")

			self.assertEqual(info, {"mode": "Global", "limit": 50, "used": 30, "remaining": 20})
			mock_db.count.assert_called_once_with(
				"POS Discount Restriction Usage", {"restriction": "RULE-1"}
			)

	def test_per_company_quota_uses_company_row_limit(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 10
			rule = FakeRule(
				enforce_usage_quota=1,
				quota_mode="Per Company",
				children={
					"companies": [
						SimpleNamespace(company="Company A", max_usage=50),
						SimpleNamespace(company="Company B", max_usage=10),
					]
				},
			)

			info = get_quota_info(rule, "Company A")

			self.assertEqual(info["limit"], 50)
			self.assertEqual(info["remaining"], 40)
			mock_db.count.assert_called_once_with(
				"POS Discount Restriction Usage", {"restriction": "RULE-1", "company": "Company A"}
			)

	def test_per_company_zero_limit_means_unlimited(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 999
			rule = FakeRule(
				enforce_usage_quota=1,
				quota_mode="Per Company",
				children={"companies": [SimpleNamespace(company="Company A", max_usage=0)]},
			)

			info = get_quota_info(rule, "Company A")

			self.assertIsNone(info["remaining"])
			check_quota(rule, "Company A")  # must not raise

	def test_check_quota_throws_when_global_exhausted(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 50
			rule = FakeRule(enforce_usage_quota=1, quota_mode="Global", global_max_usage=50)

			with self.assertRaises(frappe.ValidationError):
				check_quota(rule, "Company A")

	def test_check_quota_throws_when_company_exhausted(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 10
			rule = FakeRule(
				enforce_usage_quota=1,
				quota_mode="Per Company",
				children={"companies": [SimpleNamespace(company="Company A", max_usage=10)]},
			)

			with self.assertRaises(frappe.ValidationError):
				check_quota(rule, "Company A")

	def test_check_quota_allows_when_slot_left(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 49
			rule = FakeRule(enforce_usage_quota=1, quota_mode="Global", global_max_usage=50)

			info = check_quota(rule, "Company A")

			self.assertEqual(info["remaining"], 1)


class TestValidateCodeValue(unittest.TestCase):
	def _rule(self):
		return FakeRule(name="RULE-1", title="Rule One", require_confirmation_code=1)

	def test_missing_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = None
			with self.assertRaises(frappe.ValidationError):
				_validate_code_value(self._rule(), "", "Company A")

	def test_unknown_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = None
			with self.assertRaises(frappe.ValidationError):
				_validate_code_value(self._rule(), "ABCD2345", "Company A")

	def test_used_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = SimpleNamespace(
				name="CODE-1", status="Used", company=None
			)
			with self.assertRaises(frappe.ValidationError) as ctx:
				_validate_code_value(self._rule(), "ABCD2345", "Company A")
			self.assertIn("already been used", str(ctx.exception))

	def test_cancelled_code_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = SimpleNamespace(
				name="CODE-1", status="Cancelled", company=None
			)
			with self.assertRaises(frappe.ValidationError):
				_validate_code_value(self._rule(), "ABCD2345", "Company A")

	def test_company_mismatch_raises(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = SimpleNamespace(
				name="CODE-1", status="Available", company="Company B"
			)
			with self.assertRaises(frappe.ValidationError):
				_validate_code_value(self._rule(), "ABCD2345", "Company A")

	def test_valid_code_returns_name_and_normalizes_case(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = SimpleNamespace(
				name="CODE-1", status="Available", company=None
			)

			self.assertEqual(_validate_code_value(self._rule(), "abcd2345", "Company A"), "CODE-1")
			args = mock_db.get_value.call_args
			self.assertEqual(args[0][1]["code"], "ABCD2345")
			self.assertEqual(args[0][1]["restriction"], "RULE-1")

	def test_code_of_another_rule_is_invalid(self):
		# Lookup is scoped to (code, restriction) — a code from another rule
		# must behave exactly like an unknown code.
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = None
			with self.assertRaises(frappe.ValidationError):
				_validate_code_value(self._rule(), "ZZZZ9999", "Company A")


class TestValidateInvoiceDiscounts(unittest.TestCase):
	def test_non_pos_invoice_skipped(self):
		with GET_ALL_PATCH as mock_get_all:
			doc = FakeDoc(is_pos=0, items=[FakeItem(discount_percentage=10)])
			validate_invoice_discounts(doc)
			mock_get_all.assert_not_called()

	def test_return_invoice_skipped(self):
		with GET_ALL_PATCH as mock_get_all:
			doc = FakeDoc(is_pos=1, is_return=1, items=[FakeItem(discount_percentage=10)])
			validate_invoice_discounts(doc)
			mock_get_all.assert_not_called()

	def test_undiscounted_invoice_skipped(self):
		with GET_ALL_PATCH as mock_get_all:
			doc = FakeDoc(is_pos=1, items=[FakeItem(item_code="IT1")])
			validate_invoice_discounts(doc)
			mock_get_all.assert_not_called()

	def test_no_rule_stamps_nothing(self):
		with GET_ALL_PATCH as mock_get_all, SESSION_PATCH:
			mock_get_all.side_effect = [[], set()]
			doc = FakeDoc(is_pos=1, company="Company A", items=[FakeItem(discount_percentage=10)])

			validate_invoice_discounts(doc, "validate")

			self.assertEqual(doc.get("_set_calls", []), [])

	def test_active_rule_stamps_and_checks_quota(self):
		with (
			GET_ALL_PATCH as mock_get_all,
			GET_DOC_PATCH as mock_get_doc,
			DB_PATCH as mock_db,
		):
			mock_get_all.side_effect = rule_lookup_side_effect(
				[restriction_row("R1", "Rule One")], {"R1"}
			)
			mock_get_doc.return_value = FakeRule(
				name="R1",
				title="Rule One",
				enforce_usage_quota=1,
				quota_mode="Global",
				global_max_usage=10,
			)
			mock_db.count.return_value = 3
			doc = FakeDoc(is_pos=1, company="Company A", items=[FakeItem(discount_percentage=10)])

			validate_invoice_discounts(doc, "validate")

			self.assertEqual(doc.get("_set_calls"), [("pos_discount_restriction", "R1")])

	def test_exhausted_quota_blocks_draft_save(self):
		with (
			GET_ALL_PATCH as mock_get_all,
			GET_DOC_PATCH as mock_get_doc,
			DB_PATCH as mock_db,
		):
			mock_get_all.side_effect = rule_lookup_side_effect(
				[restriction_row("R1", "Rule One")], {"R1"}
			)
			mock_get_doc.return_value = FakeRule(
				name="R1", enforce_usage_quota=1, quota_mode="Global", global_max_usage=10
			)
			mock_db.count.return_value = 10
			doc = FakeDoc(is_pos=1, company="Company A", items=[FakeItem(discount_percentage=10)])

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")

	def test_missing_code_blocks_validation(self):
		with (
			GET_ALL_PATCH as mock_get_all,
			GET_DOC_PATCH as mock_get_doc,
			DB_PATCH as mock_db,
		):
			mock_get_all.side_effect = rule_lookup_side_effect(
				[restriction_row("R1", "Rule One")], {"R1"}
			)
			mock_get_doc.return_value = FakeRule(name="R1", require_confirmation_code=1)
			mock_db.get_value.return_value = None
			doc = FakeDoc(
				is_pos=1,
				company="Company A",
				items=[FakeItem(item_code="IT1", discount_percentage=10)],
				discount_confirmation_code="",
			)

			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc, "validate")


class TestRecordUsageOnSubmit(unittest.TestCase):
	@staticmethod
	def _code_lookup(restriction_lock="R1", validate_result=None, claim_result=None):
		def lookup(doctype, filters=None, fieldname=None, as_dict=False, for_update=False):
			if doctype == "POS Discount Restriction":
				return restriction_lock
			if doctype == "POS Discount Confirmation Code":
				return claim_result if for_update else validate_result
			raise AssertionError(f"unexpected get_value on {doctype}")

		return lookup

	def test_submit_inserts_usage_row_and_claims_code(self):
		usage_mock = MagicMock()
		rule = FakeRule(
			name="R1",
			title="Rule One",
			enforce_usage_quota=1,
			quota_mode="Per Company",
			children={
				"companies": [SimpleNamespace(company="Company A", max_usage=5)],
				"code_items": [SimpleNamespace(item="IT1")],
			},
		)
		rule.require_confirmation_code = 1

		def get_doc_side_effect(*args):
			if len(args) == 2 and args[0] == "POS Discount Restriction":
				return rule
			if isinstance(args[0], dict) and args[0].get("doctype") == "POS Discount Restriction Usage":
				return usage_mock
			raise AssertionError(f"unexpected get_doc call {args!r}")

		with (
			GET_ALL_PATCH as mock_get_all,
			GET_DOC_PATCH as mock_get_doc,
			DB_PATCH as mock_db,
			SESSION_PATCH,
			patch("pos_next.overrides.discount_restriction.now_datetime") as mock_now,
		):
			mock_now.return_value = "2026-09-04 10:00:00"
			mock_get_all.side_effect = rule_lookup_side_effect(
				[restriction_row("R1", "Rule One")], {"R1"}
			)
			mock_get_doc.side_effect = get_doc_side_effect
			mock_db.count.return_value = 1
			mock_db.get_value.side_effect = self._code_lookup(
				validate_result=SimpleNamespace(name="CODE-1", status="Available", company=None),
				claim_result=SimpleNamespace(status="Available", company=None),
			)
			doc = FakeDoc(
				is_pos=1,
				name="ACC-SINV-0001",
				company="Company A",
				items=[FakeItem(item_code="IT1", discount_percentage=10)],
				discount_confirmation_code="ABCD2345",
			)

			record_usage_on_submit(doc, "on_submit")

			# rule row locked to serialize concurrent submits
			mock_db.get_value.assert_any_call("POS Discount Restriction", "R1", "name", for_update=True)
			# per-company quota counted, not global
			mock_db.count.assert_called_once_with(
				"POS Discount Restriction Usage", {"restriction": "R1", "company": "Company A"}
			)
			# usage ledger row written
			usage_mock.insert.assert_called_once()
			# code claimed with Used status + audit fields
			set_value_args = mock_db.set_value.call_args
			self.assertEqual(set_value_args[0][0], "POS Discount Confirmation Code")
			self.assertEqual(set_value_args[0][1], "CODE-1")
			values = set_value_args[0][2]
			self.assertEqual(values["status"], "Used")
			self.assertEqual(values["used_in_invoice"], "ACC-SINV-0001")
			self.assertEqual(values["used_by"], "cashier@example.com")

	def test_duplicate_usage_row_is_swallowed(self):
		usage_mock = MagicMock()
		usage_mock.insert.side_effect = frappe.DuplicateEntryError
		rule = FakeRule(name="R1", enforce_usage_quota=1, quota_mode="Global", global_max_usage=100)

		def get_doc_side_effect(*args):
			if len(args) == 2 and args[0] == "POS Discount Restriction":
				return rule
			if isinstance(args[0], dict) and args[0].get("doctype") == "POS Discount Restriction Usage":
				return usage_mock
			raise AssertionError(f"unexpected get_doc call {args!r}")

		with (
			GET_ALL_PATCH as mock_get_all,
			GET_DOC_PATCH as mock_get_doc,
			DB_PATCH as mock_db,
			SESSION_PATCH,
		):
			mock_get_all.side_effect = rule_lookup_side_effect(
				[restriction_row("R1", "Rule One")], {"R1"}
			)
			mock_get_doc.side_effect = get_doc_side_effect
			mock_db.count.return_value = 5
			doc = FakeDoc(
				is_pos=1,
				name="ACC-SINV-0002",
				company="Company A",
				items=[FakeItem(item_code="IT1", discount_percentage=10)],
			)

			record_usage_on_submit(doc, "on_submit")  # must not raise

			usage_mock.insert.assert_called_once()

	def test_no_rule_on_submit_records_nothing(self):
		with GET_ALL_PATCH as mock_get_all, DB_PATCH as mock_db:
			mock_get_all.side_effect = [[], set()]
			doc = FakeDoc(is_pos=1, company="Company A", items=[FakeItem(discount_percentage=10)])

			record_usage_on_submit(doc, "on_submit")

			mock_db.set_value.assert_not_called()


class TestClaimCode(unittest.TestCase):
	@patch("pos_next.overrides.discount_restriction.now_datetime")
	def test_claim_flips_available_code_to_used(self, mock_now):
		mock_now.return_value = "2026-09-04 10:00:00"
		with DB_PATCH as mock_db, SESSION_PATCH:
			mock_db.get_value.return_value = SimpleNamespace(
				name="CODE-1", status="Available", company=None
			)
			rule = FakeRule(name="R1", title="Rule One")

			claim_code(rule, "ABCD2345", "Company A", "ACC-SINV-0009")

			values = mock_db.set_value.call_args[0][2]
			self.assertEqual(values["status"], "Used")

	def test_claim_raises_when_code_not_available(self):
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = SimpleNamespace(
				name="CODE-1", status="Used", company=None
			)
			rule = FakeRule(name="R1", title="Rule One")

			with self.assertRaises(frappe.ValidationError):
				claim_code(rule, "ABCD2345", "Company A", "ACC-SINV-0009")

			mock_db.set_value.assert_not_called()


class TestReleaseUsageOnCancel(unittest.TestCase):
	def test_cancel_deletes_usage_rows(self):
		with DB_PATCH as mock_db:
			release_usage_on_cancel(FakeDoc(name="ACC-SINV-0001"), "on_cancel")

			mock_db.delete.assert_called_once_with(
				"POS Discount Restriction Usage", {"sales_invoice": "ACC-SINV-0001"}
			)


class TestGetStatusAPI(unittest.TestCase):
	@patch("pos_next.api.discount_restriction.get_applicable_restriction")
	def test_no_rule_reports_not_applicable(self, mock_get_rule):
		mock_get_rule.return_value = None

		result = get_status(company="Company A")

		self.assertEqual(result, {"applicable": False})

	@patch("pos_next.api.discount_restriction.get_applicable_restriction")
	def test_active_rule_reports_quota_and_code_items(self, mock_get_rule):
		mock_get_rule.return_value = FakeRule(
			name="R1",
			title="Rule One",
			enforce_usage_quota=1,
			quota_mode="Global",
			global_max_usage=50,
			require_confirmation_code=1,
			children={"code_items": [SimpleNamespace(item="IT1"), SimpleNamespace(item="IT2")]},
		)
		with patch(
			"pos_next.api.discount_restriction.get_quota_info"
		) as mock_quota:
			mock_quota.return_value = {"mode": "Global", "limit": 50, "used": 10, "remaining": 40}

			result = get_status(company="Company A")

		self.assertTrue(result["applicable"])
		self.assertEqual(result["rule"], {"name": "R1", "title": "Rule One"})
		self.assertTrue(result["requires_code"])
		self.assertEqual(result["code_items"], ["IT1", "IT2"])
		self.assertFalse(result["quota_exhausted"])
		self.assertEqual(result["quota"]["remaining"], 40)

	@patch("pos_next.api.discount_restriction.get_applicable_restriction")
	def test_exhausted_quota_is_flagged(self, mock_get_rule):
		mock_get_rule.return_value = FakeRule(
			name="R1", enforce_usage_quota=1, quota_mode="Global", global_max_usage=5
		)
		with patch(
			"pos_next.api.discount_restriction.get_quota_info"
		) as mock_quota:
			mock_quota.return_value = {"mode": "Global", "limit": 5, "used": 5, "remaining": 0}

			result = get_status(company="Company A")

		self.assertTrue(result["quota_exhausted"])


class TestValidateConfirmationCodeAPI(unittest.TestCase):
	@patch("pos_next.api.discount_restriction.get_applicable_restriction")
	def test_no_rule_means_valid(self, mock_get_rule):
		mock_get_rule.return_value = None

		result = validate_confirmation_code(code="ABCD2345", company="Company A", items="[]")

		self.assertTrue(result["valid"])
		self.assertFalse(result["applicable"])

	@patch("pos_next.api.discount_restriction.get_applicable_restriction")
	def test_discount_on_unlisted_item_needs_no_code(self, mock_get_rule):
		mock_get_rule.return_value = FakeRule(
			require_confirmation_code=1,
			children={"code_items": [SimpleNamespace(item="IT1")]},
		)

		result = validate_confirmation_code(
			code="ABCD2345",
			company="Company A",
			items='[{"item_code": "OTHER", "discount_percentage": 10}]',
		)

		self.assertTrue(result["valid"])
		self.assertFalse(result["requires_code"])

	@patch("pos_next.api.discount_restriction.get_applicable_restriction")
	def test_bad_code_returns_invalid_with_message(self, mock_get_rule):
		mock_get_rule.return_value = FakeRule(require_confirmation_code=1)
		with patch(
			"pos_next.api.discount_restriction._validate_code_value"
		) as mock_validate:
			mock_validate.side_effect = frappe.ValidationError("Confirmation code WRONG1 is not valid")

			result = validate_confirmation_code(
				code="WRONG1",
				company="Company A",
				items='[{"item_code": "IT1", "discount_percentage": 10}]',
			)

		self.assertFalse(result["valid"])
		self.assertTrue(result["requires_code"])
		self.assertTrue(result["message"])

	@patch("pos_next.api.discount_restriction.get_applicable_restriction")
	def test_good_code_returns_valid(self, mock_get_rule):
		mock_get_rule.return_value = FakeRule(require_confirmation_code=1)
		with patch(
			"pos_next.api.discount_restriction._validate_code_value"
		) as mock_validate:
			mock_validate.return_value = "CODE-1"

			result = validate_confirmation_code(
				code="ABCD2345",
				company="Company A",
				items='[{"item_code": "IT1", "discount_percentage": 10}]',
			)

		self.assertTrue(result["valid"])
		self.assertTrue(result["requires_code"])


if __name__ == "__main__":
	unittest.main()
