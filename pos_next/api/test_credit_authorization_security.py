# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-08/09 acceptance tests: coupon and credit-sale endpoints cannot be used
by users outside the cashier's context.

- SEC-08: get_active_coupons only returns gift card codes to users with
  POS Coupon read; an arbitrary user gets a PermissionError.
- SEC-09: redeem_customer_credit / cancel_credit_journal_entries require
  doc-level write on the Sales Invoice; get_credit_sale_summary requires
  POS Profile membership (PATTERN A).

Run via pos_next/_pn_run_tests.py pos_next.api.test_credit_authorization_security
"""

import unittest
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.credit_sales import (
	cancel_credit_journal_entries,
	get_credit_sale_summary,
	redeem_customer_credit,
)
from pos_next.api.offers import get_active_coupons

ADMIN = "Administrator"

# Same profile filter as api/test_invoice_authorization_security.py
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]


class TestCreditAuthorizationSecurity(FrappeTestCase):
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
		cls.customer = frappe.db.get_value("Customer", {"is_internal_customer": 0}, "name")
		cls.item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1},
			pluck="name",
			limit=1,
		)
		if not (cls.profile and cls.customer and cls.item):
			raise unittest.SkipTest("no usable POS Profile / customer / item")

		# discover existing roles that grant the doctype perms we need; we must
		# not touch doctype/fixture permissions here (that is SEC-13's realm)
		cls.write_role = cls._role_with("Sales Invoice", "write")
		cls.coupon_read_role = cls._role_with("POS Coupon", "read")
		if not (cls.write_role and cls.coupon_read_role):
			raise unittest.SkipTest("site grants neither Sales Invoice write nor POS Coupon read")

		# cashier: profile member + invoice writer, owns the fixtures below.
		# intruder: roleless, belongs to no profile. coupon_user: may read
		# POS Coupons but is not a profile member.
		cls.cashier = f"credit-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.intruder = f"credit-sec.{uuid.uuid4().hex[:8]}@example.com"
		cls.coupon_user = f"credit-sec.{uuid.uuid4().hex[:8]}@example.com"
		for email in (cls.cashier, cls.intruder, cls.coupon_user):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Credit Sec Tester"}
			).insert(ignore_permissions=True)

		for email, roles in (
			(cls.cashier, (cls.write_role,)),
			(cls.coupon_user, (cls.coupon_read_role,)),
			(cls.intruder, ()),
		):
			for role in roles:
				frappe.get_doc(
					{
						"doctype": "Has Role",
						"parent": email,
						"parenttype": "User",
						"parentfield": "roles",
						"role": role,
					}
				).insert(ignore_permissions=True)
			frappe.clear_cache(user=email)

		# cashier joins the profile (frappe only reads this child table live)
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": cls.profile.name,
				"parenttype": "POS Profile",
				"parentfield": "pos_profile_user",
				"user": cls.cashier,
			}
		).insert(ignore_permissions=True)

		cls.invoice = cls._make_draft_invoice()
		cls.coupon = cls._make_gift_card()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			# tolerant teardown (see test_closing_shift_security): one leftover
			# must never abort the remaining cleanup on this shared dev site
			try:
				step()
			except Exception:
				pass

		for email in (cls.cashier, cls.intruder, cls.coupon_user):
			_safe(lambda email=email: frappe.db.delete("POS Profile User", {"user": email}))
			_safe(lambda email=email: frappe.db.delete("Error Log", {"owner": email}))
			_safe(lambda email=email: frappe.delete_doc("User", email, force=1, ignore_permissions=True))
		_safe(lambda: frappe.delete_doc("Sales Invoice", cls.invoice, force=1, ignore_permissions=True))
		_safe(lambda: frappe.delete_doc("POS Coupon", cls.coupon, force=1, ignore_permissions=True))
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user(ADMIN)

	def tearDown(self):
		frappe.set_user(ADMIN)

	# ---------------------------------------------------------------- helpers

	@classmethod
	def _role_with(cls, doctype, perm):
		"""A non-System-Manager role holding `perm` on `doctype`, if any."""
		for perm_doctype in ("Custom DocPerm", "DocPerm"):
			roles = frappe.get_all(
				perm_doctype,
				filters={"parent": doctype, perm: 1},
				pluck="role",
				order_by="role",
			)
			non_admin = [r for r in roles if r != "System Manager"]
			if roles:
				return (non_admin or roles)[0]
		return None

	@classmethod
	def _make_draft_invoice(cls):
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": cls.profile.company,
				"customer": cls.customer,
				"selling_price_list": frappe.db.get_value(
					"Price List", {"selling": 1, "enabled": 1}, "name"
				),
				"items": [{"item_code": cls.item[0], "qty": 1, "rate": 100}],
			}
		)
		doc.owner = cls.cashier
		doc.flags.ignore_permissions = True
		doc.insert()
		return doc.name

	@classmethod
	def _make_gift_card(cls):
		coupon = frappe.get_doc(
			{
				"doctype": "POS Coupon",
				"coupon_name": f"credit-sec-{uuid.uuid4().hex[:8]}",
				"coupon_type": "Gift Card",
				"coupon_code": uuid.uuid4().hex[:10].upper(),
				"customer": cls.customer,
				"company": cls.profile.company,
				"discount_type": "Amount",
				"discount_amount": 50,
			}
		)
		coupon.flags.ignore_permissions = True
		coupon.insert()
		return coupon.name

	# ---------------------------------------------------------------- SEC-08

	def test_active_coupons_denied_without_pos_coupon_read(self):
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_active_coupons(customer=self.customer, company=self.profile.company)
		finally:
			frappe.set_user(ADMIN)

	def test_active_coupons_return_full_codes_with_read_permission(self):
		frappe.set_user(self.coupon_user)
		try:
			coupons = get_active_coupons(customer=self.customer, company=self.profile.company)
		finally:
			frappe.set_user(ADMIN)

		expected = frappe.db.get_value("POS Coupon", self.coupon, "coupon_code")
		self.assertIn(
			self.coupon, [c["name"] for c in coupons], "authorized user must see the coupon"
		)
		self.assertIn(
			expected, [c["coupon_code"] for c in coupons], "codes must be returned unmasked"
		)

	# ---------------------------------------------------------------- SEC-09

	def test_redeem_denied_without_invoice_write(self):
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				redeem_customer_credit(self.invoice, "[]")
		finally:
			frappe.set_user(ADMIN)

	def test_redeem_allowed_for_invoice_writer(self):
		frappe.set_user(self.cashier)
		try:
			result = redeem_customer_credit(self.invoice, "[]")
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(result, [])

	def test_cancel_je_denied_without_invoice_write(self):
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				cancel_credit_journal_entries(self.invoice)
		finally:
			frappe.set_user(ADMIN)

	def test_cancel_je_allowed_for_invoice_writer(self):
		frappe.set_user(self.cashier)
		try:
			result = cancel_credit_journal_entries(self.invoice)
		finally:
			frappe.set_user(ADMIN)
		self.assertEqual(result, 0)

	def test_summary_denied_for_non_profile_user(self):
		frappe.set_user(self.intruder)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_credit_sale_summary(self.profile.name)
		finally:
			frappe.set_user(ADMIN)

	def test_summary_allowed_for_profile_member(self):
		frappe.set_user(self.cashier)
		try:
			summary = get_credit_sale_summary(self.profile.name)
		finally:
			frappe.set_user(ADMIN)
		self.assertIn("count", summary)
