# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Tests for company-wide sales lists, return scope, and shift info across POS Profiles.

Spec decisions: D8, D10.
Plan: Task 5.
"""

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, nowdate

from pos_next.api.invoices import (
	check_invoice_return_validity,
	company_profiles,
	get_invoice_for_return,
	get_invoices,
	get_returnable_invoices,
	prepare_return_invoice,
	search_invoice_by_number,
	search_invoices_for_return,
)
from pos_next.api.partial_payments import (
	get_partial_paid_invoices,
	get_partial_payment_summary,
	get_unpaid_invoices,
	get_unpaid_summary,
)
from pos_next.invoice_type import SALES_INVOICE, get_pos_invoice_doctype
from pos_next.tests._posi_test_utils import _set_invoice_type
from pos_next.tests.price_group_helpers import (
	get_default_customer,
	make_test_company,
	make_test_pos_profile,
	make_test_warehouse,
)

ADMIN = "Administrator"


class TestCompanySalesScope(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._invoice_type_baseline = frappe.db.get_single_value(
			"POS Next Global Settings", "invoice_type"
		)
		_set_invoice_type(SALES_INVOICE)
		cls.doctype = get_pos_invoice_doctype()
		cls._invoices = []

		suffix = uuid.uuid4().hex[:6]
		cls.company_a = make_test_company(f"ScpA{suffix}")
		cls.company_b = make_test_company(f"ScpB{suffix}")

		wh_a1 = make_test_warehouse(f"WA1{suffix}", cls.company_a)
		wh_a2 = make_test_warehouse(f"WA2{suffix}", cls.company_a)
		wh_b = make_test_warehouse(f"WB{suffix}", cls.company_b)

		cls.p1 = make_test_pos_profile(f"P1{suffix}", cls.company_a, wh_a1)
		cls.p2 = make_test_pos_profile(f"P2{suffix}", cls.company_a, wh_a2)
		# P2 is disabled but in the same company
		frappe.db.set_value("POS Profile", cls.p2, "disabled", 1)

		# P3 is in a different company
		cls.p3 = make_test_pos_profile(f"P3{suffix}", cls.company_b, wh_b)

		cls.cashier = f"cashier.{uuid.uuid4().hex[:8]}@example.com"
		cls.intruder = f"intruder.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.cashier, cls.intruder):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": "SalesScope Tester",
					"roles": [{"role": "POSNext Cashier"}, {"role": "Accounts Manager"}],
				}
			).insert(ignore_permissions=True)

		# Cashier is a member of P1 ONLY
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": cls.p1,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": cls.cashier,
			}
		).insert(ignore_permissions=True)

		item_names = frappe.get_all(
			"Item",
			filters={
				"disabled": 0,
				"is_sales_item": 1,
				"is_stock_item": 1,
				"has_batch_no": 0,
				"has_serial_no": 0,
			},
			pluck="name",
			limit=1,
		)
		cls.item = item_names[0]
		cls.customer_a = get_default_customer(cls.company_a)
		cls.customer_b = get_default_customer(cls.company_b)

		# Opening shifts with shift_slot
		cls.shift_p1 = cls._make_shift(cls.p1, cls.company_a, cls.cashier, "Morning")
		cls.shift_p2 = cls._make_shift(cls.p2, cls.company_a, ADMIN, "Afternoon")
		cls.shift_p3 = cls._make_shift(cls.p3, cls.company_b, ADMIN, "Night")

		# Invoices on P1
		cls.inv_p1_paid = cls._make_invoice(
			cls.p1, cls.company_a, cls.customer_a, cls.shift_p1.name, 100, 100, 0
		)
		cls.inv_p1_part = cls._make_invoice(
			cls.p1, cls.company_a, cls.customer_a, cls.shift_p1.name, 200, 50, 150
		)
		cls.inv_p1_unp = cls._make_invoice(
			cls.p1, cls.company_a, cls.customer_a, cls.shift_p1.name, 300, 0, 300
		)

		# Invoices on P2 (same company, disabled profile)
		cls.inv_p2_paid = cls._make_invoice(
			cls.p2, cls.company_a, cls.customer_a, cls.shift_p2.name, 100, 100, 0
		)
		cls.inv_p2_part = cls._make_invoice(
			cls.p2, cls.company_a, cls.customer_a, cls.shift_p2.name, 200, 80, 120
		)
		cls.inv_p2_unp = cls._make_invoice(
			cls.p2, cls.company_a, cls.customer_a, cls.shift_p2.name, 400, 0, 400
		)

		# Invoices on P3 (other company)
		cls.inv_p3_paid = cls._make_invoice(
			cls.p3, cls.company_b, cls.customer_b, cls.shift_p3.name, 100, 100, 0
		)
		cls.inv_p3_part = cls._make_invoice(
			cls.p3, cls.company_b, cls.customer_b, cls.shift_p3.name, 200, 50, 150
		)
		cls.inv_p3_unp = cls._make_invoice(
			cls.p3, cls.company_b, cls.customer_b, cls.shift_p3.name, 500, 0, 500
		)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		if getattr(cls, "_invoice_type_baseline", None):
			_set_invoice_type(cls._invoice_type_baseline)

		def _safe(step):
			try:
				step()
			except Exception:
				pass

		for email in (getattr(cls, "cashier", None), getattr(cls, "intruder", None)):
			if email:
				_safe(lambda e=email: frappe.db.delete("Error Log", {"owner": e}))
				_safe(lambda e=email: frappe.delete_doc("User", e, force=1, ignore_permissions=True))

		for shift in (
			getattr(cls, "shift_p1", None),
			getattr(cls, "shift_p2", None),
			getattr(cls, "shift_p3", None),
		):
			s_name = getattr(shift, "name", shift)
			if s_name and frappe.db.exists("POS Opening Shift", s_name):
				def _del_shift(s=s_name):
					try:
						doc = frappe.get_doc("POS Opening Shift", s)
						if doc.docstatus == 1:
							doc.cancel()
						frappe.delete_doc("POS Opening Shift", s, force=1, ignore_permissions=True)
					except Exception:
						frappe.db.delete("POS Opening Shift Detail", {"parent": s})
						frappe.db.delete("POS Opening Shift", {"name": s})
				_safe(_del_shift)

		companies = [c for c in (getattr(cls, "company_a", None), getattr(cls, "company_b", None)) if c]
		profiles = [p for p in (getattr(cls, "p1", None), getattr(cls, "p2", None), getattr(cls, "p3", None)) if p]

		for comp in companies:
			_safe(
				lambda c=comp: frappe.db.delete(
					"POS Opening Shift Detail",
					{"parent": ("in", frappe.get_all("POS Opening Shift", {"company": c}, pluck="name"))},
				)
			)
			_safe(lambda c=comp: frappe.db.delete("POS Opening Shift", {"company": c}))

		invoices = getattr(cls, "_invoices", [])
		for doctype in ("POS Invoice", "Sales Invoice"):
			if invoices:
				_safe(lambda dt=doctype: frappe.db.delete(f"{dt} Item", {"parent": ("in", invoices)}))
				_safe(lambda dt=doctype: frappe.db.delete(dt, {"name": ("in", invoices)}))
			for comp in companies:
				_safe(
					lambda dt=doctype, c=comp: frappe.db.delete(
						f"{dt} Item",
						{"parent": ("in", frappe.get_all(dt, {"company": c}, pluck="name"))},
					)
				)
				_safe(lambda dt=doctype, c=comp: frappe.db.delete(dt, {"company": c}))

		for prof in profiles:
			_safe(lambda p=prof: frappe.db.delete("POS Profile User", {"parent": p}))
			_safe(lambda p=prof: frappe.db.delete("POS Payment Method", {"parent": p}))
			_safe(lambda p=prof: frappe.db.delete("POS Settings", {"pos_profile": p}))
			_safe(lambda p=prof: frappe.delete_doc("POS Profile", p, force=1, ignore_permissions=True))

		for comp in companies:
			_safe(lambda c=comp: frappe.db.delete("Warehouse", {"company": c}))
			_safe(lambda c=comp: frappe.db.delete("Expense Claim Account", {"company": c}))
			_safe(lambda c=comp: frappe.delete_doc("Company", c, force=1, ignore_permissions=True))

		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _make_shift(cls, profile, company, user, slot):
		mop = frappe.get_all(
			"POS Payment Method",
			{"parent": profile, "parenttype": "POS Profile"},
			pluck="mode_of_payment",
			limit=1,
		)[0]
		doc = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": profile,
				"company": company,
				"user": user,
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"shift_slot": slot,
				"balance_details": [{"mode_of_payment": mop, "amount": 0}],
			}
		).insert(ignore_permissions=True)
		doc.docstatus = 1
		doc.status = "Open"
		doc.save(ignore_permissions=True)
		frappe.db.set_value("POS Opening Shift", doc.name, "shift_slot", slot, update_modified=False)
		doc.shift_slot = slot
		return doc

	@classmethod
	def _make_invoice(cls, profile, company, customer, shift, grand_total, paid, outstanding):
		currency = frappe.db.get_value("Company", company, "default_currency") or "INR"
		doc = frappe.get_doc(
			{
				"doctype": cls.doctype,
				"company": company,
				"customer": customer,
				"pos_profile": profile,
				"is_pos": 1,
				"is_return": 0,
				"posting_date": nowdate(),
				"posa_pos_opening_shift": shift,
				"grand_total": grand_total,
				"net_total": grand_total,
				"paid_amount": paid,
				"outstanding_amount": outstanding,
				"currency": currency,
				"conversion_rate": 1.0,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)

		item_row = frappe.get_doc(
			{
				"doctype": f"{cls.doctype} Item",
				"parent": doc.name,
				"parenttype": cls.doctype,
				"parentfield": "items",
				"idx": 1,
				"docstatus": 1,
				"item_code": cls.item,
				"qty": 1,
				"rate": grand_total,
				"amount": grand_total,
			}
		)
		item_row.flags.ignore_validate = True
		item_row.flags.ignore_mandatory = True
		item_row.insert(ignore_permissions=True)
		frappe.db.set_value(cls.doctype, doc.name, "docstatus", 1, update_modified=False)
		if not hasattr(cls, "_invoices"):
			cls._invoices = []
		cls._invoices.append(doc.name)
		return doc.name

	def tearDown(self):
		frappe.set_user(ADMIN)
		super().tearDown()

	def test_company_profiles_helper(self):
		# Cashier is a member of P1
		frappe.set_user(self.cashier)
		profiles = company_profiles(self.p1)
		self.assertIn(self.p1, profiles)
		self.assertIn(self.p2, profiles)  # disabled same-company included
		self.assertNotIn(self.p3, profiles)  # other company excluded

		# Non-member raises PermissionError
		frappe.set_user(self.intruder)
		with self.assertRaises(frappe.PermissionError):
			company_profiles(self.p1)

	def test_get_invoices_company_scope_and_shift_slot(self):
		frappe.set_user(self.cashier)
		rows = get_invoices(pos_profile=self.p1, limit=100)
		names = [r["name"] for r in rows]

		# P1 and P2 invoices present, P3 excluded
		self.assertIn(self.inv_p1_paid, names)
		self.assertIn(self.inv_p2_paid, names)
		self.assertNotIn(self.inv_p3_paid, names)

		# Rows carry pos_profile and shift_slot
		row_p1 = next(r for r in rows if r["name"] == self.inv_p1_paid)
		self.assertEqual(row_p1["pos_profile"], self.p1)
		self.assertEqual(row_p1["shift_slot"], "Morning")

		row_p2 = next(r for r in rows if r["name"] == self.inv_p2_paid)
		self.assertEqual(row_p2["pos_profile"], self.p2)
		self.assertEqual(row_p2["shift_slot"], "Afternoon")

	def test_get_returnable_invoices_company_scope_and_shift_slot(self):
		frappe.set_user(self.cashier)
		rows = get_returnable_invoices(limit=100, pos_profile=self.p1)
		names = [r["name"] for r in rows]

		self.assertIn(self.inv_p1_paid, names)
		self.assertIn(self.inv_p2_paid, names)
		self.assertNotIn(self.inv_p3_paid, names)

		row_p1 = next(r for r in rows if r["name"] == self.inv_p1_paid)
		self.assertEqual(row_p1["pos_profile"], self.p1)
		self.assertEqual(row_p1["shift_slot"], "Morning")

		row_p2 = next(r for r in rows if r["name"] == self.inv_p2_paid)
		self.assertEqual(row_p2["pos_profile"], self.p2)
		self.assertEqual(row_p2["shift_slot"], "Afternoon")

	def test_search_invoice_by_number_company_scope_and_shift_slot(self):
		frappe.set_user(self.cashier)
		# Search matching P1 and P2
		rows = search_invoice_by_number(self.inv_p2_paid[-6:], pos_profile=self.p1)
		names = [r["name"] for r in rows]
		self.assertIn(self.inv_p2_paid, names)
		row_p2 = next(r for r in rows if r["name"] == self.inv_p2_paid)
		self.assertEqual(row_p2["pos_profile"], self.p2)
		self.assertEqual(row_p2["shift_slot"], "Afternoon")

		# Search matching P3 yields nothing
		rows_p3 = search_invoice_by_number(self.inv_p3_paid[-6:], pos_profile=self.p1)
		self.assertNotIn(self.inv_p3_paid, [r["name"] for r in rows_p3])

	def test_search_invoices_for_return_company_scope_and_shift_slot(self):
		frappe.set_user(self.cashier)
		result = search_invoices_for_return(pos_profile=self.p1, doctype=self.doctype)
		names = [r["name"] for r in result["invoices"]]
		self.assertIn(self.inv_p1_paid, names)
		self.assertIn(self.inv_p2_paid, names)
		self.assertNotIn(self.inv_p3_paid, names)

		row_p2 = next(r for r in result["invoices"] if r["name"] == self.inv_p2_paid)
		self.assertEqual(row_p2["pos_profile"], self.p2)
		self.assertEqual(row_p2["shift_slot"], "Afternoon")

	def test_partial_paid_invoices_and_summary(self):
		frappe.set_user(self.cashier)
		rows = get_partial_paid_invoices(pos_profile=self.p1)
		names = [r["name"] for r in rows]
		self.assertIn(self.inv_p1_part, names)
		self.assertIn(self.inv_p2_part, names)
		self.assertNotIn(self.inv_p3_part, names)

		row_p2 = next(r for r in rows if r["name"] == self.inv_p2_part)
		self.assertEqual(row_p2["pos_profile"], self.p2)
		self.assertEqual(row_p2["shift_slot"], "Afternoon")

		summary = get_partial_payment_summary(pos_profile=self.p1)
		self.assertEqual(summary["count"], 2)
		self.assertEqual(summary["total_outstanding"], 150 + 120)
		self.assertEqual(summary["total_paid"], 50 + 80)

	def test_unpaid_invoices_and_summary(self):
		frappe.set_user(self.cashier)
		rows = get_unpaid_invoices(pos_profile=self.p1)
		names = [r["name"] for r in rows]
		# Includes both partial and fully unpaid of P1 and P2
		self.assertIn(self.inv_p1_unp, names)
		self.assertIn(self.inv_p1_part, names)
		self.assertIn(self.inv_p2_unp, names)
		self.assertIn(self.inv_p2_part, names)
		self.assertNotIn(self.inv_p3_unp, names)
		self.assertNotIn(self.inv_p3_part, names)

		row_p2 = next(r for r in rows if r["name"] == self.inv_p2_unp)
		self.assertEqual(row_p2["pos_profile"], self.p2)
		self.assertEqual(row_p2["shift_slot"], "Afternoon")

		summary = get_unpaid_summary(pos_profile=self.p1)
		self.assertEqual(summary["count"], 4)
		self.assertEqual(summary["total_outstanding"], 150 + 300 + 120 + 400)

	def test_return_on_same_company_succeeds_other_company_raises(self):
		frappe.set_user(self.cashier)
		# Cashier has an open shift on P1 (company_a).
		# Return on P2 (company_a) succeeds:
		validity = check_invoice_return_validity(self.inv_p2_paid)
		self.assertTrue(validity.get("valid"))

		inv_dict = get_invoice_for_return(self.inv_p2_paid)
		self.assertEqual(inv_dict["name"], self.inv_p2_paid)

		prep = prepare_return_invoice(self.inv_p2_paid, pos_opening_shift=self.shift_p1.name)
		self.assertEqual(prep["return_against"], self.inv_p2_paid)

		# Return on P3 (company_b) raises / reports wrong_outlet:
		validity_p3 = check_invoice_return_validity(self.inv_p3_paid)
		self.assertFalse(validity_p3.get("valid"))
		self.assertEqual(validity_p3.get("error_type"), "wrong_outlet")

		with self.assertRaises(frappe.PermissionError):
			get_invoice_for_return(self.inv_p3_paid)

		with self.assertRaises(frappe.PermissionError):
			prepare_return_invoice(self.inv_p3_paid, pos_opening_shift=self.shift_p1.name)

	def test_non_member_raises_permission_error(self):
		frappe.set_user(self.intruder)
		with self.assertRaises(frappe.PermissionError):
			get_invoices(pos_profile=self.p1)
		with self.assertRaises(frappe.PermissionError):
			get_returnable_invoices(pos_profile=self.p1)
		with self.assertRaises(frappe.PermissionError):
			get_partial_paid_invoices(pos_profile=self.p1)
		with self.assertRaises(frappe.PermissionError):
			get_unpaid_invoices(pos_profile=self.p1)
		with self.assertRaises(frappe.PermissionError):
			get_partial_payment_summary(pos_profile=self.p1)
		with self.assertRaises(frappe.PermissionError):
			get_unpaid_summary(pos_profile=self.p1)
