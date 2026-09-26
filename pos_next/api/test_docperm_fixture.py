# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""REL-02 guard: fixture Custom DocPerm hanya boleh berisi dua role POS.

Satu baris Custom DocPerm liar pada doctype inti membuat Frappe mengabaikan
DocPerm bawaan doctype tersebut sepenuhnya (lihat
frappe.permissions.get_all_perms), sehingga akses role lain hilang diam-diam.
Matriks perilaku runtime ada di pos_next/api/test_cashier_permissions.py.

Run via pos_next/_pn_run_tests.py pos_next.api.test_docperm_fixture
"""

import json
from collections import Counter

import frappe
from frappe.tests.utils import FrappeTestCase

FIXTURE = ("pos_next", "fixtures", "custom_docperm.json")
POS_ROLES = {"POSNext Cashier", "Nexus POS Manager"}

# Allowlist doctype — sumber kebenaran:
# - Sales Invoice, Payment Entry, POS Closing Entry, POS Opening Entry,
#   POS Profile, Promotional Scheme, POS Production Recipe:
#   pos_next/api/test_cashier_permissions.py (matriks SEC-13 + 4 test baru
#   Promotional Scheme / POS Production Recipe read-only, gate _PRINT_ROLES).
# - Bin, Item, Territory, Warehouse, Customer, Sales Invoice Item:
#   fixture warisan (HEAD) persona kasir (POSNext Cashier + Stock User +
#   POS Profile User outlet), lihat blok permissions JSON doctype app.
# - Company, Purchase Order, Purchase Receipt (entri pn-mgr-*):
#   paket Nexus POS Manager — persona manager berdiri sendiri tanpa role core.
# - Account (select=1 tanpa read, kedua role):
#   ERPNext v16 party.account_perm_check() menuntut select/read Account saat
#   set_missing_values meresolusi debit_to — field server-managed yang tidak
#   pernah dipilih kasir (pos_next/api/test_cashier_checkout_permissions.py).
#   Select tanpa read = cukup untuk validasi link, chart of accounts tetap
#   tertutup. Kedua role POS tidak punya DocPerm standar di Account, jadi
#   baris ini murni menambah.
ALLOWED_PARENTS = {
    "Account",
    "Bin",
    "Company",
    "Customer",
    "Item",
    "Payment Entry",
    "POS Closing Entry",
    "POS Opening Entry",
    "POS Production Recipe",
    "POS Profile",
    "Promotional Scheme",
    "Purchase Order",
    "Purchase Receipt",
    "Sales Invoice",
    "Sales Invoice Item",
    "Territory",
    "Warehouse",
}


class TestDocPermFixture(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(frappe.get_app_path(*FIXTURE)) as f:
            cls.entries = json.load(f)

    def test_only_pos_roles(self):
        roles = {e["role"] for e in self.entries}
        self.assertTrue(
            roles <= POS_ROLES,
            f"fixture berisi role di luar {sorted(POS_ROLES)}: {sorted(roles - POS_ROLES)}",
        )

    def test_no_duplicate_parent_role_if_owner(self):
        """Sales Invoice x POSNext Cashier punya 2 baris disengaja
        (if_owner=0 main + if_owner=1 delete-own-draft) — triple
        (parent, role, if_owner) yang sama tetap dilarang."""
        dups = {
            key: count
            for key, count in Counter(
                (e["parent"], e["role"], e.get("if_owner", 0)) for e in self.entries
            ).items()
            if count > 1
        }
        self.assertFalse(dups, f"entri (parent, role, if_owner) duplikat: {dups}")

    def test_parents_within_allowlist(self):
        strays = {e["parent"] for e in self.entries} - ALLOWED_PARENTS
        self.assertFalse(
            strays,
            f"doctype di luar allowlist masuk fixture Custom DocPerm: {sorted(strays)}",
        )
