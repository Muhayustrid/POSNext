# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-07: one-use-per-customer is re-checked at submit, under the coupon lock.

check_coupon_code guards the draft save only, and only sees committed
invoices: two drafts saved before either was submitted both pass it. The
second submit must be refused by increment_coupon_usage instead of silently
burning a second use.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_coupon_one_use_resubmit
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import today

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.pos_next.doctype.pos_coupon.pos_coupon import increment_coupon_usage
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestOneUseCouponResubmit(POSInvoiceModeMixin, FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.coupon = self._make_coupon()

	def _make_coupon(self):
		code = "CORBE07" + frappe.generate_hash(length=6).upper()
		doc = frappe.get_doc(
			{
				"doctype": "POS Coupon",
				"coupon_name": f"_CORBE07 Coupon {code}",
				"coupon_code": code,
				"coupon_type": "Promotional",
				"company": self.profile.company,
				"discount_type": "Percentage",
				"discount_percentage": 10,
				"one_use": 1,
				"maximum_use": 5,
			}
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.delete_doc("POS Coupon", doc.name, force=1, ignore_permissions=True)
		)
		return doc

	def _used(self):
		return frappe.db.get_value("POS Coupon", self.coupon.name, "used") or 0

	def test_second_submit_of_one_use_coupon_blocked(self):
		"""Two drafts stamped before either was submitted (the draft-save check
		counts only docstatus=1): the second submit must throw instead of
		silently burning the customer's second use."""
		echo1 = update_invoice(json.dumps(self._payload(coupon_code=self.coupon.coupon_code)))
		echo2 = update_invoice(json.dumps(self._payload(coupon_code=self.coupon.coupon_code)))
		self._created.append(echo2["name"])  # sweep the leftover draft

		result = submit_invoice(invoice=echo1)
		self._created.append(result["name"])
		self.assertEqual(self._used(), 1)  # the first legitimate use goes through

		with self.assertRaises(frappe.ValidationError):
			submit_invoice(invoice=echo2)
		self.assertEqual(self._used(), 1)  # quota untouched by the refused claim

	def test_increment_rechecks_one_use_per_customer(self):
		"""Direct: under the lock, a one_use coupon already used by this
		customer is refused; another customer still passes."""
		self._fabricate_submitted_usage(self.customer, self.coupon.coupon_code)
		before = self._used()

		with self.assertRaises(frappe.ValidationError):
			increment_coupon_usage(self.coupon.coupon_code, self.customer)
		self.assertEqual(self._used(), before)

		other = frappe.db.get_value(
			"Customer", {"is_internal_customer": 0, "name": ["!=", self.customer]}, "name"
		)
		if other:
			increment_coupon_usage(self.coupon.coupon_code, other)
			self.assertEqual(self._used(), before + 1)

	def _fabricate_submitted_usage(self, customer, coupon_code):
		"""A submitted Sales Invoice row without the accounting fixture: the
		one-use count only reads docstatus=1 rows (same shape as the SEC-22
		fabricated purchase history)."""
		if not frappe.get_meta("Sales Invoice").has_field("pos_coupon_code"):
			self.skipTest("no pos_coupon_code custom field on Sales Invoice")
		doc = frappe.new_doc("Sales Invoice")
		doc.company = self.profile.company
		doc.customer = customer
		doc.is_pos = 1
		doc.posting_date = today()
		doc.pos_coupon_code = coupon_code
		doc.name = "SI-CORBE07-" + frappe.generate_hash(length=8)
		doc.db_insert()
		frappe.db.set_value("Sales Invoice", doc.name, "docstatus", 1, update_modified=False)
		self.addCleanup(lambda: frappe.db.delete("Sales Invoice", {"name": doc.name}))
		return doc.name
