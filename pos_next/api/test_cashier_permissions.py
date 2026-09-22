# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-13 acceptance tests: declarative permission tightening for POSNext Cashier.

A user holding ONLY the POSNext Cashier role must:
- lose the abusable rights: Sales Invoice cancel/amend, POS Closing Entry
  cancel/amend, Payment Entry submit, POS Settings write, and
  cancel/amend/export on Bank Deposits / POS Opening Shift / POS Closing Shift;
- keep the daily flow: Sales Invoice create/write/submit, Payment Entry create,
  shift create/submit, POS Settings read, POS Coupon read (dependency of the
  SEC-08 gate in get_active_coupons).

Run via pos_next/_pn_run_tests.py pos_next.api.test_cashier_permissions
"""

import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

ADMIN = "Administrator"

DOCTYPES = (
    "Sales Invoice",
    "POS Closing Entry",
    "Payment Entry",
    "Bank Deposits",
    "POS Opening Shift",
    "POS Closing Shift",
    "POS Settings",
    "POS Coupon",
)


class TestCashierPermissions(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Role", "POSNext Cashier"):
            raise unittest.SkipTest("POSNext Cashier role does not exist on this site")

        # a user whose ONLY role is POSNext Cashier — the pure cashier probe
        cls.cashier = f"cashier-perm.{uuid.uuid4().hex[:8]}@example.com"
        frappe.get_doc(
            {"doctype": "User", "email": cls.cashier, "first_name": "Cashier Perm Tester"}
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "Has Role",
                "parent": cls.cashier,
                "parenttype": "User",
                "parentfield": "roles",
                "role": "POSNext Cashier",
            }
        ).insert(ignore_permissions=True)
        frappe.clear_cache(user=cls.cashier)

    @classmethod
    def tearDownClass(cls):
        frappe.set_user(ADMIN)

        def _safe(step):
            try:
                step()
            except Exception:
                pass

        _safe(lambda: frappe.db.delete("Error Log", {"owner": cls.cashier}))
        _safe(lambda: frappe.delete_doc("User", cls.cashier, force=1, ignore_permissions=True))
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user(self.cashier)

    def tearDown(self):
        frappe.set_user(ADMIN)

    # ---------------------------------------------------------------- helpers

    def _allows(self, doctype, ptype):
        return frappe.has_permission(doctype, ptype, user=self.cashier)

    def _assert_matrix(self, doctype, granted, denied):
        for ptype in granted:
            self.assertTrue(
                self._allows(doctype, ptype),
                f"{doctype}: cashier must keep '{ptype}'",
            )
        for ptype in denied:
            self.assertFalse(
                self._allows(doctype, ptype),
                f"{doctype}: cashier must NOT hold '{ptype}'",
            )

    # ---------------------------------------------------------------- matrix

    def test_sales_invoice_abusable_rights_removed_flow_kept(self):
        self._assert_matrix(
            "Sales Invoice",
            granted=("read", "write", "create", "submit", "print"),
            denied=("cancel", "amend"),
        )

    def test_pos_closing_entry_cancel_amend_removed(self):
        self._assert_matrix("POS Closing Entry", granted=("submit", "create"), denied=("cancel", "amend"))

    def test_payment_entry_draft_only(self):
        self._assert_matrix("Payment Entry", granted=("create", "write", "read"), denied=("submit",))

    def test_bank_deposits_cancel_amend_export_removed(self):
        self._assert_matrix(
            "Bank Deposits",
            granted=("create", "submit", "write", "read"),
            denied=("cancel", "amend", "export"),
        )

    def test_shifts_create_submit_kept_abusable_removed(self):
        # if_owner deliberately NOT set: submit_closing_shift gates with a
        # doc-less has_permission("POS Closing Shift", "submit"); if_owner
        # would zero that for the owner too and break the own-shift close.
        # Ownership is enforced by the T2/T5 endpoint gates instead.
        for doctype in ("POS Opening Shift", "POS Closing Shift"):
            self._assert_matrix(
                doctype,
                granted=("create", "submit", "write", "read"),
                denied=("cancel", "amend", "export"),
            )

    def test_pos_settings_read_only(self):
        self._assert_matrix("POS Settings", granted=("read",), denied=("write", "create"))

    def test_pos_coupon_read_only(self):
        # dependency of SEC-08: get_active_coupons gates on POS Coupon read
        self._assert_matrix("POS Coupon", granted=("read", "select"), denied=("write", "create", "export"))

    # ------------------------------------------------- role permission evidence

    def test_get_role_permissions_matches_matrix(self):
        import json

        import frappe.permissions

        evidence = {}
        for dt in DOCTYPES:
            perms = frappe.permissions.get_role_permissions(dt, user=self.cashier)
            evidence[dt] = {k: perms.get(k) for k in ("read", "write", "create", "submit", "cancel", "amend", "export")}

        self.assertEqual(evidence["Sales Invoice"]["cancel"], 0)
        self.assertEqual(evidence["Sales Invoice"]["amend"], 0)
        self.assertEqual(evidence["Sales Invoice"]["submit"], 1)
        self.assertEqual(evidence["POS Settings"]["write"], 0)
        self.assertEqual(evidence["POS Settings"]["read"], 1)
        self.assertEqual(evidence["POS Coupon"]["read"], 1)
        self.assertEqual(evidence["Payment Entry"]["submit"], 0)
        # print for the task report (bukti get_role_permissions)
        print("SEC-13 get_role_permissions evidence (pure POSNext Cashier):")
        print(json.dumps(evidence, indent=1))
