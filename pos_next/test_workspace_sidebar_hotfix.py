# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Regression guard for the WorkspaceSidebar hotfix in pos_next/__init__.py.

frappe 16.36 WorkspaceSidebar.get_can_read_items forgets to return, so
can_read is cached as None and every DocType/Report sidebar link (POS
Invoice, ...) is filtered out for non-Administrator users. The hotfix
re-adds the missing return; these tests prove a plain POSNext Cashier
gets a real can_read list on the POSNext sidebar, and that the hotfix
still repairs a frappe that ships the buggy method.

Run via pos_next/_pn_run_tests.py pos_next.test_workspace_sidebar_hotfix
"""

import unittest
import uuid

import frappe
from frappe.desk.doctype.workspace_sidebar.workspace_sidebar import WorkspaceSidebar
from frappe.tests.utils import FrappeTestCase

ADMIN = "Administrator"
SIDEBAR = "POSNext"
CACHE_KEY = "user_perm_can_read"


class TestWorkspaceSidebarHotfix(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "get_can_read_items" not in vars(WorkspaceSidebar):
            raise unittest.SkipTest("buggy method gone from frappe; hotfix is a no-op")
        if not frappe.db.exists("Role", "POSNext Cashier"):
            raise unittest.SkipTest("POSNext Cashier role does not exist on this site")
        cls.cashier = f"sidebar-hotfix.{uuid.uuid4().hex[:8]}@example.com"
        frappe.get_doc(
            {"doctype": "User", "email": cls.cashier, "first_name": "Sidebar Hotfix"}
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
        frappe.local.user_perms = None
        try:
            frappe.delete_doc("User", cls.cashier, force=1, ignore_permissions=True)
        except Exception:
            pass
        frappe.db.commit()
        super().tearDownClass()

    def tearDown(self):
        frappe.set_user(ADMIN)
        frappe.local.user_perms = None

    def _sidebar_for_cashier(self):
        """Workspace Sidebar doc as the cashier, bypassing any cached can_read
        so the get_can_read_items fallback genuinely runs."""
        frappe.set_user(self.cashier)
        frappe.local.user_perms = None  # force UserPermissions rebuild for the cashier
        frappe.cache.delete_value(CACHE_KEY, user=self.cashier)
        return frappe.get_doc("Workspace Sidebar", SIDEBAR)

    def test_cashier_can_read_is_list_with_pos_invoice(self):
        doc = self._sidebar_for_cashier()

        self.assertIsInstance(doc.can_read, list)
        self.assertIn("POS Invoice", doc.can_read)

    def test_hotfix_repairs_missing_return(self):
        """Simulate the frappe 16.36 bug (fallback without return), re-apply
        the hotfix and confirm can_read is repaired instead of None."""
        from pos_next import _apply_workspace_sidebar_hotfix

        original = WorkspaceSidebar.get_can_read_items

        def buggy(self):
            if not self.user.can_read:
                self.user.build_permissions()
            # 16.36 bug: no return

        WorkspaceSidebar.get_can_read_items = buggy
        try:
            _apply_workspace_sidebar_hotfix()
            self.assertIsNot(
                buggy, WorkspaceSidebar.get_can_read_items, "hotfix did not replace buggy method"
            )
            doc = self._sidebar_for_cashier()
            self.assertIsInstance(doc.can_read, list)
            self.assertIn("POS Invoice", doc.can_read)
        finally:
            WorkspaceSidebar.get_can_read_items = original
