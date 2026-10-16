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
from frappe.utils import add_to_date, cint, flt, get_datetime, get_time_str, now_datetime, nowdate

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
		# An open shift two hours back: offline replays link it so the queued sale
		# time can be judged against the shift window (Task 1).
		cls.shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": cls.ctx.pos_profile,
				"company": cls.ctx.company,
				"user": "Administrator",
				"posting_date": nowdate(),
				"period_start_date": add_to_date(now_datetime(), hours=-2),
				"balance_details": [{"mode_of_payment": cls.ctx.mode_of_payment, "amount": 0}],
			}
		).insert(ignore_permissions=True)
		cls.shift.reload()
		cls.shift.submit()

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
		if getattr(cls, "shift", None) and frappe.db.exists("POS Opening Shift", cls.shift.name):
			frappe.db.set_value(
				"POS Opening Shift", cls.shift.name, "docstatus", 2, update_modified=False
			)
			frappe.delete_doc("POS Opening Shift", cls.shift.name, force=1, ignore_permissions=True)
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
		self.assertEqual(cint(doc.pos_offline_price_mismatch or 0), 0)
		self.assertAlmostEqual(flt(doc.items[0].rate), 42.5, places=2)

	def _offline_mismatch_logs(self):
		return set(
			frappe.get_all(
				"Error Log", filters={"method": "POS Offline Price Mismatch"}, pluck="name"
			)
		)

	def test_offline_mismatch_accepted_and_flagged(self):
		"""An offline replay whose Price Replay disagrees (genuine claimed rule,
		a rate the server would not produce) is accepted at the charged price
		and flagged for HQ — an Error Log with the mismatch detail is written."""
		rule = self._rule(
			"_PNXT_SEC23_ReplayForgedAccepted",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=15,
		)
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		_apply_offers_and_stamp(payload, [rule])
		# The offline client priced off a stale/forged list price: its row is
		# internally consistent with the claimed 15% offer (plr 23.53 -> rate
		# 20), but the server's own list price (50) replays to 42.5.
		payload["items"][0].update(
			{"rate": 20, "price_list_rate": 20 / 0.85, "discount_percentage": 15}
		)
		payload["_paid"] = 20
		# A client pre-setting the server-managed flag must not suppress the log.
		payload["pos_offline_price_mismatch"] = 1
		offline_id = self._offline_id("ACCEPTED")
		before = self._offline_mismatch_logs()
		result = self._submit_offline(payload, offline_id=offline_id)
		doc = frappe.get_doc("Sales Invoice", result.get("name"))
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(cint(doc.pos_offline_price_mismatch), 1)
		self.assertAlmostEqual(flt(doc.items[0].rate), 20, places=2)
		new_logs = [n for n in self._offline_mismatch_logs() if n not in before]
		self.assertEqual(len(new_logs), 1)
		self.assertIn(offline_id, frappe.get_value("Error Log", new_logs[0], "error"))
		self.assertIn(_RATE_MSG, frappe.get_value("Error Log", new_logs[0], "error"))

	def test_offline_mismatch_fabricated_rule_rejected(self):
		"""A forged rule name in the offline payload never earns the accept
		path: the claim is not a real rule, so it is rejected."""
		rule = self._rule(
			"_PNXT_SEC23_ReplayFabricated",
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
		payload["items"][0]["pricing_rules"] = "_PNXT_SEC23_DOES_NOT_EXIST"
		payload["_paid"] = 20
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._submit_offline(payload, offline_id=self._offline_id("FABRICATED"))
		self.assertIn(_RATE_MSG, str(ctx.exception))

	def test_offline_mismatch_disabled_rule_rejected(self):
		"""A claimed rule that is disabled by the time of replay is not genuine:
		the offline accept path is refused."""
		rule = self._rule(
			"_PNXT_SEC23_ReplayDisabled",
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
		frappe.db.set_value("Pricing Rule", rule, "disable", 1)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._submit_offline(payload, offline_id=self._offline_id("DISABLED"))
		self.assertIn(_RATE_MSG, str(ctx.exception))

	def test_offline_mismatch_with_header_discount_flagged_once(self):
		"""Review Focus 3: a forged row plus a relayed transaction rule / header
		discount is accepted, and only ONE mismatch log is written for the
		invoice even when the replay runs on the draft save and the submit."""
		item_rule = self._rule(
			"_PNXT_SEC23_OnceItem",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=15,
		)
		txn_rule = self._rule(
			"_PNXT_SEC23_OnceTxn",
			apply_on="Transaction",
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=10,
			min_qty=0,
			apply_discount_on="Grand Total",
		)
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		_apply_offers_and_stamp(payload, [item_rule, txn_rule])
		# The row is internally consistent with the claimed 15% offer (stale
		# plr 23.53 -> rate 20); the server replays the row to 42.5. The
		# relayed header stays its own 10% of the forged total (2).
		payload["items"][0].update(
			{"rate": 20, "price_list_rate": 20 / 0.85, "discount_percentage": 15}
		)
		payload["discount_amount"] = 2
		payload["apply_discount_on"] = "Grand Total"
		payload["_paid"] = 18
		before = self._offline_mismatch_logs()
		result = self._submit_offline(payload, offline_id=self._offline_id("HEADER"))
		doc = frappe.get_doc("Sales Invoice", result.get("name"))
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(cint(doc.pos_offline_price_mismatch), 1)
		new_logs = [n for n in self._offline_mismatch_logs() if n not in before]
		self.assertEqual(len(new_logs), 1)

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

	# ------------------------------------------------------------------
	# (8) 100% manual discount (HQ-coded): SEC-23 exempts, code gate stays
	# ------------------------------------------------------------------

	def _mint_code(self, code):
		code_doc = frappe.get_doc(
			{
				"doctype": "POS Discount Confirmation Code",
				"code": code,
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
		return code

	def _full_discount_line(self, **overrides):
		line = _line(self.ctx, ITEM_A, qty=1)
		line.update(
			{
				"rate": 0,
				"price_list_rate": 50.0,
				"discount_percentage": 100,
				"discount_amount": 50.0,
			}
		)
		line.update(overrides)
		return line

	def test_full_manual_discount_passes(self):
		"""Rate-0 row with a 100% manual discount + valid HQ code submits: the
		SEC-23 replay has no engine grant to compare against, and authorization
		stays with the discount code gate (which the code satisfies)."""
		code = self._mint_code("SEC23D8AA")
		payload = self._paid_payload([self._full_discount_line()])
		payload["discount_confirmation_code"] = code.lower()
		from pos_next.api.invoices import update_invoice

		created = update_invoice(json.dumps(payload))
		self._created.append(created.get("name"))
		doc = frappe.get_doc("Sales Invoice", created.get("name"))
		self.assertAlmostEqual(flt(doc.items[0].rate), 0, places=2)
		self.assertAlmostEqual(flt(doc.grand_total), 0, places=2)

	def test_full_manual_discount_without_code_rejected(self):
		"""The SEC-23 exemption must not weaken authorization: the same row
		without an HQ code is still refused by the discount code gate."""
		payload = self._paid_payload([self._full_discount_line()])
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.ValidationError) as ctx:
			update_invoice(json.dumps(payload))
		self.assertIn("discount code", str(ctx.exception).lower())

	def test_partial_discount_rate_zero_rejected(self):
		"""A rate-0 row with only a partial discount stays rejected: the
		exemption covers the 100% case, not a half-price wipe."""
		code = self._mint_code("SEC23D8AB")
		line = self._full_discount_line(discount_percentage=50, discount_amount=25.0)
		payload = self._paid_payload([line])
		payload["discount_confirmation_code"] = code.lower()
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.ValidationError) as ctx:
			update_invoice(json.dumps(payload))
		self.assertIn(_RATE_MSG, str(ctx.exception))

	def test_full_discount_with_manual_edit_flag_rejected(self):
		"""A rate-0 row carrying the manual-edit flag never qualifies for the
		exemption — it stays on the rate-edit lane (which itself rejects
		non-positive rates)."""
		code = self._mint_code("SEC23D8AC")
		line = self._full_discount_line(is_rate_manually_edited=1)
		payload = self._paid_payload([line])
		payload["discount_confirmation_code"] = code.lower()
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.ValidationError):
			update_invoice(json.dumps(payload))

	# ------------------------------------------------------------------
	# (10) Offline zero-total replay: a lost discount must not submit a
	#      paymentless full-price invoice (the safety guard)
	# ------------------------------------------------------------------

	def _submit_offline_without_payments(self, payload, offline_id, client_grand_total=0):
		"""Offline replay of a zero-total sale: the queue carries payments [] and
		the client's grand_total, keyed by offline_id (submit_invoice builds the
		draft via update_invoice and submits it in one call)."""
		payload["offline_id"] = offline_id
		payload["grand_total"] = client_grand_total
		payload["payments"] = []
		result = submit_invoice(invoice=json.dumps(payload))
		self._created.append((result or {}).get("name"))
		return result

	def test_offline_zero_total_discount_submits_without_payments(self):
		"""Offline replay of a 100% HQ-coded discount: the client queued payments
		[] and grand_total 0, and the server honours the discount, so both totals
		are 0 and the invoice submits (the guard only fires on a divergence)."""
		code = self._mint_code("OFFZER8A")
		payload = self._paid_payload([self._full_discount_line()])
		payload["discount_confirmation_code"] = code.lower()
		result = self._submit_offline_without_payments(payload, self._offline_id("ZEROTOTAL"))
		doc = frappe.get_doc("Sales Invoice", result.get("name"))
		self.assertEqual(doc.docstatus, 1)
		self.assertAlmostEqual(flt(doc.grand_total), 0, places=2)

	def test_offline_zero_total_without_discount_rejected(self):
		"""Offline replay whose discount was lost: the client queued grand_total 0
		with payments [], but the server recomputes the full price. The guard must
		refuse it with a clear message instead of letting a paymentless full-price
		invoice through (ERPNext's cryptic payment-row error)."""
		drafts_before = set(
			frappe.get_all("Sales Invoice", {"docstatus": 0, "customer": self.ctx.customer}, pluck="name")
		)
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._submit_offline_without_payments(payload, self._offline_id("LOST"))
		self.assertIn("Offline discount could not be re-applied", str(ctx.exception))
		# update_invoice saved a draft before the guard refused it; the helper got
		# no name back, so sweep the new draft here (and prove it existed — the
		# guard fires AFTER the draft is built, not before).
		drafts_after = set(
			frappe.get_all("Sales Invoice", {"docstatus": 0, "customer": self.ctx.customer}, pluck="name")
		)
		new_drafts = drafts_after - drafts_before
		self.assertTrue(new_drafts, "update_invoice should have saved a draft before the guard")
		for name in new_drafts:
			frappe.delete_doc("Sales Invoice", name, force=1, ignore_permissions=True)

	# ------------------------------------------------------------------
	# (9) Offline replay keeps its own sale time, inside the shift window
	# ------------------------------------------------------------------

	def _offline_time_payload(self, sale_time):
		payload = self._paid_payload([_line(self.ctx, ITEM_A, qty=1)])
		payload.update(
			{
				"posa_pos_opening_shift": self.shift.name,
				"set_posting_time": 1,
				"posting_date": sale_time.strftime("%Y-%m-%d"),
				"posting_time": sale_time.strftime("%H:%M:%S"),
				"_paid": flt(payload["items"][0]["rate"]),
			}
		)
		return payload

	def test_offline_sale_time_kept(self):
		"""An offline replay inside the shift window keeps its own sale time."""
		sale_time = add_to_date(now_datetime(), minutes=-2)
		result = self._submit_offline(
			self._offline_time_payload(sale_time), offline_id=self._offline_id("SALETIME")
		)
		doc = frappe.get_doc("Sales Invoice", result.get("name"))
		self.assertEqual(
			get_time_str(doc.posting_time),
			"{0}:{1}:{2}".format(sale_time.hour, sale_time.minute, sale_time.second),
		)

	def test_offline_sale_time_before_shift_rejected(self):
		"""A sale time before the linked shift opened is refused."""
		bad = add_to_date(get_datetime(self.shift.period_start_date), days=-1)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._submit_offline(
				self._offline_time_payload(bad), offline_id=self._offline_id("BEFORE")
			)
		self.assertIn("before shift", str(ctx.exception))

	def test_offline_sale_time_future_clamped(self):
		"""A future sale time is clamped to now before it is saved."""
		payload = self._offline_time_payload(add_to_date(now_datetime(), minutes=10))
		result = self._submit_offline(payload, offline_id=self._offline_id("FUTURE"))
		doc = frappe.get_doc("Sales Invoice", result.get("name"))
		stored = get_datetime(doc.posting_date) + doc.posting_time
		self.assertLessEqual(stored, now_datetime())

	def test_online_set_posting_time_still_rejected(self):
		"""The offline lane is the only way to shift the posting date: the same
		payload through update_invoice (no offline_id) is refused."""
		payload = self._offline_time_payload(add_to_date(now_datetime(), minutes=-2))
		from pos_next.api.invoices import update_invoice

		with self.assertRaises(frappe.PermissionError) as ctx:
			update_invoice(json.dumps(payload))
		self.assertIn("Changing the invoice posting date is not allowed", str(ctx.exception))
