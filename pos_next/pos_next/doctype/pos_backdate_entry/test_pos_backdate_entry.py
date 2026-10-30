# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Backend integration tests for POS Backdate Entry DocType.

Covers testing scenarios 1 through 11 from the design spec
(docs/superpowers/specs/2026-10-18-backdate-entry-doctype-design.md):
1. Role, profile flag, non-closed shift gates; Sales Manager-only execution (R1, R5).
2. Multi-invoice submit: 2 sales + 1 return -> 3 invoices, old closing cancelled,
   new closing submitted, posting date/time preserved, closing period_end set.
3. Closing reconciliation counts: physical count respected, difference computed.
4. Correction flow: wrong payment mode corrected, original cancelled, amended_from
   set, audit comment added.
5. Package handling: quote & submit backdate package parent and components.
6. Atomic rollback on failure: invoice #2 failure rolls back all operations,
   leaving shift closed, old closing submitted, entry in Draft.
7. Confirmation code gates: discount and refund codes enforced when switch on.
8. Both invoice doctypes: POS Invoice and Sales Invoice global settings.
9. Posting datetime window validations (R4): rejected when outside shift window.
10. Correction blocked scenarios (R10): return invoices, submitted returns, etc.
11. Cancellation prohibition: before_cancel blocks entry cancellation.
"""

import json
import unittest
import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, cint, flt, getdate, now_datetime, nowdate, nowtime

from pos_next.api.backdate_invoices import (
	get_backdate_packages,
	get_item_rate,
	get_return_items,
	get_shift_context,
	pos_opening_shift_query,
	quote_backdate_package,
)
from pos_next.api.invoices import submit_invoice
from pos_next.api.packages import (
	PARENT_ROLE,
	quote,
)
from pos_next.api.shifts import get_closing_shift_data, submit_closing_shift
from pos_next.api.test_packages import (
	BASE_PRICE,
	PACKAGE,
	PARENT_ITEM,
	PROFILE,
	_ensure_package,
	_option_id,
)
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE, get_pos_invoice_doctype
from pos_next.services.confirmation_code import generate_codes_for
from pos_next.tests._posi_test_utils import _set_invoice_type
from pos_next.tests.code_gate_helpers import set_code_switch
from pos_next.tests.price_group_helpers import get_default_customer

ADMIN = "Administrator"

_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]


class TestPOSBackdateEntry(FrappeTestCase):
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
		if not cls.profile:
			raise unittest.SkipTest("no schedule-safe POS Profile")

		item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1, "has_batch_no": 0, "has_serial_no": 0},
			pluck="name",
			limit=1,
		)
		cls.customer = get_default_customer(cls.profile.company)
		cls.mode = frappe.get_all(
			"POS Payment Method",
			{"parent": cls.profile.name, "parenttype": "POS Profile"},
			pluck="mode_of_payment",
			limit=2,
		)
		cls.created_payment_methods = []
		cls.created_mop_accounts = []
		if len(cls.mode) < 2:
			cash_acc = frappe.db.get_value("Account", {"company": cls.profile.company, "account_type": "Cash"}, "name")
			if cash_acc:
				if not frappe.db.exists("Mode of Payment Account", {"parent": "Cash", "company": cls.profile.company}):
					mop_doc = frappe.get_doc("Mode of Payment", "Cash")
					mop_doc.append("accounts", {"company": cls.profile.company, "default_account": cash_acc})
					mop_doc.save(ignore_permissions=True)
					cls.created_mop_accounts.append(("Cash", cls.profile.company))
				prof_doc = frappe.get_doc("POS Profile", cls.profile.name)
				if not any(pm.mode_of_payment == "Cash" for pm in prof_doc.payments):
					prof_doc.append("payments", {"mode_of_payment": "Cash", "default": 0})
					prof_doc.save(ignore_permissions=True)
					cls.created_payment_methods.append((cls.profile.name, "Cash"))
				cls.mode = [pm.mode_of_payment for pm in prof_doc.payments][:2]
		if not (item and cls.customer and cls.mode):
			raise unittest.SkipTest("no stock sales item / customer / payment method")
		cls.item = item[0]

		cls.sales_manager_user = f"sm.{cls._uniq()}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.sales_manager_user,
				"first_name": "Sales Manager Tester",
				"roles": [{"role": "Desk User"}, {"role": "Sales User"}, {"role": "Sales Manager"}],
			}
		).insert(ignore_permissions=True)

		cls.plain_user = f"plain.{cls._uniq()}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": cls.plain_user,
				"first_name": "Plain Tester",
				"roles": [{"role": "Desk User"}],
			}
		).insert(ignore_permissions=True)

		cls._ensure_fiscal_year()
		cls._ensure_settings()
		cls._invoice_type_baseline = frappe.db.get_single_value("POS Next Global Settings", "invoice_type")

	@staticmethod
	def _uniq():
		return uuid.uuid4().hex[:8]

	@classmethod
	def _ensure_fiscal_year(cls):
		cls.fiscal_year = None
		for name in frappe.get_all(
			"Fiscal Year", filters={"year": ("like", "POS BDE Test FY%")}, pluck="name"
		):
			frappe.delete_doc("Fiscal Year", name, force=1)
		covering = frappe.get_all(
			"Fiscal Year",
			filters={
				"disabled": 0,
				"year_start_date": ["<=", add_days(nowdate(), -10)],
				"year_end_date": [">=", nowdate()],
			},
			pluck="name",
		)
		if not covering or not frappe.get_all(
			"Fiscal Year Company",
			filters={"company": cls.profile.company, "parent": ["in", covering]},
			pluck="parent",
		):
			year = getdate(nowdate()).year
			cls.fiscal_year = (
				frappe.get_doc(
					{
						"doctype": "Fiscal Year",
						"year": f"POS BDE Test FY {cls._uniq()}",
						"year_start_date": f"{year}-01-01",
						"year_end_date": f"{year}-12-31",
						"companies": [{"company": cls.profile.company}],
					}
				)
				.insert()
				.name
			)

	@classmethod
	def _ensure_settings(cls):
		name = frappe.db.get_value("POS Settings", {"pos_profile": cls.profile.name}, "name")
		cls.created_settings = False
		if name:
			cls.orig_settings = frappe.db.get_value(
				"POS Settings",
				name,
				["enabled", "allow_change_posting_date", "return_validity_days", "allow_user_to_edit_rate"],
				as_dict=True,
			)
		else:
			name = (
				frappe.get_doc(
					{"doctype": "POS Settings", "pos_profile": cls.profile.name, "enabled": 1}
				)
				.insert(ignore_permissions=True)
				.name
			)
			cls.created_settings = True
		cls.settings_name = name

	@classmethod
	def _restore_settings(cls):
		orig = getattr(cls, "orig_settings", None)
		frappe.db.set_value(
			"POS Settings",
			cls.settings_name,
			{
				"enabled": getattr(orig, "enabled", None) if orig else 1,
				"allow_change_posting_date": getattr(orig, "allow_change_posting_date", None)
				if orig
				else 0,
				"return_validity_days": getattr(orig, "return_validity_days", None) if orig else 0,
				"allow_user_to_edit_rate": getattr(orig, "allow_user_to_edit_rate", None) if orig else 0,
			},
		)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			try:
				step()
			except Exception:
				pass

		if getattr(cls, "_invoice_type_baseline", None):
			_safe(lambda: _set_invoice_type(cls._invoice_type_baseline))
		if getattr(cls, "created_settings", False):
			_safe(lambda: frappe.delete_doc("POS Settings", cls.settings_name, force=1))
		else:
			_safe(cls._restore_settings)
		if getattr(cls, "fiscal_year", None):
			_safe(lambda: frappe.delete_doc("Fiscal Year", cls.fiscal_year, force=1))

		for prof_name, mop in getattr(cls, "created_payment_methods", []):
			_safe(lambda: frappe.db.delete("POS Payment Method", {"parent": prof_name, "mode_of_payment": mop}))
		for mop, comp in getattr(cls, "created_mop_accounts", []):
			_safe(lambda: frappe.db.delete("Mode of Payment Account", {"parent": mop, "company": comp}))

		for u in (getattr(cls, "sales_manager_user", None), getattr(cls, "plain_user", None)):
			if u:
				_safe(lambda: frappe.db.delete("Error Log", {"owner": u}))
				_safe(lambda: frappe.delete_doc("User", u, force=1))

		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		self.doctype = get_pos_invoice_doctype()
		self.orig_allow_negative = cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock"))
		if not self.orig_allow_negative:
			frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 1)

		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"purpose": "Material Receipt",
				"company": self.profile.company,
				"items": [
					{
						"item_code": self.item,
						"qty": 50,
						"t_warehouse": self.profile.warehouse,
						"allow_zero_valuation_rate": 1,
					}
				],
			}
		)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()
		self.stock_entry = se

		frappe.db.set_value(
			"POS Settings",
			self.settings_name,
			{
				"enabled": 1,
				"allow_change_posting_date": 1,
				"return_validity_days": 0,
				# scenarios sell below the item's list price, which counts as a rate edit
				"allow_user_to_edit_rate": 1,
			},
		)
		self._restore_gates = [
			set_code_switch(self.profile.name, "require_refund_code", 0),
			set_code_switch(self.profile.name, "require_discount_code", 0),
		]

		# GC any open shift on the profile left from previous aborts
		for sname in frappe.get_all(
			"POS Opening Shift",
			{"pos_profile": self.profile.name, "status": "Open", "docstatus": 1},
			pluck="name",
		):
			frappe.db.set_value("POS Opening Shift", sname, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", sname, force=1, ignore_permissions=True)

		balance_details = [{"mode_of_payment": m, "amount": 0} for m in self.mode]
		self.shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": ADMIN,
				"posting_date": add_days(nowdate(), -3),
				"period_start_date": add_days(now_datetime(), -3),
				"balance_details": balance_details,
			}
		).insert(ignore_permissions=True)
		self.shift.reload()
		self.shift.submit()

	def tearDown(self):
		frappe.set_user(ADMIN)
		for restore in reversed(self._restore_gates):
			restore()

		for name in frappe.get_all("POS Backdate Entry", {"pos_opening_shift": self.shift.name}, pluck="name"):
			frappe.db.set_value("POS Backdate Entry", name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Backdate Entry", name, force=1, ignore_permissions=True)

		for name in frappe.get_all("POS Closing Shift", {"pos_opening_shift": self.shift.name}, pluck="name"):
			doc = frappe.get_doc("POS Closing Shift", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc("POS Closing Shift", name, force=1, ignore_permissions=True)

		for dt in ("POS Invoice", "Sales Invoice"):
			for name in frappe.get_all(dt, {"posa_pos_opening_shift": self.shift.name}, pluck="name"):
				doc = frappe.get_doc(dt, name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc(dt, name, force=1, ignore_permissions=True)

		if self.stock_entry and frappe.db.exists("Stock Entry", self.stock_entry.name):
			se = frappe.get_doc("Stock Entry", self.stock_entry.name)
			if se.docstatus == 1:
				se.cancel()
			frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)

		if self.shift:
			frappe.db.set_value("POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)

		if not getattr(self, "created_settings", False):
			self._restore_settings()
		# restore last: cancelling backdated invoices reposts later SLEs, which may dip negative on real data
		if not getattr(self, "orig_allow_negative", 1):
			frappe.db.set_single_value("Stock Settings", "allow_negative_stock", 0)
		frappe.db.commit()

	def _submit_original_sale(self, qty=1, rate=100.0, mode_of_payment=None, payments=None):
		mode = mode_of_payment or self.mode[0]
		pay_rows = payments or [{"mode_of_payment": mode, "amount": flt(qty * rate)}]
		payload = {
			"pos_profile": self.profile.name,
			"posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"posting_date": add_days(nowdate(), -2),
			"posting_time": "09:00:00",
			"set_posting_time": 1,
			"items": [{"item_code": self.item, "qty": qty, "rate": rate, "warehouse": self.profile.warehouse}],
			"payments": pay_rows,
		}
		frappe.flags.pos_next_backdate_entry = True
		try:
			res = submit_invoice(invoice=json.dumps(payload), data="{}")
		finally:
			frappe.flags.pos_next_backdate_entry = None
		doc = frappe.get_doc(self.doctype, res["name"])
		return res, doc

	def _close_shift(self, closing_amounts=None):
		closing_data = get_closing_shift_data(self.shift.name)
		if closing_amounts:
			for row in closing_data.get("payment_reconciliation", []):
				if row["mode_of_payment"] in closing_amounts:
					row["closing_amount"] = closing_amounts[row["mode_of_payment"]]
		res = submit_closing_shift(json.dumps(closing_data))
		return res["name"]

	def _make_bde(self, **kwargs):
		posting_date = add_days(nowdate(), -1)
		doc_dict = {
			"doctype": "POS Backdate Entry",
			"company": self.profile.company,
			"pos_opening_shift": self.shift.name,
			"closing_date": posting_date,
			"closing_time": "18:00:00",
			"invoices": [
				{
					"invoice_no": 1,
					"entry_type": "Sale",
					"customer": self.customer,
					"posting_date": posting_date,
					"posting_time": "10:00:00",
				}
			],
			"items": [
				{
					"invoice_no": 1,
					"item_code": self.item,
					"qty": 1,
					"rate": 100.0,
					"warehouse": self.profile.warehouse,
				}
			],
			"payments": [
				{
					"invoice_no": 1,
					"mode_of_payment": self.mode[0],
					"amount": 100.0,
				}
			],
		}
		doc_dict.update(kwargs)
		return frappe.get_doc(doc_dict)

	# ---------------------------------------------------------------------------
	# Scenario 1: Gates (role, profile flag, non-closed shift) & Sales Manager
	# ---------------------------------------------------------------------------
	def test_scenario_1_gates_and_sales_manager_access(self):
		# A. Non-closed shift: opening shift status is Open
		bde = self._make_bde()
		with self.assertRaises(frappe.ValidationError) as ctx:
			bde.insert()
		self.assertIn("not closed", str(ctx.exception))

		# Close shift for subsequent tests
		self._submit_original_sale()
		self._close_shift()

		# B. Profile flag disabled
		frappe.db.set_value("POS Settings", self.settings_name, "allow_change_posting_date", 0)
		try:
			bde = self._make_bde()
			with self.assertRaises(frappe.ValidationError) as ctx:
				bde.insert()
			self.assertIn("disabled", str(ctx.exception))
		finally:
			frappe.db.set_value("POS Settings", self.settings_name, "allow_change_posting_date", 1)

		# C. Plain user without backdate role rejected
		frappe.set_user(self.plain_user)
		try:
			bde = self._make_bde()
			with self.assertRaises(frappe.PermissionError):
				bde.insert()
		finally:
			frappe.set_user(ADMIN)

		# D. Sales Manager-only user: end-to-end execution (R1, R5)
		frappe.set_user(self.sales_manager_user)
		try:
			# R5: package quote access without being cashier in POS Profile
			pkgs = get_backdate_packages(self.shift.name)
			self.assertIsInstance(pkgs, list)

			bde = self._make_bde()
			bde.insert()
			bde.submit()

			self.assertEqual(bde.docstatus, 1)
			self.assertTrue(bde.new_closing_shift)
			self.assertEqual(
				frappe.db.get_value("POS Closing Shift", bde.new_closing_shift, "docstatus"), 1
			)
			self.assertEqual(
				frappe.db.get_value("POS Opening Shift", self.shift.name, "status"), "Closed"
			)
		finally:
			frappe.set_user(ADMIN)

	# ---------------------------------------------------------------------------
	# Scenario 2: Multi-invoice submission (2 sales + 1 return)
	# ---------------------------------------------------------------------------
	def test_scenario_2_multi_invoice_submission(self):
		orig_sale, orig_sale_doc = self._submit_original_sale(qty=2, rate=100.0)
		orig_closing = self._close_shift()

		posting_date = add_days(nowdate(), -1)
		bde = frappe.get_doc(
			{
				"doctype": "POS Backdate Entry",
				"company": self.profile.company,
				"pos_opening_shift": self.shift.name,
				"closing_date": posting_date,
				"closing_time": "19:00:00",
				"invoices": [
					{
						"invoice_no": 1,
						"entry_type": "Sale",
						"customer": self.customer,
						"posting_date": posting_date,
						"posting_time": "10:00:00",
					},
					{
						"invoice_no": 2,
						"entry_type": "Sale",
						"customer": self.customer,
						"posting_date": posting_date,
						"posting_time": "11:00:00",
					},
					{
						"invoice_no": 3,
						"entry_type": "Return",
						"customer": self.customer,
						"against_invoice": orig_sale["name"],
						"against_doctype": self.doctype,
						"posting_date": posting_date,
						"posting_time": "12:00:00",
					},
				],
				"items": [
					{"invoice_no": 1, "item_code": self.item, "qty": 1, "rate": 100.0, "warehouse": self.profile.warehouse},
					{"invoice_no": 2, "item_code": self.item, "qty": 2, "rate": 100.0, "warehouse": self.profile.warehouse},
					{
						"invoice_no": 3,
						"item_code": self.item,
						"qty": 1,
						"rate": 100.0,
						"warehouse": self.profile.warehouse,
						"against_item_row": orig_sale_doc.items[0].name,
					},
				],
				"payments": [
					{"invoice_no": 1, "mode_of_payment": self.mode[0], "amount": 100.0},
					{"invoice_no": 2, "mode_of_payment": self.mode[0], "amount": 200.0},
					{"invoice_no": 3, "mode_of_payment": self.mode[0], "amount": -100.0},
				],
			}
		)
		bde.insert()
		bde.submit()

		self.assertEqual(bde.docstatus, 1)
		self.assertEqual(bde.cancelled_closing_shift, orig_closing)
		self.assertEqual(frappe.db.get_value("POS Closing Shift", orig_closing, "docstatus"), 2)

		new_closing = bde.new_closing_shift
		self.assertEqual(frappe.db.get_value("POS Closing Shift", new_closing, "docstatus"), 1)

		closing_doc = frappe.get_doc("POS Closing Shift", new_closing)
		self.assertEqual(str(closing_doc.period_end_date), f"{posting_date} 19:00:00")

		inv_rows = bde.invoices
		self.assertEqual(len(inv_rows), 3)

		inv1 = frappe.get_doc(self.doctype, inv_rows[0].created_invoice)
		self.assertEqual(inv1.docstatus, 1)
		self.assertEqual(inv1.is_return, 0)
		self.assertEqual(str(inv1.posting_date), posting_date)

		inv2 = frappe.get_doc(self.doctype, inv_rows[1].created_invoice)
		self.assertEqual(inv2.docstatus, 1)
		self.assertEqual(inv2.is_return, 0)

		inv3 = frappe.get_doc(self.doctype, inv_rows[2].created_invoice)
		self.assertEqual(inv3.docstatus, 1)
		self.assertEqual(inv3.is_return, 1)
		self.assertEqual(inv3.return_against, orig_sale["name"])

	def test_partial_return_drops_zero_qty_rows(self):
		orig_sale, orig_sale_doc = self._submit_original_sale(qty=2, rate=100.0)
		self._close_shift()
		row = {
			"invoice_no": 1,
			"item_code": self.item,
			"rate": 100.0,
			"warehouse": self.profile.warehouse,
			"against_item_row": orig_sale_doc.items[0].name,
		}
		bde = self._make_bde(
			invoices=[
				{
					"invoice_no": 1,
					"entry_type": "Return",
					"customer": self.customer,
					"against_invoice": orig_sale["name"],
					"against_doctype": self.doctype,
					"posting_date": add_days(nowdate(), -1),
					"posting_time": "12:00:00",
				}
			],
			items=[{**row, "qty": 1}, {**row, "qty": 0}],
			payments=[{"invoice_no": 1, "mode_of_payment": self.mode[0], "amount": -100.0}],
		)
		bde.insert()
		self.assertEqual([flt(i.qty) for i in bde.items], [1.0])

	def test_same_shift_invoice_cannot_be_returned_and_corrected_in_one_entry(self):
		orig_sale, orig_sale_doc = self._submit_original_sale(qty=2, rate=100.0)
		self._close_shift()
		against = {
			"customer": self.customer,
			"against_invoice": orig_sale["name"],
			"against_doctype": self.doctype,
			"posting_date": add_days(nowdate(), -1),
			"posting_time": "12:00:00",
		}
		item = {
			"item_code": self.item,
			"qty": 1,
			"rate": 100.0,
			"warehouse": self.profile.warehouse,
			"against_item_row": orig_sale_doc.items[0].name,
		}
		bde = self._make_bde(
			invoices=[
				{"invoice_no": 1, "entry_type": "Return", **against},
				{"invoice_no": 2, "entry_type": "Correction", **against},
			],
			items=[{"invoice_no": 1, **item}, {"invoice_no": 2, **item}],
			payments=[
				{"invoice_no": 1, "mode_of_payment": self.mode[0], "amount": -100.0},
				{"invoice_no": 2, "mode_of_payment": self.mode[0], "amount": 100.0},
			],
		)
		with self.assertRaisesRegex(frappe.ValidationError, "already used by Invoice No. 1"):
			bde.insert()

	def test_select_invoice_no_strings_match_int_invoice_rows(self):
		# items/payments invoice_no is a Select field, so the form sends "1" while invoices keep 1
		self._submit_original_sale()
		self._close_shift()
		bde = self._make_bde(
			items=[
				{
					"invoice_no": "1",
					"item_code": self.item,
					"qty": 1,
					"rate": 100.0,
					"warehouse": self.profile.warehouse,
				}
			],
			payments=[{"invoice_no": "1", "mode_of_payment": self.mode[0], "amount": 100.0}],
		)
		bde.insert()
		bde.submit()
		self.assertTrue(bde.invoices[0].created_invoice)

	# ---------------------------------------------------------------------------
	# Scenario 3: Closing reconciliation counts
	# ---------------------------------------------------------------------------
	def test_scenario_3_closing_reconciliation_counts(self):
		# D3 verification: Cash preserves old physical count, non-cash matches new expected
		cash_mode = next((m for m in self.mode if "cash" in m.lower()), self.mode[0])
		non_cash_mode = next((m for m in self.mode if m != cash_mode), self.mode[1] if len(self.mode) > 1 else self.mode[0])

		self._submit_original_sale(
			qty=2,
			rate=100.0,
			payments=[
				{"mode_of_payment": cash_mode, "amount": 100.0},
				{"mode_of_payment": non_cash_mode, "amount": 100.0},
			],
		)
		# Close shift with physical cash count = 80 (diff = -20) and non-cash count = 100 (diff = 0)
		self._close_shift(closing_amounts={cash_mode: 80.0, non_cash_mode: 100.0})

		# Backdate entry: adds new sale of 50.0 on non_cash_mode
		# Per D3: cash closing_amount stays 80.0 (old count); non_cash closing_amount becomes 150.0 (new expected)
		bde = self._make_bde(
			items=[{"invoice_no": 1, "item_code": self.item, "qty": 1, "rate": 50.0, "warehouse": self.profile.warehouse}],
			payments=[{"invoice_no": 1, "mode_of_payment": non_cash_mode, "amount": 50.0}],
			closing_payments=[
				{"mode_of_payment": cash_mode, "closing_amount": 80.0},
				{"mode_of_payment": non_cash_mode, "closing_amount": 150.0},
			],
		)
		bde.insert()
		bde.submit()

		new_closing = frappe.get_doc("POS Closing Shift", bde.new_closing_shift)
		recon_cash = next(
			(r for r in new_closing.payment_reconciliation if r.mode_of_payment == cash_mode),
			None,
		)
		recon_non_cash = next(
			(r for r in new_closing.payment_reconciliation if r.mode_of_payment == non_cash_mode),
			None,
		)
		self.assertIsNotNone(recon_cash)
		self.assertEqual(flt(recon_cash.closing_amount), 80.0)
		self.assertEqual(flt(recon_cash.expected_amount), 100.0)
		self.assertEqual(flt(recon_cash.difference), -20.0)

		self.assertIsNotNone(recon_non_cash)
		self.assertEqual(flt(recon_non_cash.closing_amount), 150.0)
		self.assertEqual(flt(recon_non_cash.expected_amount), 150.0)
		self.assertEqual(flt(recon_non_cash.difference), 0.0)

	# ---------------------------------------------------------------------------
	# Scenario 4: Correction flow
	# ---------------------------------------------------------------------------
	def test_scenario_4_correction_flow(self):
		mode0 = self.mode[0]
		mode1 = self.mode[1] if len(self.mode) > 1 else self.mode[0]
		orig_sale, _ = self._submit_original_sale(qty=1, rate=100.0, mode_of_payment=mode0)
		orig_closing_name = self._close_shift()

		orig_closing = frappe.get_doc("POS Closing Shift", orig_closing_name)
		recon_m0 = next((r for r in orig_closing.payment_reconciliation if r.mode_of_payment == mode0), None)
		recon_m1 = next((r for r in orig_closing.payment_reconciliation if r.mode_of_payment == mode1), None)
		self.assertEqual(flt(recon_m0.expected_amount), 100.0)
		if mode1 != mode0:
			self.assertEqual(flt(recon_m1.expected_amount), 0.0)

		posting_date = add_days(nowdate(), -1)
		# Correction splits payment across two modes (40 on mode0 and 60 on mode1)
		pay_rows = (
			[
				{"invoice_no": 1, "mode_of_payment": mode0, "amount": 40.0},
				{"invoice_no": 1, "mode_of_payment": mode1, "amount": 60.0},
			]
			if mode1 != mode0
			else [{"invoice_no": 1, "mode_of_payment": mode0, "amount": 100.0}]
		)
		bde = frappe.get_doc(
			{
				"doctype": "POS Backdate Entry",
				"company": self.profile.company,
				"pos_opening_shift": self.shift.name,
				"closing_date": posting_date,
				"closing_time": "18:00:00",
				"invoices": [
					{
						"invoice_no": 1,
						"entry_type": "Correction",
						"against_invoice": orig_sale["name"],
						"against_doctype": self.doctype,
						"customer": self.customer,
						"posting_date": posting_date,
						"posting_time": "10:00:00",
					}
				],
				"items": [
					{
						"invoice_no": 1,
						"item_code": self.item,
						"qty": 1,
						"rate": 100.0,
						"warehouse": self.profile.warehouse,
					}
				],
				"payments": pay_rows,
			}
		)
		bde.insert()
		bde.submit()

		self.assertEqual(frappe.db.get_value(self.doctype, orig_sale["name"], "docstatus"), 2)

		new_inv_name = bde.invoices[0].created_invoice
		new_inv = frappe.get_doc(self.doctype, new_inv_name)
		self.assertEqual(new_inv.docstatus, 1)
		self.assertEqual(new_inv.amended_from, orig_sale["name"])

		comments = frappe.get_all(
			"Comment",
			filters={"reference_name": orig_sale["name"], "reference_doctype": self.doctype},
			pluck="content",
		)
		self.assertTrue(any(new_inv_name in c and bde.name in c for c in comments))

		new_closing = frappe.get_doc("POS Closing Shift", bde.new_closing_shift)
		txns = [t.get("pos_invoice") or t.get("sales_invoice") for t in new_closing.pos_transactions]
		self.assertIn(new_inv_name, txns)
		self.assertNotIn(orig_sale["name"], txns)

		# Verify closing shift payment reconciliation reflects MOP shift
		new_recon_m0 = next((r for r in new_closing.payment_reconciliation if r.mode_of_payment == mode0), None)
		new_recon_m1 = next((r for r in new_closing.payment_reconciliation if r.mode_of_payment == mode1), None)
		if mode1 != mode0:
			self.assertEqual(flt(new_recon_m0.expected_amount), 40.0)
			self.assertEqual(flt(new_recon_m1.expected_amount), 60.0)

	# ---------------------------------------------------------------------------
	# Scenario 5: Package handling in backdate entry
	# ---------------------------------------------------------------------------
	def test_scenario_5_package_handling(self):
		_ensure_package()
		self._submit_original_sale()
		self._close_shift()

		pkg_list = get_backdate_packages(self.shift.name)
		pkg = next(p for p in pkg_list if p["name"] == PACKAGE)
		opt_id = pkg["options"][0]["option_id"]
		quote_res = quote(
			PACKAGE,
			[{"group_key": "accessory", "options": [{"option_id": opt_id, "qty": 1}]}],
			self.profile.name,
		)

		posting_date = add_days(nowdate(), -1)
		bde_items = []
		for idx, line in enumerate(quote_res["lines"]):
			bde_items.append(
				{
					"invoice_no": 1,
					"item_code": line["item_code"],
					"qty": line["qty"],
					# the form leaves uom blank on package rows
					"uom": "",
					"rate": quote_res["total"] if idx == 0 else 0,
					"warehouse": self.profile.warehouse,
					"pos_package": PACKAGE,
					"pos_package_instance": "pkg-bde-1",
					"pos_package_role": line["role"],
					"pos_package_snapshot": json.dumps(quote_res["snapshot"]) if line["role"] == PARENT_ROLE else None,
				}
			)

		bde = frappe.get_doc(
			{
				"doctype": "POS Backdate Entry",
				"company": self.profile.company,
				"pos_opening_shift": self.shift.name,
				"closing_date": posting_date,
				"closing_time": "18:00:00",
				"invoices": [
					{
						"invoice_no": 1,
						"entry_type": "Sale",
						"customer": self.customer,
						"posting_date": posting_date,
						"posting_time": "10:00:00",
					}
				],
				"items": bde_items,
				"payments": [
					{
						"invoice_no": 1,
						"mode_of_payment": self.mode[0],
						"amount": quote_res["total"],
					}
				],
			}
		)
		bde.insert()
		bde.submit()

		self.assertEqual(bde.docstatus, 1)
		created_inv = frappe.get_doc(self.doctype, bde.invoices[0].created_invoice)
		self.assertEqual(created_inv.docstatus, 1)
		self.assertEqual(len(created_inv.items), len(quote_res["lines"]))

	# ---------------------------------------------------------------------------
	# Scenario 6: Atomic rollback on failure
	# ---------------------------------------------------------------------------
	def test_scenario_6_atomic_rollback_on_failure(self):
		orig_sale, _ = self._submit_original_sale()
		orig_closing = self._close_shift()

		posting_date = add_days(nowdate(), -1)
		bde = frappe.get_doc(
			{
				"doctype": "POS Backdate Entry",
				"company": self.profile.company,
				"pos_opening_shift": self.shift.name,
				"closing_date": posting_date,
				"closing_time": "18:00:00",
				"invoices": [
					{
						"invoice_no": 1,
						"entry_type": "Sale",
						"customer": self.customer,
						"posting_date": posting_date,
						"posting_time": "10:00:00",
					},
					{
						"invoice_no": 2,
						"entry_type": "Sale",
						"customer": self.customer,
						"posting_date": posting_date,
						"posting_time": "11:00:00",
					},
				],
				"items": [
					{"invoice_no": 1, "item_code": self.item, "qty": 1, "rate": 100.0, "warehouse": self.profile.warehouse},
					{"invoice_no": 2, "item_code": self.item, "qty": 1, "rate": 100.0, "warehouse": self.profile.warehouse},
				],
				"payments": [
					{"invoice_no": 1, "mode_of_payment": self.mode[0], "amount": 100.0},
					{"invoice_no": 2, "mode_of_payment": self.mode[0], "amount": 100.0},
				],
			}
		)
		bde.insert()

		call_count = [0]
		from pos_next.pos_next.doctype.pos_backdate_entry.pos_backdate_entry import _submit_backdated

		created_invoices = []
		def fake_submit(payload):
			call_count[0] += 1
			if call_count[0] == 2:
				raise frappe.ValidationError("Simulated ledger failure on invoice 2")
			res = _submit_backdated(payload)
			inv_name = res.get("name") if isinstance(res, dict) else getattr(res, "name", None)
			if inv_name:
				created_invoices.append(inv_name)
			return res

		with patch("pos_next.pos_next.doctype.pos_backdate_entry.pos_backdate_entry._submit_backdated", side_effect=fake_submit):
			with self.assertRaises(frappe.ValidationError) as ctx:
				bde.submit()
			self.assertIn("Invoice No. 2: Simulated ledger failure", str(ctx.exception))

		# Rollback verification
		bde.reload()
		self.assertEqual(bde.docstatus, 0)
		self.assertEqual(frappe.db.get_value("POS Opening Shift", self.shift.name, "status"), "Closed")
		self.assertEqual(frappe.db.get_value("POS Closing Shift", orig_closing, "docstatus"), 1)

		# Invoice 1 must have been rolled back
		invoices_on_shift = frappe.get_all(self.doctype, {"posa_pos_opening_shift": self.shift.name}, pluck="name")
		self.assertEqual(invoices_on_shift, [orig_sale["name"]])

		# R7: Assert no surviving Repost Item Valuation rows from the rolled back attempt
		self.assertTrue(len(created_invoices) > 0)
		surviving_riv = frappe.get_all(
			"Repost Item Valuation",
			filters={"voucher_no": ["in", created_invoices]},
		)
		self.assertEqual(len(surviving_riv), 0)

	# ---------------------------------------------------------------------------
	# Scenario 7: Confirmation code gates
	# ---------------------------------------------------------------------------
	def test_scenario_7_confirmation_code_gates(self):
		orig_sale, orig_sale_doc = self._submit_original_sale(qty=2, rate=100.0)
		self._close_shift()

		# A. Discount code switch
		frappe.db.set_value("POS Settings", self.settings_name, "require_discount_code", 1)
		try:
			bde = self._make_bde(
				items=[
					{
						"invoice_no": 1,
						"item_code": self.item,
						"qty": 1,
						"rate": 100.0,
						"discount_percentage": 10.0,
						"warehouse": self.profile.warehouse,
					}
				],
				payments=[{"invoice_no": 1, "mode_of_payment": self.mode[0], "amount": 90.0}],
			)
			bde.insert()

			# Without confirmation code: rejected
			with self.assertRaises(frappe.ValidationError):
				bde.submit()

			bde.reload()
			# With valid confirmation code: accepted
			code_res = generate_codes_for("POS Discount Confirmation Code", 1, company=self.profile.company)
			valid_code = code_res["codes"][0]
			bde.invoices[0].confirmation_code = valid_code
			bde.save()
			bde.submit()
			self.assertEqual(bde.docstatus, 1)
		finally:
			frappe.db.set_value("POS Settings", self.settings_name, "require_discount_code", 0)

		# B. Refund code switch
		# Note: bde submit above re-closed the shift, so the shift is currently Closed.
		# orig_sale was submitted before initial closing, so it is valid for return on this shift!

		frappe.db.set_value("POS Settings", self.settings_name, "require_refund_code", 1)
		try:
			bde_ret = frappe.get_doc(
				{
					"doctype": "POS Backdate Entry",
					"company": self.profile.company,
					"pos_opening_shift": self.shift.name,
					"closing_date": add_days(nowdate(), -1),
					"closing_time": "18:00:00",
					"invoices": [
						{
							"invoice_no": 1,
							"entry_type": "Return",
							"customer": self.customer,
							"against_invoice": orig_sale["name"],
							"against_doctype": self.doctype,
							"posting_date": add_days(nowdate(), -1),
							"posting_time": "10:00:00",
						}
					],
					"items": [
						{
							"invoice_no": 1,
							"item_code": self.item,
							"qty": 1,
							"rate": 100.0,
							"warehouse": self.profile.warehouse,
							"against_item_row": orig_sale_doc.items[0].name,
						}
					],
					"payments": [
						{"invoice_no": 1, "mode_of_payment": self.mode[0], "amount": -100.0}
					],
				}
			)
			bde_ret.insert()

			# Without refund code: rejected
			with self.assertRaises(frappe.ValidationError):
				bde_ret.submit()

			bde_ret.reload()
			# With valid refund confirmation code: accepted
			code_res = generate_codes_for("POS Refund Code", 1, company=self.profile.company)
			bde_ret.invoices[0].confirmation_code = code_res["codes"][0]
			bde_ret.save()
			bde_ret.submit()
			self.assertEqual(bde_ret.docstatus, 1)
		finally:
			frappe.db.set_value("POS Settings", self.settings_name, "require_refund_code", 0)

	# ---------------------------------------------------------------------------
	# Scenario 8: Both invoice doctypes (POS Invoice & Sales Invoice)
	# ---------------------------------------------------------------------------
	def test_scenario_8_both_invoice_doctypes(self):
		# POS Invoice mode
		_set_invoice_type(POS_INVOICE)
		self._submit_original_sale()
		self._close_shift()

		bde = self._make_bde()
		bde.insert()
		bde.submit()
		self.assertEqual(bde.invoices[0].invoice_doctype, POS_INVOICE)
		self.assertTrue(frappe.db.exists("POS Invoice", bde.invoices[0].created_invoice))

		# Sales Invoice mode on fresh shift
		_set_invoice_type(SALES_INVOICE)
		try:
			shift2 = frappe.get_doc(
				{
					"doctype": "POS Opening Shift",
					"pos_profile": self.profile.name,
					"company": self.profile.company,
					"user": ADMIN,
					"posting_date": add_days(nowdate(), -2),
					"period_start_date": add_days(now_datetime(), -2),
					"balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
				}
			).insert(ignore_permissions=True)
			shift2.submit()

			# Sale + close
			payload = {
				"pos_profile": self.profile.name,
				"posa_pos_opening_shift": shift2.name,
				"customer": self.customer,
				"posting_date": add_days(nowdate(), -2),
				"posting_time": "09:00:00",
				"items": [{"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}],
				"payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
			}
			frappe.flags.pos_next_backdate_entry = True
			try:
				submit_invoice(invoice=json.dumps(payload), data="{}")
			finally:
				frappe.flags.pos_next_backdate_entry = None

			closing_data = get_closing_shift_data(shift2.name)
			submit_closing_shift(json.dumps(closing_data))

			bde2 = frappe.get_doc(
				{
					"doctype": "POS Backdate Entry",
					"company": self.profile.company,
					"pos_opening_shift": shift2.name,
					"closing_date": add_days(nowdate(), -1),
					"closing_time": "18:00:00",
					"invoices": [
						{
							"invoice_no": 1,
							"entry_type": "Sale",
							"customer": self.customer,
							"posting_date": add_days(nowdate(), -1),
							"posting_time": "10:00:00",
						}
					],
					"items": [
						{
							"invoice_no": 1,
							"item_code": self.item,
							"qty": 1,
							"rate": 100.0,
							"warehouse": self.profile.warehouse,
						}
					],
					"payments": [
						{
							"invoice_no": 1,
							"mode_of_payment": self.mode[0],
							"amount": 100.0,
						}
					],
				}
			)
			bde2.insert()
			bde2.submit()

			self.assertEqual(bde2.invoices[0].invoice_doctype, SALES_INVOICE)
			self.assertTrue(frappe.db.exists("Sales Invoice", bde2.invoices[0].created_invoice))

			# Clean up shift2: delete BDE first to avoid LinkExistsError on closing shift
			if frappe.db.exists("POS Backdate Entry", bde2.name):
				frappe.db.set_value("POS Backdate Entry", bde2.name, "docstatus", 2, update_modified=False)
				frappe.delete_doc("POS Backdate Entry", bde2.name, force=1, ignore_permissions=True)
			for cs in frappe.get_all("POS Closing Shift", {"pos_opening_shift": shift2.name}, pluck="name"):
				cdoc = frappe.get_doc("POS Closing Shift", cs)
				if cdoc.docstatus == 1:
					cdoc.cancel()
				frappe.delete_doc("POS Closing Shift", cs, force=1, ignore_permissions=True)
			for si in frappe.get_all("Sales Invoice", {"posa_pos_opening_shift": shift2.name}, pluck="name"):
				sdoc = frappe.get_doc("Sales Invoice", si)
				if sdoc.docstatus == 1:
					sdoc.cancel()
				frappe.delete_doc("Sales Invoice", si, force=1, ignore_permissions=True)
			frappe.db.set_value("POS Opening Shift", shift2.name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", shift2.name, force=1, ignore_permissions=True)
		finally:
			_set_invoice_type(POS_INVOICE)

	# ---------------------------------------------------------------------------
	# Scenario 9: Posting datetime window validations (R4)
	# ---------------------------------------------------------------------------
	def test_scenario_9_posting_datetime_window_validations(self):
		self._submit_original_sale()
		self._close_shift()

		# Invoice posting date before shift period start
		bde_early = self._make_bde(
			invoices=[
				{
					"invoice_no": 1,
					"entry_type": "Sale",
					"customer": self.customer,
					"posting_date": add_days(nowdate(), -10),
					"posting_time": "10:00:00",
				}
			]
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			bde_early.insert()
		self.assertIn("shift period", str(ctx.exception))

		# Invoice posting date in future
		bde_future = self._make_bde(
			invoices=[
				{
					"invoice_no": 1,
					"entry_type": "Sale",
					"customer": self.customer,
					"posting_date": add_days(nowdate(), 2),
					"posting_time": "10:00:00",
				}
			]
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			bde_future.insert()
		self.assertIn("shift period", str(ctx.exception))

		# Closing datetime before invoice max posting datetime
		bde_closing_early = self._make_bde(
			closing_date=add_days(nowdate(), -2),
			closing_time="08:00:00",
			invoices=[
				{
					"invoice_no": 1,
					"entry_type": "Sale",
					"customer": self.customer,
					"posting_date": add_days(nowdate(), -1),
					"posting_time": "10:00:00",
				}
			],
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			bde_closing_early.insert()
		self.assertIn("Closing date/time", str(ctx.exception))

		# Closing datetime in future
		bde_closing_future = self._make_bde(
			closing_date=add_days(nowdate(), 2),
			closing_time="12:00:00",
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			bde_closing_future.insert()
		self.assertIn("cannot be in the future", str(ctx.exception))

	# ---------------------------------------------------------------------------
	# Scenario 10: Correction blocked scenarios (R10)
	# ---------------------------------------------------------------------------
	def test_scenario_10_correction_blocked_scenarios(self):
		orig_sale, orig_sale_doc = self._submit_original_sale(qty=2, rate=100.0)
		orig_amended, _ = self._submit_original_sale(qty=1, rate=100.0)
		other_inv, _ = self._submit_original_sale(qty=1, rate=100.0)

		# Submit a return against the sale while shift is open
		ret_item_field = "sales_invoice_item" if self.doctype == "Sales Invoice" else "pos_invoice_item"
		ret_payload = {
			"pos_profile": self.profile.name,
			"posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"is_return": 1,
			"return_against": orig_sale["name"],
			"posting_date": add_days(nowdate(), -1),
			"posting_time": "10:00:00",
			"set_posting_time": 1,
			"items": [
				{
					"item_code": self.item,
					"qty": -1,
					"rate": 100.0,
					"warehouse": self.profile.warehouse,
					ret_item_field: orig_sale_doc.items[0].name,
				}
			],
			"payments": [{"mode_of_payment": self.mode[0], "amount": -100.0}],
		}
		frappe.flags.pos_next_backdate_entry = True
		try:
			ret_inv = submit_invoice(invoice=json.dumps(ret_payload), data="{}")
		finally:
			frappe.flags.pos_next_backdate_entry = None

		self._close_shift()

		# A. Cannot correct a return invoice
		bde_ret = self._make_bde(
			invoices=[
				{
					"invoice_no": 1,
					"entry_type": "Correction",
					"against_invoice": ret_inv["name"],
					"against_doctype": self.doctype,
					"customer": self.customer,
					"posting_date": add_days(nowdate(), -1),
					"posting_time": "11:00:00",
				}
			]
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			bde_ret.insert()
		self.assertIn("Cannot correct a return invoice", str(ctx.exception))

		# B. Cannot correct original sale because a submitted return exists against it
		bde_sale_with_return = self._make_bde(
			invoices=[
				{
					"invoice_no": 1,
					"entry_type": "Correction",
					"against_invoice": orig_sale["name"],
					"against_doctype": self.doctype,
					"customer": self.customer,
					"posting_date": add_days(nowdate(), -1),
					"posting_time": "11:00:00",
				}
			]
		)
		with self.assertRaises(frappe.ValidationError) as ctx:
			bde_sale_with_return.insert()
		self.assertIn("submitted return exists against it", str(ctx.exception))

		# C. Cannot correct invoice that has already been amended
		frappe.db.set_value(self.doctype, orig_amended["name"], "amended_from", "PREV-INV-001")
		try:
			bde_amended = self._make_bde(
				invoices=[
					{
						"invoice_no": 1,
						"entry_type": "Correction",
						"against_invoice": orig_amended["name"],
						"against_doctype": self.doctype,
						"customer": self.customer,
						"posting_date": add_days(nowdate(), -1),
						"posting_time": "11:00:00",
					}
				]
			)
			with self.assertRaises(frappe.ValidationError) as ctx:
				bde_amended.insert()
			self.assertIn("has already been amended", str(ctx.exception))
		finally:
			frappe.db.set_value(self.doctype, orig_amended["name"], "amended_from", None)

		# D. Cannot correct invoice that has already been corrected by another invoice
		frappe.db.set_value(self.doctype, other_inv["name"], "amended_from", orig_amended["name"])
		try:
			bde_already_corrected = self._make_bde(
				invoices=[
					{
						"invoice_no": 1,
						"entry_type": "Correction",
						"against_invoice": orig_amended["name"],
						"against_doctype": self.doctype,
						"customer": self.customer,
						"posting_date": add_days(nowdate(), -1),
						"posting_time": "11:00:00",
					}
				]
			)
			with self.assertRaises(frappe.ValidationError) as ctx:
				bde_already_corrected.insert()
			self.assertIn("has already been corrected", str(ctx.exception))
		finally:
			frappe.db.set_value(self.doctype, other_inv["name"], "amended_from", None)

		# E. Cannot correct invoice with linked Payment Entry
		bde_with_pe = self._make_bde(
			invoices=[
				{
					"invoice_no": 1,
					"entry_type": "Correction",
					"against_invoice": orig_amended["name"],
					"against_doctype": self.doctype,
					"customer": self.customer,
					"posting_date": add_days(nowdate(), -1),
					"posting_time": "11:00:00",
					}
				]
		)
		orig_sql = frappe.db.sql
		def mock_sql_pe(query, *args, **kwargs):
			if "tabPayment Entry Reference" in str(query):
				return [("PE-MOCK-001",)]
			return orig_sql(query, *args, **kwargs)

		with patch("frappe.db.sql", side_effect=mock_sql_pe):
			with self.assertRaises(frappe.ValidationError) as ctx:
				bde_with_pe.insert()
			self.assertIn("linked Payment Entries", str(ctx.exception))

		# F. Cannot correct invoice with linked Wallet Transaction
		orig_exists = frappe.db.exists
		def mock_exists_wallet(dt, *args, **kwargs):
			if dt == "Wallet Transaction":
				return True
			return orig_exists(dt, *args, **kwargs)

		with patch("frappe.db.exists", side_effect=mock_exists_wallet):
			with self.assertRaises(frappe.ValidationError) as ctx:
				bde_with_pe.insert()
			self.assertIn("linked Payment Entries", str(ctx.exception))

	# ---------------------------------------------------------------------------
	# Scenario 11: Cancel prohibited
	# ---------------------------------------------------------------------------
	def test_scenario_11_before_cancel_prohibited(self):
		self._submit_original_sale()
		self._close_shift()

		bde = self._make_bde()
		bde.insert()
		bde.submit()

		with self.assertRaises(frappe.ValidationError) as ctx:
			bde.cancel()
		self.assertIn("cannot be cancelled", str(ctx.exception))

	# ---------------------------------------------------------------------------
	# Helper unit tests: get_shift_context, get_item_rate, quote_backdate_package,
	# pos_opening_shift_query, get_return_items
	# ---------------------------------------------------------------------------
	def test_helper_get_shift_context(self):
		self._submit_original_sale()
		self._close_shift()

		# A. Happy path returns context payload with warehouse, currency, etc.
		ctx = get_shift_context(self.shift.name)
		self.assertEqual(ctx["shift"]["name"], self.shift.name)
		self.assertEqual(ctx["warehouse"], self.profile.warehouse)
		expected_currency = frappe.get_cached_value("Company", self.profile.company, "default_currency")
		self.assertEqual(ctx["currency"], expected_currency)
		self.assertIn(self.mode[0], ctx["payment_modes"])
		self.assertTrue(len(ctx["shift_invoices"]) > 0)

		# B. Role gate: plain user without backdate role rejected
		frappe.set_user(self.plain_user)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_shift_context(self.shift.name)
		finally:
			frappe.set_user(ADMIN)

		# C. Profile flag gate: disabled allow_change_posting_date rejected
		frappe.db.set_value("POS Settings", self.settings_name, "allow_change_posting_date", 0)
		try:
			with self.assertRaises(frappe.ValidationError) as c:
				get_shift_context(self.shift.name)
			self.assertIn("disabled", str(c.exception))
		finally:
			frappe.db.set_value("POS Settings", self.settings_name, "allow_change_posting_date", 1)

	def test_helper_get_item_rate(self):
		# A. Date-aware item rate lookup
		p_date = add_days(nowdate(), -1)
		details = get_item_rate(self.shift.name, self.item, posting_date=p_date)
		self.assertEqual(details["item_code"], self.item)
		self.assertGreater(flt(details["rate"]), 0)
		self.assertGreater(flt(details["price_list_rate"]), 0)

		# B. Role gate: plain user rejected
		frappe.set_user(self.plain_user)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_item_rate(self.shift.name, self.item, posting_date=p_date)
		finally:
			frappe.set_user(ADMIN)

	def test_helper_quote_backdate_package(self):
		_ensure_package()
		pkg_list = get_backdate_packages(self.shift.name)
		pkg = next(p for p in pkg_list if p["name"] == PACKAGE)
		opt_id = pkg["options"][0]["option_id"]
		choices = [{"group_key": "accessory", "options": [{"option_id": opt_id, "qty": 1}]}]
		p_date = add_days(nowdate(), -1)

		# A. Dated quote
		quote_res = quote_backdate_package(self.shift.name, PACKAGE, choices, posting_date=p_date)
		self.assertGreater(flt(quote_res["total"]), 0)
		self.assertGreater(len(quote_res["lines"]), 0)

		# B. Role gate: plain user rejected
		frappe.set_user(self.plain_user)
		try:
			with self.assertRaises(frappe.PermissionError):
				quote_backdate_package(self.shift.name, PACKAGE, choices, posting_date=p_date)
		finally:
			frappe.set_user(ADMIN)

	def test_helper_pos_opening_shift_query(self):
		# A. Role gate: plain user rejected
		frappe.set_user(self.plain_user)
		try:
			with self.assertRaises(frappe.PermissionError):
				pos_opening_shift_query("POS Opening Shift", "", "name", 0, 20, {"company": self.profile.company})
		finally:
			frappe.set_user(ADMIN)

		# B. Sales Manager with role passes
		frappe.set_user(self.sales_manager_user)
		try:
			results = pos_opening_shift_query("POS Opening Shift", "", "name", 0, 20, {"company": self.profile.company})
			self.assertIsInstance(results, list)
		finally:
			frappe.set_user(ADMIN)

	def test_helper_get_return_items(self):
		orig_sale, _ = self._submit_original_sale(qty=2, rate=100.0)
		self._close_shift()

		# A. Plain user rejected by role gate
		frappe.set_user(self.plain_user)
		try:
			with self.assertRaises(frappe.PermissionError):
				get_return_items(orig_sale["name"], doctype=self.doctype)
		finally:
			frappe.set_user(ADMIN)

		# B. Sales Manager passes despite NOT being in POS Profile User (SEC-05 exemption)
		frappe.set_user(self.sales_manager_user)
		try:
			items = get_return_items(orig_sale["name"], doctype=self.doctype)
			self.assertIsInstance(items, list)
			self.assertTrue(len(items) > 0)
			self.assertTrue(items[0].get("against_item_row"))
			self.assertEqual(flt(items[0].get("remaining_qty")), 2.0)
		finally:
			frappe.set_user(ADMIN)

		# C. Profile flag gate: disabled allow_change_posting_date rejected
		frappe.db.set_value("POS Settings", self.settings_name, "allow_change_posting_date", 0)
		try:
			with self.assertRaises(frappe.ValidationError) as c:
				get_return_items(orig_sale["name"], doctype=self.doctype)
			self.assertIn("disabled", str(c.exception))
		finally:
			frappe.db.set_value("POS Settings", self.settings_name, "allow_change_posting_date", 1)
