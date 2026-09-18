# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Closing-shift tests in POS Invoice mode.

A POS Next POS Invoice posts its own GL + Stock Ledger entries at submit, so
closing a shift must only collect it into pos_transactions (linking it for
edit/cancel blocking) and must NOT consolidate it into a Sales Invoice —
consolidating an already-accounted invoice would post the same money and stock
twice. Run via
pos_next/_pn_run_tests.py pos_next.tests.test_pos_invoice_closing
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import submit_invoice
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	get_pos_invoices,
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

	def test_close_shift_does_not_consolidate(self):
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
		frappe.db.commit()

		# No consolidation: the POS Invoice keeps its own books (GL/SLE made at
		# submit stay the only ones) and is never folded into a Sales Invoice.
		posi = frappe.get_doc("POS Invoice", self.posi_name)
		self.assertFalse(posi.consolidated_invoice)
		self.assertEqual(frappe.db.count("POS Invoice Merge Log", {"pos_invoice": self.posi_name}), 0)

		# The entries made at submit are still the only ones (nothing doubled).
		self.assertGreater(
			frappe.db.count("GL Entry", {"voucher_no": self.posi_name, "voucher_type": "POS Invoice"}),
			0,
		)
		self.assertGreater(
			frappe.db.count(
				"Stock Ledger Entry", {"voucher_no": self.posi_name, "voucher_type": "POS Invoice"}
			),
			0,
		)

	def test_shift_holding_both_doctypes_is_collected_whole(self):
		"""A shift can hold both doctypes (invoices created before the site-wide
		switch). Closing must read both, or the older half silently vanishes
		from every closing total."""
		# a Sales Invoice on the same shift, as pre-switch rows are
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": self.customer,
				"company": self.profile.company,
				"pos_profile": self.profile.name,
				"is_pos": 1,
				"update_stock": 0,
				"posa_pos_opening_shift": self.shift.name,
				"items": [
					{"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
				],
				"payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
			}
		)
		si.flags.ignore_permissions = True
		si.insert()
		si.submit()
		self._created.append(si.name)

		collected = {row["name"] for row in get_pos_invoices(self.shift.name)}
		self.assertIn(self.posi_name, collected)
		self.assertIn(si.name, collected)
