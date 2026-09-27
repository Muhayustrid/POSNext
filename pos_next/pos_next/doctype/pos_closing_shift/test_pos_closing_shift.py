# Copyright (c) 2020, Youssef Restom and Contributors
# See license.txt

"""pending_printed_drafts (COR-BE-15): the closing preview counts the shift's
printed drafts on the resolver's invoice doctype. posa_is_printed exists only
on Sales Invoice (see submit_printed_invoices — POS Invoices carry no
printed-draft state and are never auto-submitted), so the count must be 0 in
POS Invoice mode and printed-only in Sales Invoice mode.

Run via pos_next/_pn_run_tests.py
pos_next.pos_next.doctype.pos_closing_shift.test_pos_closing_shift
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.invoice_type import SALES_INVOICE
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	make_closing_shift_from_opening,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin, _set_invoice_type


class TestPendingPrintedDraftsCount(POSInvoiceModeMixin, FrappeTestCase):
	"""The preview's pending_printed_drafts is correct in both modes."""

	def _preview(self):
		opening = frappe.get_doc("POS Opening Shift", self.shift.name)
		return make_closing_shift_from_opening(json.dumps(opening.as_dict(), default=str))

	def _fabricate_si_draft(self, printed):
		# raw db_insert like tests/test_closing_shift_race.py: the count only
		# reads shift link + docstatus + posa_is_printed, full SI validation
		# would drag in payments/GL just to test a COUNT
		doc = frappe.new_doc("Sales Invoice")
		doc.company = self.profile.company
		doc.customer = self.customer
		doc.is_pos = 1
		doc.posting_date = nowdate()
		doc.posa_pos_opening_shift = self.shift.name
		if printed:
			doc.posa_is_printed = 1
		doc.name = "SI-PPD-" + frappe.generate_hash(length=8)
		doc.db_insert()
		return doc.name

	def _fabricate_pos_draft(self):
		doc = frappe.new_doc("POS Invoice")
		doc.company = self.profile.company
		doc.customer = self.customer
		doc.is_pos = 1
		doc.posting_date = nowdate()
		doc.posa_pos_opening_shift = self.shift.name
		doc.name = "POS-PPD-" + frappe.generate_hash(length=8)
		doc.db_insert()
		return doc.name

	def test_sales_invoice_mode_counts_only_printed_drafts(self):
		_set_invoice_type(SALES_INVOICE)
		try:
			self._fabricate_si_draft(printed=True)
			self._fabricate_si_draft(printed=False)
			preview = self._preview()
			self.assertEqual(preview["pending_printed_drafts"], 1)
		finally:
			_set_invoice_type("POS Invoice")

	def test_pos_invoice_mode_has_no_printed_draft_state(self):
		# the mixin setUp put the site in POS Invoice mode: a plain POS Invoice
		# draft carries no printed-draft state and is never auto-submitted, so
		# nothing is pending — and the resolver's doctype must not be probed
		# for posa_is_printed (column exists on Sales Invoice only)
		self._fabricate_pos_draft()
		preview = self._preview()
		self.assertEqual(preview["pending_printed_drafts"], 0)
