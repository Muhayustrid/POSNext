# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""SEC-14 / SEC-15 / SEC-22 regression tests: money-and-counter race cluster.

- SEC-14 wallet double-spend: pending wallet payments must cover BOTH invoice
  doctypes and open DRAFTS (outstanding_amount is 0 until submit), and debit
  paths must take the wallet row lock (PATTERN C) instead of racing.
- SEC-15 coupon usage race: increment runs under a row lock inside the caller's
  submit transaction, re-checks maximum_use, and never commits manually.
- SEC-22 self-referral gift-card farm: self-referral rejected, one referrer
  gift card per referral code, "new customer" derived server-side.

Run via pos_next/_pn_run_tests.py pos_next.api.test_money_race_security
"""

import inspect
import json
import unittest
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, today

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.api.promotions import apply_referral_code as gated_apply_referral_code
from pos_next.api.wallet import get_customer_wallet_balance, get_or_create_wallet
from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE
from pos_next.pos_next.doctype.pos_coupon.pos_coupon import (
	decrement_coupon_usage,
	increment_coupon_usage,
)
from pos_next.pos_next.doctype.referral_code.referral_code import apply_referral_code
from pos_next.pos_next.doctype.wallet_transaction.wallet_transaction import create_wallet_credit
from pos_next.tests.price_group_helpers import get_default_company, get_default_customer

CUSTOMER = "_SEC1422 Wallet Race Customer"
REFERRER = "_SEC1422 Referrer Customer"
WALLET_MOP = "_SEC1422 Wallet MoP"


def _make_customer(name):
	if frappe.db.exists("Customer", {"customer_name": name}):
		return frappe.db.get_value("Customer", {"customer_name": name}, "name")
	customer_group = (
		frappe.db.get_value("Customer Group", {"is_group": 0}, "name", order_by="creation asc")
		or "All Customer Groups"
	)
	doc = frappe.get_doc({"doctype": "Customer", "customer_name": name, "customer_group": customer_group})
	doc.insert(ignore_permissions=True)
	return doc.name


def _make_wallet_mop(company):
	"""Mode of Payment flagged is_wallet_payment so invoice rows count as wallet."""
	if frappe.db.exists("Mode of Payment", WALLET_MOP):
		return WALLET_MOP

	account = (
		frappe.get_cached_value("Company", company, "default_cash_account")
		or frappe.get_cached_value("Company", company, "default_receivable_account")
	)
	doc = frappe.get_doc(
		{
			"doctype": "Mode of Payment",
			"mode_of_payment": WALLET_MOP,
			"enabled": 1,
			"type": "Cash",
			"is_wallet_payment": 1,
			"accounts": [{"company": company, "default_account": account}],
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


class TestWalletDoubleSpend(IntegrationTestCase):
	"""SEC-14: pending wallet math covers drafts + both doctypes; debits serialize."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		cls.customer = _make_customer(CUSTOMER)
		cls.wallet = get_or_create_wallet(cls.customer, cls.company, force_create=True)
		if not cls.wallet:
			raise unittest.SkipTest("no wallet account configured on company")
		if not frappe.get_cached_value("Company", cls.company, "default_expense_account"):
			raise unittest.SkipTest("no default expense account on company")
		cls.mop = _make_wallet_mop(cls.company)
		cls.item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1},
			pluck="name",
			limit=1,
		)
		if not cls.item:
			raise unittest.SkipTest("no sales item on site")

	def _balance(self):
		return get_customer_wallet_balance(self.customer, self.company)

	def _credit(self, amount):
		wallet_name = self.wallet.name if hasattr(self.wallet, "name") else self.wallet["name"]
		return create_wallet_credit(
			wallet=wallet_name,
			amount=amount,
			source_type="Manual Adjustment",
			remarks="SEC-14 test credit",
			submit=True,
		)

	def _draft_invoice_with_wallet_payment(self, amount):
		"""Open DRAFT Sales Invoice paying `amount` from the wallet.

		Draft on purpose: outstanding_amount stays 0 until submit — exactly the
		state the old outstanding_amount>0 filter dropped.
		"""
		price_list = (
			frappe.db.get_value("Selling Settings", "Selling Settings", "selling_price_list")
			or frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")
		)
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.company,
				"customer": self.customer,
				"is_pos": 1,
				"posting_date": today(),
				"selling_price_list": price_list,
				"items": [{"item_code": self.item[0], "qty": 1, "rate": 10}],
				"payments": [{"mode_of_payment": self.mop, "amount": amount}],
			}
		)
		return si.insert(ignore_permissions=True)

	def _debit(self, amount):
		wallet_name = self.wallet.name if hasattr(self.wallet, "name") else self.wallet["name"]
		return frappe.get_doc(
			{
				"doctype": "Wallet Transaction",
				"transaction_type": "Debit",
				"wallet": wallet_name,
				"company": self.company,
				"posting_date": today(),
				"amount": amount,
				"source_type": "Manual Adjustment",
			}
		).insert(ignore_permissions=True)

	def test_draft_sales_invoice_wallet_pending_reduces_balance(self):
		"""Wallet pending on a Sales Invoice draft (docstatus 0, outstanding 0)
		must reduce the available balance — the miss let the same balance be
		spent twice (SEC-14a)."""
		base = self._balance()
		self._credit(100)
		self.assertAlmostEqual(flt(self._balance()), flt(base) + 100, places=2)

		si = self._draft_invoice_with_wallet_payment(80)
		self.assertAlmostEqual(flt(self._balance()), flt(base) + 20, places=2)

		frappe.delete_doc("Sales Invoice", si.name, force=1, ignore_permissions=True)

	def test_sequential_debits_over_balance_one_rejected(self):
		"""Two debits over the (already partly earmarked) balance: the second
		is rejected, the legitimate one goes through (SEC-14b)."""
		base = self._balance()
		self._credit(100)
		si = self._draft_invoice_with_wallet_payment(80)
		available = self._balance()
		self.assertAlmostEqual(available, flt(base) + 20, places=2)

		with self.assertRaises(frappe.ValidationError):
			self._debit(available + 1)  # more than what is left

		wt = self._debit(10)  # the legitimate path keeps working
		wt.submit()
		self.assertEqual(wt.docstatus, 1)
		self.assertAlmostEqual(flt(self._balance()), available - 10, places=2)

		frappe.delete_doc("Sales Invoice", si.name, force=1, ignore_permissions=True)

	def test_debit_after_committed_debit_sees_new_balance(self):
		"""Serial proof of the row lock: a debit submitted first is visible to
		the second one's validation (the interleaving a concurrent double-spend
		needs to pass) (SEC-14b)."""
		base = self._balance()
		self._credit(100)

		first = self._debit(70)
		first.submit()
		self.assertAlmostEqual(flt(self._balance()), flt(base) + 30, places=2)

		with self.assertRaises(frappe.ValidationError):
			self._debit(70)  # must validate against the committed 30, not 100


