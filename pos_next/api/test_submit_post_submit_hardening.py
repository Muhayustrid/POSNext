# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-04 / COR-BE-05 acceptance tests for submit_invoice's post-submit
lane.

- COR-BE-04: a failing redeem_customer_credit used to be swallowed after the
  invoice was already submitted — the invoice issued "paid by credit" with no
  JE allocation and the credit left reusable. The failure must propagate (one
  request = one DB transaction, so the throw rolls the whole checkout back).
- COR-BE-05: _complete_offline_sync used to swallow marking failures, leaving
  the sync record Pending without an invoice pointer; after 5 minutes the
  same offline_id could submit again (duplicate sale).

Run via
pos_next/_pn_run_tests.py pos_next.api.test_submit_post_submit_hardening
"""

import unittest
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.invoices import _complete_offline_sync, submit_invoice
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


class _ForcedRedeemFailure(Exception):
    pass


class TestCreditRedeemFailure(FrappeTestCase):
    """COR-BE-04: redeem failure must fail the checkout, not print a warning."""

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

    @classmethod
    def tearDownClass(cls):
        _set_invoice_type(cls.original_invoice_type or POS_INVOICE)
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        _set_invoice_type(SALES_INVOICE)
        self._created = []
        self.shift = self._make_shift()

    def tearDown(self):
        frappe.set_user("Administrator")
        for name in dict.fromkeys(self._created):
            for doctype in ("Sales Invoice", "POS Invoice"):
                if frappe.db.exists(doctype, name):
                    doc = frappe.get_doc(doctype, name)
                    if doc.docstatus == 1:
                        doc.flags.ignore_permissions = True
                        doc.cancel()
                    frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
                    break
        frappe.db.set_value(
            "POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
        )
        frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
        frappe.db.commit()

    def _make_shift(self):
        shift = frappe.get_doc(
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
        shift.flags.ignore_permissions = True
        shift.insert()
        shift.reload()
        shift.submit()
        return shift

    def _paid_credit_payload(self):
        """A normally PAID invoice that additionally claims customer credit —
        enough to reach the redeem lane without the credit-sale machinery."""
        return {
            "pos_profile": self.profile.name,
            "posa_pos_opening_shift": self.shift.name,
            "customer": self.customer,
            "items": [
                {"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
            ],
            "payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
            "redeemed_customer_credit": 20,
            "customer_credit_dict": [
                {"type": "Invoice", "credit_origin": "NONEXISTENT-SINV", "credit_to_redeem": 20}
            ],
        }

    def test_redeem_failure_propagates_after_submit(self):
        """The forced redeem failure must surface to the caller — the old flow
        swallowed it and left the invoice issued anyway."""
        payload = self._paid_credit_payload()
        with mock.patch(
            "pos_next.api.credit_sales.redeem_customer_credit",
            side_effect=_ForcedRedeemFailure("forced redeem failure"),
        ) as mocked:
            with self.assertRaises(_ForcedRedeemFailure):
                submit_invoice(invoice=payload)

        # the redeem lane only runs post-submit: the call must reference a
        # submitted invoice (this ordering is why the re-raise design is safe)
        called_name = mocked.call_args.args[0]
        self.assertEqual(frappe.db.get_value("Sales Invoice", called_name, "docstatus"), 1)
        self._created.append(called_name)


class _SubmitInvoiceFixtureMixin:
    """Shared profile/shift/stock fixtures for the submit_invoice classes."""

    def _make_fixtures(self):
        self.profile = frappe.db.get_value(
            "POS Profile",
            _PROFILE_FILTER,
            ["name", "company", "warehouse"],
            as_dict=True,
            order_by="creation asc",
        )
        if not self.profile:
            self.skipTest("no schedule-safe POS Profile")
        item = frappe.get_all(
            "Item",
            filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
            pluck="name",
            limit=1,
        )
        if not item:
            self.skipTest("no stock sales item on site")
        self.item = item[0]
        self.customer = frappe.db.get_value(
            "Customer", {"is_internal_customer": 0}, "name", order_by="creation asc"
        )
        if not self.customer:
            self.skipTest("no non-internal customer")
        mode = frappe.get_all(
            "POS Payment Method",
            {"parent": self.profile.name, "parenttype": "POS Profile"},
            pluck="mode_of_payment",
            limit=1,
        )
        if not mode:
            self.skipTest("profile has no payment methods")
        self.mode = mode[0]
        self.shift = self._make_shift()
        self._make_stock()

    def _make_shift(self):
        shift = frappe.get_doc(
            {
                "doctype": "POS Opening Shift",
                "pos_profile": self.profile.name,
                "company": self.profile.company,
                "user": "Administrator",
                "posting_date": nowdate(),
                "period_start_date": frappe.utils.now_datetime(),
                "balance_details": [{"mode_of_payment": self.mode, "amount": 0}],
            }
        )
        shift.flags.ignore_permissions = True
        shift.insert()
        shift.reload()
        shift.submit()
        return shift

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
                        "qty": 5,
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

    def _cleanup(self, sync_offline_ids=()):
        frappe.set_user("Administrator")
        for name in dict.fromkeys(getattr(self, "_created", [])):
            for doctype in ("Sales Invoice", "POS Invoice"):
                if frappe.db.exists(doctype, name):
                    doc = frappe.get_doc(doctype, name)
                    if doc.docstatus == 1:
                        doc.flags.ignore_permissions = True
                        doc.cancel()
                    frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
                    break
        for offline_id in sync_offline_ids:
            frappe.db.delete("Offline Invoice Sync", {"offline_id": offline_id})
        if getattr(self, "stock_entry", None):
            se = frappe.get_doc("Stock Entry", self.stock_entry.name)
            if se.docstatus == 1:
                se.cancel()
            frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)
        if getattr(self, "shift", None):
            frappe.db.set_value(
                "POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
            )
            frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
        frappe.db.commit()


class TestOfflineSyncCompletion(_SubmitInvoiceFixtureMixin, FrappeTestCase):
    """COR-BE-05: a sync-marking failure must fail the request (rolling the
    invoice back with it) instead of leaving a pointerless Pending record that
    a later replay can double-submit."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.original_invoice_type = frappe.db.get_single_value(
            "POS Next Global Settings", "invoice_type"
        )

    @classmethod
    def tearDownClass(cls):
        _set_invoice_type(cls.original_invoice_type or POS_INVOICE)
        frappe.db.commit()
        super().tearDownClass()

    def setUp(self):
        frappe.set_user("Administrator")
        _set_invoice_type(POS_INVOICE)
        self._created = []
        self._make_fixtures()
        self.offline_id = f"corbe05.{frappe.utils.now_datetime().strftime('%H%M%S%f')}"
        frappe.db.delete("Offline Invoice Sync", {"offline_id": self.offline_id})
        frappe.db.commit()

    def tearDown(self):
        self._cleanup(sync_offline_ids=[self.offline_id])

    def _payload(self):
        return {
            "pos_profile": self.profile.name,
            "posa_pos_opening_shift": self.shift.name,
            "customer": self.customer,
            "offline_id": self.offline_id,
            "items": [
                {"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
            ],
            "payments": [{"mode_of_payment": self.mode, "amount": 100}],
        }

    def _payload_without_offline_id(self):
        payload = self._payload()
        payload.pop("offline_id")
        return payload

    def test_marking_failure_fails_request_and_rolls_back(self):
        """Forced sync-marking failure: the checkout must throw AND nothing may
        survive the (emulated) request rollback — no invoice, no sync record —
        so a client retry starts clean instead of duplicating the sale."""
        real_get_doc = frappe.get_doc

        def _fail_sync_marking(*args, **kwargs):
            if args and args[0] == "Offline Invoice Sync":
                raise frappe.ValidationError("forced sync marking failure")
            return real_get_doc(*args, **kwargs)

        # fixture boundary: what the throw rolls back must be exactly the
        # request's own writes, like the HTTP request handler does
        frappe.db.commit()
        before = set(
            frappe.get_all("POS Invoice", filters={"posa_pos_opening_shift": self.shift.name}, pluck="name")
        )
        with mock.patch("frappe.get_doc", side_effect=_fail_sync_marking):
            with self.assertRaises(frappe.ValidationError):
                submit_invoice(invoice=self._payload())
        frappe.db.rollback()

        self.assertFalse(
            frappe.db.exists("Offline Invoice Sync", {"offline_id": self.offline_id}),
            "the pending reservation must roll back with the failed request",
        )
        # delta, not absolute count: shift names recycle after deletion, so
        # pre-existing rows can legitimately sit on this name
        after = set(
            frappe.get_all("POS Invoice", filters={"posa_pos_opening_shift": self.shift.name}, pluck="name")
        )
        self.assertEqual(
            after - before,
            set(),
            "no invoice may survive a failed offline checkout",
        )

        # clean retry: exactly one invoice, and a replay dedups to it
        first = submit_invoice(invoice=self._payload())
        second = submit_invoice(invoice=self._payload())
        self._created.append(first["name"])
        self.assertEqual(first["name"], second["name"])

    def test_replay_after_cancel_dedupes_to_new_invoice(self):
        """Review MAJOR 2: reusing a Synced record whose invoice was later
        cancelled must CLEAR the dead pointer — the fill-only write refuses
        to overwrite it, so a stale pointer would let every later replay of
        the same offline_id mint a fresh duplicate."""
        first = submit_invoice(invoice=self._payload())
        self._created.append(first["name"])
        record = frappe.get_doc("Offline Invoice Sync", {"offline_id": self.offline_id})
        self.assertEqual(record.status, "Synced")

        # the desk cancels the synced invoice; replaying the same offline_id
        # reuses the record and must end up pointing at the NEW invoice
        frappe.get_doc("POS Invoice", first["name"]).cancel()
        second = submit_invoice(invoice=self._payload())
        self._created.append(second["name"])
        self.assertNotEqual(first["name"], second["name"])
        self.assertEqual(
            frappe.db.get_value("Offline Invoice Sync", record.name, "pos_invoice"),
            second["name"],
            "reuse must clear the dead pointer so the new invoice is recorded",
        )

        # and a third replay dedups to the second, not to a fresh invoice
        third = submit_invoice(invoice=self._payload())
        self.assertEqual(third["name"], second["name"])

    def test_pointer_write_is_idempotent(self):
        """The invoice pointer must only ever fill an empty column — a re-run
        can never repoint a record that already names another invoice."""
        # pos_invoice is a Link: the record must name a real invoice up front
        existing = submit_invoice(invoice=self._payload_without_offline_id())
        self._created.append(existing["name"])
        record = frappe.get_doc(
            {
                "doctype": "Offline Invoice Sync",
                "offline_id": self.offline_id,
                "sales_invoice": "",
                "pos_invoice": existing["name"],
                "status": "Pending",
            }
        )
        record.flags.ignore_permissions = True
        record.insert()

        _complete_offline_sync(record.name, "SOME-OTHER-INV", "POS Invoice")

        self.assertEqual(
            frappe.db.get_value("Offline Invoice Sync", record.name, "pos_invoice"),
            existing["name"],
        )
