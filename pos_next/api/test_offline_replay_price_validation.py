# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-23 acceptance tests: magnitude verification of the money path inside
_validate_item_rates.

The SEC-04 detector skips offer-attributed rows, free/zero-rate rows and the
relayed header discount, so a forged client could claim pricing_rules and
submit any discount magnitude. The gate replays the same pricing pipeline
apply_offers runs (ERPNext engine + apply_min_max_price_discounts + the
transaction evaluator) on server list prices and rejects magnitudes outside
the tolerance band. The audit-only escape hatch (POS Settings /
POS Next Global Settings: price_replay_audit_only, default 0 = enforce) must
log instead of reject.

Run via pos_next/_pn_run_tests.py
pos_next.api.test_offline_replay_price_validation
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

import pos_next  # noqa: F401 — ensure app hooks load.
from pos_next.api.invoices import submit_invoice
from pos_next.test_promotions import (
	ITEM_A,
	ITEM_B,
	_apply_offers_and_stamp,
	_cart_payload,
	_ctx,
	_line,
	_make_rule,
)

_RATE_MSG = "server-calculated offer rate"
_FREE_MSG = "not granted by any applied pricing rule"
_HEADER_MSG = "does not match the server-calculated"


def _set_invoice_type(value):
	"""Flip the site switch past the switch guard (shared dev site holds real
	open shifts; see test_pos_invoice_submit._set_invoice_type)."""
	frappe.db.set_single_value("POS Next Global Settings", "invoice_type", value)
	try:
		del frappe.local._pos_next_invoice_doctype
	except AttributeError:
		pass


def _cancel_wallet_transactions_for(invoice_names):
	"""Same sweep as test_draft_submit_rate_gate: loyalty-to-wallet mints a WT
	per submitted invoice and blocks invoice deletion."""
	for wt_name in frappe.get_all(
		"Wallet Transaction",
		filters={
			"reference_doctype": ("in", ("Sales Invoice", "POS Invoice")),
			"reference_name": ("in", list(dict.fromkeys(n for n in invoice_names if n))),
			"docstatus": ("!=", 2),
		},
		pluck="name",
	):
		wt = frappe.get_doc("Wallet Transaction", wt_name)
		if wt.docstatus == 1:
			wt.flags.ignore_permissions = True
			wt.cancel()
		frappe.delete_doc("Wallet Transaction", wt_name, force=1, ignore_permissions=True)