class TestCouponUsageRace(IntegrationTestCase):
	"""SEC-15: usage increment is locked, enforces maximum_use, never commits."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()

	def _make_coupon(self, maximum_use):
		code = "SEC15" + frappe.generate_hash(length=6).upper()
		doc = frappe.get_doc(
			{
				"doctype": "POS Coupon",
				"coupon_name": f"_SEC15 Coupon {code}",
				"coupon_code": code,
				"coupon_type": "Promotional",
				"company": self.company,
				"discount_type": "Percentage",
				"discount_percentage": 10,
				"maximum_use": maximum_use,
			}
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("POS Coupon", doc.name, force=1, ignore_permissions=True))
		return doc

	def _used(self, coupon):
		return frappe.db.get_value("POS Coupon", coupon.name, "used") or 0

	def test_increment_stops_at_max(self):
		coupon = self._make_coupon(maximum_use=1)
		increment_coupon_usage(coupon.coupon_code.lower())
		self.assertEqual(self._used(coupon), 1)

		with self.assertRaises(frappe.ValidationError):
			increment_coupon_usage(coupon.coupon_code)
		self.assertEqual(self._used(coupon), 1)  # counter never passes maximum_use

	def test_last_claim_wins(self):
		"""One use left, two sequential claims: exactly one gets it."""
		coupon = self._make_coupon(maximum_use=2)
		frappe.db.set_value("POS Coupon", coupon.name, "used", 1)

		increment_coupon_usage(coupon.coupon_code)  # claim A takes the last use
		self.assertEqual(self._used(coupon), 2)

		with self.assertRaises(frappe.ValidationError):  # claim B is refused
			increment_coupon_usage(coupon.coupon_code)

	def test_decrement_floors_at_zero(self):
		coupon = self._make_coupon(maximum_use=5)
		increment_coupon_usage(coupon.coupon_code)
		decrement_coupon_usage(coupon.coupon_code)
		self.assertEqual(self._used(coupon), 0)
		decrement_coupon_usage(coupon.coupon_code)  # no negative counters
		self.assertEqual(self._used(coupon), 0)


class TestReferralSelfFarm(IntegrationTestCase):
	"""SEC-22: self-referral, gift-card farming, client-claimed new customers."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		cls.referrer = _make_customer(REFERRER)
		if not frappe.db.exists("Referral Code", {"customer": cls.referrer}):
			doc = frappe.get_doc(
				{
					"doctype": "Referral Code",
					"company": cls.company,
					"customer": cls.referrer,
					"referrer_discount_type": "Amount",
					"referrer_discount_amount": 10,
					"referee_discount_type": "Percentage",
					"referee_discount_percentage": 10,
				}
			)
			doc.insert(ignore_permissions=True)
			cls.referral = doc
		else:
			cls.referral = frappe.get_doc("Referral Code", {"customer": cls.referrer})

	def _gift_cards(self):
		return frappe.get_all(
			"POS Coupon",
			filters={"referral_code": self.referral.name, "coupon_type": "Gift Card"},
			pluck="name",
		)

	def _fresh_referee(self, tag):
		return _make_customer(f"_SEC1422 Referee {tag} {frappe.generate_hash(length=4)}")

	def _fabricate_purchase_history(self, customer):
		"""A submitted Sales Invoice row without the full accounting fixture —
		the server-side 'new customer' guard only counts docstatus=1 rows."""
		doc = frappe.new_doc("Sales Invoice")
		doc.company = self.company
		doc.customer = customer
		doc.is_pos = 1
		doc.posting_date = today()
		doc.name = "SI-SEC22-" + frappe.generate_hash(length=8)
		doc.db_insert()
		frappe.db.set_value("Sales Invoice", doc.name, "docstatus", 1, update_modified=False)
		self.addCleanup(lambda: frappe.db.delete("Sales Invoice", {"name": doc.name}))
		return doc.name

	def test_gift_card_capped_per_referral_code(self):
		# relative baselines: the shared dev site may hold residue from prior runs
		gifts_before = self._gift_cards()
		count_before = frappe.db.get_value("Referral Code", self.referral.name, "referrals_count") or 0

		referee1 = self._fresh_referee("A")
		result1 = apply_referral_code(self.referral.referral_code, referee1)
		self.assertIsNotNone(result1["referee_coupon"])
		gifts = self._gift_cards()
		self.assertEqual(len(gifts), len(gifts_before) + (0 if gifts_before else 1))
		self.assertEqual(result1["referrer_coupon"]["name"], gifts[0])

		# second, different referee: welcome coupon yes, second gift card no
		referee2 = self._fresh_referee("B")
		result2 = apply_referral_code(self.referral.referral_code, referee2)
		self.assertIsNotNone(result2["referee_coupon"])
		self.assertEqual(self._gift_cards(), gifts)
		self.assertEqual(result2["referrer_coupon"]["name"], gifts[0])
		self.assertEqual(
			frappe.db.get_value("Referral Code", self.referral.name, "referrals_count"),
			count_before + 2,
		)

	def test_purchase_history_blocks_referee(self):
		referee = self._fresh_referee("C")
		self._fabricate_purchase_history(referee)

		with self.assertRaises(frappe.ValidationError):
			apply_referral_code(self.referral.referral_code, referee)
		# nothing minted for a used customer
		self.assertFalse(
			frappe.db.exists(
				"POS Coupon", {"referral_code": self.referral.name, "customer": referee}
			)
		)

	def test_self_referral_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			apply_referral_code(self.referral.referral_code, self.referrer)


