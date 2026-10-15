# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-13 acceptance: _set_payment_accounts must not swallow errors.

get_payment_account() ends in frappe.throw when no cash/bank account can be
resolved for a mode of payment. The old blanket ``except Exception`` in
_set_payment_accounts logged and swallowed that throw, so a payment row was
left without an account and the payment JE could post to the wrong account
silently. Configuration errors from the resolver must surface to the caller.

Run via pos_next/_pn_run_tests.py pos_next.api.test_payment_account_error
"""

import unittest
from unittest import mock

import frappe

from pos_next.api.invoices import _set_payment_accounts


class TestSetPaymentAccountsErrorPropagation(unittest.TestCase):
    def test_validation_error_from_get_payment_account_propagates(self):
        """RED: the old blanket except swallowed the resolver's throw."""
        payments = [{"mode_of_payment": "Cash", "amount": 100}]
        with mock.patch(
            "pos_next.api.invoices.get_payment_account",
            side_effect=frappe.ValidationError("Please set default Cash or Bank account"),
        ):
            with self.assertRaises(frappe.ValidationError):
                _set_payment_accounts(payments, "Test Company")

    def test_account_still_set_on_success(self):
        payments = [{"mode_of_payment": "Cash", "amount": 100}]
        with mock.patch(
            "pos_next.api.invoices.get_payment_account",
            return_value={"account": "Cash - TC"},
        ):
            _set_payment_accounts(payments, "Test Company")
        self.assertEqual(payments[0].get("account"), "Cash - TC")

    def test_rows_with_account_are_untouched(self):
        payments = [{"mode_of_payment": "Cash", "amount": 100, "account": "Preset - TC"}]
        with mock.patch("pos_next.api.invoices.get_payment_account") as resolver:
            _set_payment_accounts(payments, "Test Company")
        resolver.assert_not_called()
        self.assertEqual(payments[0].get("account"), "Preset - TC")


class TestGetPaymentAccountFromModeOfPayment(unittest.TestCase):
    def test_mapped_mode_of_payment_uses_its_account(self):
        from pos_next.api.invoices import get_payment_account

        row = frappe.db.get_value(
            "Mode of Payment Account",
            {"default_account": ["is", "set"]},
            ["parent", "company", "default_account"],
            as_dict=True,
        )
        result = get_payment_account(row.parent, row.company)
        self.assertEqual(result["account"], row.default_account)

    def test_unmapped_mode_of_payment_raises_instead_of_guessing(self):
        """RED: an unmapped mode silently fell back to a Company cash/bank
        account, so the payment posted to an account the mode never named."""
        from pos_next.api.invoices import get_payment_account

        company = frappe.db.get_value("Company", {"name": ["like", "_Test Company"]}, "name")
        with self.assertRaises(frappe.ValidationError):
            get_payment_account("_PNXT Unmapped Wallet", company)
