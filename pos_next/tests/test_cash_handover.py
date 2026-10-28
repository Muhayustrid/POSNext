# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, flt, now_datetime, nowdate

from pos_next.api.invoices import submit_invoice
from pos_next.api.shifts import (
	create_opening_shift,
	get_cash_handover,
	get_opening_dialog_data,
)
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	make_closing_shift_from_opening,
	submit_closing_shift,
)
from pos_next.services.cash_mode import get_modal_awal_modes
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestCashHandover(POSInvoiceModeMixin, FrappeTestCase):
	def setUp(self):
		self._created_shifts = []
		super().setUp()
		self._old_pos_settings_invoice_type = frappe.db.get_single_value("POS Settings", "invoice_type")
		if self._old_pos_settings_invoice_type != "POS Invoice":
			frappe.db.set_single_value("POS Settings", "invoice_type", "POS Invoice")

	def tearDown(self):
		try:
			frappe.set_user("Administrator")
			for name in getattr(self, "_created_shifts", []):
				self._cleanup_shift(name)
			self._created_shifts = []
		finally:
			if (
				getattr(self, "_old_pos_settings_invoice_type", None)
				and self._old_pos_settings_invoice_type != "POS Invoice"
			):
				frappe.db.set_single_value("POS Settings", "invoice_type", self._old_pos_settings_invoice_type)
			super().tearDown()

	def _cash_mode(self):
		return next(iter(get_modal_awal_modes(self.profile.name)))

	def _set_opening_cash(self, amount=200):
		cash = self._cash_mode()
		detail_name = frappe.db.get_value(
			"POS Opening Shift Detail",
			{"parent": self.shift.name, "mode_of_payment": cash},
			"name",
		)
		if detail_name:
			frappe.db.set_value("POS Opening Shift Detail", detail_name, "amount", amount)
		else:
			frappe.get_doc(
				{
					"doctype": "POS Opening Shift Detail",
					"parent": self.shift.name,
					"parenttype": "POS Opening Shift",
					"parentfield": "balance_details",
					"mode_of_payment": cash,
					"amount": amount,
				}
			).insert(ignore_permissions=True)
		self.shift.reload()

	def _close_shift(self):
		closing_name = submit_closing_shift({"pos_opening_shift": self.shift.name})
		self.closing = frappe.get_doc("POS Closing Shift", closing_name)
		return self.closing

	def _cleanup_shift(self, shift_name):
		frappe.set_user("Administrator")
		if frappe.db.exists("POS Opening Shift", shift_name):
			frappe.db.set_value("POS Opening Shift", shift_name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", shift_name, force=1, ignore_permissions=True)
			frappe.db.commit()

	def test_no_closing_today_returns_none(self):
		existing = frappe.db.sql(
			"""
			SELECT pcs.name
			FROM `tabPOS Closing Shift` pcs
			INNER JOIN `tabPOS Opening Shift` pos ON pcs.pos_opening_shift = pos.name
			WHERE pcs.docstatus = 1
			  AND pcs.pos_profile = %(profile)s
			  AND pos.docstatus = 1
			  AND pos.posting_date = %(today)s
			""",
			{"profile": self.profile.name, "today": nowdate()},
		)
		if existing:
			self.skipTest("submitted closing already exists for this profile today")
		self.assertIsNone(get_cash_handover(self.profile.name))

	def test_proposal_is_previous_expected_cash(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		submit_invoice(
			self._payload(
				payments=[{"mode_of_payment": cash, "amount": 800}],
				items=[{"item_code": self.item, "qty": 8, "rate": 100, "warehouse": self.profile.warehouse}],
			)
		)
		self._close_shift()

		handover = get_cash_handover(self.profile.name)
		self.assertIsNotNone(handover)
		self.assertEqual(handover["closing"], self.closing.name)
		self.assertEqual(handover["amounts"][cash], 1000)

	def test_create_opening_stores_proposal_and_received(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		submit_invoice(
			self._payload(
				payments=[{"mode_of_payment": cash, "amount": 800}],
				items=[{"item_code": self.item, "qty": 8, "rate": 100, "warehouse": self.profile.warehouse}],
			)
		)
		self._close_shift()

		res = create_opening_shift(
			self.profile.name,
			self.profile.company,
			[{"mode_of_payment": cash, "opening_amount": 200, "proposed_amount": 999}],
		)
		b_shift_name = res["pos_opening_shift"]["name"]
		self._created_shifts.append(b_shift_name)
		self.addCleanup(self._cleanup_shift, b_shift_name)

		b_shift = frappe.get_doc("POS Opening Shift", b_shift_name)
		self.assertEqual(b_shift.handover_from, self.closing.name)
		detail = next(d for d in b_shift.balance_details if d.mode_of_payment == cash)
		self.assertEqual(flt(detail.proposed_amount), 1000)
		self.assertEqual(flt(detail.amount), 200)

		preview = make_closing_shift_from_opening(json.dumps(b_shift.as_dict(), default=str))
		recon = [r for r in (preview.get("payment_reconciliation") or []) if r.get("mode_of_payment") == cash]
		self.assertTrue(recon)
		self.assertEqual(flt(recon[0].get("opening_amount")), 200)

	def test_client_handover_fields_ignored(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		submit_invoice(
			self._payload(
				payments=[{"mode_of_payment": cash, "amount": 800}],
				items=[{"item_code": self.item, "qty": 8, "rate": 100, "warehouse": self.profile.warehouse}],
			)
		)
		self._close_shift()

		res = create_opening_shift(
			self.profile.name,
			self.profile.company,
			[{"mode_of_payment": cash, "opening_amount": 200, "proposed_amount": 999, "handover_from": "FAKE-CS"}],
		)
		b_shift_name = res["pos_opening_shift"]["name"]
		self._created_shifts.append(b_shift_name)
		self.addCleanup(self._cleanup_shift, b_shift_name)

		b_shift = frappe.get_doc("POS Opening Shift", b_shift_name)
		self.assertEqual(b_shift.handover_from, self.closing.name)
		detail = next(d for d in b_shift.balance_details if d.mode_of_payment == cash)
		self.assertEqual(flt(detail.proposed_amount), 1000)
		self.assertEqual(flt(detail.amount), 200)

	def test_non_modal_awal_mode_not_proposed(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		self._close_shift()

		modal_awal_modes = get_modal_awal_modes(self.profile.name)
		non_cash = frappe.db.get_value(
			"Mode of Payment",
			{"name": ["not in", list(modal_awal_modes)]},
			"name",
		)
		if not non_cash:
			self.skipTest("no non-Modal-Awal mode of payment exists")

		frappe.get_doc(
			{
				"doctype": "POS Closing Shift Detail",
				"parent": self.closing.name,
				"parenttype": "POS Closing Shift",
				"parentfield": "payment_reconciliation",
				"mode_of_payment": non_cash,
				"opening_amount": 0,
				"expected_amount": 500,
				"closing_amount": 500,
			}
		).insert(ignore_permissions=True)

		handover = get_cash_handover(self.profile.name)
		self.assertIsNotNone(handover)
		self.assertNotIn(non_cash, handover["amounts"])
		self.assertIn(cash, handover["amounts"])

	def test_cancelled_closing_not_proposed(self):
		self._close_shift()
		self.closing.reload()
		self.closing.cancel()

		handover = get_cash_handover(self.profile.name)
		if handover is not None:
			self.assertNotEqual(handover["closing"], self.closing.name)
		else:
			self.assertIsNone(handover)

	def test_yesterday_closing_not_proposed(self):
		self._close_shift()
		frappe.db.set_value(
			"POS Opening Shift",
			self.shift.name,
			"posting_date",
			add_to_date(nowdate(), days=-1),
		)

		handover = get_cash_handover(self.profile.name)
		if handover is not None:
			self.assertNotEqual(handover["closing"], self.closing.name)
		else:
			self.assertIsNone(handover)

	def test_auto_close_in_same_request_is_proposed(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		frappe.db.set_value(
			"POS Opening Shift",
			self.shift.name,
			"pos_schedule_deadline",
			add_to_date(now_datetime(), minutes=-5),
		)

		res = create_opening_shift(
			self.profile.name,
			self.profile.company,
			[{"mode_of_payment": cash, "opening_amount": 200}],
		)
		b_shift_name = res["pos_opening_shift"]["name"]
		self._created_shifts.append(b_shift_name)
		self.addCleanup(self._cleanup_shift, b_shift_name)

		closing_name = frappe.db.get_value(
			"POS Closing Shift",
			{"pos_opening_shift": self.shift.name},
			"name",
		)
		self.assertTrue(closing_name)
		self.closing = frappe.get_doc("POS Closing Shift", closing_name)
		self.assertEqual(self.closing.auto_closed, 1)

		b_shift = frappe.get_doc("POS Opening Shift", b_shift_name)
		self.assertEqual(b_shift.handover_from, closing_name)
		detail = next(d for d in b_shift.balance_details if d.mode_of_payment == cash)
		self.assertEqual(flt(detail.proposed_amount), 200)

	def test_dialog_data_includes_handover(self):
		users = frappe.get_all(
			"POS Profile User",
			{"parent": self.profile.name, "parenttype": "POS Profile"},
			pluck="user",
		)
		if not users:
			self.skipTest("profile has no users in POS Profile User")

		self._close_shift()
		original_user = frappe.session.user
		frappe.set_user(users[0])
		try:
			data = get_opening_dialog_data()
			self.assertIn("handover", data)
			self.assertIn(self.profile.name, data["handover"])
			self.assertIsNotNone(data["handover"][self.profile.name])
			self.assertEqual(data["handover"][self.profile.name]["closing"], self.closing.name)
		finally:
			frappe.set_user(original_user)

	def test_no_sales_created(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		self._close_shift()

		counts_before = {
			dt: frappe.db.count(dt) for dt in ("POS Invoice", "Sales Invoice", "Payment Entry")
		}

		res = create_opening_shift(
			self.profile.name,
			self.profile.company,
			[{"mode_of_payment": cash, "opening_amount": 200}],
		)
		b_shift_name = res["pos_opening_shift"]["name"]
		self._created_shifts.append(b_shift_name)
		self.addCleanup(self._cleanup_shift, b_shift_name)

		counts_after = {
			dt: frappe.db.count(dt) for dt in ("POS Invoice", "Sales Invoice", "Payment Entry")
		}
		self.assertEqual(counts_before, counts_after)

	def test_handover_with_for_update_reads_current_closing(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		self._close_shift()

		handover = get_cash_handover(self.profile.name, for_update=True)
		self.assertIsNotNone(handover)
		self.assertEqual(handover["closing"], self.closing.name)
		self.assertEqual(handover["amounts"][cash], 200)

	def test_cannot_cancel_closing_when_used_as_handover(self):
		cash = self._cash_mode()
		self._set_opening_cash(200)
		self._close_shift()

		res = create_opening_shift(
			self.profile.name,
			self.profile.company,
			[{"mode_of_payment": cash, "opening_amount": 200}],
		)
		b_shift_name = res["pos_opening_shift"]["name"]
		self._created_shifts.append(b_shift_name)
		self.addCleanup(self._cleanup_shift, b_shift_name)

		self.closing.reload()
		with self.assertRaises(frappe.ValidationError):
			self.closing.cancel()

		# Ensure closing is still submitted and opening B is still open
		self.assertEqual(frappe.db.get_value("POS Closing Shift", self.closing.name, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("POS Opening Shift", b_shift_name, "status"), "Open")

		# Once opening B is cancelled, cancelling the closing shift succeeds
		self._cleanup_shift(b_shift_name)
		self.closing.reload()
		self.closing.cancel()
		self.assertEqual(frappe.db.get_value("POS Closing Shift", self.closing.name, "docstatus"), 2)
