# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Native HQ persona matrix (role overhaul, 30 Sep).

The HQ/back-office persona is served by native ERPNext roles — no custom role:
- pure "Sales Manager": monitoring, monthly targets, discount confirmation
  codes, backdate lane; and NOT an outlet manager (no Purchase Order submit);
- pure "Accounts Manager": wallet manual credit + backdate lane;
- the single-role cashier stays outside all of the above.

Run via pos_next/_pn_run_tests.py pos_next.api.test_hq_native_permissions
"""

import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

ADMIN = "Administrator"


class TestHQNativePermissions(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.sales_mgr = f"hq-sales.{uuid.uuid4().hex[:8]}@example.com"
        cls.acct_mgr = f"hq-acct.{uuid.uuid4().hex[:8]}@example.com"
        cls.cashier = f"hq-cashier.{uuid.uuid4().hex[:8]}@example.com"
        for email, first_name, role in (
            (cls.sales_mgr, "HQ Sales", "Sales Manager"),
            (cls.acct_mgr, "HQ Accounts", "Accounts Manager"),
            (cls.cashier, "HQ Cashier", "POSNext Cashier"),
        ):
            if not frappe.db.exists("Role", role):
                raise unittest.SkipTest(f"{role} role does not exist on this site")
            frappe.get_doc(
                {"doctype": "User", "email": email, "first_name": first_name}
            ).insert(ignore_permissions=True)
            frappe.get_doc(
                {
                    "doctype": "Has Role",
                    "parent": email,
                    "parenttype": "User",
                    "parentfield": "roles",
                    "role": role,
                }
            ).insert(ignore_permissions=True)
            frappe.clear_cache(user=email)

    @classmethod
    def tearDownClass(cls):
        frappe.set_user(ADMIN)
        for email in (cls.sales_mgr, cls.acct_mgr, cls.cashier):
            try:
                frappe.db.delete("Error Log", {"owner": email})
            except Exception:
                pass
            try:
                frappe.delete_doc("User", email, force=1, ignore_permissions=True)
            except Exception:
                pass
        frappe.db.commit()
        super().tearDownClass()

    def tearDown(self):
        frappe.set_user(ADMIN)

    # ------------------------------------------------------------------ HQ sales

    def test_sales_manager_monthly_target_write(self):
        self.assertTrue(frappe.has_permission("POS Monthly Target", "write", user=self.sales_mgr))
        self.assertTrue(frappe.has_permission("POS Monthly Target", "create", user=self.sales_mgr))

    def test_sales_manager_discount_code_create(self):
        self.assertTrue(
            frappe.has_permission("POS Discount Confirmation Code", "create", user=self.sales_mgr)
        )

    def test_sales_manager_backdate_lane(self):
        from pos_next.api.backdate_invoices import has_backdate_role

        frappe.set_user(self.sales_mgr)
        self.assertTrue(has_backdate_role())

    def test_sales_manager_hq_monitoring_gate(self):
        from pos_next.api.hq_monitoring import _check_hq_access

        frappe.set_user(self.sales_mgr)
        _check_hq_access()  # must not raise

    def test_sales_manager_is_not_outlet_manager(self):
        """HQ sales must not inherit the outlet manager's purchasing lane."""
        self.assertFalse(frappe.has_permission("Purchase Order", "submit", user=self.sales_mgr))
        self.assertFalse(frappe.has_permission("Purchase Receipt", "submit", user=self.sales_mgr))

    # --------------------------------------------------------------- HQ accounts

    def test_accounts_manager_wallet_create(self):
        self.assertTrue(frappe.has_permission("Wallet Transaction", "create", user=self.acct_mgr))

    def test_accounts_manager_backdate_lane(self):
        from pos_next.api.backdate_invoices import has_backdate_role

        frappe.set_user(self.acct_mgr)
        self.assertTrue(has_backdate_role())

    def test_accounts_manager_hq_monitoring_gate(self):
        from pos_next.api.hq_monitoring import _check_hq_access

        frappe.set_user(self.acct_mgr)
        _check_hq_access()  # must not raise

    # ------------------------------------------------------------------- cashier

    def test_cashier_outside_hq_lanes(self):
        from pos_next.api.backdate_invoices import has_backdate_role
        from pos_next.api.hq_monitoring import _check_hq_access

        self.assertFalse(frappe.has_permission("POS Monthly Target", "write", user=self.cashier))
        self.assertFalse(
            frappe.has_permission("POS Discount Confirmation Code", "create", user=self.cashier)
        )
        self.assertFalse(frappe.has_permission("Wallet Transaction", "create", user=self.cashier))

        frappe.set_user(self.cashier)
        self.assertFalse(has_backdate_role())
        self.assertRaises(frappe.PermissionError, _check_hq_access)
