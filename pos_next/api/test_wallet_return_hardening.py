# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-03 acceptance tests: a failed wallet reversal must fail the return.

reverse_wallet_transactions_for_return used to swallow every per-row failure,
so submit_invoice flipped wallet_reversal_ok to True regardless and went on to
credit_return_to_wallet — the customer kept the old credit AND received the
refund credit (double credit). Run via
pos_next/_pn_run_tests.py pos_next.api.test_wallet_return_hardening
"""

import unittest
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate, today

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.api.wallet import get_or_create_wallet
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE
from pos_next.pos_next.doctype.wallet_transaction.wallet_transaction import (
	WalletTransaction,
	create_wallet_credit,
)

_PROFILE_FILTER = [
    ["disabled", "=", 0],
    ["pos_schedule_enforce_closing", "=", 0],
]


def _set_invoice_type(value):
    frappe.db.set_single_value("POS Next Global Settings", "invoice_type", value)
    try:
        del frappe.local._pos_next_invoice_doctype
    except AttributeError:
        pass  # not cached yet


class TestWalletReturnReversal(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.original_invoice_type = frappe.db.get_single_value(
            "POS Next Global Settings", "invoice_type"
        )
        cls.profile = frappe.db.get_value(
            "POS Profile",
            _PROFILE_FILTER,
            ["name", "company", "warehouse"],
            as_dict=True,
            order_by="creation asc",
        )
        if not cls.profile:
            raise unittest.SkipTest("no schedule-safe POS Profile")
        item = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
            pluck="name",
            limit=1,
        )
        if not item:
            raise unittest.SkipTest("no stock sales item on site")
        cls.item = item[0]
        cls.customer = frappe.db.get_value(
            "Customer", {"is_internal_customer": 0}, "name", order_by="creation asc"
        )
        if not cls.customer:
            raise unittest.SkipTest("no non-internal customer")
        cls.mode = frappe.get_all(
            "POS Payment Method",
            {"parent": cls.profile.name, "parenttype": "POS Profile"},
            pluck="mode_of_payment",
            limit=1,
        )
        if not cls.mode:
            raise unittest.SkipTest("profile has no payment methods")
        cls.wallet = get_or_create_wallet(cls.customer, cls.profile.company, force_create=True)
        if not cls.wallet:
            raise unittest.SkipTest("no wallet account configured on company")

        # the refund-code gate (fail-closed) demands a discount code on every
        # return; these tests exercise the wallet lane, not that gate — toggle
        # it off for the profile's settings row (or the global single) and
        # restore afterwards
        cls._refund_gate_row = frappe.db.get_value(
            "POS Settings", {"pos_profile": cls.profile.name, "enabled": 1}, "name"
        )
        if cls._refund_gate_row:
            cls._refund_gate_backup = frappe.db.get_value(
                "POS Settings", cls._refund_gate_row, "require_refund_code"
            )
            frappe.db.set_value(
                "POS Settings", cls._refund_gate_row, "require_refund_code", 0, update_modified=False
            )
        else:
            cls._refund_gate_backup = frappe.db.get_single_value(
                "POS Next Global Settings", "require_refund_code"
            )
            frappe.db.set_single_value("POS Next Global Settings", "require_refund_code", 0)

    @classmethod
    def tearDownClass(cls):
        if cls._refund_gate_row:
            frappe.db.set_value(
                "POS Settings",
                cls._refund_gate_row,
                "require_refund_code",
                cls._refund_gate_backup,
                update_modified=False,
            )
        else:
            frappe.db.set_single_value(
                "POS Next Global Settings", "require_refund_code", cls._refund_gate_backup
            )
        _set_invoice_type(cls.original_invoice_type or POS_INVOICE)
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        _set_invoice_type(SALES_INVOICE)
        self._created = []
        self._wallet_txns = []
        self.shift = self._make_shift()
        self._make_stock()
        self.original = self._submit_original()

    def tearDown(self):
        frappe.set_user("Administrator")
        # wallet rows first: cancelling an invoice under a linked WT complains
        for wt_name in self._wallet_txns:
            if frappe.db.exists("Wallet Transaction", wt_name):
                wt = frappe.get_doc("Wallet Transaction", wt_name)
                if wt.docstatus == 1:
                    wt.flags.ignore_permissions = True
                    wt.cancel()
                frappe.delete_doc("Wallet Transaction", wt_name, force=1, ignore_permissions=True)
        # a return that failed mid-submit is not tracked in _created; sweep it
        # BEFORE the original (return_against links block the original's cancel)
        for name in frappe.get_all(
            "Sales Invoice",
            filters={"return_against": getattr(self, "original", ""), "docstatus": ["!=", 2]},
            pluck="name",
        ):
            doc = frappe.get_doc("Sales Invoice", name)
            if doc.docstatus == 1:
                doc.flags.ignore_permissions = True
                doc.cancel()
            frappe.delete_doc("Sales Invoice", name, force=1, ignore_permissions=True)
        for name in reversed(dict.fromkeys(self._created)):
            for doctype in ("Sales Invoice", "POS Invoice"):
                if frappe.db.exists(doctype, name):
                    doc = frappe.get_doc(doctype, name)
                    if doc.docstatus == 1:
                        doc.flags.ignore_permissions = True
                        doc.cancel()
                    frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
                    break
        if getattr(self, "stock_entry", None):
            se = frappe.get_doc("Stock Entry", self.stock_entry.name)
            if se.docstatus == 1:
                se.cancel()
            frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)
        if getattr(self, "shift", None):
            frappe.db.set_value(
                "POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
            )
            frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
        frappe.db.commit()

    def _make_shift(self):
        shift = frappe.get_doc(
            {
                "doctype": "POS Opening Shift",
                "pos_profile": self.profile.name,
                "company": self.profile.company,
                "user": "Administrator",
                "posting_date": nowdate(),
                "period_start_date": frappe.utils.now_datetime(),
                "balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
            }
        )
        shift.flags.ignore_permissions = True
        shift.insert()
        shift.reload()
        shift.submit()
        return shift

    def _make_stock(self):
        se = frappe.get_doc(
            {
                "doctype": "Stock Entry",
                "stock_entry_type": "Material Receipt",
                "purpose": "Material Receipt",
                "company": self.profile.company,
                "items": [
                    {
                        "item_code": self.item,
                        "qty": 5,
                        "t_warehouse": self.profile.warehouse,
                        "allow_zero_valuation_rate": 1,
                    }
                ],
            }
        )
        se.flags.ignore_permissions = True
        se.insert()
        se.submit()
        self.stock_entry = se

    def _submit_original(self):
        result = submit_invoice(
            invoice={
                "pos_profile": self.profile.name,
                "posa_pos_opening_shift": self.shift.name,
                "customer": self.customer,
                "items": [
                    {"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
                ],
                "payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
            }
        )
        self._created.append(result["name"])
        return result["name"]

    def _credit_wallet_against_original(self, amount=50):
        wallet_name = self.wallet.name if hasattr(self.wallet, "name") else self.wallet["name"]
        wt = create_wallet_credit(
            wallet=wallet_name,
            amount=amount,
            source_type="Manual Adjustment",
            remarks="COR-BE-03 test credit",
            reference_doctype="Sales Invoice",
            reference_name=self.original,
            submit=True,
        )
        self._wallet_txns.append(wt.name)
        return wt

    def _submit_return(self, add_to_balance=1):
        result = submit_invoice(
            invoice={
                "is_return": 1,
                "return_against": self.original,
                "pos_profile": self.profile.name,
                "posa_pos_opening_shift": self.shift.name,
                "customer": self.customer,
                "items": [
                    {"item_code": self.item, "qty": -1, "rate": 100, "warehouse": self.profile.warehouse}
                ],
                "payments": [],
                "add_to_customer_balance": add_to_balance,
            }
        )
        self._created.append(result["name"])
        return result

    def test_return_fails_when_reversal_fails(self):
        """One failing reversal row must fail the whole return: no submit, no
        refund credit (the old flow credited the wallet anyway)."""
        self._credit_wallet_against_original(50)
        credit_mock = mock.patch(
            "pos_next.pos_next.doctype.wallet_transaction.wallet_transaction.credit_return_to_wallet"
        )
        cancel_mock = mock.patch.object(
            WalletTransaction, "cancel", side_effect=Exception("forced reversal failure")
        )
        with cancel_mock, credit_mock as mocked_credit:
            with self.assertRaises(frappe.ValidationError) as ctx:
                self._submit_return(add_to_balance=1)
            # OUR gate, not an unrelated validation
            self.assertIn("reversal", str(ctx.exception))
            mocked_credit.assert_not_called()

    def test_reversal_reports_per_row_results(self):
        """The wallet helper must report per-row success so callers can gate
        on it (old contract: None, failures only in the error log)."""
        from pos_next.pos_next.doctype.wallet_transaction.wallet_transaction import (
            reverse_wallet_transactions_for_return,
        )

        self._credit_wallet_against_original(50)
        return_doc = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "customer": self.customer,
                "company": self.profile.company,
                "is_return": 1,
                "return_against": self.original,
                "posa_pos_opening_shift": self.shift.name,
                "items": [{"item_code": self.item, "qty": -1, "rate": 100}],
            }
        )
        return_doc.flags.ignore_permissions = True
        return_doc.insert()
        self._created.append(return_doc.name)

        with mock.patch.object(
            WalletTransaction, "cancel", side_effect=Exception("forced reversal failure")
        ):
            results = reverse_wallet_transactions_for_return(self.original, return_doc.name)

        self.assertIsInstance(results, list)
        self.assertTrue(results, "a wallet credit exists so one reversal row must be reported")
        self.assertFalse(all(r.get("success") for r in results))

    def test_refund_credit_failure_fails_return(self):
        """A refund-credit failure must fail the whole return (old flow:
        orange alert, return still submitted, customer credit silently
        lost). One request = one DB transaction — the raise rolls the
        submit back with it."""
        self._credit_wallet_against_original(50)
        # boundary: what the raise rolls back must be exactly the request's
        # own writes, like the HTTP request handler does
        frappe.db.commit()
        with mock.patch(
            "pos_next.pos_next.doctype.wallet_transaction.wallet_transaction.credit_return_to_wallet",
            side_effect=Exception("forced refund credit failure"),
        ):
            with self.assertRaises(Exception) as ctx:
                self._submit_return(add_to_balance=1)
            self.assertIn("forced refund credit failure", str(ctx.exception))
        frappe.db.rollback()
        self.assertFalse(
            frappe.get_all(
                "Sales Invoice", {"return_against": self.original}, pluck="name"
            ),
            "no return for this original may survive a failed refund credit",
        )

    def test_successful_return_still_credits_wallet(self):
        """Guard: the fixed gate must not block honest returns — reversal
        succeeds, refund credit is created."""
        self._credit_wallet_against_original(50)
        result = self._submit_return(add_to_balance=1)
        rows = frappe.get_all(
            "Wallet Transaction",
            filters={
                "reference_doctype": "Sales Invoice",
                "reference_name": result["name"],
                "transaction_type": "Credit",
                "source_type": "Refund",
            },
            pluck="name",
        )
        for name in rows:
            self._wallet_txns.append(name)
        self.assertTrue(rows, "refund credit must exist for the return")
