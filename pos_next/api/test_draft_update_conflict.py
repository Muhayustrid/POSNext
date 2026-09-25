# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-11 acceptance: a stale draft update must be rejected with HTTP 409.

Two devices editing the same draft used to be last-writer-wins: a payload
carrying an older `modified` silently overwrote the other device's edits.
When the payload carries `modified` and the DB value is newer, update_invoice
must reject with a ValidationError whose http_status_code is 409. The same
modified value is an idempotent retry and must pass. Payloads without
`modified` (what the honest UI sends) are not compared.

Run via pos_next/_pn_run_tests.py pos_next.api.test_draft_update_conflict
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from pos_next.api.invoices import update_invoice
from pos_next.invoice_type import POS_INVOICE, get_pos_invoice_doctype

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


class TestDraftUpdateConflict(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.original_invoice_type = frappe.db.get_single_value(
            "POS Next Global Settings", "invoice_type"
        )
        # resolve the doctype under the same forced mode setUp uses: a module
        # that leaked Sales mode must not pin cls.doctype to Sales Invoice
        # while every draft below is created as a POS Invoice
        _set_invoice_type(POS_INVOICE)
        cls.doctype = get_pos_invoice_doctype()
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

    @classmethod
    def tearDownClass(cls):
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
                "posting_date": frappe.utils.nowdate(),
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

    def _payload(self, name=None):
        payload = {
            "pos_profile": self.profile.name,
            "posa_pos_opening_shift": self.shift.name,
            "customer": self.customer,
            "items": [
                {
                    "item_code": self.item,
                    "qty": 1,
                    "rate": 100,
                    "uom": self.uom,
                    "warehouse": self.profile.warehouse,
                }
            ],
            "payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
        }
        if name:
            payload["name"] = name
        return payload

    def _create_draft(self):
        created = update_invoice(json.dumps(self._payload()))
        name = created.get("name")
        self._created.append(name)
        return name

    def test_stale_modified_update_rejected_with_409(self):
        """RED: the old flow let the stale payload overwrite the draft."""
        draft = self._create_draft()
        db_modified = frappe.db.get_value(self.doctype, draft, "modified")
        stale = frappe.utils.add_to_date(get_datetime(db_modified), seconds=-60).__str__()
        with self.assertRaises(frappe.ValidationError) as ctx:
            update_invoice(json.dumps({**self._payload(draft), "modified": stale}))
        self.assertEqual(getattr(ctx.exception, "http_status_code", None), 409)

    def test_same_modified_update_is_idempotent_retry(self):
        draft = self._create_draft()
        db_modified = str(frappe.db.get_value(self.doctype, draft, "modified"))
        result = update_invoice(json.dumps({**self._payload(draft), "modified": db_modified}))
        self.assertEqual(result.get("name"), draft)

    def test_payload_without_modified_still_saves(self):
        draft = self._create_draft()
        result = update_invoice(json.dumps(self._payload(draft)))
        self.assertEqual(result.get("name"), draft)
