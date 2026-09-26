# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Two-persona role system: positive matrix for Nexus POS Manager.

A user holding ONLY the Nexus POS Manager role must:
- hold the full back-office flow: Sales Invoice create/submit/cancel/amend,
  Payment Entry + Purchase Order + Purchase Receipt submission lifecycle,
  Promotional Scheme maintenance, POS Settings read/write,
  Wallet Transaction and POS Discount Confirmation Code create,
  Company/POS Profile write;
- NOT be System Manager: the branding tampering-stats gate must still reject
  them (that gate stays System Manager only), while the backdate lane accepts
  them via has_backdate_role.

Run via pos_next/_pn_run_tests.py pos_next.api.test_manager_permissions
"""

import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

ADMIN = "Administrator"


class TestManagerPermissions(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Role", "Nexus POS Manager"):
            raise unittest.SkipTest("Nexus POS Manager role does not exist on this site")

        # a user whose ONLY role is Nexus POS Manager — the pure manager probe
        cls.manager = f"manager-perm.{uuid.uuid4().hex[:8]}@example.com"
        frappe.get_doc(
            {"doctype": "User", "email": cls.manager, "first_name": "Manager Perm Tester"}
        ).insert(ignore_permissions=True)
        frappe.get_doc(
            {
                "doctype": "Has Role",
                "parent": cls.manager,
                "parenttype": "User",
                "parentfield": "roles",
                "role": "Nexus POS Manager",
            }
        ).insert(ignore_permissions=True)
        frappe.clear_cache(user=cls.manager)

    @classmethod
    def tearDownClass(cls):
        frappe.set_user(ADMIN)

        def _safe(step):
            try:
                step()
            except Exception:
                pass

        _safe(lambda: frappe.db.delete("Error Log", {"owner": cls.manager}))
        _safe(lambda: frappe.delete_doc("User", cls.manager, force=1, ignore_permissions=True))
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user(self.manager)

    def tearDown(self):
        frappe.set_user(ADMIN)

    # ---------------------------------------------------------------- helpers

    def _allows(self, doctype, ptype):
        return frappe.has_permission(doctype, ptype, user=self.manager)

    def _assert_matrix(self, doctype, granted, denied=()):
        for ptype in granted:
            self.assertTrue(
                self._allows(doctype, ptype),
                f"{doctype}: manager must hold '{ptype}'",
            )
        for ptype in denied:
            self.assertFalse(
                self._allows(doctype, ptype),
                f"{doctype}: manager must NOT hold '{ptype}'",
            )

    # ---------------------------------------------------------------- matrix

    def test_sales_invoice_full_lifecycle(self):
        self._assert_matrix("Sales Invoice", granted=("read", "create", "submit", "cancel", "amend"))

    def test_payment_entry_full_lifecycle(self):
        self._assert_matrix("Payment Entry", granted=("create", "submit", "cancel"))

    def test_purchase_order_full_lifecycle(self):
        self._assert_matrix("Purchase Order", granted=("create", "submit", "cancel"))

    def test_purchase_receipt_lifecycle(self):
        self._assert_matrix("Purchase Receipt", granted=("create", "submit"))

    def test_promotional_scheme_maintenance(self):
        self._assert_matrix("Promotional Scheme", granted=("read", "write", "delete"))

    def test_pos_settings_read_write(self):
        self._assert_matrix("POS Settings", granted=("read", "write"))

    def test_wallet_transaction_create(self):
        self._assert_matrix("Wallet Transaction", granted=("create",))

    def test_discount_confirmation_code_create(self):
        self._assert_matrix("POS Discount Confirmation Code", granted=("create",))

    def test_company_write(self):
        self._assert_matrix("Company", granted=("write",))

    def test_pos_profile_write(self):
        self._assert_matrix("POS Profile", granted=("write",))

    def test_manager_is_not_system_manager(self):
        self.assertNotIn("System Manager", frappe.get_roles())

    def test_branding_tampering_stats_still_system_manager_only(self):
        from pos_next.api.branding import get_tampering_stats

        self.assertRaises(frappe.PermissionError, get_tampering_stats)

    def test_backdate_role_granted(self):
        from pos_next.api.backdate_invoices import has_backdate_role

        self.assertTrue(has_backdate_role())