class TestNoManualCommitInMoneyPaths(IntegrationTestCase):
	"""The grep the audit asks for: touched functions stay commit-free."""

	Touched = (
		"pos_next.api.wallet:validate_wallet_payment",
		"pos_next.api.wallet:get_pending_wallet_payments",
		"pos_next.pos_next.doctype.wallet.wallet:get_customer_wallet_balance",
		"pos_next.pos_next.doctype.wallet_transaction.wallet_transaction:WalletTransaction.validate_amount",
		"pos_next.pos_next.doctype.wallet_transaction.wallet_transaction:WalletTransaction.update_wallet_balance",
		"pos_next.pos_next.doctype.pos_coupon.pos_coupon:increment_coupon_usage",
		"pos_next.pos_next.doctype.pos_coupon.pos_coupon:decrement_coupon_usage",
		"pos_next.pos_next.doctype.referral_code.referral_code:apply_referral_code",
		"pos_next.pos_next.doctype.referral_code.referral_code:create_referral_code",
	)

	LockPinned = (
		"pos_next.api.wallet:validate_wallet_payment",
		"pos_next.pos_next.doctype.wallet_transaction.wallet_transaction:WalletTransaction.validate_amount",
		"pos_next.pos_next.doctype.wallet_transaction.wallet_transaction:WalletTransaction.update_wallet_balance",
		"pos_next.pos_next.doctype.pos_coupon.pos_coupon:increment_coupon_usage",
		"pos_next.pos_next.doctype.pos_coupon.pos_coupon:decrement_coupon_usage",
		"pos_next.pos_next.doctype.referral_code.referral_code:apply_referral_code",
	)

	def _load(self, path):
		module_name, func_path = path.rsplit(":", 1)
		obj = frappe.get_module(module_name)
		for part in func_path.split("."):
			obj = getattr(obj, part)
		return obj

	def test_no_frappe_db_commit_in_touched_functions(self):
		for path in self.Touched:
			source = inspect.getsource(self._load(path))
			self.assertNotIn("frappe.db.commit", source, path)

	def test_row_locks_present_in_touched_functions(self):
		for path in self.LockPinned:
			source = inspect.getsource(self._load(path))
			self.assertIn("for_update=True", source, path)


