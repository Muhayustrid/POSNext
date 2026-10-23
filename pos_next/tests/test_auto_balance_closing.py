# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Tests for Auto-balance closing feature.

Proves:
1. Setting on + client sends closing_amount 0 -> closing has closing_amount == expected and difference 0.
2. auto_balance=True parameter forces the same behavior even when setting is off.
3. Setting off keeps client counts (e.g. closing_amount 0 gives difference -expected).
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from pos_next.api.invoices import submit_invoice
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import _submit_closing
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestAutoBalanceClosing(POSInvoiceModeMixin, FrappeTestCase):
	def setUp(self):
		super().setUp()
		old_single = frappe.db.get_single_value("POS Settings", "invoice_type")
		if old_single != "POS Invoice":
			frappe.db.set_single_value("POS Settings", "invoice_type", "POS Invoice")
			self.addCleanup(frappe.db.set_single_value, "POS Settings", "invoice_type", old_single)
	def _set_setting(self, value):
		setting_row = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile.name, "enabled": 1}, "name"
		)
		if setting_row:
			old_val = frappe.db.get_value("POS Settings", setting_row, "auto_balance_closing")
			frappe.db.set_value("POS Settings", setting_row, "auto_balance_closing", value, update_modified=False)
			self.addCleanup(
				frappe.db.set_value, "POS Settings", setting_row, "auto_balance_closing", old_val, update_modified=False
			)
		old_global = frappe.db.get_single_value("POS Next Global Settings", "auto_balance_closing")
		frappe.db.set_single_value("POS Next Global Settings", "auto_balance_closing", value)
		self.addCleanup(
			frappe.db.set_single_value, "POS Next Global Settings", "auto_balance_closing", old_global
		)

	def test_setting_on_auto_balances_closing_amounts(self):
		self._set_setting(1)
		# Submit a sale so expected_amount > 0
		submit_invoice(self._payload())

		closing_name = _submit_closing(
			self.shift.name,
			counted=[{"mode_of_payment": self.mode[0], "closing_amount": 0}],
		)
		closing = frappe.get_doc("POS Closing Shift", closing_name)
		self.closing = closing

		reconciliation = [r for r in closing.payment_reconciliation if r.mode_of_payment == self.mode[0]]
		self.assertTrue(reconciliation)
		row = reconciliation[0]
		self.assertGreater(flt(row.expected_amount), 0)
		self.assertEqual(flt(row.closing_amount), flt(row.expected_amount))
		self.assertEqual(flt(row.difference), 0)

	def test_auto_balance_param_forces_balance_when_setting_off(self):
		self._set_setting(0)
		submit_invoice(self._payload())

		closing_name = _submit_closing(
			self.shift.name,
			counted=[{"mode_of_payment": self.mode[0], "closing_amount": 0}],
			auto_balance=True,
		)
		closing = frappe.get_doc("POS Closing Shift", closing_name)
		self.closing = closing

		reconciliation = [r for r in closing.payment_reconciliation if r.mode_of_payment == self.mode[0]]
		self.assertTrue(reconciliation)
		row = reconciliation[0]
		self.assertGreater(flt(row.expected_amount), 0)
		self.assertEqual(flt(row.closing_amount), flt(row.expected_amount))
		self.assertEqual(flt(row.difference), 0)

	def test_setting_off_keeps_client_counts(self):
		self._set_setting(0)
		submit_invoice(self._payload())

		closing_name = _submit_closing(
			self.shift.name,
			counted=[{"mode_of_payment": self.mode[0], "closing_amount": 0}],
		)
		closing = frappe.get_doc("POS Closing Shift", closing_name)
		self.closing = closing

		reconciliation = [r for r in closing.payment_reconciliation if r.mode_of_payment == self.mode[0]]
		self.assertTrue(reconciliation)
		row = reconciliation[0]
		self.assertGreater(flt(row.expected_amount), 0)
		self.assertEqual(flt(row.closing_amount), 0)
		self.assertEqual(flt(row.difference), -flt(row.expected_amount))

	def test_non_manager_cannot_change_auto_balance_closing(self):
		from unittest.mock import patch

		from pos_next.pos_next.doctype.pos_settings import pos_settings as mod

		setting_name = frappe.db.get_value("POS Settings", {"pos_profile": self.profile.name})
		if not setting_name:
			doc = frappe.new_doc("POS Settings")
			doc.pos_profile = self.profile.name
			doc.enabled = 1
			doc.auto_balance_closing = 0
			doc.insert()
			setting_name = doc.name
			self.addCleanup(doc.delete)
		else:
			old_val = frappe.db.get_value("POS Settings", setting_name, "auto_balance_closing")
			frappe.db.set_value("POS Settings", setting_name, "auto_balance_closing", 0, update_modified=False)
			self.addCleanup(
				frappe.db.set_value, "POS Settings", setting_name, "auto_balance_closing", old_val, update_modified=False
			)

		with patch.object(mod, "is_management_user", return_value=False):
			mod.update_pos_settings(self.profile.name, {"auto_balance_closing": 1})

		self.assertEqual(
			frappe.db.get_value("POS Settings", setting_name, "auto_balance_closing"), 0
		)

	def test_bootstrap_preload_and_get_pos_settings_agree_on_auto_balance_closing(self):
		from pos_next.api.bootstrap import _get_pos_settings as bootstrap_get_pos_settings
		from pos_next.api.pos_profile import get_pos_settings as profile_get_pos_settings
		from pos_next.pos_next.doctype.pos_settings.pos_settings import (
			get_pos_settings as desk_get_pos_settings,
		)

		setting_name = frappe.db.get_value("POS Settings", {"pos_profile": self.profile.name})
		if not setting_name:
			doc = frappe.new_doc("POS Settings")
			doc.pos_profile = self.profile.name
			doc.enabled = 1
			doc.auto_balance_closing = 1
			doc.hide_expected_amount = 1
			doc.insert()
			self.addCleanup(doc.delete)
		else:
			old_vals = frappe.db.get_value(
				"POS Settings", setting_name, ["auto_balance_closing", "hide_expected_amount", "enabled"], as_dict=True
			)
			frappe.db.set_value(
				"POS Settings",
				setting_name,
				{"auto_balance_closing": 1, "hide_expected_amount": 1, "enabled": 1},
				update_modified=False,
			)
			self.addCleanup(
				frappe.db.set_value, "POS Settings", setting_name, old_vals, update_modified=False
			)

		profile_doc = frappe.get_doc("POS Profile", self.profile.name)
		bootstrap_settings = bootstrap_get_pos_settings(profile_doc)
		desk_settings = desk_get_pos_settings(self.profile.name)
		api_settings = profile_get_pos_settings(self.profile.name)

		self.assertEqual(bootstrap_settings.get("auto_balance_closing"), 1)
		self.assertEqual(bootstrap_settings.get("hide_expected_amount"), 1)
		self.assertEqual(api_settings.get("auto_balance_closing"), 1)
		self.assertEqual(api_settings.get("hide_expected_amount"), 1)
		self.assertEqual(desk_settings.get("auto_balance_closing"), 1)
		self.assertEqual(desk_settings.get("hide_expected_amount"), 1)

	def test_bootstrap_preload_resolved_defaults_includes_auto_balance_closing(self):
		from pos_next.api.bootstrap import _get_pos_settings as bootstrap_get_pos_settings

		setting_name = frappe.db.get_value("POS Settings", {"pos_profile": self.profile.name})
		if setting_name:
			old_enabled = frappe.db.get_value("POS Settings", setting_name, "enabled")
			frappe.db.set_value("POS Settings", setting_name, "enabled", 0, update_modified=False)
			self.addCleanup(
				frappe.db.set_value, "POS Settings", setting_name, "enabled", old_enabled, update_modified=False
			)

		old_global = frappe.db.get_single_value("POS Next Global Settings", "auto_balance_closing")
		frappe.db.set_single_value("POS Next Global Settings", "auto_balance_closing", 1)
		self.addCleanup(
			frappe.db.set_single_value, "POS Next Global Settings", "auto_balance_closing", old_global
		)

		profile_doc = frappe.get_doc("POS Profile", self.profile.name)
		bootstrap_settings = bootstrap_get_pos_settings(profile_doc)
		self.assertEqual(bootstrap_settings.get("auto_balance_closing"), 1)

