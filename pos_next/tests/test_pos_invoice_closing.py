# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Closing-shift tests in POS Invoice mode: make_closing_shift_from_opening
must collect POS Invoices into pos_transactions (pos_invoice column) and
on_submit must consolidate them into a (submitted, is_consolidated) Sales
Invoice with real GL + Stock Ledger entries. Run via
pos_next/_pn_run_tests.py pos_next.tests.test_pos_invoice_closing
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import cint

from pos_next.api.invoices import submit_invoice
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	make_closing_shift_from_opening,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestClosingConsolidation(POSInvoiceModeMixin, FrappeTestCase):
	def setUp(self):
		super().setUp()
		# one submitted POS Invoice on the shift (client doctype guess ignored)
		result = submit_invoice(invoice=self._payload())
		self.posi_name = result.get("name")
		self._created.append(self.posi_name)
		frappe.db.commit()

	def test_close_shift_consolidates(self):
		opening = frappe.get_doc("POS Opening Shift", self.shift.name)
		result = make_closing_shift_from_opening(
			json.dumps(
				{
					"name": self.shift.name,
					"period_start_date": str(opening.period_start_date),
					"pos_profile": self.profile.name,
					"user": "Administrator",
					"company": self.profile.company,
				}
			)
		)
		# POS Invoice mode must fill the pos_invoice column, not sales_invoice
		txns = result["pos_transactions"]
		self.assertEqual(len(txns), 1)
		self.assertEqual(txns[0].get("pos_invoice"), self.posi_name)
		self.assertFalse(txns[0].get("sales_invoice"))

		closing_doc = frappe.get_doc(result)
		closing_doc.flags.ignore_permissions = True
		closing_doc.insert(ignore_permissions=True)
		self.closing = closing_doc
		closing_doc.submit()

		posi = frappe.get_doc("POS Invoice", self.posi_name)
		self.assertTrue(posi.consolidated_invoice)
		cons = frappe.get_doc("Sales Invoice", posi.consolidated_invoice)
		self.assertEqual(cint(cons.is_consolidated), 1)
		self.assertGreater(frappe.db.count("GL Entry", {"voucher_no": cons.name}), 0)
		self.assertGreater(frappe.db.count("Stock Ledger Entry", {"voucher_no": cons.name}), 0)
		self.assertTrue(frappe.db.exists("POS Invoice Merge Log", {"pos_invoice": self.posi_name}))
		frappe.db.commit()