# Schedule-safe profile filter + site invoice-type flip, duplicated from
# test_pos_invoice_submit.py on purpose (no cross-subpackage test imports).
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
]


def _set_invoice_type(value):
	frappe.db.set_single_value("POS Next Global Settings", "invoice_type", value)
	try:
		del frappe.local._pos_next_invoice_doctype
	except AttributeError:
		pass  # not cached yet (`in frappe.local` is unreliable on v16)


def _cancel_wallet_transactions_for(invoice_names):
	"""Cancel + delete every live Wallet Transaction that references any of
	these invoices: cancelling an invoice under a linked WT complains
	(frappe.LinkExistsError), and a submit mints loyalty credits on its own
	when the customer carries a loyalty program."""
	names = list(dict.fromkeys(n for n in invoice_names if n))
	if not names:
		return
	for wt_name in frappe.get_all(
		"Wallet Transaction",
		filters={
			"reference_doctype": ("in", ("Sales Invoice", "POS Invoice")),
			"reference_name": ("in", names),
			"docstatus": ("!=", 2),
		},
		pluck="name",
	):
		wt = frappe.get_doc("Wallet Transaction", wt_name)
		if wt.docstatus == 1:
			wt.flags.ignore_permissions = True
			wt.cancel()
		frappe.delete_doc("Wallet Transaction", wt_name, force=1, ignore_permissions=True)


