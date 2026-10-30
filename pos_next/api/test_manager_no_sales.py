# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""POSNext Manager never sells: shift open, draft, submit, delete draft and
partial payments are refused server-side (even with POSNext Cashier too);
the manager can generate a one-day refund code for an allowed company.

Run via pos_next/_pn_run_tests.py pos_next.api.test_manager_no_sales
"""

import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import getdate, today

from pos_next.api.bootstrap import get_initial_data
from pos_next.api.invoices import delete_invoice, submit_invoice, update_invoice
from pos_next.api.partial_payments import add_payment_to_partial_invoice
from pos_next.api.shifts import create_opening_shift
from pos_next.pos_next.doctype.pos_refund_code.pos_refund_code import generate_daily_code

ADMIN = "Administrator"


class TestManagerNoSales(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		if not frappe.db.exists("Role", "POSNext Manager"):
			raise unittest.SkipTest("POSNext Manager role does not exist on this site")
		cls.manager = f"mgr-nosale.{uuid.uuid4().hex[:8]}@example.com"
		user = frappe.get_doc(
			{"doctype": "User", "email": cls.manager, "first_name": "No Sale", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		# a manager who also holds the cashier role is still blocked
		user.add_roles("POSNext Manager", "POSNext Cashier")
		cls.company = frappe.get_all("Company", pluck="name", limit=1)[0]
		frappe.get_doc(
			{"doctype": "User Permission", "user": cls.manager, "allow": "Company", "for_value": cls.company}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=cls.manager)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		frappe.db.delete("POS Refund Code", {"owner": cls.manager})
		frappe.db.delete("User Permission", {"user": cls.manager})
		frappe.delete_doc("User", cls.manager, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user(ADMIN)

	def test_sales_endpoints_refuse_manager(self):
		frappe.set_user(self.manager)
		calls = [
			lambda: create_opening_shift("any", self.company, "[]"),
			lambda: update_invoice({"doctype": "Sales Invoice"}),
			lambda: submit_invoice(invoice={"doctype": "Sales Invoice"}, data={}),
			lambda: delete_invoice("any"),
			lambda: add_payment_to_partial_invoice("any", "[]"),
		]
		for call in calls:
			with self.assertRaises(frappe.PermissionError):
				call()

	def test_bootstrap_reports_cannot_sell(self):
		frappe.set_user(self.manager)
		self.assertFalse(get_initial_data()["can_sell"])
		frappe.set_user(ADMIN)
		self.assertTrue(get_initial_data()["can_sell"])

	def test_daily_refund_code_for_allowed_company(self):
		frappe.set_user(self.manager)
		res = generate_daily_code(self.company)
		frappe.set_user(ADMIN)
		doc = frappe.get_doc("POS Refund Code", {"code": res["code"]})
		self.assertEqual(getdate(doc.valid_from), getdate(today()))
		self.assertEqual(getdate(doc.valid_upto), getdate(today()))
		self.assertEqual(doc.company_scope, "Selected Outlets")
		self.assertEqual([r.company for r in doc.companies], [self.company])

	def test_new_daily_code_disables_previous_but_not_hq_codes(self):
		hq = frappe.get_doc(
			{
				"doctype": "POS Refund Code",
				"status": "Active",
				"company_scope": "Selected Outlets",
				"companies": [{"company": self.company}],
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.db.delete, "POS Refund Code", {"name": hq.name})
		frappe.set_user(self.manager)
		first = generate_daily_code(self.company)["code"]
		second = generate_daily_code(self.company)["code"]
		frappe.set_user(ADMIN)
		status = lambda code: frappe.db.get_value("POS Refund Code", {"code": code}, "status")  # noqa: E731
		self.assertEqual(status(first), "Disabled")
		self.assertEqual(status(second), "Active")
		self.assertEqual(frappe.db.get_value("POS Refund Code", hq.name, "status"), "Active")

	def test_daily_refund_code_refuses_foreign_company_and_cashier(self):
		other = frappe.get_all("Company", filters={"name": ["!=", self.company]}, pluck="name", limit=1)
		frappe.set_user(self.manager)
		if other:
			with self.assertRaises(frappe.PermissionError):
				generate_daily_code(other[0])
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			generate_daily_code(self.company)
