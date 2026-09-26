# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Two-persona cashier checkout acceptance.

A cashier user (POSNext Cashier + Stock User, no accounting roles) must be able
to drive the checkout path end to end. Two server-side spots broke this:

1. update_invoice → POS Invoice.set_missing_values → get_party_account()
   runs an Account select/read permission check (account_perm_check) that the
   cashier cannot pass. debit_to is a server-managed field the cashier never
   chooses, so the app must resolve it with permission-safe reads before
   set_missing_values runs.

2. get_sales_persons returned an empty list for the cashier because
   frappe.get_list enforced Sales Person read permission. The endpoint is a
   public name/commission dropdown, so it must read with ignore_permissions.

Run via pos_next/_pn_run_tests.py pos_next.api.test_cashier_checkout_permissions
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import update_invoice
from pos_next.api.pos_profile import get_sales_persons
from pos_next.invoice_type import POS_INVOICE, get_pos_invoice_doctype

CASHIER = "kasir.checkout@pnxt.test"
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


class TestCashierCheckoutPermissions(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.original_invoice_type = frappe.db.get_single_value(
            "POS Next Global Settings", "invoice_type"
        )
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
        frappe.set_user("Administrator")
        cls._make_cashier_user()
        cls._grant_profile_access()
        item = frappe.get_all(
            "Item",
            filters={
                "disabled": 0,
                "is_sales_item": 1,
                "is_stock_item": 1,
                "name": ["not like", "\\_PNXT\\_%"],
            },
            pluck="name",
            limit=1,
            order_by="creation asc",
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
        frappe.set_user("Administrator")
        _set_invoice_type(cls.original_invoice_type or POS_INVOICE)
        if getattr(cls, "sales_person", None) and frappe.db.exists(
            "Sales Person", cls.sales_person
        ):
            frappe.delete_doc("Sales Person", cls.sales_person, force=1, ignore_permissions=True)
        if frappe.db.exists("User", CASHIER):
            profile_doc = frappe.get_doc("POS Profile", cls.profile.name)
            profile_doc.set(
                "applicable_for_users",
                [r for r in profile_doc.get("applicable_for_users") or [] if r.user != CASHIER],
            )
            profile_doc.save(ignore_permissions=True)
            frappe.delete_doc("User", CASHIER, force=1, ignore_permissions=True)
        frappe.db.commit()
        super().tearDownClass()

    @classmethod
    def _make_cashier_user(cls):
        if frappe.db.exists("User", CASHIER):
            return
        user = frappe.new_doc("User")
        user.email = CASHIER
        user.first_name = "Kasir Checkout Fixture"
        user.enabled = 1
        user.send_welcome_email = 0
        user.user_type = "System User"
        user.flags.ignore_permissions = True
        user.insert()
        user.add_roles("POSNext Cashier")
        user.add_roles("Stock User")
        user.save(ignore_permissions=True)

    @classmethod
    def _grant_profile_access(cls):
        profile_doc = frappe.get_doc("POS Profile", cls.profile.name)
        if any(r.user == CASHIER for r in profile_doc.get("applicable_for_users") or []):
            return
        profile_doc.append("applicable_for_users", {"user": CASHIER, "default": 1})
        profile_doc.save(ignore_permissions=True)
        frappe.db.commit()

    @classmethod
    def _make_sales_person(cls):
        existing = frappe.db.get_value(
            "Sales Person",
            {"enabled": 1, "is_group": 0},
            "name",
            order_by="creation asc",
        )
        if existing:
            cls.sales_person = None  # pre-existing data, do not delete
            return existing
        doc = frappe.new_doc("Sales Person")
        doc.sales_person_name = "Kasir Checkout SP"
        doc.enabled = 1
        doc.is_group = 0
        doc.flags.ignore_permissions = True
        doc.insert()
        cls.sales_person = doc.name
        frappe.db.commit()
        return doc.name

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
                "user": CASHIER,
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
        frappe.set_user(CASHIER)

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

    def test_cashier_can_save_draft_via_update_invoice(self):
        """The whole checkout blocks here: the client saves the draft through
        update_invoice on every cart change before it can submit anything."""
        result = update_invoice(json.dumps(self._payload()))
        name = result.get("name") if isinstance(result, dict) else result
        self.assertTrue(name)
        self._created.append(name)
        doc = frappe.get_doc(self.doctype, name)
        self.assertEqual(doc.docstatus, 0)
        self.assertTrue(doc.debit_to, "server must resolve debit_to for the cashier")

    def test_cashier_can_list_sales_persons(self):
        """The salesperson dropdown silently returned [] for cashiers."""
        seeded = self._make_sales_person()
        result = get_sales_persons(self.profile.name)
        self.assertIsInstance(result, list)
        self.assertTrue(result, "cashier must see the sales person list")
        self.assertIn(seeded, [r.get("name") for r in result])