class TestOfflineReplayPriceValidation(FrappeTestCase):
	"""Every submit path must verify offer magnitudes server-side."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.ctx = _ctx()
		cls.original_invoice_type = frappe.db.get_single_value(
			"POS Next Global Settings", "invoice_type"
		)
		cls.settings_row = frappe.db.get_value(
			"POS Settings", {"pos_profile": cls.ctx.pos_profile, "enabled": 1}, "name"
		)
		if cls.settings_row:
			cls.settings_backup = frappe.db.get_value(
				"POS Settings",
				cls.settings_row,
				["price_replay_audit_only", "allow_user_to_edit_rate", "max_discount_allowed"],
				as_dict=True,
			)

	@classmethod
	def tearDownClass(cls):
		if cls.settings_row and getattr(cls, "settings_backup", None):
			frappe.db.set_value(
				"POS Settings",
				cls.settings_row,
				{
					"price_replay_audit_only": cls.settings_backup.price_replay_audit_only or 0,
					"allow_user_to_edit_rate": cls.settings_backup.allow_user_to_edit_rate or 0,
					"max_discount_allowed": cls.settings_backup.max_discount_allowed or 0,
				},
				update_modified=False,
			)
		_set_invoice_type(cls.original_invoice_type or "POS Invoice")
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		_set_invoice_type("Sales Invoice")
		self._created = []
		self._rule_names = []
		if self.settings_row:
			frappe.db.set_value(
				"POS Settings",
				self.settings_row,
				{"price_replay_audit_only": 0},
				update_modified=False,
			)

	def tearDown(self):
		# Disable (not delete) test rules like test_promotions does.
		for rule_name in self._rule_names:
			try:
				frappe.db.set_value("Pricing Rule", rule_name, "disable", 1)
			except Exception:
				pass
		_cancel_wallet_transactions_for(self._created)
		for name in dict.fromkeys(self._created):
			if frappe.db.exists("Sales Invoice", name):
				doc = frappe.get_doc("Sales Invoice", name)
				if doc.docstatus == 1:
					doc.flags.ignore_permissions = True
					doc.cancel()
				frappe.delete_doc("Sales Invoice", name, force=1, ignore_permissions=True)
		# The offline sync records outlive their invoices: a leftover Synced
		# record makes the next run's dedup return a live (unrelated) invoice
		# whose name the series handed out again.
		for record in frappe.get_all(
			"Offline Invoice Sync",
			filters={"offline_id": ("like", "_PNXT_SEC23_%")},
			pluck="name",
		):
			frappe.delete_doc(
				"Offline Invoice Sync", record, force=1, ignore_permissions=True
			)
		frappe.db.commit()

	@staticmethod
	def _offline_id(tag):
		"""Unique per run — a deterministic offline_id would collide with the
		Offline Invoice Sync record a previous run left behind."""
		return "_PNXT_SEC23_{0}-{1}".format(tag, frappe.utils.now_datetime().strftime("%Y%m%d%H%M%S%f"))

	def _rule(self, title, **fields):
		name = _make_rule(title, **fields)
		self._rule_names.append(name)
		return name

	def _set_audit_only(self, value):
		if not self.settings_row:
			self.skipTest("promo profile has no enabled POS Settings row")
		frappe.db.set_value(
			"POS Settings", self.settings_row, {"price_replay_audit_only": value}, update_modified=False
		)
		frappe.db.commit()

	def _paid_payload(self, items, **extra):
		payload = _cart_payload(self.ctx, items)
		payload.update(extra)
		return payload

	def _submit_offline(self, payload, offline_id):
		"""The offline replay: submit_invoice without a draft name, keyed by
		offline_id — the create branch builds the draft via update_invoice and
		submits it in one call."""
		payload["offline_id"] = offline_id
		payload["payments"] = [
			{"mode_of_payment": self.ctx.mode_of_payment, "amount": flt(payload.get("_paid", 0))}
		]
		payload.pop("_paid", None)
		result = submit_invoice(invoice=json.dumps(payload))
		name = (result or {}).get("name")
		self._created.append(name)
		return result

	# ------------------------------------------------------------------
	# (1) Honest rule passes
	# ------------------------------------------------------------------

	def test_honest_rule_passes(self):
		"""15% engine-discounted row, relayed unchanged, submits cleanly."""
		rule = self._rule(
			"_PNXT_SEC23_Honest",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=15,
		)
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		_apply_offers_and_stamp(payload, [rule])
		payload["payments"] = [{"mode_of_payment": self.ctx.mode_of_payment, "amount": 42.5}]
		from pos_next.api.invoices import update_invoice

		created = update_invoice(json.dumps(payload))
		name = created.get("name")
		self._created.append(name)
		self.assertTrue(name)
		# the honest row's rate equals the engine replay exactly
		doc = frappe.get_doc("Sales Invoice", name)
		self.assertAlmostEqual(flt(doc.items[0].rate), 42.5, places=2)

	# ------------------------------------------------------------------
	# (2) Forged magnitude rejected
	# ------------------------------------------------------------------

	def test_forged_magnitude_rejected(self):
		"""Row claims the rule but submits a deeper discount than the engine
		grants: the replay must reject it."""
		rule = self._rule(
			"_PNXT_SEC23_Forged",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=15,
		)
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		_apply_offers_and_stamp(payload, [rule])
		# forge: engine says 42.5, client claims 20 while keeping the claim
		payload["items"][0]["rate"] = 20
		payload["items"][0]["discount_percentage"] = 60
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.ValidationError) as ctx:
			update_invoice(json.dumps(payload))
		self.assertIn(_RATE_MSG, str(ctx.exception))
		self.assertIn("20", str(ctx.exception))
		self.assertIn("42.5", str(ctx.exception))

	# ------------------------------------------------------------------
	# (3) Manual edit within the SEC-04 cap still passes
	# ------------------------------------------------------------------

	def test_manual_edit_within_cap_passes(self):
		"""A rule-less row edited below list price within max_discount_allowed
		is the SEC-04 manual lane — the replay gate must not touch it. Manual
		discounts still need a head-office confirmation code (product rule),
		so one is minted for the cart."""
		code_doc = frappe.get_doc(
			{
				"doctype": "POS Discount Confirmation Code",
				"code": "SEC23XY",
				"status": "Active",
				"valid_from": nowdate(),
				"company_scope": "All Outlets",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(
			lambda: frappe.delete_doc(
				"POS Discount Confirmation Code", code_doc.name, force=1, ignore_permissions=True
			)
		)
		if self.settings_row:
			frappe.db.set_value(
				"POS Settings",
				self.settings_row,
				{"allow_user_to_edit_rate": 1, "max_discount_allowed": 20},
				update_modified=False,
			)
		payload = self._paid_payload(
			[{**_line(self.ctx, ITEM_B, qty=1), "rate": 72, "is_rate_manually_edited": 1}]
		)
		payload["discount_confirmation_code"] = "sec23xy"
		payload["payments"] = [{"mode_of_payment": self.ctx.mode_of_payment, "amount": 72}]
		from pos_next.api.invoices import update_invoice

		created = update_invoice(json.dumps(payload))
		self._created.append(created.get("name"))
		doc = frappe.get_doc("Sales Invoice", created.get("name"))
		self.assertAlmostEqual(flt(doc.items[0].rate), 72, places=2)

	# ------------------------------------------------------------------
	# (4) Forged free item rejected
	# ------------------------------------------------------------------

	def test_forged_free_item_rejected(self):
		"""An is_free_item/rate-0 row with no engine grant must be rejected."""
		payload = self._paid_payload(
			[
				_line(self.ctx, ITEM_A, qty=1),
				{
					"item_code": ITEM_B,
					"qty": 1,
					"rate": 0,
					"price_list_rate": 0,
					"uom": "Nos",
					"warehouse": self.ctx.warehouse,
					"conversion_factor": 1,
					"discount_percentage": 0,
					"discount_amount": 0,
					"pricing_rules": "",
					"is_free_item": 1,
				},
			]
		)
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.ValidationError) as ctx:
			update_invoice(json.dumps(payload))
		self.assertIn(_FREE_MSG, str(ctx.exception))

	# ------------------------------------------------------------------
	# (5) Min/Max honest passes
	# ------------------------------------------------------------------

	def test_min_max_honest_passes(self):
		"""Min-price ranking (cheapest line carries the discount), relayed
		honestly, must pass the replay."""
		rule = self._rule(
			"_PNXT_SEC23_MinMax",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}, {"item_code": ITEM_B}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=20,
			apply_discount_on_price="Min",
			mixed_conditions=1,
		)
		payload = self._paid_payload(
			[_line(self.ctx, ITEM_A, qty=1), _line(self.ctx, ITEM_B, qty=1)]
		)
		_apply_offers_and_stamp(payload, [rule])
		# Min ranks ITEM_A (50) as the winner: 20% -> 40; ITEM_B stays 80.
		self.assertAlmostEqual(flt(payload["items"][0]["rate"]), 40, places=2)
		from pos_next.api.invoices import update_invoice

		created = update_invoice(json.dumps(payload))
		self._created.append(created.get("name"))
		doc = frappe.get_doc("Sales Invoice", created.get("name"))
		rates = sorted(flt(it.rate) for it in doc.items)
		self.assertAlmostEqual(rates[0], 40, places=2)
		self.assertAlmostEqual(rates[1], 80, places=2)

	def test_min_max_forged_rejected(self):
		"""A client inflating the Min/Max discount beyond the ranking's blend
		must be rejected."""
		rule = self._rule(
			"_PNXT_SEC23_MinMaxForged",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}, {"item_code": ITEM_B}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=20,
			apply_discount_on_price="Min",
			mixed_conditions=1,
		)
		payload = self._paid_payload(
			[_line(self.ctx, ITEM_A, qty=1), _line(self.ctx, ITEM_B, qty=1)]
		)
		_apply_offers_and_stamp(payload, [rule])
		payload["items"][0]["rate"] = 25  # engine says 40
		payload["items"][0]["discount_percentage"] = 50
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.ValidationError) as ctx:
			update_invoice(json.dumps(payload))
		self.assertIn(_RATE_MSG, str(ctx.exception))

	# ------------------------------------------------------------------
	# (6) Transaction-level discount honest passes / forged rejected
	# ------------------------------------------------------------------

	def test_transaction_level_honest_passes(self):
		"""Header relay of a Transaction Price rule must reconcile with the
		engine stamp. The rule is windowless on purpose: a windowed rule
		(min_qty/min_amt) cannot survive the relay stash at draft save —
		invoice_doc totals are only computed later (pre-existing, tracked by
		test_promotions.test_transaction_level_discount) — and this module
		tests the replay gate, not that stash timing."""
		rule = self._rule(
			"_PNXT_SEC23_Txn",
			apply_on="Transaction",
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=10,
			min_qty=0,
			apply_discount_on="Grand Total",
		)
		payload = self._paid_payload(
			[_line(self.ctx, ITEM_A, qty=1), _line(self.ctx, ITEM_B, qty=1)]
		)
		_apply_offers_and_stamp(payload, [rule])
		self.assertAlmostEqual(flt(payload.get("discount_amount")), 13, places=2)
		payload["payments"] = [{"mode_of_payment": self.ctx.mode_of_payment, "amount": 117}]
		from pos_next.api.invoices import submit_invoice, update_invoice

		created = update_invoice(json.dumps(payload))
		name = created.get("name")
		self._created.append(name)
		submit_invoice(
			invoice=json.dumps(created, default=str),
			data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
		)
		doc = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(doc.docstatus, 1)
		self.assertAlmostEqual(flt(doc.discount_amount), 13, places=2)

	def test_forged_header_discount_rejected(self):
		"""A header discount larger than the engine stamp must be rejected
		(no confirmation code on the payload)."""
		rule = self._rule(
			"_PNXT_SEC23_TxnForged",
			apply_on="Transaction",
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=10,
			min_qty=0,
			apply_discount_on="Grand Total",
		)
		payload = self._paid_payload(
			[_line(self.ctx, ITEM_A, qty=1), _line(self.ctx, ITEM_B, qty=1)]
		)
		_apply_offers_and_stamp(payload, [rule])
		payload["discount_amount"] = 60  # engine stamp is 13
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.ValidationError) as ctx:
			update_invoice(json.dumps(payload))
		self.assertIn(_HEADER_MSG, str(ctx.exception))

	# ------------------------------------------------------------------
	# (7) Offline replay: honest passes, forged rejected
	# ------------------------------------------------------------------

	def test_honest_offline_replay_passes(self):
		"""submit_invoice keyed by offline_id (no draft name) with an honest
		offer payload must submit."""
		rule = self._rule(
			"_PNXT_SEC23_Replay",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=15,
		)
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		_apply_offers_and_stamp(payload, [rule])
		payload["_paid"] = 42.5
		result = self._submit_offline(payload, offline_id=self._offline_id("OK"))
		self.assertTrue(result.get("name"))
		doc = frappe.get_doc("Sales Invoice", result.get("name"))
		self.assertEqual(doc.docstatus, 1)
		self.assertAlmostEqual(flt(doc.items[0].rate), 42.5, places=2)

	def test_forged_offline_replay_rejected(self):
		"""The offline replay path runs the same gate: a forged magnitude in
		the queued payload must be rejected at submit."""
		rule = self._rule(
			"_PNXT_SEC23_ReplayForged",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=15,
		)
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		_apply_offers_and_stamp(payload, [rule])
		payload["items"][0]["rate"] = 20
		payload["items"][0]["discount_percentage"] = 60
		payload["_paid"] = 20
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._submit_offline(payload, offline_id=self._offline_id("FORGED"))
		self.assertIn(_RATE_MSG, str(ctx.exception))

	# ------------------------------------------------------------------
	# Escape hatch: audit-only toggle logs instead of rejecting
	# ------------------------------------------------------------------

	def test_audit_only_toggle_logs_instead_of_rejecting(self):
		"""price_replay_audit_only=1: the replay gate logs the mismatch instead
		of throwing (default 0 rejects — covered by the tests above). The
		scenario is a forged free item, whose row carries no visible discount
		and therefore stays invisible to the independent discount-code gate —
		only the replay gate can see it."""
		self._set_audit_only(1)
		payload = self._paid_payload(
			[
				_line(self.ctx, ITEM_A, qty=1),
				{
					"item_code": ITEM_B,
					"qty": 1,
					"rate": 0,
					"price_list_rate": 0,
					"uom": "Nos",
					"warehouse": self.ctx.warehouse,
					"conversion_factor": 1,
					"discount_percentage": 0,
					"discount_amount": 0,
					"pricing_rules": "",
					"is_free_item": 1,
				},
			]
		)
		from pos_next.api.invoices import update_invoice

		created = update_invoice(json.dumps(payload))
		self._created.append(created.get("name"))
		self.assertTrue(created.get("name"), "audit-only must not reject")
		logs = frappe.get_all(
			"Error Log",
			filters={"method": "POS Price Replay Mismatch (audit-only)"},
			limit=1,
		)
		self.assertTrue(logs, "audit-only must write the mismatch to the Error Log")
