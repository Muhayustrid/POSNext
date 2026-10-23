# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Tests for auto-close expired shifts scheduled task and reprint endpoint."""

import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, flt, now_datetime

from pos_next.api.invoices import submit_invoice
from pos_next.api.shifts import get_recent_auto_closed_shift
from pos_next.tasks.auto_close_shifts import GRACE_MINUTES, auto_close_expired_shifts
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestAutoCloseShifts(POSInvoiceModeMixin, FrappeTestCase):
	def setUp(self):
		super().setUp()
		old_single = frappe.db.get_single_value("POS Settings", "invoice_type")
		if old_single != "POS Invoice":
			frappe.db.set_single_value("POS Settings", "invoice_type", "POS Invoice")
			self.addCleanup(frappe.db.set_single_value, "POS Settings", "invoice_type", old_single)

	def _setup_schedule_snapshot(self, shift_name, enabled=1, enforce=1, deadline_offset_minutes=-35):
		deadline = now_datetime() + datetime.timedelta(minutes=deadline_offset_minutes)
		frappe.db.set_value(
			"POS Opening Shift",
			shift_name,
			{
				"pos_schedule_enabled": enabled,
				"pos_schedule_enforce_closing": enforce,
				"pos_schedule_deadline": deadline,
			},
			update_modified=False,
		)
		return deadline

	def test_job_closes_enforced_shift_past_grace_period(self):
		# Submit a sale so expected_amount > 0
		submit_invoice(self._payload())
		self._setup_schedule_snapshot(self.shift.name, enabled=1, enforce=1, deadline_offset_minutes=-35)

		auto_close_expired_shifts()

		self.shift.reload()
		self.assertEqual(self.shift.status, "Closed")
		self.assertTrue(self.shift.pos_closing_shift)

		closing = frappe.get_doc("POS Closing Shift", self.shift.pos_closing_shift)
		self.closing = closing
		self.assertEqual(closing.docstatus, 1)
		self.assertEqual(closing.auto_closed, 1)

		reconciliation = [r for r in closing.payment_reconciliation if r.mode_of_payment == self.mode[0]]
		self.assertTrue(reconciliation)
		row = reconciliation[0]
		self.assertGreater(flt(row.expected_amount), 0)
		self.assertEqual(flt(row.closing_amount), flt(row.expected_amount))
		self.assertEqual(flt(row.difference), 0)

	def test_job_leaves_alone_shift_within_grace_period(self):
		self._setup_schedule_snapshot(self.shift.name, enabled=1, enforce=1, deadline_offset_minutes=-10)

		auto_close_expired_shifts()

		self.shift.reload()
		self.assertEqual(self.shift.status, "Open")
		self.assertFalse(self.shift.pos_closing_shift)

	def test_job_leaves_alone_shift_without_enforce(self):
		self._setup_schedule_snapshot(self.shift.name, enabled=1, enforce=0, deadline_offset_minutes=-35)

		auto_close_expired_shifts()

		self.shift.reload()
		self.assertEqual(self.shift.status, "Open")
		self.assertFalse(self.shift.pos_closing_shift)

	def test_job_leaves_alone_already_closed_shift(self):
		self._setup_schedule_snapshot(self.shift.name, enabled=1, enforce=1, deadline_offset_minutes=-35)
		# Close it normally first
		from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import _submit_closing

		closing_name = _submit_closing(self.shift.name, auto_balance=True)
		self.closing = frappe.get_doc("POS Closing Shift", closing_name)

		# Run auto-close again
		auto_close_expired_shifts()

		# Ensure no new closing was created
		self.shift.reload()
		self.assertEqual(self.shift.pos_closing_shift, closing_name)

	def test_failing_shift_does_not_stop_next(self):
		from pos_next.tasks import auto_close_shifts as acs_mod

		self._setup_schedule_snapshot(self.shift.name, enabled=1, enforce=1, deadline_offset_minutes=-35)
		frappe.db.commit()

		call_count = 0
		orig_submit = acs_mod._submit_closing

		def mock_submit(opening_shift_name, **kwargs):
			nonlocal call_count
			call_count += 1
			if opening_shift_name == "FAILING-SHIFT":
				raise RuntimeError("Simulated failure on first shift")
			return orig_submit(opening_shift_name, **kwargs)

		with patch.object(acs_mod, "_get_expired_shifts", return_value=[
			frappe._dict({"name": "FAILING-SHIFT"}),
			frappe._dict({"name": self.shift.name}),
		]):
			with patch.object(acs_mod, "_submit_closing", side_effect=mock_submit):
				auto_close_expired_shifts()

		self.assertEqual(call_count, 2)
		self.shift.reload()
		self.assertEqual(self.shift.status, "Closed")
		self.closing = frappe.get_doc("POS Closing Shift", self.shift.pos_closing_shift)

	def test_endpoint_returns_auto_closed_for_owner_only(self):
		self._setup_schedule_snapshot(self.shift.name, enabled=1, enforce=1, deadline_offset_minutes=-35)
		auto_close_expired_shifts()

		self.shift.reload()
		closing_name = self.shift.pos_closing_shift
		self.closing = frappe.get_doc("POS Closing Shift", closing_name)

		# Owner should receive closing name
		orig_user = frappe.session.user
		try:
			frappe.set_user(self.shift.user)
			res = get_recent_auto_closed_shift()
			self.assertEqual(res, closing_name)

			# Another user receives None
			frappe.set_user("Administrator" if self.shift.user != "Administrator" else "Guest")
			res_other = get_recent_auto_closed_shift()
			self.assertIsNone(res_other)
		finally:
			frappe.set_user(orig_user)

	def test_endpoint_ignores_closing_older_than_24h(self):
		self._setup_schedule_snapshot(self.shift.name, enabled=1, enforce=1, deadline_offset_minutes=-35)
		auto_close_expired_shifts()

		self.shift.reload()
		closing_name = self.shift.pos_closing_shift
		self.closing = frappe.get_doc("POS Closing Shift", closing_name)

		# Backdate creation of the closing shift beyond 24h
		old_creation = add_to_date(now_datetime(), hours=-25)
		frappe.db.set_value("POS Closing Shift", closing_name, "creation", old_creation, update_modified=False)

		orig_user = frappe.session.user
		try:
			frappe.set_user(self.shift.user)
			res = get_recent_auto_closed_shift()
			self.assertIsNone(res)
		finally:
			frappe.set_user(orig_user)
