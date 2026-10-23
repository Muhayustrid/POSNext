# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Tests for closing always using expected amounts.

Proves:
1. submit_closing_shift ignores any client-supplied closing_amount and records closing_amount == expected_amount.
2. Direct _submit_closing(counted={...}) path (used by Backdate Entry) still honors explicit counted amounts.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from pos_next.api.invoices import submit_invoice
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	_submit_closing,
	submit_closing_shift,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestClosingAlwaysExpected(POSInvoiceModeMixin, FrappeTestCase):
	def setUp(self):
		super().setUp()
		old_single = frappe.db.get_single_value("POS Settings", "invoice_type")
		if old_single != "POS Invoice":
			frappe.db.set_single_value("POS Settings", "invoice_type", "POS Invoice")
			self.addCleanup(frappe.db.set_single_value, "POS Settings", "invoice_type", old_single)

	def test_submit_ignores_client_counted_amount(self):
		submit_invoice(self._payload())

		closing_name = submit_closing_shift(
			{
				"pos_opening_shift": self.shift.name,
				"payment_reconciliation": [{"mode_of_payment": self.mode[0], "closing_amount": 1}],
			}
		)
		closing = frappe.get_doc("POS Closing Shift", closing_name)
		self.closing = closing

		reconciliation = [r for r in closing.payment_reconciliation if r.mode_of_payment == self.mode[0]]
		self.assertTrue(reconciliation)
		row = reconciliation[0]
		self.assertGreater(flt(row.expected_amount), 0)
		self.assertEqual(flt(row.closing_amount), flt(row.expected_amount))
		self.assertEqual(flt(row.difference), 0)

	def test_backdate_counted_path_kept(self):
		submit_invoice(self._payload())

		closing_name = _submit_closing(
			self.shift.name,
			counted=[{"mode_of_payment": self.mode[0], "closing_amount": 1}],
		)
		closing = frappe.get_doc("POS Closing Shift", closing_name)
		self.closing = closing

		reconciliation = [r for r in closing.payment_reconciliation if r.mode_of_payment == self.mode[0]]
		self.assertTrue(reconciliation)
		row = reconciliation[0]
		self.assertGreater(flt(row.expected_amount), 0)
		self.assertEqual(flt(row.closing_amount), 1)
		self.assertEqual(flt(row.difference), 1 - flt(row.expected_amount))
