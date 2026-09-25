# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Audit remediation tests: endpoint read gates (grup 6 klaster BE-1).

- SEC-NEW-05: get_referral_codes must not leak referral codes (the secret
  of the redeemer side) without promotion read permission.
- SEC-NEW-07: get_customer_balance / get_available_credit must require
  Customer read on the probed customer.
- SEC-NEW-08: wallet read endpoints (get_customer_wallet,
  get_customer_wallet_balance, get_or_create_wallet) must require Customer
  read; the cashier's own payment flow (Customer read) keeps working.
- SEC-NEW-10: get_create_pos_profile is management-only; get_pos_settings
  is profile-member or management only (the booting cashier of the ACTIVE
  profile must keep passing).

Run via pos_next/_pn_run_tests.py pos_next.api.test_endpoint_security_gates
"""

import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.credit_sales import get_available_credit, get_customer_balance
from pos_next.api.pos_profile import get_create_pos_profile, get_pos_settings
from pos_next.api.promotions import get_referral_codes
from pos_next.api.wallet import (
    get_customer_wallet,
    get_customer_wallet_balance,
    get_or_create_wallet,
    get_wallet_info,
)

ADMIN = "Administrator"
MANAGEMENT_ROLE = "Nexus POS Manager"

# Schedule-safe profile (same filter as the other security suites on this
# shared dev site).
_PROFILE_FILTER = [
    ["disabled", "=", 0],
    ["pos_schedule_enforce_closing", "=", 0],
]


class TestEndpointSecurityGates(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if not frappe.db.exists("Role", "POSNext Cashier"):
            raise unittest.SkipTest("POSNext Cashier role does not exist on this site")
        cls.profile = frappe.db.get_value(
            "POS Profile",
            _PROFILE_FILTER,
            ["name", "company", "warehouse"],
            as_dict=True,
            order_by="creation asc",
        )
        cls.customer = frappe.db.get_value("Customer", {"is_internal_customer": 0}, "name")
        if not (cls.profile and cls.customer):
            raise unittest.SkipTest("no usable POS Profile / customer")

        # cashier: profile member with the daily-flow roles (Customer read
        # comes with POSNext Cashier, same as the real POS lane)
        cls.cashier = f"sec-gate.{uuid.uuid4().hex[:8]}@example.com"
        cls.manager = f"sec-gate.{uuid.uuid4().hex[:8]}@example.com"
        cls.intruder = f"sec-gate.{uuid.uuid4().hex[:8]}@example.com"
        for email in (cls.cashier, cls.manager, cls.intruder):
            frappe.get_doc(
                {"doctype": "User", "email": email, "first_name": "Security Gate Tester"}
            ).insert(ignore_permissions=True)

        frappe.get_doc(
            {
                "doctype": "POS Profile User",
                "parent": cls.profile.name,
                "parenttype": "POS Profile",
                "parentfield": "pos_profile_user",
                "user": cls.cashier,
            }
        ).insert(ignore_permissions=True)

        for role in ("Stock User", "POSNext Cashier"):
            frappe.get_doc(
                {
                    "doctype": "Has Role",
                    "parent": cls.cashier,
                    "parenttype": "User",
                    "parentfield": "roles",
                    "role": role,
                }
            ).insert(ignore_permissions=True)

        if not frappe.db.exists("Role", MANAGEMENT_ROLE):
            frappe.get_doc({"doctype": "Role", "role_name": MANAGEMENT_ROLE}).insert(
                ignore_permissions=True
            )
        # System Manager + Accounts User alongside the app role: the picker's
        # happy path reads master data (Mode of Payment, accounts, ...) whose
        # grants live on the accounts roles on this site. The gate itself is
        # proven by the cashier's refusal.
        for role in (MANAGEMENT_ROLE, "System Manager", "Accounts User"):
            frappe.get_doc(
                {
                    "doctype": "Has Role",
                    "parent": cls.manager,
                    "parenttype": "User",
                    "parentfield": "roles",
                    "role": role,
                }
            ).insert(ignore_permissions=True)

        # get_create_pos_profile needs a company User Permission to finish the
        # happy path (check_user_company); grant it to the manager.
        frappe.get_doc(
            {
                "doctype": "User Permission",
                "user": cls.manager,
                "allow": "Company",
                "for_value": cls.profile.company,
            }
        ).insert(ignore_permissions=True)

        # a wallet for the probed customer so the cashier positive path can
        # exercise get_or_create_wallet's "existing wallet" branch
        cls.wallet = get_or_create_wallet(cls.customer, cls.profile.company, force_create=True)

        frappe.clear_cache(user=cls.cashier)
        frappe.clear_cache(user=cls.manager)
        frappe.clear_cache(user=cls.intruder)

    @classmethod
    def tearDownClass(cls):
        frappe.set_user(ADMIN)

        def _safe(step):
            try:
                step()
            except Exception:
                pass

        for email in (cls.cashier, cls.manager, cls.intruder):
            _safe(lambda: frappe.db.delete("User Permission", {"user": email}))
            _safe(lambda email=email: frappe.db.delete("Has Role", {"parent": email}))
            _safe(lambda email=email: frappe.db.delete("POS Profile User", {"user": email}))
            _safe(lambda email=email: frappe.db.delete("Error Log", {"owner": email}))
            _safe(
                lambda email=email: frappe.delete_doc(
                    "User", email, force=1, ignore_permissions=True
                )
            )
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user(ADMIN)

    def tearDown(self):
        frappe.set_user(ADMIN)

    # ------------------------------------------------------------ SEC-NEW-05

    def test_sec_new_05_get_referral_codes_is_gated(self):
        if frappe.has_permission("Promotional Scheme", "read", user=self.intruder):
            self.skipTest("site grants Promotional Scheme read to roleless users")

        frappe.set_user(self.intruder)
        try:
            with self.assertRaises(frappe.PermissionError):
                get_referral_codes()
        finally:
            frappe.set_user(ADMIN)

        # the promotion reader keeps their list (POSNext Cashier has read)
        frappe.set_user(self.cashier)
        try:
            self.assertIsInstance(get_referral_codes(), list)
        finally:
            frappe.set_user(ADMIN)

        self.assertIsInstance(get_referral_codes(), list)

    # ------------------------------------------------------------ SEC-NEW-07

    def test_sec_new_07_customer_balance_is_customer_read_gated(self):
        if frappe.has_permission("Customer", "read", user=self.intruder):
            self.skipTest("site grants Customer read to roleless users")

        frappe.set_user(self.intruder)
        try:
            with self.assertRaises(frappe.PermissionError):
                get_customer_balance(self.customer, self.profile.company)
            with self.assertRaises(frappe.PermissionError):
                get_available_credit(self.customer, self.profile.company)
        finally:
            frappe.set_user(ADMIN)

        # the cashier's credit-sale flow (Customer read) still works
        frappe.set_user(self.cashier)
        try:
            balance = get_customer_balance(self.customer, self.profile.company)
            self.assertEqual(set(balance), {"total_outstanding", "total_credit", "net_balance"})
            self.assertIsInstance(get_available_credit(self.customer, self.profile.company), list)
        finally:
            frappe.set_user(ADMIN)

    # ------------------------------------------------------------ SEC-NEW-08

    def test_sec_new_08_wallet_reads_are_customer_read_gated(self):
        if frappe.has_permission("Customer", "read", user=self.intruder):
            self.skipTest("site grants Customer read to roleless users")

        frappe.set_user(self.intruder)
        try:
            with self.assertRaises(frappe.PermissionError):
                get_customer_wallet(self.customer, self.profile.company)
            with self.assertRaises(frappe.PermissionError):
                get_customer_wallet_balance(self.customer, self.profile.company)
            with self.assertRaises(frappe.PermissionError):
                get_or_create_wallet(self.customer, self.profile.company)
        finally:
            frappe.set_user(ADMIN)

    def test_sec_new_08_cashier_wallet_flow_kept(self):
        # cashier holds Customer read: the wallet payment lane must not break
        frappe.set_user(self.cashier)
        try:
            self.assertIsInstance(
                get_customer_wallet_balance(self.customer, self.profile.company), float
            )
            # may be None when the customer has no wallet yet; never a throw
            wallet = get_customer_wallet(self.customer, self.profile.company)
            self.assertTrue(wallet is None or isinstance(wallet, dict))

            if self.wallet:
                wallet = get_or_create_wallet(self.customer, self.profile.company)
                self.assertTrue(wallet, "existing wallet must still be returned to the cashier")
        finally:
            frappe.set_user(ADMIN)

    def test_sec_new_08_get_wallet_info_is_customer_read_gated(self):
        # get_wallet_info was the one wallet read endpoint left ungated: it
        # leaks wallet existence/balance (and can auto-create a wallet) for
        # any customer name the caller types
        if frappe.has_permission("Customer", "read", user=self.intruder):
            self.skipTest("site grants Customer read to roleless users")

        frappe.set_user(self.intruder)
        try:
            with self.assertRaises(frappe.PermissionError):
                get_wallet_info(self.customer, self.profile.company)
        finally:
            frappe.set_user(ADMIN)

        # the cashier's own info panel (Customer read) keeps working
        frappe.set_user(self.cashier)
        try:
            info = get_wallet_info(self.customer, self.profile.company)
            self.assertIsInstance(info, dict)
            self.assertIn("wallet_balance", info)
        finally:
            frappe.set_user(ADMIN)

    # ------------------------------------------------------------ SEC-NEW-10

    def test_sec_new_10_get_create_pos_profile_is_manager_only(self):
        frappe.set_user(self.intruder)
        try:
            with self.assertRaises(frappe.PermissionError):
                get_create_pos_profile()
        finally:
            frappe.set_user(ADMIN)

        # cashiers are NOT management: the picker stays closed for them too
        frappe.set_user(self.cashier)
        try:
            with self.assertRaises(frappe.PermissionError):
                get_create_pos_profile()
        finally:
            frappe.set_user(ADMIN)

        # management role passes the gate (and finishes the normal flow)
        frappe.set_user(self.manager)
        try:
            data = get_create_pos_profile()
            self.assertIsInstance(data, dict)
            self.assertIn("applicable_for_users", data)
        finally:
            frappe.set_user(ADMIN)

        # Administrator passes the role gate; unrelated setup gaps (e.g. no
        # company User Permission on this shared dev site) are not this
        # endpoint's concern
        try:
            get_create_pos_profile()
        except frappe.PermissionError:
            self.fail("management gate blocked Administrator")
        except Exception:
            pass

    def test_sec_new_10_get_pos_settings_member_or_manager(self):
        frappe.set_user(self.intruder)
        try:
            with self.assertRaises(frappe.PermissionError):
                get_pos_settings(self.profile.name)
        finally:
            frappe.set_user(ADMIN)

        # KRITIS: the booting cashier of the ACTIVE profile must keep passing
        frappe.set_user(self.cashier)
        try:
            settings = get_pos_settings(self.profile.name)
            self.assertIsInstance(settings, dict)
        finally:
            frappe.set_user(ADMIN)

        frappe.set_user(self.manager)
        try:
            self.assertIsInstance(get_pos_settings(self.profile.name), dict)
        finally:
            frappe.set_user(ADMIN)

        self.assertIsInstance(get_pos_settings(self.profile.name), dict)

        # no profile requested: generic defaults only, nothing profile-scoped
        frappe.set_user(self.intruder)
        try:
            self.assertIsInstance(get_pos_settings(None), dict)
        finally:
            frappe.set_user(ADMIN)
