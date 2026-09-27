# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-13 acceptance tests: declarative permission tightening for POSNext Cashier.

A user holding ONLY the POSNext Cashier role must:
- lose the abusable rights: Sales Invoice / POS Invoice cancel/amend, POS
  Closing Entry cancel/amend, Payment Entry submit, POS Settings write, and
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

from pos_next.tests.price_group_helpers import get_default_company, get_default_customer

ADMIN = "Administrator"

DOCTYPES = (
    "Sales Invoice",
    "POS Invoice",
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
        # "delete" denied doc-less by design: the A4 grant is delete=1 with
        # if_owner=1, and a doc-less check under if_owner is always False
        self._assert_matrix(
            "Sales Invoice",
            granted=("read", "write", "create", "submit", "print"),
            denied=("cancel", "amend", "delete"),
        )

    def test_pos_invoice_abusable_rights_removed_flow_kept(self):
        # POS Invoice = doctype mode aktif situs (reversal 25 Sep); baris
        # fixture-nya kloning Sales Invoice persis. read membuka SPA
        # "Lihat Detail" + print kasir (E2E-01 27 Sep), cancel/amend tertutup.
        self._assert_matrix(
            "POS Invoice",
            granted=("read", "write", "create", "submit", "print"),
            denied=("cancel", "amend", "delete"),
        )

    def test_sales_invoice_delete_own_draft_only(self):
        """A4: kasir may delete their OWN draft (if_owner grant), never
        another user's draft."""
        self._assert_delete_own_draft_only("Sales Invoice")

    def test_pos_invoice_delete_own_draft_only(self):
        """E2E-01 parity: the if_owner delete lane must fall through to POS
        Invoice — the doctype the site actually runs in."""
        self._assert_delete_own_draft_only("POS Invoice")

    def _assert_delete_own_draft_only(self, doctype):
        item = frappe.get_all(
            "Item", filters={"disabled": 0, "is_sales_item": 1}, pluck="name", limit=1
        )
        if not item:
            self.skipTest("missing sales item on this site")
        customer = get_default_customer()
        if not customer:
            self.skipTest("missing customer on this site")
        grp = frappe.db.get_value(
            "Customer", customer, ["customer_group", "territory"], as_dict=True
        )
        profile = None
        shift = None
        pos_customer = None
        if doctype == "POS Invoice":
            # company/warehouse mengikuti profil agar validation lolos
            profile = frappe.db.get_value(
                "POS Profile", {"disabled": 0}, ["name", "company", "warehouse"], as_dict=True
            )
            if not profile:
                self.skipTest("no POS Profile on this site")
            # pelanggan polos tanpa price group: resolusi harga memakai daftar
            # umum. Pelanggan bawaan helper bisa ber-price-group franchise,
            # membuat price_list_rate melompat dan rate daftar umum terbaca
            # diskon manual → gate kode head-office melempar.
            plain_customer = frappe.get_doc(
                {
                    "doctype": "Customer",
                    "customer_name": f"POS Perm Cust {uuid.uuid4().hex[:8]}",
                    "customer_group": grp.customer_group,
                    "territory": grp.territory,
                }
            )
            plain_customer.flags.ignore_permissions = True
            plain_customer.insert()
            pos_customer = plain_customer.name
            self.addCleanup(
                lambda: frappe.delete_doc(
                    "Customer", pos_customer, force=1, ignore_permissions=True
                )
            )
            mode = frappe.get_all(
                "POS Payment Method",
                {"parent": profile.name, "parenttype": "POS Profile"},
                pluck="mode_of_payment",
                limit=1,
            )
            if not mode:
                self.skipTest("profile has no payment methods")
            # rate di bawah price_list_rate terbaca diskon manual → gate kode
            # head-office melempar; pakai harga daftar apa adanya
            price_list = (
                frappe.db.get_value("Selling Settings", "Selling Settings", "selling_price_list")
                or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
            )
            price = frappe.get_all(
                "Item Price",
                filters={"item_code": item[0], "price_list": price_list, "selling": 1},
                fields=["price_list_rate"],
                limit=1,
            )
            pos_rate = price[0].price_list_rate if price else 10
            # validate_pos_opening_entry menolak POS Invoice tanpa shift
            # terbuka yang cocok dengan profilnya — buat satu utk draft uji
            shift = frappe.get_doc(
                {
                    "doctype": "POS Opening Shift",
                    "pos_profile": profile.name,
                    "company": profile.company,
                    "user": ADMIN,
                    "posting_date": frappe.utils.nowdate(),
                    "period_start_date": frappe.utils.now_datetime(),
                    "balance_details": [{"mode_of_payment": mode[0], "amount": 0}],
                }
            )
            shift.flags.ignore_permissions = True
            shift.insert()
            shift.submit()

            def _drop_shift():
                frappe.db.set_value(
                    "POS Opening Shift", shift.name, "docstatus", 2, update_modified=False
                )
                frappe.delete_doc(
                    "POS Opening Shift", shift.name, force=1, ignore_permissions=True
                )

            self.addCleanup(_drop_shift)

        def _draft(owner):
            rate = pos_rate if profile else 10
            doc_data = {
                "doctype": doctype,
                "customer": pos_customer or customer,
                "selling_price_list": (
                    frappe.db.get_value("Selling Settings", "Selling Settings", "selling_price_list")
                    or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
                ),
                "items": [{"item_code": item[0], "qty": 1, "rate": rate}],
            }
            if profile:
                doc_data["pos_profile"] = profile.name
                doc_data["company"] = profile.company
                doc_data["posa_pos_opening_shift"] = shift.name
                doc_data["items"][0]["warehouse"] = profile.warehouse
                # POS Invoice menuntut >= 1 mode pembayaran (validate_mode_of_payment)
                doc_data["payments"] = [{"mode_of_payment": mode[0], "amount": rate}]
            else:
                company = get_default_company()
                if not company:
                    self.skipTest("missing company on this site")
                doc_data["company"] = company
            doc = frappe.get_doc(doc_data)
            # insert under Administrator (validation reads accounts the cashier
            # cannot), then place ownership explicitly — the permission engine
            # keys on doc.owner
            frappe.set_user(ADMIN)
            try:
                doc.flags.ignore_permissions = True
                doc.insert()
            finally:
                frappe.set_user(self.cashier)
            if owner != ADMIN:
                frappe.db.set_value(doctype, doc.name, "owner", owner)
                doc = frappe.get_doc(doctype, doc.name)
            return doc

        own_draft = _draft(self.cashier)
        other_draft = _draft(ADMIN)
        self.addCleanup(
            lambda: frappe.delete_doc(doctype, own_draft.name, force=1, ignore_permissions=True)
        )
        self.addCleanup(
            lambda: frappe.delete_doc(doctype, other_draft.name, force=1, ignore_permissions=True)
        )

        self.assertTrue(
            frappe.has_permission(doctype, "delete", doc=own_draft, user=self.cashier),
            "kasir must be able to delete their own draft",
        )
        self.assertFalse(
            frappe.has_permission(doctype, "delete", doc=other_draft, user=self.cashier),
            "kasir must NOT delete another user's draft",
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

    def test_promotional_scheme_read_only(self):
        # unblocks the POS Promotions dialog (get_promotions checks read)
        self._assert_matrix("Promotional Scheme", granted=("read",), denied=("write", "create"))

    def test_production_recipe_read_only(self):
        self._assert_matrix("POS Production Recipe", granted=("read",), denied=("write", "create"))

    def test_no_purchasing(self):
        # design decision: cashiers sell and produce, they do not purchase
        self._assert_matrix("Purchase Order", granted=(), denied=("create",))
        self._assert_matrix("Purchase Receipt", granted=(), denied=("create",))

    def test_direct_print_permissions(self):
        from pos_next.api.qz import _PRINT_ROLES

        self.assertTrue(
            {"POSNext Cashier", "Nexus POS Manager"}.issubset(set(_PRINT_ROLES)),
            "both POS roles must stay in the QZ direct-print gate",
        )
        self._assert_matrix("Sales Invoice", granted=("print",), denied=())
        self._assert_matrix("POS Closing Shift", granted=("print",), denied=())
        self._assert_matrix("POS Print Log", granted=("create", "read"), denied=())

    # ------------------------------------------------- role permission evidence

    def test_get_role_permissions_matches_matrix(self):
        import json

        import frappe.permissions

        evidence = {}
        for dt in DOCTYPES:
            perms = frappe.permissions.get_role_permissions(dt, user=self.cashier)
            evidence[dt] = {k: perms.get(k) for k in ("read", "write", "create", "submit", "cancel", "amend", "delete", "export")}

        self.assertEqual(evidence["Sales Invoice"]["cancel"], 0)
        self.assertEqual(evidence["Sales Invoice"]["amend"], 0)
        self.assertEqual(evidence["Sales Invoice"]["submit"], 1)
        # A4 grant: delete exists but is owner-scoped. Frappe aggregates an
        # if_owner-only ptype to 0 doc-less; it surfaces only with is_owner.
        self.assertEqual(evidence["Sales Invoice"]["delete"], 0)
        self.assertEqual(evidence["POS Invoice"]["cancel"], 0)
        self.assertEqual(evidence["POS Invoice"]["submit"], 1)
        owner_scoped = frappe.permissions.get_role_permissions(
            "Sales Invoice", user=self.cashier, is_owner=True
        )
        self.assertEqual(owner_scoped.get("if_owner", {}).get("delete"), 1)
        self.assertEqual(evidence["POS Settings"]["write"], 0)
        self.assertEqual(evidence["POS Settings"]["read"], 1)
        self.assertEqual(evidence["POS Coupon"]["read"], 1)
        self.assertEqual(evidence["Payment Entry"]["submit"], 0)
        # print for the task report (bukti get_role_permissions)
        print("SEC-13 get_role_permissions evidence (pure POSNext Cashier):")
        print(json.dumps(evidence, indent=1))
