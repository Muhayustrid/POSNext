# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-08 acceptance: return taxes mirror the original invoice's flags.

The Sales Invoice validate hook used to stamp ``included_in_print_rate``
from TODAY's setting. When the global tax-inclusive toggle changed after the
original invoice was made, a return for it was re-rated with the new flag and
its tax numbers drifted away from the original invoice. For is_return with
return_against, the hook must copy the original invoice's
included_in_print_rate instead of applying today's setting.

Run via pos_next/_pn_run_tests.py pos_next.api.test_return_tax_flag_mirror
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.invoices import update_invoice
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE

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


class TestReturnTaxFlagMirror(FrappeTestCase):
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
        cls.tax_account = frappe.db.get_value(
            "Account",
            {"company": cls.profile.company, "is_group": 0, "account_type": "Tax", "disabled": 0},
            "name",
        )
        if not cls.tax_account:
            raise unittest.SkipTest("no non-group Tax account for the profile company")
        # the toggle under test lives on the profile's enabled POS Settings
        # row when one exists, else on the global single
        cls._settings_row = frappe.db.get_value(
            "POS Settings", {"pos_profile": cls.profile.name, "enabled": 1}, "name"
        )
        if cls._settings_row:
            cls._tax_backup = frappe.db.get_value(
                "POS Settings", cls._settings_row, "tax_inclusive"
            )
        else:
            cls._tax_backup = None
        # the refund-code gate demands a code on every return; these tests
        # exercise the tax flag mirror, not that gate — toggle it off
        # (same pattern as test_wallet_return_hardening)
        cls._refund_gate_row = cls._settings_row
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
        if cls._settings_row:
            frappe.db.set_value(
                "POS Settings", cls._settings_row, "tax_inclusive", cls._tax_backup, update_modified=False
            )
        _set_invoice_type(cls.original_invoice_type or POS_INVOICE)
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        _set_invoice_type(SALES_INVOICE)
        self._set_tax_inclusive(1)
        self._created = []
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
        self.original = self._make_invoice()

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in dict.fromkeys(self._created):
            if frappe.db.exists("Sales Invoice", name):
                frappe.delete_doc("Sales Invoice", name, force=1, ignore_permissions=True)
        frappe.db.set_value("POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False)
        frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
        self._set_tax_inclusive(self._tax_backup)
        frappe.db.commit()

    def _set_tax_inclusive(self, value):
        if self._settings_row:
            frappe.db.set_value(
                "POS Settings", self._settings_row, "tax_inclusive", value, update_modified=False
            )
        else:
            frappe.db.set_single_value("POS Next Global Settings", "tax_inclusive", value)
        frappe.db.commit()

    def _taxes(self, inclusive):
        return [
            {
                "charge_type": "On Net Total",
                "account_head": self.tax_account,
                "rate": 11,
                "description": "Tax",
                "included_in_print_rate": inclusive,
            }
        ]

    def _make_invoice(self, is_return=0, return_against=None, tax_inclusive=1):
        payload = {
            "pos_profile": self.profile.name,
            "posa_pos_opening_shift": self.shift.name,
            "customer": self.customer,
            "is_return": is_return,
            "items": [
                {
                    "item_code": self.item,
                    "qty": -1 if is_return else 1,
                    "rate": 100,
                    "uom": self.uom,
                    "warehouse": self.profile.warehouse,
                }
            ],
            "payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
            "taxes": self._taxes(tax_inclusive),
        }
        if return_against:
            payload["return_against"] = return_against
        created = update_invoice(json.dumps(payload))
        self._created.append(created.get("name"))
        return created

    def _saved_flag(self, invoice_name):
        return frappe.db.get_value(
            "Sales Taxes and Charges",
            {"parent": invoice_name, "parenttype": "Sales Invoice"},
            "included_in_print_rate",
        )

    def test_original_invoice_stamped_inclusive(self):
        """Sanity: with the setting ON the original's tax row is inclusive."""
        self.assertEqual(self._saved_flag(self.original.get("name")), 1)

    def test_return_mirrors_original_flag_not_todays_setting(self):
        """RED: with the setting flipped OFF, the return used to follow the
        new setting (0) instead of the original invoice (1)."""
        self._set_tax_inclusive(0)
        ret = self._make_invoice(is_return=1, return_against=self.original.get("name"), tax_inclusive=0)
        self.assertEqual(
            self._saved_flag(ret.get("name")),
            1,
            "return tax rows must mirror the original invoice, not today's setting",
        )
