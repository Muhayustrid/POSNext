# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Audit-trail acceptance tests for manual rate edits (log_manual_rate_edit).

The audit lane in submit_invoice used to fire for Sales Invoice only, so a
manual rate on a POS Invoice (the default mode) was never logged — and the
Comment's reference_doctype was hardcoded to "Sales Invoice" besides. Both
must follow the resolved invoice doctype: a manual rate on a POS Invoice
leaves a Comment with reference_doctype="POS Invoice" on that invoice, the
same holds for Sales Invoice, and no flag/no change leaves no comment.

Run via
pos_next/_pn_run_tests.py pos_next.api.test_manual_rate_audit
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE

# same schedule-safe profile filter as test_draft_submit_rate_gate
_PROFILE_FILTER = [
    ["disabled", "=", 0],
    ["pos_schedule_enforce_closing", "=", 0],
]

_MANUAL_EDIT_CONTENT = "%Manual rate edit%"


def _set_invoice_type(value):
    """Flip the site switch past the switch guard (shared dev site holds real
    open shifts; see test_draft_submit_rate_gate._set_invoice_type)."""
    frappe.db.set_single_value("POS Next Global Settings", "invoice_type", value)
    try:
        del frappe.local._pos_next_invoice_doctype
    except AttributeError:
        pass  # not cached yet


def _cancel_wallet_transactions_for(invoice_names):
    """Cancel + delete Wallet Transactions referencing these invoices.

    The loyalty-to-wallet conversion mints a WT per submitted invoice on this
    site, and cancelling an invoice under a linked WT throws LinkExistsError.
    Same sweep as api/test_draft_submit_rate_gate.py."""
    names = list(dict.fromkeys(n for n in invoice_names if n))
    if not names:
        return
    for wt_name in frappe.get_all(
        "Wallet Transaction",
        filters={
            "reference_doctype": ("in", (SALES_INVOICE, POS_INVOICE)),
            "reference_name": ("in", names),
            "docstatus": ("!=", 2),
        },
        pluck="name",
    ):
        wt = frappe.get_doc("Wallet Transaction", wt_name)
        if wt.docstatus == 1:
            wt.flags.ignore_permissions = True
            wt.cancel()
        frappe.delete_doc("Wallet Transaction", wt_name, force=1, ignore_permissions=True)


