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
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from pos_next.api.invoices import submit_invoice
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	get_pos_invoices,
	make_closing_shift_from_opening,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestClosingCashModeRegression(POSInvoiceModeMixin, FrappeTestCase):
	"""Change given (kembalian) must net into the profile's own cash row.

	The old fallback subtracted change from the generic "Cash" mode whenever
	``posa_cash_mode_of_payment`` was empty, minting a phantom second cash
	row with a negative expected amount in the closing reconciliation
	("kok cashnya ada 2?").
	"""

	def setUp(self):
		super().setUp()
		self._field_baseline = frappe.db.get_value(
			"POS Profile", self.profile.name, "posa_cash_mode_of_payment"
		)
		frappe.db.set_value(
			"POS Profile",
			self.profile.name,
			"posa_cash_mode_of_payment",
			None,
			update_modified=False,
		)
		self.outlet_cash, self._minted_mop = self._ensure_outlet_cash_row()

	def _ensure_outlet_cash_row(self):
		"""A Cash-type payment row not named "Cash", made the profile's
		default — the shape of a real outlet profile ("Cash PKU DELANGGU",
		default row, cash field left empty). Returns (mode_name, minted)."""
		rows = frappe.get_all(
			"POS Payment Method",
			{"parent": self.profile.name, "parenttype": "POS Profile"},
			["name", "mode_of_payment", "default"],
			order_by="idx asc",
		)
		types = (
			{
				r.name: r.type
				for r in frappe.get_all(
					"Mode of Payment",
					filters={"name": ["in", [row.mode_of_payment for row in rows]]},
					fields=["name", "type"],
				)
			}
			if rows
			else {}
		)
		self._defaults_baseline = {row.mode_of_payment: row.default for row in rows}
		outlet_cash = next(
			(
				row.mode_of_payment
				for row in rows
				if types.get(row.mode_of_payment) == "Cash" and row.mode_of_payment != "Cash"
			),
			None,
		)
		minted = False
		if not outlet_cash:
			account = self._cash_account_for_company(self.profile.company)
			if not account:
				self.skipTest("no cash account to mint a Mode of Payment against")
			outlet_cash = f"Outlet Cash {uuid.uuid4().hex[:6].upper()}"
			frappe.get_doc(
				{
					"doctype": "Mode of Payment",
					"mode_of_payment": outlet_cash,
					"type": "Cash",
					"accounts": [{"company": self.profile.company, "default_account": account}],
				}
			).insert(ignore_permissions=True)
			minted = True

		if self._defaults_baseline.get(outlet_cash):
			return outlet_cash, minted

		profile = frappe.get_doc("POS Profile", self.profile.name)
		for row in profile.payments:
			row.default = 1 if row.mode_of_payment == outlet_cash else 0
		if minted:
			profile.append("payments", {"mode_of_payment": outlet_cash, "default": 1, "amount": 0})
		profile.save(ignore_permissions=True)
		return outlet_cash, minted

	@staticmethod
	def _cash_account_for_company(company):
		"""The generic "Cash" MOP's account for the company, else the
		company's default cash account — ERPNext's POS Profile validator
		refuses payment rows whose MOP has no account for this company."""
		return frappe.db.get_value(
			"Mode of Payment Account", {"parent": "Cash", "company": company}, "default_account"
		) or frappe.db.get_value("Company", company, "default_cash_account")

	def tearDown(self):
		# mixin first: it cancels + deletes the invoices, so the minted MOP
		# has no live references left when it is removed below
		super().tearDown()
		frappe.db.set_value(
			"POS Profile",
			self.profile.name,
			"posa_cash_mode_of_payment",
			self._field_baseline,
			update_modified=False,
		)
		if getattr(self, "_minted_mop", False) or getattr(self, "_defaults_baseline", None):
			profile = frappe.get_doc("POS Profile", self.profile.name)
			profile.payments = [
				row
				for row in profile.payments
				if row.mode_of_payment != getattr(self, "outlet_cash", None)
			]
			for row in profile.payments:
				row.default = self._defaults_baseline.get(row.mode_of_payment, row.default)
			profile.save(ignore_permissions=True)
		if getattr(self, "_minted_mop", False):
			if frappe.db.exists("Mode of Payment", self.outlet_cash):
				frappe.delete_doc("Mode of Payment", self.outlet_cash, force=1, ignore_permissions=True)
		frappe.db.commit()

	def test_change_nets_into_profile_cash_row(self):
		# the SPA always sends the payment row's MOP type (useInvoice.addPayment);
		# ERPNext derives change_amount = paid - grand from a Cash-typed row.
		# Pay 400 for a 100 bill: 300 goes back as change, the drawer keeps 100.
		result = submit_invoice(
			invoice=self._payload(
				payments=[{"mode_of_payment": self.outlet_cash, "amount": 400, "type": "Cash"}],
			)
		)
		self._created.append(result.get("name"))
		frappe.db.commit()

		opening = frappe.get_doc("POS Opening Shift", self.shift.name)
		closing = make_closing_shift_from_opening(
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
		rows = {row["mode_of_payment"]: row for row in closing["payment_reconciliation"]}

		# the change nets into the outlet's cash row, not into a phantom one
		self.assertEqual(flt(rows[self.outlet_cash]["expected_amount"]), 100)
		generic = rows.get("Cash")
		if generic is not None:
			# a generic "Cash" row may exist (opening seed) but must stay
			# untouched by the change
			self.assertEqual(flt(generic["expected_amount"]), flt(generic["opening_amount"]))


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
