# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""A POS Next POS Invoice moves stock at submit, so ERPNext's reserved-qty
math must not count it again (double deduction -> false negative availability).
Run via
pos_next/_pn_run_tests.py pos_next.tests.test_pos_reserved_qty
"""

import frappe
from erpnext.accounts.doctype.pos_invoice.pos_invoice import get_pos_reserved_qty, get_stock_availability
from erpnext.stock.utils import get_stock_balance
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import submit_invoice
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestPOSReservedQty(POSInvoiceModeMixin, FrappeTestCase):
	def test_pos_next_invoice_not_reserved(self):
		wh = self.profile.warehouse
		before = get_pos_reserved_qty(self.item, wh)

		result = submit_invoice(invoice=self._payload())
		self._created.append(result.get("name"))
		frappe.db.commit()

		self.assertEqual(get_pos_reserved_qty(self.item, wh), before)
		available, _, _ = get_stock_availability(self.item, wh)
		self.assertEqual(available, get_stock_balance(self.item, wh) - before)
