# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the shared confirmation-code controller helper.

Mocked-frappe style (same as test_discount_code.py::TestControllerValidate) —
no database needed; run via
pos_next/_pn_run_tests.py pos_next.api.test_confirmation_code
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from pos_next.pos_next.doctype.pos_refund_code.pos_refund_code import POSRefundCode
from pos_next.services.confirmation_code import (
	CODE_ALPHABET,
	CODE_LENGTH,
	generate_codes_for,
	prepare_code,
)

SVC_PATH = "pos_next.services.confirmation_code"
SVC_DB_PATCH = patch(f"{SVC_PATH}.frappe.db", new_callable=MagicMock)
SVC_PERM_PATCH = patch(f"{SVC_PATH}.frappe.has_permission")


def _refund_doc(**fields):
	doc = object.__new__(POSRefundCode)
	doc.__dict__.update(fields)
	doc.doctype = "POS Refund Code"
	# defaults matching the doctype (explicit values win)
	doc.valid_from = fields.get("valid_from")
	doc.valid_upto = fields.get("valid_upto")
	doc.company_scope = fields.get("company_scope", "All Outlets")
	doc.companies = fields.get("companies", [])
	return doc


class TestPrepareCode(unittest.TestCase):
	def test_code_is_normalized_to_upper_case(self):
		doc = _refund_doc(code="messi123", used_count=0, __islocal=1)
		with SVC_DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			doc.validate()

		self.assertEqual(doc.code, "MESSI123")

	def test_too_short_manual_code_rejected(self):
		doc = _refund_doc(code="ab1", used_count=0, __islocal=1)
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_spaces_and_symbols_rejected(self):
		for bad in ("messi 123", "messi-123"):
			doc = _refund_doc(code=bad, used_count=0, __islocal=1)
			with self.assertRaises(frappe.ValidationError):
				doc.validate()

	def test_over_twenty_characters_rejected(self):
		doc = _refund_doc(code="A" * 21, used_count=0, __islocal=1)
		with self.assertRaises(frappe.ValidationError):
			doc.validate()

	def test_duplicate_code_rejected_with_code_in_message(self):
		doc = _refund_doc(code="MESSI123", used_count=0, __islocal=1)
		with SVC_DB_PATCH as mock_db:
			mock_db.exists.return_value = True
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.validate()

		self.assertIn("MESSI123", str(ctx.exception))

	def test_blank_code_is_auto_generated(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.exists.return_value = False

			doc = _refund_doc(code="", used_count=0, __islocal=1)
			doc.validate()

		self.assertEqual(len(doc.code), CODE_LENGTH)

	def test_generated_code_uses_safe_alphabet(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.exists.return_value = False

			doc = _refund_doc(code="", used_count=0, __islocal=1)
			doc.validate()

		self.assertTrue(set(doc.code) <= set(CODE_ALPHABET))

	def test_used_code_value_cannot_change(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.get_value.return_value = "ABCD2345"
			mock_db.exists.return_value = False
			doc = _refund_doc(code="ZZZZ9999", used_count=2, name="CODE-1")
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.validate()

		self.assertIn("already been used", str(ctx.exception))

	def test_used_code_can_be_disabled_without_value_change(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.get_value.return_value = "ABCD2345"
			mock_db.exists.return_value = False
			doc = _refund_doc(code="ABCD2345", used_count=2, name="CODE-1", status="Disabled")
			doc.validate()  # must not raise

	def test_valid_upto_before_valid_from_raises(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			doc = _refund_doc(
				code="ABCD2345", used_count=0, __islocal=1, valid_from="2026-09-10", valid_upto="2026-09-01"
			)
			with self.assertRaises(frappe.ValidationError):
				doc.validate()

	def test_scope_without_outlets_raises(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			for scope in ("Selected Outlets", "All Outlets Except"):
				doc = _refund_doc(
					code="ABCD2345", used_count=0, __islocal=1, company_scope=scope, companies=[]
				)
				with self.assertRaises(frappe.ValidationError):
					doc.validate()

	def test_scope_with_outlet_passes(self):
		doc = _refund_doc(
			code="ABCD2345",
			used_count=0,
			__islocal=1,
			company_scope="Selected Outlets",
			companies=[{"company": "Company A"}],
		)
		with SVC_DB_PATCH as mock_db:
			mock_db.exists.return_value = False
			doc.validate()  # must not raise

	def test_legacy_unchanged_short_code_passes(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.get_value.return_value = "AB2"
			mock_db.exists.return_value = False
			doc = _refund_doc(code="AB2", used_count=0, name="CODE-1", status="Disabled")
			doc.validate()  # must not raise

	def test_legacy_short_code_value_change_rejected(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.get_value.return_value = "AB2"
			mock_db.exists.return_value = False
			doc = _refund_doc(code="CD3", used_count=0, name="CODE-1")
			with self.assertRaises(frappe.ValidationError):
				doc.validate()

	def test_new_short_code_rejected(self):
		with SVC_DB_PATCH as mock_db:
			mock_db.get_value.return_value = None
			mock_db.exists.return_value = False
			doc = _refund_doc(code="AB2", used_count=0, __islocal=1)
			with self.assertRaises(frappe.ValidationError):
				doc.validate()


class TestGenerateCodesFor(unittest.TestCase):
	def test_requires_create_permission(self):
		with SVC_PERM_PATCH as mock_perm:
			mock_perm.side_effect = frappe.PermissionError("No permission")
			with self.assertRaises(frappe.PermissionError):
				generate_codes_for("POS Refund Code", 1)

	def test_count_bounds_are_enforced(self):
		with SVC_PERM_PATCH:
			for bad in (0, 501):
				with self.assertRaises(frappe.ValidationError):
					generate_codes_for("POS Refund Code", bad)


class TestRefundGenerateCodes(unittest.TestCase):
	def test_delegates_to_helper_with_refund_doctype(self):
		from pos_next.pos_next.doctype.pos_refund_code import pos_refund_code

		with patch(
			"pos_next.pos_next.doctype.pos_refund_code.pos_refund_code.generate_codes_for"
		) as mock_gen:
			mock_gen.return_value = {"codes": ["ABCD2345"]}
			result = pos_refund_code.generate_codes(count=2, company=" Company A ", notes=" Lebaran ")

		self.assertEqual(result, {"codes": ["ABCD2345"]})
		mock_gen.assert_called_once_with("POS Refund Code", 2, " Company A ", " Lebaran ")


if __name__ == "__main__":
	unittest.main()
