# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-11 and PERF-19 acceptance tests.

PERF-11: the pending wallet sum feeds the SEC-14 double-spend guard, so it
must count EVERY open wallet invoice (Fix Round 1: one aggregate query per
doctype — the round-0 row limit let a customer with >50 open wallet invoices
overspend) with the wallet modes resolved once, never per payment row.
PERF-19: check_opening_shift / create_opening_shift must serve the read-only
POS Profile / Company masters through frappe.get_cached_doc, never
frappe.get_doc (both flows keep answering exactly as before).

Fixture note: the dev site's RQ backlog rejects every enqueue
(QueueOverloaded), and User.on_update enqueues unconditionally — so test-only
User / Customer / Mode of Payment / profile-user rows go straight in at the
SQL layer (db_insert / INSERT). The money path under test (Wallet Transaction
credit, the pending aggregation) runs through the real controllers.

Run via pos_next/_pn_run_tests.py pos_next.api.test_perf_be1
"""

import json
import unittest
import uuid
from unittest import mock

import frappe
from frappe.tests import IntegrationTestCase
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime, nowdate, today

from pos_next.api.shifts import check_opening_shift, create_opening_shift
from pos_next.api.wallet import (
	get_customer_wallet_balance,
	get_or_create_wallet,
	get_pending_wallet_payments,
	validate_wallet_payment,
)
from pos_next.pos_next.doctype.wallet_transaction.wallet_transaction import create_wallet_credit
from pos_next.tests.price_group_helpers import get_default_company

ADMIN = "Administrator"
OPENING_CASH = 100
# same schedule-safe profile filter as api/test_closing_shift_security.py
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]


def _sql_insert(table, columns, row):
	"""One flat INSERT with frappe-style timestamps — the SQL-layer fixture helper."""
	names = ", ".join(f"`{c}`" for c in columns)
	marks = ", ".join(f"%({c})s" for c in columns)
	frappe.db.sql(
		f"INSERT INTO `{table}` ({names}) VALUES ({marks})",
		{
			**row,
			"owner": ADMIN,
			"creation": frappe.utils.now(),
			"modified": frappe.utils.now(),
			"modified_by": ADMIN,
		},
	)


class TestPendingWalletPayments(unittest.TestCase):
	"""The sum is one aggregate query per doctype: wallet modes resolved once,
	zero per-row lookups, no row limit to reason about."""

	def test_single_mode_lookup_and_pure_aggregation(self):
		mop_lookups = []
		aggregate_queries = []
		real_sql = frappe.db.sql

		def fake_get_value(doctype, *args, **kwargs):
			if doctype == "Mode of Payment":
				mop_lookups.append(args)
				return 1
			return None

		def spy_sql(query, *args, **kwargs):
			text = " ".join(str(query).split())
			if "SUM(sip.amount)" in text:
				aggregate_queries.append(text)
			return real_sql(query, *args, **kwargs)

		with (
			mock.patch("frappe.db.table_exists", return_value=True),
			mock.patch("frappe.get_all", return_value=["_PERF11 Wallet"]) as get_all,
			mock.patch("frappe.db.get_value", side_effect=fake_get_value),
			mock.patch("frappe.db.sql", side_effect=spy_sql),
		):
			total = get_pending_wallet_payments("_PERF11R1 nobody")

		# no open wallet invoices for a fresh name: the aggregates return NULL
		self.assertEqual(total, 0.0)
		# wallet modes: one lookup, never per payment row
		self.assertEqual(get_all.call_count, 1)
		self.assertEqual(mop_lookups, [])
		# exactly one aggregate per doctype — query count is independent of
		# how many open invoices / payment rows exist
		self.assertEqual(len(aggregate_queries), 2)


class TestPendingWalletPaymentsUnbounded(IntegrationTestCase):
	"""SEC-14 money path: a customer with more open wallet invoices than any
	row limit must still have every earmark counted — validate_wallet_payment
	must reject a payment that exceeds balance minus ALL pending."""

	OPEN_DRAFTS = 55
	DRAFT_AMOUNT = 1
	WALLET_CREDIT = 100
	REQUESTED = 50  # passes against a 50-row-limited window, must fail on 55

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		cls.wallet_mode = cls._make_wallet_mode()
		cls.customer = cls._make_customer()
		cls.wallet = get_or_create_wallet(cls.customer, cls.company, force_create=True)
		if not cls.wallet:
			raise unittest.SkipTest("no wallet account configured on company")
		cls.wallet_txn = create_wallet_credit(
			wallet=cls.wallet.name,
			amount=cls.WALLET_CREDIT,
			source_type="Manual Adjustment",
			remarks="_PERF11R1 fixture",
			submit=True,
		)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			try:
				step()
			except Exception:
				pass

		if getattr(cls, "wallet_txn", None):
			txn_name = cls.wallet_txn.name

			def _drop_credit():
				txn = frappe.get_doc("Wallet Transaction", txn_name)
				if txn.docstatus == 1:
					txn.cancel()
				frappe.db.delete("Wallet Transaction", {"name": txn_name})

			_safe(_drop_credit)
			# belt and braces on the shared dev site: any GL rows the cancel
			# could not reverse must not outlive the fixture
			_safe(lambda: frappe.db.delete("GL Entry", {"voucher_no": txn_name}))
		if getattr(cls, "wallet", None):
			_safe(lambda: frappe.db.delete("Wallet", {"name": cls.wallet.name}))
		if getattr(cls, "customer", None):
			_safe(lambda: frappe.db.delete("Customer", {"name": cls.customer}))
		if getattr(cls, "_wallet_mode_created", False):
			_safe(lambda: frappe.db.delete("Mode of Payment", {"name": cls.wallet_mode}))
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _make_wallet_mode(cls):
		name = "_PERF11R1 Wallet MOP"
		existing = frappe.db.get_value("Mode of Payment", {"mode_of_payment": name, "enabled": 1}, "name")
		if existing:
			return name
		doc = frappe.new_doc("Mode of Payment")
		doc.name = name
		doc.mode_of_payment = name
		doc.enabled = 1
		doc.type = "General"
		doc.is_wallet_payment = 1
		doc.db_insert()
		cls._wallet_mode_created = True
		return name

	@classmethod
	def _make_customer(cls):
		name = f"_PERF11R1 Customer {frappe.generate_hash(length=6)}"
		customer_group = (
			frappe.db.get_value("Customer Group", {"is_group": 0}, "name", order_by="creation asc")
			or "All Customer Groups"
		)
		doc = frappe.new_doc("Customer")
		doc.name = name
		doc.customer_name = name
		doc.customer_group = customer_group
		doc.territory = "All Territories"
		doc.db_insert()
		return name

	@classmethod
	def _make_open_wallet_drafts(cls, test, count, amount):
		"""Draft wallet-payment invoices built straight at the SQL layer: the
		dev site's RQ backlog can reject doc.insert(), and the pending sum only
		reads the parent's customer/docstatus/is_pos plus the child's amount."""
		for _ in range(count):
			doc = frappe.new_doc("Sales Invoice")
			doc.company = cls.company
			doc.customer = cls.customer
			doc.is_pos = 1
			doc.posting_date = today()
			doc.name = "SI-PERF11R1-" + frappe.generate_hash(length=8)
			doc.db_insert()
			payment_name = "SIP-PERF11R1-" + frappe.generate_hash(length=8)
			_sql_insert(
				"tabSales Invoice Payment",
				(
					"name",
					"owner",
					"creation",
					"modified",
					"modified_by",
					"parent",
					"parentfield",
					"parenttype",
					"idx",
					"docstatus",
					"mode_of_payment",
					"amount",
					"base_amount",
				),
				{
					"name": payment_name,
					"parent": doc.name,
					"parentfield": "payments",
					"parenttype": "Sales Invoice",
					"idx": 1,
					"docstatus": 0,
					"mode_of_payment": cls.wallet_mode,
					"amount": amount,
					"base_amount": amount,
				},
			)
			test.addCleanup(lambda n=doc.name: frappe.db.delete("Sales Invoice", {"name": n}))
			test.addCleanup(lambda n=payment_name: frappe.db.delete("Sales Invoice Payment", {"name": n}))

	def test_validation_counts_every_open_wallet_invoice(self):
		# sanity: the wallet credit landed — a 0 balance would reject everything
		# and prove nothing about the limit
		self.assertEqual(
			get_customer_wallet_balance(self.customer, self.company), float(self.WALLET_CREDIT)
		)

		self._make_open_wallet_drafts(self, self.OPEN_DRAFTS, self.DRAFT_AMOUNT)

		# no row limit: all 55 earmarks count (a 50-row window saw only 50)
		self.assertEqual(
			get_pending_wallet_payments(self.customer),
			float(self.OPEN_DRAFTS * self.DRAFT_AMOUNT),
		)

		# balance 100 - pending 55 = 45 available: a 50 wallet payment must be
		# rejected. Against the row-limited window (pending 50, available 50)
		# this same request slipped through — the double-spend this test pins.
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": self.customer,
				"is_pos": 1,
				"is_consolidated": 0,
				"payments": [{"mode_of_payment": self.wallet_mode, "amount": self.REQUESTED}],
			}
		)
		doc.name = "SI-PERF11R1-VALIDATE"
		with self.assertRaises(frappe.ValidationError):
			validate_wallet_payment(doc)


class TestShiftMasterDocs(FrappeTestCase):
	"""PERF-19: masters served from the cache, flow answers unchanged."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.profile = frappe.db.get_value(
			"POS Profile",
			_PROFILE_FILTER,
			["name", "company"],
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
		if not (cls.profile and cls.mode):
			raise unittest.SkipTest("no schedule-safe POS Profile with a payment method")
		# checker owns the fixture shift (check_opening_shift path); cashier
		# holds no open shift and may open one (create_opening_shift path)
		cls.checker = f"perf19c.{uuid.uuid4().hex[:8]}@example.com"
		cls.cashier = f"perf19o.{uuid.uuid4().hex[:8]}@example.com"
		# User.on_update unconditionally enqueues a contact job and the dev
		# site's RQ backlog rejects every enqueue — user + role + profile-user
		# rows go straight in at the SQL layer
		cls._make_user(cls.checker)
		cls._make_user(cls.cashier, roles=("POSNext Cashier",))
		for email in (cls.checker, cls.cashier):
			_sql_insert(
				"tabPOS Profile User",
				(
					"name",
					"owner",
					"creation",
					"modified",
					"modified_by",
					"parent",
					"parenttype",
					"parentfield",
					"user",
					"default",
					"idx",
					"docstatus",
				),
				{
					"name": "PPU-PERF11R1-" + frappe.generate_hash(length=8),
					"parent": cls.profile.name,
					"parenttype": "POS Profile",
					"parentfield": "applicable_for_users",
					"user": email,
					"default": 1,
					"idx": 99,
					"docstatus": 0,
				},
			)
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": cls.profile.name,
				"company": cls.profile.company,
				"user": cls.checker,
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": cls.mode[0], "amount": OPENING_CASH}],
			}
		).insert(ignore_permissions=True)
		shift.submit()
		cls.shift_name = shift.name

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)

		def _safe(step):
			try:
				step()
			except Exception:
				pass

		if getattr(cls, "shift_name", None):
			def _drop():
				doc = frappe.get_doc("POS Opening Shift", cls.shift_name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.db.delete("POS Opening Shift", {"name": cls.shift_name})

			_safe(_drop)
			# raw fallbacks so a hook failure can never strand the fixture on
			# the shared dev site
			_safe(lambda: frappe.db.delete("POS Opening Shift Detail", {"parent": cls.shift_name}))
			_safe(lambda: frappe.db.delete("POS Opening Shift", {"name": cls.shift_name}))
		for email in (cls.checker, cls.cashier):
			_safe(lambda e=email: frappe.db.delete("POS Profile User", {"user": e}))
			_safe(lambda e=email: frappe.db.delete("Has Role", {"parent": e}))
			_safe(lambda e=email: frappe.db.delete("User", {"name": e}))
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _make_user(cls, email, roles=()):
		doc = frappe.new_doc("User")
		doc.name = email
		doc.email = email
		doc.first_name = "PERF11R1"
		doc.enabled = 1
		doc.user_type = "System User"
		doc.db_insert()
		for idx, role in enumerate(roles, start=1):
			_sql_insert(
				"tabHas Role",
				(
					"name",
					"owner",
					"creation",
					"modified",
					"modified_by",
					"parent",
					"parenttype",
					"parentfield",
					"role",
					"idx",
					"docstatus",
				),
				{
					"name": "HR-PERF11R1-" + frappe.generate_hash(length=8),
					"parent": email,
					"parenttype": "User",
					"parentfield": "roles",
					"role": role,
					"idx": idx,
					"docstatus": 0,
				},
			)

	def _spy_get_doc(self):
		real = frappe.get_doc
		seen = []

		def spy(*args, **kwargs):
			if args and isinstance(args[0], str):
				seen.append(args)
			return real(*args, **kwargs)

		return seen, mock.patch("frappe.get_doc", side_effect=spy)

	def _remove_created_shift(self, name):
		try:
			doc = frappe.get_doc("POS Opening Shift", name)
			if doc.docstatus == 1:
				doc.cancel()
			frappe.db.delete("POS Opening Shift", {"name": name})
		except Exception:
			frappe.db.delete("POS Opening Shift Detail", {"parent": name})
			frappe.db.delete("POS Opening Shift", {"name": name})

	def test_check_opening_shift_flow_without_master_get_doc(self):
		frappe.set_user(self.checker)
		seen, patcher = self._spy_get_doc()
		try:
			with patcher:
				data = check_opening_shift()
		finally:
			frappe.set_user(ADMIN)

		# the flow still answers correctly (PERF-19 must not change behavior)
		self.assertIsNotNone(data)
		self.assertEqual(data["pos_opening_shift"].name, self.shift_name)
		self.assertEqual(data["pos_profile"].name, self.profile.name)
		self.assertEqual(data["company"].name, self.profile.company)
		# and the read-only masters never go through frappe.get_doc
		self.assertFalse([args for args in seen if args[0] in ("POS Profile", "Company")])

	def test_create_opening_shift_flow_without_master_get_doc(self):
		frappe.set_user(self.cashier)
		seen, patcher = self._spy_get_doc()
		data = None
		try:
			with patcher:
				data = create_opening_shift(
					self.profile.name,
					self.profile.company,
					json.dumps([{"mode_of_payment": self.mode[0], "opening_amount": OPENING_CASH}]),
				)
		finally:
			frappe.set_user(ADMIN)
			if data:
				self._remove_created_shift(data["pos_opening_shift"]["name"])

		# the profile/company echoed back are the requested ones
		self.assertEqual(data["pos_opening_shift"]["pos_profile"], self.profile.name)
		self.assertEqual(data["pos_profile"].name, self.profile.name)
		self.assertEqual(data["company"].name, self.profile.company)
		self.assertFalse([args for args in seen if args[0] in ("POS Profile", "Company")])
