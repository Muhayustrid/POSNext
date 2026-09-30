# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Legacy-field guards (audit P2): reads of posawesome-era ``posa_*`` /
``custom_*`` fields must degrade to a sane fallback on a site where the
column/field is absent, instead of raising SQL 1054.

Every test simulates the absence with mocks (``frappe.db.has_column`` False,
or meta without the field) — deterministic, no DDL required.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_legacy_column_guards
"""

import unittest
from unittest import mock

import frappe

from pos_next.api.invoices import _should_block
from pos_next.services.cash_mode import get_cash_mode_of_payment


def _pcs():
	from pos_next.pos_next.doctype.pos_closing_shift import pos_closing_shift

	return pos_closing_shift


class TestBlockSaleGuard(unittest.TestCase):
	"""posa_block_sale_beyond_available_qty: fallback 0 — never block."""

	def test_missing_column_never_blocks(self):
		with (
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.db.get_single_value", return_value=0),
		):
			self.assertFalse(_should_block("PROFILE"))

	def test_present_column_keeps_unset_means_block(self):
		with (
			mock.patch("frappe.db.has_column", return_value=True),
			mock.patch("frappe.db.get_single_value", return_value=0),
			mock.patch("frappe.db.get_value", return_value=None) as gv,
		):
			self.assertTrue(_should_block("PROFILE"))
			gv.assert_called_once()


class TestAllowDeleteGuard(unittest.TestCase):
	"""posa_allow_delete: fallback 0 — nothing is ever deletable."""

	def _doc(self):
		return _pcs().POSClosingShift(
			{"doctype": "POS Closing Shift", "pos_profile": "P", "pos_opening_shift": "S"}
		)

	def test_missing_toggle_column_deletes_nothing(self):
		doc = self._doc()
		with (
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.db.get_value") as gv,
			mock.patch("frappe.db.sql") as sql,
			mock.patch("frappe.delete_doc") as dd,
		):
			doc.delete_draft_invoices()
			gv.assert_not_called()
			sql.assert_not_called()
			dd.assert_not_called()

	def test_missing_shift_column_deletes_nothing(self):
		pcs = _pcs()
		doc = self._doc()

		def has_column(doctype, column):
			return column != "posa_pos_opening_shift"

		with (
			mock.patch.object(pcs, "get_pos_invoice_doctype", return_value="POS Invoice"),
			mock.patch("frappe.db.has_column", side_effect=has_column),
			mock.patch("frappe.db.get_value", return_value=1),
			mock.patch("frappe.db.sql") as sql,
			mock.patch("frappe.delete_doc") as dd,
		):
			doc.delete_draft_invoices()
			sql.assert_not_called()
			dd.assert_not_called()


class TestCashModeGuard(unittest.TestCase):
	"""posa_cash_mode_of_payment: absent field continues the fallback chain."""

	def test_missing_field_falls_back_to_generic_cash(self):
		with (
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.get_all", return_value=[]),
		):
			self.assertEqual(get_cash_mode_of_payment("PROFILE"), "Cash")


class TestShiftColumnGuards(unittest.TestCase):
	"""posa_is_printed / posa_pos_opening_shift on Sales Invoice: treat as
	"no legacy Sales Invoice data" — empty results, skipped lanes."""

	def test_count_pending_printed_is_zero_without_legacy_columns(self):
		pcs = _pcs()
		with (
			mock.patch.object(pcs, "get_pos_invoice_doctype", return_value="Sales Invoice"),
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.db.count") as cnt,
		):
			self.assertEqual(pcs._count_pending_printed_drafts("S"), 0)
			cnt.assert_not_called()

	def test_submit_printed_is_noop_without_legacy_columns(self):
		pcs = _pcs()
		with (
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.get_all") as ga,
		):
			pcs.submit_printed_invoices("S", "Sales Invoice")
			ga.assert_not_called()

	def test_payments_entries_empty_without_legacy_columns(self):
		pcs = _pcs()
		with (
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.db.sql") as sql,
		):
			self.assertEqual(pcs.get_payments_entries("S"), [])
			sql.assert_not_called()

	def test_cash_mode_endpoint_rejects_unassigned_user(self):
		pcs = _pcs()
		with (
			mock.patch("frappe.db.exists", return_value=False),
			mock.patch("frappe.has_permission", return_value=False),
		):
			with self.assertRaises(frappe.PermissionError):
				pcs.get_effective_cash_mode_of_payment("PROFILE")

	def test_cash_mode_endpoint_serves_assigned_user(self):
		pcs = _pcs()
		with (
			mock.patch("frappe.db.exists", return_value=True),
			mock.patch.object(pcs, "_resolve_cash_mode", return_value="Cash PKU"),
		):
			self.assertEqual(pcs.get_effective_cash_mode_of_payment("PROFILE"), "Cash PKU")


class TestBrandsFieldGuard(unittest.TestCase):
	"""custom_brands_table (Table field — never a parent column): guard on
	meta.has_field, fallback = skip rows, UI hides brands."""

	def test_update_skips_brand_rows_without_the_field(self):
		from pos_next.api.pos_profile import update_pos_profile

		appended = []
		doc = frappe._dict(name="P", save=lambda: None)
		doc.append = lambda key, value: appended.append(key)
		meta = mock.Mock()
		meta.has_field = lambda f: f != "custom_brands_table"
		with (
			mock.patch("frappe.get_meta", return_value=meta),
			mock.patch("frappe.get_doc", return_value=doc),
			mock.patch(
				"pos_next.api.pos_profile._parse_list_parameter", side_effect=lambda value, name: value
			),
		):
			update_pos_profile(pos_profile_name="P", brands=[{"brand": "B"}])
		self.assertNotIn("custom_brands_table", appended)


class TestBankDepositGuard(unittest.TestCase):
	"""custom_bank_deposit: fallback 0/None — no bank-deposit link exists."""

	def test_report_skips_without_legacy_column(self):
		from pos_next.pos_next.report.payments_and_cash_control_report.payments_and_cash_control_report import (
			_get_bank_deposit_data,
		)

		with (
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.db.sql") as sql,
		):
			self.assertEqual(_get_bank_deposit_data([frappe._dict(shift="S")]), {})
			sql.assert_not_called()

	def test_deposit_submit_skips_without_legacy_column(self):
		from pos_next.pos_next.doctype.bank_deposits.bank_deposits import BankDeposits

		doc = BankDeposits({"doctype": "Bank Deposits", "pos_closing_shift": "C", "name": "B"})
		with (
			mock.patch("frappe.db.has_column", return_value=False),
			mock.patch("frappe.db.set_value") as sv,
		):
			doc.on_submit()
			sv.assert_not_called()


class TestSettingsResolverUnknownField(unittest.TestCase):
	"""B1: a fieldname that is not a POS Settings column resolves through the
	global tier instead of raising SQL 1054."""

	def test_unknown_field_resolves_without_sql_1054(self):
		from pos_next.api import settings_resolver

		self.assertIsNone(
			settings_resolver.get_effective_pos_setting("NO-SUCH-PROFILE", "not_a_real_column")
		)
		settings = settings_resolver.get_effective_pos_settings(
			"NO-SUCH-PROFILE", ["invoice_type", "not_a_real_column"]
		)
		self.assertEqual(settings["enabled"], 1)
		self.assertIsNone(settings["not_a_real_column"])
		self.assertIn("invoice_type", settings)


class TestBrandingAlertDedupe(unittest.TestCase):
	"""B3: a persistent condition must not re-notify every hourly run — one
	alert per issue kind per day (redis marker)."""

	def test_persistent_condition_notifies_once(self):
		from pos_next.tasks import branding_monitor

		cache = {}
		single = frappe._dict(
			enabled=1, tampering_attempts=60, encrypted_signature=None, name="BW", last_validation=None
		)
		fake_cache = mock.Mock(
			get_value=lambda key: cache.get(key),
			set_value=lambda key, value, expires_in_sec=None: cache.__setitem__(key, value),
		)
		logged = []
		with (
			mock.patch("frappe.db.exists", return_value=True),
			mock.patch("frappe.get_single", return_value=single),
			mock.patch("frappe.cache", return_value=fake_cache),
			mock.patch.object(branding_monitor, "send_tampering_alert") as alert,
			mock.patch.object(
				branding_monitor.frappe, "log_error", side_effect=lambda **kwargs: logged.append(kwargs)
			),
		):
			branding_monitor.monitor_branding_integrity()
			branding_monitor.monitor_branding_integrity()

		self.assertEqual(alert.call_count, 1)
		self.assertEqual(len(logged), 1)
		self.assertIn("bw-branding-alert:tampering", cache)
		self.assertIn("bw-branding-alert:signature", cache)