class TestCouponReleaseOnCancel(IntegrationTestCase):
	"""A1: cancel hands the claimed coupon use back through the real
	on_cancel wiring — both the POS Invoice path (pos_invoice_events) and the
	Sales Invoice path (hooks.py doc_events)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()

	def setUp(self):
		self._created = []
		# restore target captured before any flip in this test (tearDown must
		# leave the site in the mode it started in, not force Sales mode)
		self._invoice_type_baseline = frappe.db.get_single_value(
			"POS Next Global Settings", "invoice_type"
		)
		self.profile = frappe.db.get_value(
			"POS Profile",
			_PROFILE_FILTER,
			["name", "company", "warehouse"],
			as_dict=True,
			order_by="creation asc",
		)
		if not self.profile:
			raise unittest.SkipTest("no schedule-safe POS Profile")
		item = frappe.get_all(
			"Item",
			filters=[
				["disabled", "=", 0],
				["is_sales_item", "=", 1],
				["is_stock_item", "=", 1],
				["name", "not like", "\\_PNXT\\_%"],
			],
			order_by="creation asc",
			pluck="name",
			limit=1,
		)
		if not item:
			raise unittest.SkipTest("no stock sales item")
		self.item = item[0]
		self.customer = get_default_customer()
		if not self.customer:
			raise unittest.SkipTest("no non-internal customer")
		# the shared default customer may carry a loyalty program (residue of
		# a crashed COR-BE-16 run or a real assignment): a submit would then
		# mint a loyalty Wallet Transaction for the invoice, and for a POS
		# Invoice that WT's Sales Invoice reference does not even validate.
		# Coupon quota is orthogonal — park the program for the test.
		self._loyalty_backup = frappe.db.get_value("Customer", self.customer, "loyalty_program")
		frappe.db.set_value(
			"Customer", self.customer, "loyalty_program", None, update_modified=False
		)

		def _restore_loyalty():
			# guaranteed restore even if setUp raises after the park (unittest
			# runs cleanups when setUp fails; tearDown would be skipped) — and
			# it must COMMIT: tearDown has already committed the parked (NULL)
			# state, so an uncommitted restore here would be rolled back by
			# the test framework and strand NULL on this real customer
			frappe.db.set_value(
				"Customer",
				self.customer,
				"loyalty_program",
				self._loyalty_backup,
				update_modified=False,
			)
			frappe.db.commit()

		self.addCleanup(_restore_loyalty)
		self.mode = frappe.get_all(
			"POS Payment Method",
			{"parent": self.profile.name, "parenttype": "POS Profile"},
			pluck="mode_of_payment",
			limit=1,
		)
		if not self.mode:
			raise unittest.SkipTest("profile has no payment methods")
		# real Material Receipt so stock validation on submit passes
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"purpose": "Material Receipt",
				"company": self.profile.company,
				"items": [
					{
						"item_code": self.item,
						"qty": 5,
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
		self.shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": "Administrator",
				"posting_date": frappe.utils.nowdate(),
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
			}
		).insert(ignore_permissions=True)
		self.shift.reload()
		self.shift.submit()

	def tearDown(self):
		# sweep by shift link covers drafts left behind by a failed submit
		for doctype in ("POS Invoice", "Sales Invoice"):
			for name in frappe.get_all(
				doctype, {"posa_pos_opening_shift": self.shift.name}, pluck="name"
			):
				self._created.append(name)
		# wallet rows first: cancelling an invoice under a linked WT complains
		_cancel_wallet_transactions_for(dict.fromkeys(self._created))
		for name in dict.fromkeys(self._created):
			for doctype in ("POS Invoice", "Sales Invoice"):
				if frappe.db.exists(doctype, name):
					doc = frappe.get_doc(doctype, name)
					if doc.docstatus == 1:
						doc.flags.ignore_permissions = True
						doc.cancel()
					frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
					break
		se = frappe.get_doc("Stock Entry", self.stock_entry.name)
		if se.docstatus == 1:
			se.cancel()
		frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)
		frappe.db.set_value(
			"POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
		)
		frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
		_set_invoice_type(getattr(self, "_invoice_type_baseline", None) or SALES_INVOICE)
		frappe.db.commit()

	def _payload(self, coupon_code=None):
		# The coupon_code key is always present (possibly null), mirroring the
		# frontend contract: useInvoice.js sends it on every submit.
		payload = {
			"pos_profile": self.profile.name,
			"posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"doctype": "Sales Invoice",  # server resolves the doctype
			"items": [
				{"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
			],
			"payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
			"coupon_code": coupon_code,
		}
		return payload

	def _make_coupon(self):
		code = "SEC15" + frappe.generate_hash(length=6).upper()
		doc = frappe.get_doc(
			{
				"doctype": "POS Coupon",
				"coupon_name": f"_SEC15 Cancel Coupon {code}",
				"coupon_code": code,
				"coupon_type": "Promotional",
				# the invoice's company, not the default one: check_coupon_code
				# rejects coupons whose company differs from the invoice's
				"company": self.profile.company,
				"discount_type": "Percentage",
				"discount_percentage": 10,
				"maximum_use": 5,
			}
		)
		doc.insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("POS Coupon", doc.name, force=1, ignore_permissions=True))
		return doc

	def _used(self, coupon):
		return frappe.db.get_value("POS Coupon", coupon.name, "used") or 0

	def _round_trip(self, doctype):
		"""Honest round trip through the endpoint: submit with the coupon in
		the payload — update_invoice validates it, stamps the custom
		pos_coupon_code field (ERPNext's own coupon_code Link stays empty) and
		the SEC-15 increment claims one use inside the submit transaction;
		cancel hands the use back through the real on_cancel wiring — both the
		POS Invoice path (pos_invoice_events) and the Sales Invoice path
		(hooks.py doc_events)."""
		_set_invoice_type(POS_INVOICE if doctype == "POS Invoice" else SALES_INVOICE)
		coupon = self._make_coupon()
		before = self._used(coupon)
		result = submit_invoice(invoice=self._payload(coupon_code=coupon.coupon_code))
		name = result.get("name")
		self._created.append(name)
		self.assertTrue(name)
		self.assertEqual(self._used(coupon), before + 1)
		self.assertEqual(
			frappe.db.get_value(doctype, name, "pos_coupon_code"), coupon.coupon_code
		)
		self.assertIsNone(frappe.db.get_value(doctype, name, "coupon_code"))
		doc = frappe.get_doc(doctype, name)  # cancel as Administrator
		doc.flags.ignore_permissions = True
		doc.cancel()
		self.assertEqual(self._used(coupon), before)

	def test_client_cannot_stamp_coupon_fields(self):
		"""A smuggled pos_coupon_code is stripped server-side (only the
		validated code is stamped), and an invalid coupon_code throws instead
		of riding the payload onto the document."""
		_set_invoice_type(POS_INVOICE)
		coupon = self._make_coupon()
		before = self._used(coupon)
		payload = self._payload(coupon_code=coupon.coupon_code)
		payload["pos_coupon_code"] = "EVIL"
		result = submit_invoice(invoice=payload)
		name = result.get("name")
		self._created.append(name)
		self.assertEqual(
			frappe.db.get_value("POS Invoice", name, "pos_coupon_code"), coupon.coupon_code
		)
		self.assertIsNone(frappe.db.get_value("POS Invoice", name, "coupon_code"))
		self.assertEqual(self._used(coupon), before + 1)

		with self.assertRaises(frappe.ValidationError):
			submit_invoice(invoice=self._payload(coupon_code="NOT-A-COUPON"))

	def test_coupon_cleared_when_payload_key_null(self):
		"""Re-saving the draft with the coupon key present but null clears the
		stamp, so the subsequent submit burns no quota (key-presence semantics
		honestly mirrored on the document)."""
		_set_invoice_type(POS_INVOICE)
		coupon = self._make_coupon()
		before = self._used(coupon)
		# (a) draft save with a valid coupon stamps the canonical code
		created = update_invoice(json.dumps(self._payload(coupon_code=coupon.coupon_code)))
		name = created.get("name")
		self._created.append(name)
		self.assertEqual(
			frappe.db.get_value("POS Invoice", name, "pos_coupon_code"), coupon.coupon_code
		)
		# (b) same draft, coupon key present but null: stamp must be cleared
		payload = self._payload()
		payload["name"] = name
		update_invoice(json.dumps(payload))
		self.assertIsNone(frappe.db.get_value("POS Invoice", name, "pos_coupon_code"))
		# (c) submit the coupon-less draft: quota untouched
		payload = self._payload()
		payload["name"] = name
		result = submit_invoice(invoice=payload)
		self.assertEqual(result.get("name"), name)
		self._created.append(result.get("name"))
		self.assertEqual(self._used(coupon), before)

	def test_submit_with_echoed_draft_payload(self):
		"""Pins the real-frontend shape: Step 2 submits update_invoice's echoed
		as_dict() verbatim — it carries coupon_code=None (every docfield is in
		the echo) while the doc holds the stamped code. Submit must honour the
		doc stamp, burn one use, and release it on cancel."""
		_set_invoice_type(POS_INVOICE)
		coupon = self._make_coupon()
		before = self._used(coupon)
		result = update_invoice(json.dumps(self._payload(coupon_code=coupon.coupon_code)))
		name = result.get("name")
		self._created.append(name)
		# echo shape: coupon_code key present with None, stamp on the custom field
		self.assertIn("coupon_code", result)
		self.assertIsNone(result.get("coupon_code"))
		self.assertEqual(result.get("pos_coupon_code"), coupon.coupon_code)
		# submit the echoed dict verbatim (existing-draft branch)
		result = submit_invoice(invoice=result)
		self.assertEqual(result.get("name"), name)
		self._created.append(result.get("name"))
		self.assertEqual(self._used(coupon), before + 1)
		# cancel hands the claim back
		doc = frappe.get_doc("POS Invoice", name)
		doc.flags.ignore_permissions = True
		doc.cancel()
		self.assertEqual(self._used(coupon), before)

	def test_pos_invoice_cancel_returns_coupon_use(self):
		self._round_trip("POS Invoice")

	def test_sales_invoice_cancel_returns_coupon_use(self):
		self._round_trip("Sales Invoice")

	def test_cancel_without_coupon_is_noop(self):
		_set_invoice_type(POS_INVOICE)
		result = submit_invoice(invoice=self._payload())
		name = result.get("name")
		self._created.append(name)
		doc = frappe.get_doc("POS Invoice", name)
		doc.flags.ignore_permissions = True
		doc.cancel()  # invoice without a coupon: must not raise


class TestApplyReferralCodeGate(IntegrationTestCase):
	"""A3: promotions.apply_referral_code mints coupons, so it is a write on
	the promotion stack and must carry the same gate as the sibling CRUD."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		cls.referrer = _make_customer("_SEC1422 Gate Referrer")
		if frappe.db.exists("Referral Code", {"customer": cls.referrer}):
			cls.referral = frappe.get_doc("Referral Code", {"customer": cls.referrer})
		else:
			cls.referral = frappe.get_doc(
				{
					"doctype": "Referral Code",
					"company": cls.company,
					"customer": cls.referrer,
					"referrer_discount_type": "Amount",
					"referrer_discount_amount": 10,
					"referee_discount_type": "Percentage",
					"referee_discount_percentage": 10,
				}
			).insert(ignore_permissions=True)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		super().tearDownClass()

	def _make_user(self, roles):
		email = f"ref-gate.{uuid.uuid4().hex[:8]}@example.com"
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Ref Gate Tester"}
		).insert(ignore_permissions=True)
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
		self.addCleanup(lambda: frappe.delete_doc("User", email, force=1, ignore_permissions=True))
		return email

	def test_roleless_user_blocked(self):
		user = self._make_user([])  # only implicit All/Guest roles
		frappe.set_user(user)
		try:
			with self.assertRaises(frappe.PermissionError) as ctx:
				gated_apply_referral_code(self.referral.referral_code, self.referrer)
		finally:
			frappe.set_user("Administrator")
		self.assertIn("permission", str(ctx.exception).lower())

	def test_user_with_write_passes_gate(self):
		# Sales Manager holds Promotional Scheme write without the
		# Administrator bypass — the smallest honest "allowed" probe
		user = self._make_user(["Sales Manager"])
		referee = _make_customer(f"_SEC1422 Gate Referee {frappe.generate_hash(length=4)}")
		frappe.set_user(user)
		try:
			result = gated_apply_referral_code(self.referral.referral_code, referee)
		finally:
			frappe.set_user("Administrator")
		self.assertTrue(result.get("success"))
		self.assertTrue(
			frappe.db.exists("POS Coupon", {"referral_code": self.referral.name, "customer": referee})
		)