class TestManualRateAudit(FrappeTestCase):
    """A manual rate edit must leave exactly one audit Comment on the
    submitted invoice, referenced with the invoice's own doctype and name."""

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
            filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1, "has_batch_no": 0, "has_serial_no": 0},
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
        cls.price_list = frappe.db.get_value("POS Profile", cls.profile.name, "selling_price_list")
        if not cls.price_list:
            raise unittest.SkipTest("profile has no selling price list")
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        # the audit lane is gated on the same POS Settings mirror the rate
        # gates use; snapshot the profile's row so tests can toggle it
        cls._settings_row = frappe.db.get_value(
            "POS Settings", {"pos_profile": cls.profile.name, "enabled": 1}, "name"
        )
        if cls._settings_row:
            cls._settings_backup = frappe.db.get_value(
                "POS Settings",
                cls._settings_row,
                ["allow_user_to_edit_rate", "max_discount_allowed"],
                as_dict=True,
            )

    @classmethod
    def tearDownClass(cls):
        if cls._settings_row and getattr(cls, "_settings_backup", None):
            frappe.db.set_value(
                "POS Settings",
                cls._settings_row,
                {
                    "allow_user_to_edit_rate": cls._settings_backup.allow_user_to_edit_rate,
                    "max_discount_allowed": cls._settings_backup.max_discount_allowed,
                },
                update_modified=False,
            )
        _set_invoice_type(cls.original_invoice_type or POS_INVOICE)
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        self._created = []
        self._item_prices = []
        self._set_rate_settings(allow_edit=1, max_discount=0)
        self._make_discount_code()
        self.shift = frappe.get_doc(
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
        self.shift.flags.ignore_permissions = True
        self.shift.insert()
        self.shift.reload()
        self.shift.submit()
        self._make_stock()
        # deterministic server list price: latest valid_from wins in the
        # UOM price map, so today's row overrides whatever the site had
        self._make_item_price(100)

    def tearDown(self):
        frappe.set_user("Administrator")
        _cancel_wallet_transactions_for(self._created)
        for name in dict.fromkeys(self._created):
            # audit Comments are linked by reference, not by cascade — sweep
            # them so a cancelled/deleted invoice leaves no dangling comment
            frappe.db.delete(
                "Comment",
                {
                    "reference_doctype": ("in", (SALES_INVOICE, POS_INVOICE)),
                    "reference_name": name,
                },
            )
            for doctype in ("POS Invoice", "Sales Invoice"):
                if frappe.db.exists(doctype, name):
                    doc = frappe.get_doc(doctype, name)
                    if doc.docstatus == 1:
                        doc.flags.ignore_permissions = True
                        doc.cancel()
                    frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
                    break
        for name in self._item_prices:
            if frappe.db.exists("Item Price", name):
                frappe.delete_doc("Item Price", name, force=1, ignore_permissions=True)
        if getattr(self, "discount_code", None):
            frappe.delete_doc(
                "POS Discount Confirmation Code", self.discount_code, force=1, ignore_permissions=True
            )
        if getattr(self, "stock_entry", None):
            se = frappe.get_doc("Stock Entry", self.stock_entry.name)
            if se.docstatus == 1:
                se.cancel()
            frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)
        frappe.db.set_value(
            "POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
        )
        frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
        frappe.db.commit()

    def _set_rate_settings(self, allow_edit, max_discount):
        """Rate editing on, cap off (0 = uncapped) so an honest 20% manual
        reduction passes the gate and only the audit lane is under test."""
        if self._settings_row:
            frappe.db.set_value(
                "POS Settings",
                self._settings_row,
                {"allow_user_to_edit_rate": allow_edit, "max_discount_allowed": max_discount},
                update_modified=False,
            )
        else:
            self.skipTest("profile has no enabled POS Settings row to toggle")
        frappe.db.commit()

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
                        "qty": 9,
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

    def _make_discount_code(self):
        """An active head-office code: a manual rate BELOW list price is a
        discount in disguise, so the discount-code gate requires one."""
        doc = frappe.get_doc(
            {
                "doctype": "POS Discount Confirmation Code",
                "status": "Active",
                "company_scope": "All Outlets",
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert()
        self.discount_code = doc.code
        self._discount_code_name = doc.name

    def _make_item_price(self, rate):
        existing = frappe.db.get_value(
            "Item Price",
            {
                "item_code": self.item,
                "price_list": self.price_list,
                "selling": 1,
                "valid_from": "2020-01-01",
            },
            "name",
        )
        if existing:
            self._item_prices.append(existing)
            return
        doc = frappe.get_doc(
            {
                "doctype": "Item Price",
                "item_code": self.item,
                "price_list": self.price_list,
                "selling": 1,
                "buying": 0,
                "valid_from": "2020-01-01",
                "price_list_rate": rate,
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert()
        self._item_prices.append(doc.name)

    def _submit(self, rate, price_list_rate, manual_flag, with_code=True):
        """One-step checkout: list price 100 server-side, pay cash. A manual
        reduction rides the active head-office code (the discount-code gate
        runs on draft save and submit for a rate below list price)."""
        payload = {
            "pos_profile": self.profile.name,
            "posa_pos_opening_shift": self.shift.name,
            "customer": self.customer,
            "items": [
                {
                    "item_code": self.item,
                    "qty": 1,
                    "rate": rate,
                    "price_list_rate": price_list_rate,
                    "is_rate_manually_edited": manual_flag,
                    "uom": self.uom,
                    "warehouse": self.profile.warehouse,
                }
            ],
            "payments": [{"mode_of_payment": self.mode[0], "amount": rate}],
        }
        if with_code:
            payload["discount_confirmation_code"] = self.discount_code
        result = submit_invoice(invoice=payload)
        self._created.append(result.get("name"))
        return result

    def _audit_comments(self, invoice_name, reference_doctype):
        return frappe.get_all(
            "Comment",
            filters={
                "comment_type": "Comment",
                "reference_doctype": reference_doctype,
                "reference_name": invoice_name,
                "content": ("like", _MANUAL_EDIT_CONTENT),
            },
            fields=["content"],
        )

    def test_pos_invoice_manual_rate_creates_audit_comment(self):
        """Manual rate (100 -> 80) on a POS Invoice: exactly one audit Comment
        on THAT invoice, with reference_doctype="POS Invoice" — never the old
        hardcoded "Sales Invoice" reference."""
        _set_invoice_type(POS_INVOICE)
        result = self._submit(rate=80, price_list_rate=100, manual_flag=1)
        name = result.get("name")
        self.assertEqual(result.get("status"), 1)

        comments = self._audit_comments(name, POS_INVOICE)
        self.assertEqual(
            len(comments),
            1,
            "exactly one manual-rate audit Comment must sit on the POS Invoice",
        )
        self.assertIn(self.item, comments[0].content)
        self.assertFalse(
            self._audit_comments(name, SALES_INVOICE),
            "the audit Comment must not be mis-referenced to Sales Invoice",
        )

    def test_sales_invoice_manual_rate_creates_audit_comment(self):
        """Sales Invoice keeps its audit trail: same manual rate, Comment
        referenced with reference_doctype="Sales Invoice"."""
        _set_invoice_type(SALES_INVOICE)
        result = self._submit(rate=80, price_list_rate=100, manual_flag=1)
        name = result.get("name")
        self.assertEqual(result.get("status"), 1)

        comments = self._audit_comments(name, SALES_INVOICE)
        self.assertEqual(
            len(comments),
            1,
            "exactly one manual-rate audit Comment must sit on the Sales Invoice",
        )
        self.assertIn(self.item, comments[0].content)
        self.assertFalse(
            self._audit_comments(name, POS_INVOICE),
            "the audit Comment must not be mis-referenced to POS Invoice",
        )

    def test_honest_rate_leaves_no_audit_comment(self):
        """No manual-edit flag (and no rate change): submit succeeds with no
        audit Comment on the invoice under either reference doctype."""
        _set_invoice_type(POS_INVOICE)
        result = self._submit(rate=100, price_list_rate=100, manual_flag=0)
        name = result.get("name")
        self.assertEqual(result.get("status"), 1)
        self.assertFalse(self._audit_comments(name, POS_INVOICE))
        self.assertFalse(self._audit_comments(name, SALES_INVOICE))
