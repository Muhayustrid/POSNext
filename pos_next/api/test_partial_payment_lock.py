# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-12: partial-payment endpoints read the outstanding under a row lock.

Two concurrent payment requests must serialize on the invoice row: the second
one must see the outstanding the first one already consumed, not the stale
value loaded by get_doc. Also pins the COR-BE-14 cleanup: the unreachable
`docstatus == 2` branch is gone (docstatus != 1 already covers it).

Run via pos_next/_pn_run_tests.py pos_next.api.test_partial_payment_lock
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from pos_next.api.partial_payments import (
	add_payment_to_partial_invoice,
	create_payment_entry,
)
from pos_next.tests.price_group_helpers import get_default_company, get_default_customer


class TestOutstandingLockedRead(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		cls.customer = get_default_customer()
		if not (cls.company and cls.customer):
			raise unittest.SkipTest("no default company/customer on site")

	def _fabricate_invoice(self, outstanding):
		doc = frappe.new_doc("Sales Invoice")
		doc.company = self.company
		doc.customer = self.customer
		doc.is_pos = 1
		doc.posting_date = today()
		doc.name = "SI-PPLOCK-" + frappe.generate_hash(length=8)
		doc.db_insert()
		frappe.db.set_value("Sales Invoice", doc.name, "docstatus", 1, update_modified=False)
		frappe.db.set_value(
			"Sales Invoice", doc.name, "outstanding_amount", outstanding, update_modified=False
		)
		self.addCleanup(lambda: frappe.db.delete("Sales Invoice", {"name": doc.name}))
		return doc.name

	def _spy_outstanding_reads(self):
		seen = []
		original = frappe.db.get_value

		def wrapper(*args, **kwargs):
			seen.append((args, kwargs))
			return original(*args, **kwargs)

		frappe.db.get_value = wrapper
		self.addCleanup(setattr, frappe.db, "get_value", original)
		return seen

	def _assert_locked_read(self, seen, invoice_name):
		locked = [
			(args, kwargs)
			for args, kwargs in seen
			if len(args) >= 3
			and args[:3] == ("Sales Invoice", invoice_name, "outstanding_amount")
			and kwargs.get("for_update")
		]
		self.assertTrue(locked, "outstanding must be read with for_update")

	def test_create_payment_entry_locks_outstanding_and_refuses_overpay(self):
		name = self._fabricate_invoice(0)
		seen = self._spy_outstanding_reads()
		with self.assertRaises(frappe.ValidationError):
			create_payment_entry(invoice_name=name, amount=10, mode_of_payment="Cash")
		self._assert_locked_read(seen, name)
		# behaviour: nothing was created behind the refusal
		self.assertFalse(frappe.db.exists("Payment Entry", {"reference_no": f"POS-{name}"}))

	def test_add_payment_batch_locks_outstanding_and_refuses_overpay(self):
		name = self._fabricate_invoice(0)
		seen = self._spy_outstanding_reads()
		with self.assertRaises(frappe.ValidationError):
			add_payment_to_partial_invoice(name, [{"mode_of_payment": "Cash", "amount": 10}])
		self._assert_locked_read(seen, name)
		self.assertFalse(frappe.db.exists("Payment Entry", {"reference_no": f"POS-{name}"}))
