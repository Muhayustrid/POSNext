# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-02 acceptance tests: the existing-draft branch of submit_invoice
must run the same price verification loop as update_invoice.

A crafted submit could carry rate == price_list_rate straight into
invoice_doc.update(invoice) -> save() -> submit(), slipping an in-discount
past the discount-code gate / max_discount_allowed (an extension of the
SEC-04 hole). Run via
pos_next/_pn_run_tests.py pos_next.api.test_draft_submit_rate_gate
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE

# same schedule-safe profile filter as test_pos_invoice_submit
_PROFILE_FILTER = [
    ["disabled", "=", 0],
    ["pos_schedule_enforce_closing", "=", 0],
]


def _set_invoice_type(value):
    """Flip the site switch past the switch guard (shared dev site holds real
    open shifts; see test_pos_invoice_submit._set_invoice_type)."""
    frappe.db.set_single_value("POS Next Global Settings", "invoice_type", value)
    try:
        del frappe.local._pos_next_invoice_doctype
    except AttributeError:
        pass  # not cached yet


class TestDraftSubmitRateGate(FrappeTestCase):
    """Submit of an existing draft must re-verify every item rate server-side."""

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
            "Customer",
            {"customer_name": "_Test Customer", "name": ["!=", ""]},
            "name",
            order_by="creation asc",
        ) or frappe.db.get_value(
            "Customer", {"is_internal_customer": 0}, "name", order_by="creation asc"
        )
        if not cls.customer:
            raise unittest.SkipTest("no customer on site")
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
        # the cart carries each row's uom (posCart.js), which is what the
        # server-side UOM price lookup keys on
        cls.uom = frappe.db.get_value("Item", cls.item, "stock_uom")

        # snapshot the profile's POS Settings mirror fields we toggle per test
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
        # deterministic server list price: latest valid_from wins in the
        # UOM price map, so today's row overrides whatever the site had
        self._make_item_price(100)

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in dict.fromkeys(self._created):
            for doctype in ("POS Invoice", "Sales Invoice"):
                if frappe.db.exists(doctype, name):
                    doc = frappe.get_doc(doctype, name)
                    if doc.docstatus == 1:
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
        frappe.db.set_value(
            "POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
        )
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

    def _set_rate_settings(self, allow_edit, max_discount):
        """Toggle the two POS Settings mirror fields on the profile's row."""
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

    def _create_draft(self, rate=100):
        created = update_invoice(
            json.dumps(
                {
                    "pos_profile": self.profile.name,
                    "posa_pos_opening_shift": self.shift.name,
                    "customer": self.customer,
                    "items": [
                        {
                            "item_code": self.item,
                            "qty": 1,
                            "rate": rate,
                            "uom": self.uom,
                            "warehouse": self.profile.warehouse,
                        }
                    ],
                    "payments": [{"mode_of_payment": self.mode[0], "amount": rate}],
                }
            )
        )
        name = created.get("name")
        self._created.append(name)
        return name

    def _submit_existing(self, draft_name, rate, price_list_rate=None, manual_flag=0, **invoice_extra):
        """Step-2 submit against an EXISTING draft: the branch that used to
        skip price verification entirely. price_list_rate defaults to rate —
        the crafted payload forges the list price itself so nothing downstream
        can see a discount. invoice_extra rides invoice-level fields (e.g. a
        forged selling_price_list)."""
        if price_list_rate is None:
            price_list_rate = rate
        payload = {
            "name": draft_name,
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
        payload.update(invoice_extra)
        return submit_invoice(invoice=payload)

    def test_forged_list_price_submit_throws_rate_edit_gate(self):
        """Server Item Price 100, draft parked at 100, submit sneaks
        rate=50 WITH price_list_rate=50 (list price forged, no visible
        discount): rate editing disabled -> the submit must throw OUR gate."""
        self._set_rate_settings(allow_edit=0, max_discount=0)
        draft = self._create_draft(rate=100)
        with self.assertRaises(frappe.ValidationError) as ctx:
            self._submit_existing(draft, rate=50)
        self.assertIn("Rate editing is not allowed", str(ctx.exception))
        self._created = [n for n in self._created if n != draft]
        self.assertFalse(
            frappe.db.exists("POS Invoice", {"name": draft, "docstatus": 1}),
            "manipulated draft must not end up submitted",
        )

    def test_forged_selling_price_list_cannot_disable_server_basis(self):
        """Review MAJOR 1: the submit branch must resolve the server price
        from the POS Profile's price list. selling_price_list rides the
        payload unstripped — forging it empty must not turn the whole server
        basis off (the old fallback made server_plr 0 = gates skipped)."""
        self._set_rate_settings(allow_edit=0, max_discount=0)
        draft = self._create_draft(rate=100)
        with self.assertRaises(frappe.ValidationError) as ctx:
            self._submit_existing(draft, rate=50, selling_price_list="")
        self.assertIn("Rate editing is not allowed", str(ctx.exception))
        self._created = [n for n in self._created if n != draft]
        self.assertFalse(
            frappe.db.exists("POS Invoice", {"name": draft, "docstatus": 1})
        )

    def test_over_max_discount_submit_throws(self):
        """Rate editing allowed but capped at 5%: a 50% in-discount (forged
        list price) on submit must throw the max-discount gate."""
        self._set_rate_settings(allow_edit=1, max_discount=5)
        draft = self._create_draft(rate=100)
        with self.assertRaises(frappe.ValidationError) as ctx:
            self._submit_existing(draft, rate=50)
        self.assertIn("exceeds the maximum allowed discount", str(ctx.exception))
        self._created = [n for n in self._created if n != draft]
        self.assertFalse(
            frappe.db.exists("POS Invoice", {"name": draft, "docstatus": 1})
        )

    def test_no_server_price_with_manual_flag_still_gated(self):
        """No server Item Price (fixture removed) + client manual-edit flag:
        the payload's own price_list_rate is the only basis left, and the
        discount must still pass the gate."""
        self._set_rate_settings(allow_edit=1, max_discount=5)
        for name in list(self._item_prices):
            frappe.delete_doc("Item Price", name, force=1, ignore_permissions=True)
            self._item_prices.remove(name)
        frappe.db.commit()
        draft = self._create_draft(rate=100)
        with self.assertRaises(frappe.ValidationError) as ctx:
            self._submit_existing(draft, rate=50, price_list_rate=100, manual_flag=1)
        self.assertIn("exceeds the maximum allowed discount", str(ctx.exception))
        self._created = [n for n in self._created if n != draft]
        self.assertFalse(
            frappe.db.exists("POS Invoice", {"name": draft, "docstatus": 1})
        )

    def test_honest_rate_still_submits(self):
        """The same branch must keep working for an honest client: draft and
        submit both at the server list price."""
        self._set_rate_settings(allow_edit=0, max_discount=0)
        draft = self._create_draft(rate=100)
        result = self._submit_existing(draft, rate=100)
        self.assertEqual(result.get("status"), 1)
