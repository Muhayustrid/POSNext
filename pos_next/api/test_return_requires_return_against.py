# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-09 acceptance: a return payload must carry return_against.

A return without return_against used to skip validate_return_items entirely
(no quantity cap against the original sale) and skipped the wallet reversal /
credit note lanes. The honest UI always sends return_against
(ReturnInvoiceDialog falls back to the source invoice), so the API must
reject the payload instead of accepting an uncapped standalone return.

Run via
pos_next/_pn_run_tests.py pos_next.api.test_return_requires_return_against
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.invoice_type import POS_INVOICE

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


class TestReturnRequiresReturnAgainst(FrappeTestCase):
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
        cls.price_list = frappe.db.get_value("POS Profile", cls.profile.name, "selling_price_list")
        if not cls.price_list:
            raise unittest.SkipTest("profile has no selling price list")
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")
        # the refund-code gate (fail-closed) demands a discount code on every
        # return; these tests exercise the return_against gate, not that one —
        # toggle it off and restore afterwards (same pattern as
        # test_wallet_return_hardening)
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
        _set_invoice_type(POS_INVOICE)
        self._created = []
        self._item_prices = []
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
        self._make_item_price(100)

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in dict.fromkeys(self._created):
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
        if getattr(self, "stock_entry", None):
            se = frappe.get_doc("Stock Entry", self.stock_entry.name)
            if se.docstatus == 1:
                se.cancel()
            frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)
        frappe.db.set_value("POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False)
        frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
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

    def _make_item_price(self, rate):
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

    def _items(self, qty):
        return [
            {
                "item_code": self.item,
                "qty": qty,
                "rate": 100,
                "uom": self.uom,
                "warehouse": self.profile.warehouse,
            }
        ]

    def _payments(self):
        return [{"mode_of_payment": self.mode[0], "amount": 100}]

    def _sell_one(self):
        """A submitted original invoice of qty 1 at the server list price."""
        draft = update_invoice(
            json.dumps(
                {
                    "pos_profile": self.profile.name,
                    "posa_pos_opening_shift": self.shift.name,
                    "customer": self.customer,
                    "items": self._items(1),
                    "payments": self._payments(),
                }
            )
        )
        self._created.append(draft.get("name"))
        return submit_invoice(
            invoice={
                "name": draft.get("name"),
                "pos_profile": self.profile.name,
                "posa_pos_opening_shift": self.shift.name,
                "customer": self.customer,
                "items": self._items(1),
                "payments": self._payments(),
            }
        )

    def _return_payload(self, **extra):
        payload = {
            "pos_profile": self.profile.name,
            "posa_pos_opening_shift": self.shift.name,
            "customer": self.customer,
            "is_return": 1,
            "items": self._items(-1),
            "payments": self._payments(),
        }
        payload.update(extra)
        return payload

    def test_return_without_return_against_is_rejected(self):
        """RED: the old flow accepted the uncapped standalone return draft."""
        created = None
        try:
            created = update_invoice(json.dumps(self._return_payload()))
        except frappe.ValidationError as e:
            self.assertIn("requires a return_against", str(e))
        else:
            self.fail("return payload without return_against must be rejected")
        finally:
            if created and created.get("name"):
                self._created.append(created.get("name"))

    def test_return_with_return_against_still_saves(self):
        """The legit UI flow (always sends return_against) keeps working."""
        original = self._sell_one()
        self._created.append(original.get("name"))
        draft = update_invoice(
            json.dumps(self._return_payload(return_against=original.get("name")))
        )
        self._created.append(draft.get("name"))
        self.assertEqual(draft.get("is_return"), 1)
        self.assertEqual(draft.get("return_against"), original.get("name"))
