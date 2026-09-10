# Copyright (c) 2025, POS Next and contributors
# For license information, please see license.txt

import json
import unittest

import frappe
from frappe.tests import IntegrationTestCase

from pos_next.api.shifts import get_session_summary
from pos_next.tests.price_group_helpers import (
	get_default_company,
	make_test_pos_profile,
	make_test_warehouse,
	make_test_item,
	manual_item_price,
)


def _make_item(code, rate):
	"""Create a priced test item; return its actual item_code.

	Some sites re-code items on insert (naming series), so the requested code
	and the stored item_code may differ.
	"""
	item_code = frappe.db.get_value("Item", {"item_code": code}, "item_code")
	if item_code:
		return item_code
	name = make_test_item(code, "Nos", is_stock_item=0)
	item_code = frappe.db.get_value("Item", name, "item_code")
	price_list = frappe.db.get_value("Price List", {"selling": 1}, "name")
	manual_item_price(item_code, price_list, price_list_rate=rate)
	return item_code


class TestSessionSummary(IntegrationTestCase):
	"""Session summary must agree with POS Closing Shift on the same dataset.

	Fixtures: one opening shift with 100000 opening cash and four invoices —
	2 x item_a @1000 (paid exact), 1 x item_b @250 (paid exact), one return
	of 2 x item_a, and one package (parent @50000 + component qty 2 @ 0).
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		cls.cash_mode = frappe.db.get_value(
			"Mode of Payment", {"enabled": 1, "type": "Cash"}, "name"
		) or frappe.db.get_value("Mode of Payment", {"enabled": 1}, "name")
		# internal customers only transact with their 'Allowed To Transact With'
		# companies (ERPNext check) — the shared site's first row is internal
		cls.customer = frappe.db.get_value("Customer", {"is_internal_customer": 0}, "name")
		if not cls.customer:
			raise unittest.SkipTest("no non-internal customer")
		cls.user = "session.summary.tester@example.com"
		if not frappe.db.exists("User", cls.user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.user,
					"first_name": "Session",
					"roles": [{"role": "Sales User"}],
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.get_value("User", cls.user, "enabled"):
			# a previous run's cleanup disabled the shared fixture user
			frappe.db.set_value("User", cls.user, "enabled", 1)
		cls.pos_profile = make_test_pos_profile(
			"SessionSummary", cls.company, make_test_warehouse("SessionSummary", cls.company)
		)
		# refund code gate fails closed; turn it off for this test profile
		if not frappe.db.exists("POS Settings", {"pos_profile": cls.pos_profile}):
			frappe.get_doc(
				{
					"doctype": "POS Settings",
					"pos_profile": cls.pos_profile,
					"require_refund_code": 0,
				}
			).insert(ignore_permissions=True)
		cls.debtors_account = frappe.db.get_value(
			"Account",
			{"company": cls.company, "account_type": "Receivable", "is_group": 0},
			"name",
		)
		cls.cash_account = frappe.get_cached_value("Company", cls.company, "default_cash_account")
		cls.other_user = "session.summary.other@example.com"
		if not frappe.db.exists("User", cls.other_user):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.other_user,
					"first_name": "Other",
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.get_value("User", cls.other_user, "enabled"):
			frappe.db.set_value("User", cls.other_user, "enabled", 1)
		# payment-method fixtures: extra profile methods (one unused, one used
		# but unconfigured) and a second non-cash profile
		cls.qr_mode = cls._make_mode_of_payment("_SESSUM QR", "Bank")
		cls.card_mode = cls._make_mode_of_payment("_SESSUM Card", "Bank")
		cls.ewallet_mode = cls._make_mode_of_payment("_SESSUM E-Wallet", "Phone")
		cls.pm_profile = make_test_pos_profile(
			"SessionSummaryPM",
			cls.company,
			make_test_warehouse("SessionSummaryPM", cls.company),
			payments=[
				{"mode_of_payment": cls.cash_mode, "default": 1},
				{"mode_of_payment": cls.qr_mode},
				{"mode_of_payment": cls.card_mode},
			],
		)
		frappe.db.commit()

		cls.item_a = _make_item("_SESSUM_ITEM_A", 1000)
		cls.item_b = _make_item("_SESSUM_ITEM_B", 250)
		cls.pkg_parent = _make_item("_SESSUM_PKG_PARENT", 50000)
		cls.pkg_component = _make_item("_SESSUM_PKG_COMPONENT", 0)
		cls.other_group = cls._make_item_group()
		frappe.db.set_value("Item", cls.item_b, "item_group", cls.other_group)
		cls.shift_group = cls._make_shift_group()
		frappe.db.set_value("POS Profile", cls.pos_profile, "pos_profile_group", cls.shift_group)
		cls.opening_shift = cls._make_opening_shift(opening_cash=100000)
		cls._make_invoice([{"item": cls.item_a, "qty": 2, "rate": 1000}], paid=2000)
		cls._make_invoice([{"item": cls.item_b, "qty": 2, "rate": 250}], paid=500)
		cls._make_invoice(
			[{"item": cls.item_a, "qty": -2, "rate": 1000}],
			paid=-2000,
			is_return=True,
		)
		cls._make_invoice(
			[
				{"item": cls.pkg_parent, "qty": 1, "rate": 50000, "role": "Package"},
				{"item": cls.pkg_component, "qty": 2, "rate": 0, "role": "Package Item"},
			],
			paid=50000,
		)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		super().tearDownClass()

	@classmethod
	def _make_opening_shift(
		cls, opening_cash=0, pos_profile=None, extra_cash=None, extra_cash_amount=0
	):
		details = [{"mode_of_payment": cls.cash_mode, "amount": opening_cash}]
		if extra_cash:
			details.append({"mode_of_payment": extra_cash, "amount": extra_cash_amount})
		doc = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"company": cls.company,
				"pos_profile": pos_profile or cls.pos_profile,
				"user": cls.user,
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": details,
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc.name

	@classmethod
	def _make_mode_of_payment(cls, name, mop_type):
		if frappe.db.exists("Mode of Payment", name):
			return name
		account = (
			frappe.get_cached_value("Company", cls.company, "default_bank_account")
			or cls.cash_account
		)
		doc = frappe.get_doc(
			{
				"doctype": "Mode of Payment",
				"mode_of_payment": name,
				"enabled": 1,
				"type": mop_type,
				"accounts": [{"company": cls.company, "default_account": account}],
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	@classmethod
	def _make_item_group(cls):
		name = "_SESSUM Group B"
		if frappe.db.exists("Item Group", name):
			return name
		parent = frappe.db.get_value(
			"Item Group",
			frappe.db.get_value("Item", cls.item_a, "item_group"),
			"parent_item_group",
		)
		doc = frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": name,
				"parent_item_group": parent,
				"is_group": 0,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	@classmethod
	def _make_shift_group(cls):
		name = "_SESSUM Shift Group"
		if frappe.db.exists("POS Profile Group", name):
			return name
		doc = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": name,
				"pos_schedule_enabled": 0,
				"profiles": [{"pos_profile": cls.pos_profile}],
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	@classmethod
	def _make_invoice(cls, rows, paid, is_return=False, pay_mode=None, invoice_discount=None):
		return cls._make_invoice_on(
			cls.opening_shift, rows, paid, is_return=is_return, pay_mode=pay_mode,
			invoice_discount=invoice_discount,
		)

	@classmethod
	def _make_invoice_on(
		cls, shift, rows, paid, is_return=False, pay_mode=None, invoice_discount=None
	):
		inv = frappe.new_doc("Sales Invoice")
		inv.company = cls.company
		inv.customer = cls.customer
		inv.is_pos = 1
		inv.posa_pos_opening_shift = shift
		if is_return:
			inv.is_return = 1
		if invoice_discount:
			inv.apply_discount_on = "Grand Total"
			inv.discount_amount = invoice_discount
		needs_code = bool(invoice_discount) or any(
			row.get("price_list_rate", row["rate"]) != row["rate"] for row in rows
		)
		if needs_code:
			inv.discount_confirmation_code = cls._make_discount_code()
		for row in rows:
			inv.append(
				"items",
				{
					"item_code": row["item"],
					"qty": row["qty"],
					"rate": row["rate"],
					# returns mirror the original row's sign, like the POS does
					"price_list_rate": row.get("price_list_rate", row["rate"]),
					"pos_package_role": row.get("role"),
				},
			)
		inv.append(
			"payments",
			{
				"mode_of_payment": pay_mode or cls.cash_mode,
				"amount": paid,
				"base_amount": paid,
			},
		)
		inv.insert(ignore_permissions=True)
		inv.submit()
		return inv.name

	@classmethod
	def _make_discount_code(cls):
		code = "SESSUMKES"
		if not frappe.db.exists("POS Discount Confirmation Code", {"code": code}):
			frappe.get_doc(
				{
					"doctype": "POS Discount Confirmation Code",
					"code": code,
					"status": "Active",
				}
			).insert(ignore_permissions=True)
		return code

	@classmethod
	def _tax_account(cls):
		existing = frappe.db.get_value(
			"Account", {"company": cls.company, "account_type": "Tax", "is_group": 0}, "name"
		)
		if existing:
			return existing
		parent = frappe.db.get_value(
			"Account",
			{
				"company": cls.company,
				"account_type": "Tax",
				"is_group": 1,
				"parent_account": ("is", "set"),
			},
			"name",
		) or frappe.db.get_value(
			"Account",
			{"company": cls.company, "root_type": "Liability", "is_group": 1},
			"name",
			order_by="lft asc",
		)
		doc = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": "_SESSUM VAT",
				"parent_account": parent,
				"company": cls.company,
				"account_type": "Tax",
				"is_group": 0,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def setUp(self):
		frappe.set_user(self.user)

	def _make_payment_entry(self, invoice, amount, shift):
		pe = frappe.new_doc("Payment Entry")
		pe.payment_type = "Receive"
		pe.company = self.company
		pe.party_type = "Customer"
		pe.party = self.customer
		pe.paid_from = self.debtors_account
		pe.paid_to = self.cash_account
		pe.paid_amount = amount
		pe.received_amount = amount
		pe.mode_of_payment = self.cash_mode
		pe.reference_no = shift
		pe.reference_date = frappe.utils.nowdate()
		pe.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice,
				"allocated_amount": amount,
			},
		)
		# the shift-owner test user lacks Payment Entry rights; the session
		# owner posts these from the POS in production
		frappe.set_user("Administrator")
		pe.insert(ignore_permissions=True)
		pe.submit()
		frappe.set_user(self.user)
		return pe.name

	# gross 2000 + 500 + 50000 = 52500; return -2000; net 50500
	GROSS = 52500
	RETURN = 2000
	NET = 50500

	def test_totals_match_closing_aggregation(self):
		summary = get_session_summary(self.opening_shift)

		self.assertEqual(summary["sales_count"], 3)
		self.assertEqual(summary["returns_count"], 1)
		self.assertEqual(summary["counted_invoices"], 4)
		self.assertEqual(summary["invoice_count"], 4)
		self.assertEqual(summary["gross_sales"], self.GROSS)
		self.assertEqual(summary["returns_total"], self.RETURN)
		self.assertEqual(summary["net_sales"], self.NET)
		# 2 + 2 - 2 + 1 + 2 (component rows count toward movement)
		self.assertEqual(summary["total_qty"], 5)
		self.assertEqual(summary["average_sale"], self.NET / 3)

		# Cross-check against the authoritative closing builder
		opening = frappe.get_doc("POS Opening Shift", self.opening_shift).as_dict()
		closing = frappe.get_attr(
			"pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift"
			".make_closing_shift_from_opening"
		)(json.dumps(opening, default=str))
		self.assertEqual(summary["net_sales"], closing["grand_total"])
		self.assertEqual(summary["total_qty"], closing["total_quantity"])

	def test_cash_opening_and_expected(self):
		summary = get_session_summary(self.opening_shift)

		self.assertEqual(summary["opening_cash"], 100000)
		cash = next(p for p in summary["payments"] if p["mode_of_payment"] == self.cash_mode)
		self.assertEqual(cash["amount"], self.NET)
		self.assertEqual(summary["cash_expected"], 100000 + self.NET)

	def test_package_components_not_double_counted(self):
		summary = get_session_summary(self.opening_shift)

		self.assertEqual(len(summary["packages"]), 1)
		pkg = summary["packages"][0]
		self.assertEqual(pkg["item_code"], self.pkg_parent)
		self.assertEqual(pkg["qty"], 1)
		self.assertEqual(pkg["base_net_amount"], 50000)

		item_codes = {i["item_code"] for i in summary["items"]}
		self.assertNotIn(self.pkg_component, item_codes)
		item_a = next(i for i in summary["items"] if i["item_code"] == self.item_a)
		self.assertEqual(item_a["qty"], 0)  # 2 sold - 2 returned
		self.assertEqual(item_a["base_net_amount"], 0)

	def test_credit_return_without_payments_excluded(self):
		"""Mirror closing: a return with no payment rows moved no money."""
		# dedicated shift so the shared-fixture numbers stay untouched
		shift = self._make_opening_shift(opening_cash=0)
		inv = frappe.new_doc("Sales Invoice")
		inv.company = self.company
		inv.customer = self.customer
		inv.is_pos = 1
		inv.posa_pos_opening_shift = shift
		inv.is_return = 1
		inv.append(
			"items",
			{"item_code": self.item_b, "qty": -1, "rate": 250, "price_list_rate": 250},
		)
		inv.insert(ignore_permissions=True)
		inv.submit()

		summary = get_session_summary(shift)
		self.assertEqual(summary["invoice_count"], 1)  # raw count sees it
		self.assertEqual(summary["counted_invoices"], 0)  # drawer aggregates don't
		self.assertEqual(summary["net_sales"], 0)
		self.assertEqual(summary["returns_count"], 0)
		self.assertEqual(summary["items"][0]["qty"], -1)  # stock moved

	def test_currency_breakdown_present(self):
		summary = get_session_summary(self.opening_shift)
		self.assertEqual(len(summary["currency_breakdown"]), 1)
		self.assertEqual(
			summary["currency_breakdown"][0]["currency"], summary["company_currency"]
		)
		self.assertEqual(summary["currency_breakdown"][0]["amount"], self.NET)

	def test_items_truncated_flag_false_when_under_cap(self):
		summary = get_session_summary(self.opening_shift)
		self.assertFalse(summary["items_truncated"])
		self.assertEqual(
			summary["items_shown"], summary["items_total_groups"]
		)

	def test_partial_payment_entry_updates_outstanding(self):
		"""Later Payment Entry reconciliation must be reflected, unlike the
		naive grand_total - paid_amount approximation."""
		shift = self._make_opening_shift(opening_cash=0)
		inv = self._make_invoice_on(
			shift, [{"item": self.item_a, "qty": 1, "rate": 1000}], paid=400
		)

		summary = get_session_summary(shift)
		self.assertEqual(summary["credit_outstanding"], 600)

		self._make_payment_entry(inv, 600, shift)
		summary = get_session_summary(shift)
		self.assertEqual(summary["credit_outstanding"], 0)

	def test_cash_change_deducted_from_cash_collected(self):
		shift = self._make_opening_shift(opening_cash=5000)
		self._make_invoice_on(
			shift, [{"item": self.item_a, "qty": 2, "rate": 1000}], paid=2500
		)

		summary = get_session_summary(shift)
		cash = next(
			p for p in summary["payments"] if p["mode_of_payment"] == self.cash_mode
		)
		self.assertEqual(cash["amount"], 2000)  # 2500 paid - 500 change
		self.assertEqual(summary["cash_expected"], 5000 + 2000)

	def test_foreign_currency_not_summed_into_company_total(self):
		"""USD invoice stays visible in its own currency line, converted once
		for the base-currency totals — never mixed into IDR amounts."""
		# inserting one account shifts the nested-set (lft/rgt) chart; on a
		# bloated chart (this shared dev site got its tabAccount wrecked by a
		# runaway transaction) that takes hours, not seconds. Table-status
		# estimate is instant; an exact COUNT(*) would itself be a full scan.
		status = frappe.db.sql("SHOW TABLE STATUS LIKE 'tabAccount'", as_dict=True)
		if status and (status[0].get("Rows") or 0) > 50000:
			raise unittest.SkipTest(
				f"tabAccount too large ({status[0].get('Rows')} rows) for USD fixture insert"
			)
		shift = self._make_opening_shift(opening_cash=0)
		self._make_invoice_on(
			shift, [{"item": self.item_a, "qty": 1, "rate": 1000}], paid=1000
		)
		usd_pl, usd_account, usd_customer = self._make_usd_fixtures()
		before = get_session_summary(shift)

		inv = frappe.new_doc("Sales Invoice")
		inv.company = self.company
		inv.customer = usd_customer
		inv.is_pos = 1
		inv.posa_pos_opening_shift = shift
		inv.currency = "USD"
		inv.conversion_rate = 16000
		inv.selling_price_list = usd_pl
		inv.debit_to = usd_account
		inv.append(
			"items",
			{"item_code": self.item_a, "qty": 1, "rate": 100, "price_list_rate": 100},
		)
		inv.append(
			"payments",
			{"mode_of_payment": self.cash_mode, "amount": 100},
		)
		inv.insert(ignore_permissions=True)
		inv.submit()

		summary = get_session_summary(shift)
		self.assertEqual(
			summary["net_sales"], before["net_sales"] + 1600000  # 100 USD x 16000
		)
		breakdown = {row["currency"]: row["amount"] for row in summary["currency_breakdown"]}
		self.assertEqual(breakdown["USD"], 100)
		self.assertEqual(
			breakdown[summary["company_currency"]], before["net_sales"]
		)

	@classmethod
	def _make_usd_fixtures(cls):
		abbr = frappe.get_cached_value("Company", cls.company, "abbr")
		# group parent of the existing receivable account (chart-agnostic)
		debtors_parent = frappe.db.get_value(
			"Account", cls.debtors_account, "parent_account"
		)
		# a customer with prior IDR entries cannot be invoiced in USD
		usd_customer = "_SESSUM USD Customer"
		if not frappe.db.exists("Customer", usd_customer):
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": usd_customer, "customer_type": "Individual"}
			).insert(ignore_permissions=True)
		account = f"_SESSUM Debtors USD - {abbr}"
		if not frappe.db.exists("Account", account):
			# ERPNext's child-company account sync re-inserts as the current
			# user (ignore_permissions doesn't propagate), and the shift-owner
			# test user lacks Account rights — same elevation idiom as
			# _make_payment_entry above
			prev_user = frappe.session.user
			frappe.set_user("Administrator")
			frappe.get_doc(
				{
					"doctype": "Account",
					"account_name": "_SESSUM Debtors USD",
					"parent_account": debtors_parent,
					"company": cls.company,
					"account_type": "Receivable",
					"account_currency": "USD",
					"is_group": 0,
				}
			).insert(ignore_permissions=True)
			frappe.set_user(prev_user)
		price_list = "_SESSUM USD Selling"
		if not frappe.db.exists("Price List", price_list):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": price_list,
					"currency": "USD",
					"selling": 1,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
			frappe.get_doc(
				{
					"doctype": "Item Price",
					"price_list": price_list,
					"item_code": cls.item_a,
					"price_list_rate": 100,
					"currency": "USD",
				}
			).insert(ignore_permissions=True)
		return price_list, account, usd_customer

	def test_nonowner_denied(self):
		frappe.set_user(self.other_user)
		with self.assertRaises(frappe.PermissionError):
			get_session_summary(self.opening_shift)

	def test_nonowner_with_doctype_read_allowed(self):
		"""Documented fallback: a user with POS Opening Shift read access may
		view a shift they do not own (e.g. supervisor)."""
		from unittest.mock import patch

		frappe.set_user(self.other_user)
		with patch("frappe.has_permission", return_value=True):
			summary = get_session_summary(self.opening_shift)
		self.assertEqual(summary["net_sales"], self.NET)

	def test_missing_shift_throws(self):
		with self.assertRaises(frappe.DoesNotExistError):
			get_session_summary("_SESSUM_NO_SUCH_SHIFT")

	def test_empty_shift_reports_zeros(self):
		shift = self._make_opening_shift(opening_cash=25000)
		summary = get_session_summary(shift)
		self.assertEqual(summary["net_sales"], 0)
		self.assertEqual(summary["invoice_count"], 0)
		self.assertEqual(summary["average_sale"], 0)
		self.assertEqual(summary["opening_cash"], 25000)
		self.assertEqual(summary["cash_expected"], 25000)
		self.assertEqual(summary["items"], [])
		self.assertEqual(summary["packages"], [])

	def test_shift_info_group_cashier_and_open_closing(self):
		summary = get_session_summary(self.opening_shift)
		self.assertEqual(
			summary["cashier"], frappe.db.get_value("User", self.user, "full_name")
		)
		# current profile group is only claimed while the shift is open
		self.assertEqual(summary["shift_name"], self.shift_group)
		self.assertIsNone(summary["closing_source"])
		self.assertIsNone(summary["closing_time"])

	def test_closing_time_estimate_from_schedule_deadline(self):
		shift = self._make_opening_shift(opening_cash=0)
		frappe.db.set_value(
			"POS Opening Shift", shift, "pos_schedule_deadline", "2026-01-01 22:00:00"
		)

		summary = get_session_summary(shift)
		self.assertEqual(summary["closing_source"], "estimate")
		self.assertEqual(
			frappe.utils.get_datetime(summary["closing_time"]),
			frappe.utils.get_datetime("2026-01-01 22:00:00"),
		)

	def test_closing_time_actual_from_closing_shift(self):
		shift = self._make_opening_shift(opening_cash=0)
		opening = frappe.get_doc("POS Opening Shift", shift).as_dict()
		closing = frappe.get_attr(
			"pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift"
			".make_closing_shift_from_opening"
		)(json.dumps(opening, default=str))
		frappe.set_user("Administrator")
		doc = frappe.get_doc(closing)
		doc.insert(ignore_permissions=True)
		doc.submit()
		frappe.set_user(self.user)

		summary = get_session_summary(shift)
		self.assertEqual(summary["closing_source"], "actual")
		self.assertEqual(
			frappe.utils.get_datetime(summary["closing_time"]),
			frappe.utils.get_datetime(doc.period_end_date),
		)
		# closed shift must NOT claim the profile's current group as history
		self.assertIsNone(summary["shift_name"])

	def test_expense_unsupported_and_cash_in_hand_is_estimate(self):
		shift = self._make_opening_shift(opening_cash=20000)
		self._make_invoice_on(shift, [{"item": self.item_a, "qty": 1, "rate": 1000}], paid=1000)

		summary = get_session_summary(shift)
		# no shift-scoped expense ledger exists in this app: never a fake zero
		self.assertIsNone(summary["expense"])
		self.assertFalse(summary["expense_supported"])
		self.assertEqual(summary["opening_cash"], 20000)
		self.assertEqual(summary["cash_collected"], 1000)
		self.assertEqual(summary["cash_in_hand"], 21000)
		self.assertEqual(summary["cash_expected"], summary["cash_in_hand"])

	def test_payment_methods_all_configured_plus_unconfigured(self):
		shift = self._make_opening_shift(opening_cash=20000, pos_profile=self.pm_profile)
		self._make_invoice_on(
			shift,
			[{"item": self.item_a, "qty": 1, "rate": 300}],
			paid=300,
			pay_mode=self.qr_mode,
		)
		self._make_invoice_on(
			shift,
			[{"item": self.item_a, "qty": 1, "rate": 150}],
			paid=150,
			pay_mode=self.ewallet_mode,
		)

		summary = get_session_summary(shift)
		rows = {r["mode_of_payment"]: r for r in summary["payments"]}
		# every configured method appears, zero included
		self.assertEqual(rows[self.cash_mode]["amount"], 0)
		self.assertTrue(rows[self.cash_mode]["configured"])
		self.assertTrue(rows[self.cash_mode]["is_cash"])
		self.assertEqual(rows[self.qr_mode]["amount"], 300)
		self.assertTrue(rows[self.qr_mode]["configured"])
		# used method missing from the profile is still reported
		self.assertEqual(rows[self.ewallet_mode]["amount"], 150)
		self.assertFalse(rows[self.ewallet_mode]["configured"])
		self.assertFalse(rows[self.ewallet_mode]["is_cash"])
		# configured but never used
		self.assertEqual(rows[self.card_mode]["amount"], 0)
		self.assertFalse(rows[self.card_mode]["is_cash"])
		# configured methods come first, in profile order
		self.assertEqual(
			[r["mode_of_payment"] for r in summary["payments"][:3]],
			[self.cash_mode, self.qr_mode, self.card_mode],
		)

		self.assertEqual(summary["total_cash"], 0)
		self.assertEqual(summary["total_non_cash"], 450)
		self.assertEqual(summary["methods_grand_total"], 450)
		self.assertEqual(summary["opening_cash"], 20000)
		self.assertEqual(summary["cash_in_hand"], 20000)

	def test_opening_cash_sums_all_cash_type_modes(self):
		second_cash = self._make_mode_of_payment("_SESSUM Cash 2", "Cash")
		shift = self._make_opening_shift(
			pos_profile=self.pm_profile, extra_cash=second_cash, extra_cash_amount=7000
		)

		summary = get_session_summary(shift)
		self.assertEqual(summary["opening_cash"], 7000)

	def test_charges_classified_by_account_type(self):
		tax_account = self._tax_account()
		income_account = frappe.get_cached_value("POS Profile", self.pos_profile, "income_account")
		shift = self._make_opening_shift(opening_cash=0)

		inv = frappe.new_doc("Sales Invoice")
		inv.company = self.company
		inv.customer = self.customer
		inv.is_pos = 1
		inv.posa_pos_opening_shift = shift
		inv.append(
			"items",
			{"item_code": self.item_a, "qty": 1, "rate": 1000, "price_list_rate": 1000},
		)
		inv.append(
			"taxes",
			{
				"charge_type": "On Net Total",
				"account_head": tax_account,
				"rate": 11,
				"description": "PPN 11%",
			},
		)
		inv.append(
			"taxes",
			{
				"charge_type": "Actual",
				"account_head": income_account,
				"tax_amount": 50,
				"description": "Service Charge",
			},
		)
		inv.append(
			"payments",
			{"mode_of_payment": self.cash_mode, "amount": 1160, "base_amount": 1160},
		)
		inv.insert(ignore_permissions=True)
		inv.submit()

		summary = get_session_summary(shift)
		# only the explicit Tax account counts as tax (110), never by name guess
		self.assertEqual(summary["tax_total"], 110)
		self.assertEqual(summary["total_tax"], 110)
		self.assertEqual(summary["other_charges_total"], 50)
		self.assertEqual(len(summary["other_charges"]), 1)
		self.assertEqual(summary["other_charges"][0]["account_head"], income_account)
		self.assertEqual(summary["other_charges"][0]["amount"], 50)
		# category revenue stays net: pre-tax 1000, not the 1160 grand total
		self.assertEqual(
			sum(c["base_net_amount"] for c in summary["categories"]), 1000
		)

	def test_discount_item_and_invoice_levels_combined(self):
		shift = self._make_opening_shift(opening_cash=0)
		# item-level: rate 900 under a 1000 list price
		self._make_invoice_on(
			shift,
			[{"item": self.item_a, "qty": 1, "rate": 900, "price_list_rate": 1000}],
			paid=900,
		)
		# invoice-level additional discount
		self._make_invoice_on(
			shift,
			[{"item": self.item_a, "qty": 1, "rate": 1000}],
			paid=900,
			invoice_discount=100,
		)

		summary = get_session_summary(shift)
		self.assertEqual(summary["item_discount"], 100)
		self.assertEqual(summary["invoice_discount"], 100)
		self.assertEqual(summary["total_discount"], 200)
		self.assertEqual(summary["net_total"], 1800)

	def test_categories_grouped_by_item_group_snapshot(self):
		summary = get_session_summary(self.opening_shift)
		cats = {c["category"]: c for c in summary["categories"]}
		group_a = frappe.db.get_value("Item", self.item_a, "item_group")

		self.assertEqual(set(cats), {group_a, self.other_group})
		# item_b: 2 x 250, no returns
		self.assertEqual(cats[self.other_group]["base_net_amount"], 500)
		# group A: item_a nets to 0 (2 sold - 2 returned) + package parent 50000
		self.assertEqual(cats[group_a]["base_net_amount"], 50000)
		self.assertEqual(summary["categories_total_groups"], 2)
		self.assertFalse(summary["categories_truncated"])

	def test_categories_include_negative_returns(self):
		shift = self._make_opening_shift(opening_cash=0)
		self._make_invoice_on(
			shift, [{"item": self.item_a, "qty": 2, "rate": 1000}], paid=2000
		)
		self._make_invoice_on(
			shift, [{"item": self.item_a, "qty": -1, "rate": 1000}], paid=-1000, is_return=True
		)

		summary = get_session_summary(shift)
		self.assertEqual(len(summary["categories"]), 1)
		cat = summary["categories"][0]
		self.assertEqual(cat["qty"], 1)
		self.assertEqual(cat["base_net_amount"], 1000)
