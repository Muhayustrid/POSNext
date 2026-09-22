# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Task 11 (Fase 5) acceptance tests: the batch of small security gates.

- SEC-16: jinja closing-print helpers return an empty structure for users
  without read access / shift ownership — never a throw, never data.
- SEC-18: partial-payment `account` must be a Bank/Cash account of the
  invoice's company.
- SEC-19: customer search is permission-scoped (get_list) and not a
  1-character PII oracle; full customer documents need Customer read.
- SEC-20: a forged pos_queue_number above the daily clamp cannot move (or
  seed) the shared queue counter.
- SEC-21: QZ Tray certificate/signing endpoints are print-role only.
- E1: delete_invoice is profile+owner gated and no longer force-deletes.
- E2: check_invoice_return_validity cannot probe other profiles' invoices.
- E3: get_credit_invoices requires POS Profile membership (same as the T6
  summary gate).

Run via pos_next/_pn_run_tests.py pos_next.api.test_medium_gates_security
"""

import json
import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, nowdate

from pos_next.api.credit_sales import get_credit_invoices
from pos_next.api.customers import get_customer_details, get_customers
from pos_next.api.invoices import (
	check_invoice_return_validity,
	delete_invoice,
	submit_invoice,
	update_invoice,
)
from pos_next.api.partial_payments import create_payment_entry
from pos_next.api.qz import get_certificate, get_certificate_download, sign_message
from pos_next.api.shifts import submit_closing_shift
from pos_next.invoice_type import get_pos_invoice_doctype
from pos_next.overrides.queue_counter import bump_queue_counter
from pos_next.pos_next.utils.pos_closing_print import get_sales_recap

ADMIN = "Administrator"
OPENING_CASH = 100

# Schedule-safe profile (same filter as test_invoice_authorization_security /
# test_closing_shift_security): shift flows must never throw for being outside
# a scheduled window on this shared dev site.
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]


class TestMediumGatesSecurity(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.profile = frappe.db.get_value(
			"POS Profile",
			_PROFILE_FILTER,
			["name", "company", "warehouse"],
			as_dict=True,
			order_by="creation asc",
		)
		cls.mode = (
			frappe.get_all(
				"POS Payment Method",
				{"parent": cls.profile.name, "parenttype": "POS Profile"},
				pluck="mode_of_payment",
				limit=1,
			)
			if cls.profile
			else None
		)
		cls.item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
			pluck="name",
			limit=1,
		)
		cls.customer = frappe.db.get_value("Customer", {"is_internal_customer": 0}, "name")
		if not (cls.profile and cls.mode and cls.item and cls.customer):
			raise unittest.SkipTest("no usable POS Profile / item / customer")

		cls.doctype = get_pos_invoice_doctype()

		# cashier and cashier2 are profile users; the intruder is roleless and
		# belongs to no profile.
		cls.cashier = f"med-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.cashier2 = f"med-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.intruder = f"med-sec.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.cashier, cls.cashier2, cls.intruder):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Medium Gate Tester"}
			).insert(ignore_permissions=True)

		for email in (cls.cashier, cls.cashier2):
			frappe.get_doc(
				{
					"doctype": "POS Profile User",
					"parent": cls.profile.name,
					"parenttype": "POS Profile",
					"parentfield": "pos_profile_user",
					"user": email,
				}
			).insert(ignore_permissions=True)

		# Roles on the cashier only (never on cashier2/intruder): the real
		# POSNext Cashier role (Customer read + POS Opening Shift write for
		# the closing flow) plus Stock User for the invoice submit recipe.
		for role in ("Stock User", "POSNext Cashier"):
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": cls.cashier,
					"parenttype": "User",
					"parentfield": "roles",
					"role": role,
				}
			).insert(ignore_permissions=True)

		# E1 drops force-delete, so the draft owner needs a role granting
		# delete on the doctype (same discovery as
		# test_invoice_authorization_security.setUpClass).
		cls.delete_role = None
		for perm_doctype in ("Custom DocPerm", "DocPerm"):
			roles = frappe.get_all(
				perm_doctype,
				filters={"parent": cls.doctype, "delete": 1},
				pluck="role",
				order_by="role",
			)
			if roles:
				cls.delete_role = ([r for r in roles if r != "System Manager"] or roles)[0]
				break
		if cls.delete_role:
			frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": cls.cashier,
					"parenttype": "User",
					"parentfield": "roles",
					"role": cls.delete_role,
				}
			).insert(ignore_permissions=True)

		frappe.clear_cache(user=cls.cashier)
		frappe.clear_cache(user=cls.cashier2)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			# tolerant teardown: one leftover must never abort the remaining
			# cleanup on this shared dev site
			try:
				step()
			except Exception:
				pass

		for email in (cls.cashier, cls.cashier2, cls.intruder):
			_safe(lambda email=email: frappe.db.delete("Error Log", {"owner": email}))
			_safe(lambda email=email: frappe.delete_doc("User", email, force=1, ignore_permissions=True))
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(ADMIN)

	def tearDown(self):
		frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- helpers

	def _payload(self, payment_amount=None, **overrides):
		payload = {
			"pos_profile": self.profile.name,
			"customer": self.customer,
			"items": [
				{"item_code": self.item[0], "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
			],
			"payments": [{"mode_of_payment": self.mode[0], "amount": payment_amount or 100}],
		}
		payload.update(overrides)
		return payload

	def _make_stock_receipt(self):
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"purpose": "Material Receipt",
				"company": self.profile.company,
				"items": [
					{
						"item_code": self.item[0],
						"qty": 20,
						"t_warehouse": self.profile.warehouse,
						"allow_zero_valuation_rate": 1,
					}
				],
			}
		)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()

	def _make_draft(self, user, **overrides):
		frappe.set_user(user)
		return update_invoice(self._payload(**overrides))

	def _submit(self, user, payment_amount=None):
		"""Full legit submit as `user` (opening shift recipe of
		api/test_pos_invoice_submit.py). payment_amount < 100 leaves an
		outstanding balance for the partial-payment gates."""
		self._make_stock_receipt()
		frappe.set_user(ADMIN)
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": user,
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
			}
		).insert(ignore_permissions=True)
		shift.reload()
		shift.submit()

		frappe.set_user(user)
		draft = update_invoice(self._payload(payment_amount=payment_amount))
		submit_invoice(
			invoice=json.dumps(
				self._payload(
					payment_amount=payment_amount,
					name=draft["name"],
					doctype=self.doctype,
					posa_pos_opening_shift=shift.name,
				)
			),
			data=json.dumps({}),
		)
		return draft["name"]

	# ---------------------------------------------------------------- SEC-16

	def test_sec16_closing_recap_print_is_scoped(self):
		frappe.set_user(ADMIN)
		opening = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": self.cashier,
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": OPENING_CASH}],
			}
		).insert(ignore_permissions=True)
		opening.submit()

		frappe.set_user(self.cashier)
		try:
			closing_name = submit_closing_shift(
				json.dumps(
					{
						"pos_opening_shift": opening.name,
						"payment_reconciliation": [
							{"mode_of_payment": self.mode[0], "closing_amount": OPENING_CASH}
						],
					}
				)
			)["name"]

			# the shift's own cashier may still print their recap
			recap = get_sales_recap(closing_name)
			self.assertEqual(recap["total_order"], 0)
			self.assertEqual(len(recap["payment_methods"]), 1)

			# a roleless outsider gets an empty structure — no throw, no data
			frappe.set_user(self.intruder)
			recap = get_sales_recap(closing_name)
			self.assertEqual(recap["total_order"], 0)
			self.assertEqual(recap["total_sales"], 0)
			self.assertEqual(recap["payment_methods"], [])
			self.assertEqual(get_sales_recap(closing_name)["categories"], [])
		finally:
			frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- SEC-18

	def test_sec18_payment_account_company_and_type_validated(self):
		invoice = self._submit(self.cashier, payment_amount=50)  # outstanding 50

		# same-company non-Bank/Cash account is rejected
		bad_type = frappe.get_all(
			"Account",
			filters={
				"company": self.profile.company,
				"is_group": 0,
				"account_type": ["not in", ["Bank", "Cash"]],
			},
			pluck="name",
			limit=1,
		)
		frappe.set_user(self.cashier)
		try:
			if bad_type:
				with self.assertRaises(frappe.ValidationError):
					create_payment_entry(invoice_name=invoice, amount=10, payment_account=bad_type[0])

			# other-company account is rejected (skipped on single-company sites)
			other_company = frappe.get_all(
				"Account",
				filters={"company": ["!=", self.profile.company], "account_type": "Cash", "is_group": 0},
				pluck="name",
				limit=1,
			)
			if other_company:
				with self.assertRaises(frappe.ValidationError):
					create_payment_entry(invoice_name=invoice, amount=10, payment_account=other_company[0])

			# the legit cash account still books the payment
			from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

			account = get_bank_cash_account(self.mode[0], self.profile.company).get("account")
			pe_name = create_payment_entry(invoice_name=invoice, amount=10, payment_account=account)
			self.assertTrue(pe_name)
			self.assertEqual(frappe.db.get_value("Payment Entry", pe_name, "docstatus"), 1)
		finally:
			frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- SEC-19

	def test_sec19_customers_scoped_and_not_one_char_oracle(self):
		# single-character probe returns empty even for Administrator
		self.assertEqual(get_customers(search_term="a"), [])

		if frappe.has_permission("Customer", "read", user=self.intruder):
			self.skipTest("site grants Customer read to roleless users")

		# roleless user: no list, no full customer dump
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_customers(search_term=self.customer[:4])
			with self.assertRaises(frappe.PermissionError):
				get_customer_details(self.customer)
		finally:
			frappe.set_user(ADMIN)

		# the legit path still returns the searched customer for the cashier
		frappe.set_user(self.cashier)
		try:
			rows = get_customers(search_term=self.customer[:4])
			self.assertIn(self.customer, [row.name for row in rows])
			self.assertTrue(get_customer_details(self.customer))
		finally:
			frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- SEC-20

	def test_sec20_forged_queue_number_cannot_move_counter(self):
		company = self.profile.company
		today = nowdate()
		name = frappe.db.get_value("POS Queue Counter", {"company": company, "date": today})
		original = frappe.db.get_value("POS Queue Counter", name, "current_number") if name else None
		if name:
			frappe.db.set_value("POS Queue Counter", name, "current_number", 2)
		else:
			name = (
				frappe.get_doc(
					{
						"doctype": "POS Queue Counter",
						"company": company,
						"date": today,
						"current_number": 2,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		try:
			# legit offline receipt advances the counter (happy path survives)
			bump_queue_counter(frappe._dict(company=company, pos_queue_number=7, pos_queue_date=today))
			self.assertEqual(frappe.db.get_value("POS Queue Counter", name, "current_number"), 7)

			# forged payload far above the daily clamp is ignored
			bump_queue_counter(
				frappe._dict(company=company, pos_queue_number=999999, pos_queue_date=today)
			)
			self.assertEqual(frappe.db.get_value("POS Queue Counter", name, "current_number"), 7)

			# and it cannot even seed a counter on a day without one
			bump_queue_counter(
				frappe._dict(company=company, pos_queue_number=999999, pos_queue_date="2020-01-01")
			)
			self.assertIsNone(
				frappe.db.get_value("POS Queue Counter", {"company": company, "date": "2020-01-01"})
			)
		finally:
			if original is None:
				frappe.db.delete("POS Queue Counter", name)
			else:
				frappe.db.set_value("POS Queue Counter", name, "current_number", original)

	# ---------------------------------------------------------------- SEC-21

	def test_sec21_qz_endpoints_are_print_role_only(self):
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_certificate()
			with self.assertRaises(frappe.PermissionError):
				get_certificate_download()
			with self.assertRaises(frappe.PermissionError):
				sign_message("hello")
		finally:
			frappe.set_user(ADMIN)

		# Administrator passes the role gate (the certificate may simply not
		# be set up on this site — that is a different, non-permission error)
		try:
			get_certificate()
		except frappe.PermissionError:
			self.fail("QZ role gate blocked Administrator")
		except Exception:
			pass
		try:
			sign_message("hello")
		except frappe.PermissionError:
			self.fail("QZ role gate blocked Administrator")
		except Exception:
			pass

	# -------------------------------------------------------------------- E1

	def test_e1_delete_invoice_is_profile_and_owner_gated(self):
		draft = self._make_draft(self.cashier)
		name = draft["name"]

		# roleless outsider: profile gate
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				delete_invoice(name)
		finally:
			frappe.set_user(ADMIN)
		self.assertTrue(frappe.db.exists(self.doctype, name))

		# same-profile cashier, not the owner: ownership gate
		frappe.set_user(self.cashier2)
		try:
			with self.assertRaises(frappe.PermissionError):
				delete_invoice(name)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(frappe.db.get_value(self.doctype, name, "docstatus"), 0)

		# the owner still deletes their own draft
		frappe.set_user(self.cashier)
		try:
			self.assertIn("Deleted", delete_invoice(name))
		finally:
			frappe.set_user(ADMIN)
		self.assertFalse(frappe.db.exists(self.doctype, name))

	# -------------------------------------------------------------------- E2

	def test_e2_return_validity_check_is_profile_gated(self):
		invoice = self._submit(self.cashier)

		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				check_invoice_return_validity(invoice)
		finally:
			frappe.set_user(ADMIN)

		# the invoice's own profile member still gets the verdict
		frappe.set_user(self.cashier)
		try:
			self.assertIn("valid", check_invoice_return_validity(invoice))
		finally:
			frappe.set_user(ADMIN)

	# -------------------------------------------------------------------- E3

	def test_e3_get_credit_invoices_requires_profile_membership(self):
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_credit_invoices(self.profile.name)
		finally:
			frappe.set_user(ADMIN)

		frappe.set_user(self.cashier)
		try:
			self.assertIsInstance(get_credit_invoices(self.profile.name), list)
		finally:
			frappe.set_user(ADMIN)

		self.assertIsInstance(get_credit_invoices(self.profile.name), list)
