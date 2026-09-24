# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-NEW-02 acceptance tests: get_draft_invoices must be shift- and owner-scoped.

The shift filter used to test the wrong column name (``pos_opening_shift``
vs the real ``posa_pos_opening_shift``) so it never applied, and there was
no ownership gate — any cashier received every cashier's drafts and could
resume (overwrite) a peer's cart. Run via
pos_next/_pn_run_tests.py pos_next.api.test_draft_invoices_security
"""

import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import get_draft_invoices
from pos_next.tests.price_group_helpers import get_default_customer

ADMIN = "Administrator"
MANAGER_ROLE = "Nexus POS Manager"

# same schedule-safe profile filter as test_pos_invoice_submit
_PROFILE_FILTER = [
    ["disabled", "=", 0],
    ["pos_schedule_enforce_closing", "=", 0],
]


class TestDraftInvoiceScoping(FrappeTestCase):
    """Draft resume listing: own rows for cashiers, whole shift for managers."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
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
            "Item", filters={"disabled": 0, "is_sales_item": 1}, pluck="name", limit=1
        )
        if not item:
            raise unittest.SkipTest("no sales item on site")
        cls.item = item[0]
        cls.customer = get_default_customer()
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
        if not frappe.db.exists("Role", MANAGER_ROLE):
            raise unittest.SkipTest(f"{MANAGER_ROLE} role does not exist on this site")
        if not frappe.db.exists("Role", "POSNext Cashier"):
            raise unittest.SkipTest("POSNext Cashier role does not exist on this site")
        # this site grants POS Invoice doc read via Accounts User (ERPNext
        # standard DocPerm) — the read grant cashiers need to list at all
        if not frappe.db.exists("Role", "Accounts User"):
            raise unittest.SkipTest("Accounts User role does not exist on this site")

        # production cashiers hold POSNext Cashier; Accounts User supplies the
        # POS Invoice doc read this site hands out — the exact lane where the
        # missing owner filter leaked peers' drafts
        cls.cashier_a = cls._make_user("draft-sec-a", roles=["POSNext Cashier", "Accounts User"])
        cls.cashier_b = cls._make_user("draft-sec-b", roles=["POSNext Cashier", "Accounts User"])
        cls.manager = cls._make_user("draft-sec-m", roles=[MANAGER_ROLE, "Accounts User"])

    @classmethod
    def tearDownClass(cls):
        frappe.set_user(ADMIN)
        for user in (cls.cashier_a, cls.cashier_b, cls.manager):
            try:
                frappe.delete_doc("User", user, force=1, ignore_permissions=True)
            except Exception:
                pass
        frappe.db.commit()
        super().tearDownClass()

    @classmethod
    def _make_user(cls, prefix, roles=()):
        email = f"{prefix}.{uuid.uuid4().hex[:8]}@example.com"
        frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": prefix,
                # Website User (the programmatic default) cannot list doctypes
                # at all — POS staff are System Users
                "user_type": "System User",
                "send_welcome_email": 0,
            }
        ).insert(ignore_permissions=True)
        for role in roles:
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
        return email

    def setUp(self):
        frappe.set_user(ADMIN)
        self._drafts = []
        self.shift = self._make_shift()
        self.other_shift = self._make_shift()
        # A and B each park a draft on the SAME shift; one more of A's sits on
        # the other shift (must never travel with either caller)
        self.draft_a = self._make_draft(self.shift.name, self.cashier_a)
        self.draft_b = self._make_draft(self.shift.name, self.cashier_b)
        self.draft_other = self._make_draft(self.other_shift.name, self.cashier_a)

    def tearDown(self):
        frappe.set_user(ADMIN)
        for name in dict.fromkeys(self._drafts):
            if frappe.db.exists("POS Invoice", name):
                frappe.delete_doc("POS Invoice", name, force=1, ignore_permissions=True)
        for shift in (self.shift, self.other_shift):
            frappe.db.set_value(
                "POS Opening Shift", shift.name, "docstatus", 2, update_modified=False
            )
            frappe.delete_doc("POS Opening Shift", shift.name, force=1, ignore_permissions=True)
        frappe.db.commit()

    def _make_shift(self):
        shift = frappe.get_doc(
            {
                "doctype": "POS Opening Shift",
                "pos_profile": self.profile.name,
                "company": self.profile.company,
                "user": ADMIN,
                "posting_date": frappe.utils.nowdate(),
                "period_start_date": frappe.utils.now_datetime(),
                "balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
            }
        )
        shift.flags.ignore_permissions = True
        shift.insert()
        shift.reload()
        shift.submit()
        return shift

    @classmethod
    def _make_draft(cls, shift, owner):
        """Minimal draft inserted as ADMIN, then re-owned — visibility keys on
        doc.owner (same trick as test_cashier_permissions._draft)."""
        doc = frappe.get_doc(
            {
                "doctype": "POS Invoice",
                "company": cls.profile.company,
                "customer": cls.customer,
                "pos_profile": cls.profile.name,
                "is_pos": 1,
                "posa_pos_opening_shift": shift,
                "items": [{"item_code": cls.item, "qty": 1, "rate": 10}],
                "payments": [{"mode_of_payment": cls.mode[0], "amount": 10}],
            }
        )
        doc.flags.ignore_permissions = True
        doc.insert()
        frappe.db.set_value("POS Invoice", doc.name, "owner", owner)
        return doc.name

    def _visible_names(self, user, shift, doctype="POS Invoice"):
        frappe.set_user(user)
        return {d.name for d in get_draft_invoices(shift, doctype=doctype)}

    def test_cashier_cannot_see_peers_drafts(self):
        # the leak: before the fix cashier A received every cashier's drafts
        visible = self._visible_names(self.cashier_a, self.shift.name)
        self.assertIn(self.draft_a, visible)
        self.assertNotIn(self.draft_b, visible)
        self.assertNotIn(self.draft_other, visible)

    def test_other_cashier_sees_only_their_own(self):
        visible = self._visible_names(self.cashier_b, self.shift.name)
        self.assertIn(self.draft_b, visible)
        self.assertNotIn(self.draft_a, visible)
        self.assertNotIn(self.draft_other, visible)

    def test_manager_sees_whole_shift_not_other_shifts(self):
        visible = self._visible_names(self.manager, self.shift.name)
        self.assertIn(self.draft_a, visible)
        self.assertIn(self.draft_b, visible)
        self.assertNotIn(self.draft_other, visible)

    def test_shift_filter_applies_for_admin_too(self):
        # the dead has_column check meant even the shift filter never ran
        visible = self._visible_names(ADMIN, self.shift.name)
        self.assertIn(self.draft_a, visible)
        self.assertIn(self.draft_b, visible)
        self.assertNotIn(self.draft_other, visible)

    def test_explicit_sales_invoice_doctype_scoped_too(self):
        frappe.set_user(ADMIN)
        si = frappe.get_doc(
            {
                "doctype": "Sales Invoice",
                "company": self.profile.company,
                "customer": self.customer,
                "pos_profile": self.profile.name,
                "is_pos": 1,
                "posa_pos_opening_shift": self.shift.name,
                "items": [{"item_code": self.item, "qty": 1, "rate": 10}],
            }
        )
        si.flags.ignore_permissions = True
        si.insert()
        self._drafts.append(si.name)
        frappe.db.set_value("Sales Invoice", si.name, "owner", self.cashier_a)

        visible = self._visible_names(self.cashier_b, self.shift.name, doctype="Sales Invoice")
        self.assertNotIn(si.name, visible)
        visible = self._visible_names(self.cashier_a, self.shift.name, doctype="Sales Invoice")
        self.assertIn(si.name, visible)
