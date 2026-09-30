# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Server-own enforcement of the stamped POS Coupon (A2/B1/B2/B3 + RECHECK).

Scenario lock (a-f) on top of the unit-level checks:

a. a draft with discount_amount and NO apply_discount_on saves with
   "Grand Total" — the ERPNext 417 ("Please select Apply Discount On"
   thrown from inside calculate_taxes_and_totals) is dead.
b. a 10% coupon on a 15.000 cart with the client sending exactly 1.500:
   stamp lands, submit succeeds WITHOUT a head-office code, the coupon
   quota burns one use, the invoice keeps discount_amount 1.500.
c. the same coupon overclaimed at 5.000 is NOT trusted: the checkout
   CLAMPS it to the coupon's own math (1.500) and the submit succeeds
   with the server figure — no path carries the 5.000 anywhere.
d. a min_amount 100.000 coupon on the 15.000 cart is refused server-side
   (the UI check alone is not the gate).
e. a header discount with NO coupon and NO code still fails with the
   head-office code message — the legacy gate is intact.
f. a header discount fully explained by the coupon BUT with an item-level
   manual discount still demands the head-office code (only the header is
   exempted, never the rows).

RECHECK locks:
- basis inflation is dead: the coupon owns apply_discount_on and the
  amount; apply_discount_on="Grand Total" + smuggled
  is_cash_or_non_trade_discount + an overclaimed figure are all ignored.
- gate state is server-owned: is_pos=0 / is_consolidated=1 in a payload
  cannot mute the coupon lane or the head-office gate.
- a coupon can never ride a return: the claim is refused, the copied
  stamp is dropped, and any stamped return that still exists throws.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_coupon_discount_enforcement
"""

import json
import random
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from pos_next.api.invoices import submit_invoice, update_invoice
from pos_next.overrides.discount_code import (
	enforce_stamped_coupon_discount,
	stamped_coupon_explains_header,
)
from pos_next.pos_next.doctype.pos_coupon.pos_coupon import (
	coupon_base_amount,
	expected_coupon_discount,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin

# The confirmation-code doctype rejects 0/1/I/L/O — draw from a safe charset.
_SAFE_CHARS = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _safe_code(prefix):
	return prefix + "".join(random.choice(_SAFE_CHARS) for _ in range(8))


def _make_coupon(test, **overrides):
	"""A fresh 10% Promotional coupon scoped to the test's profile company."""
	code = _safe_code("CPNHARD")
	doc = frappe.get_doc(
		{
			"doctype": "POS Coupon",
			"coupon_name": f"_CPNHARD Coupon {code}",
			"coupon_code": code,
			"coupon_type": "Promotional",
			"company": test.profile.company,
			"apply_on": "Grand Total",
			"discount_type": "Percentage",
			"discount_percentage": 10,
			"maximum_use": 100,
		}
	)
	doc.update(overrides)
	doc.insert(ignore_permissions=True)
	# hard DB delete (frappe.db.delete), not delete_doc: v16's delete_doc
	# enqueues delete_dynamic_links, and this shared dev site's queue has no
	# running worker to drain it (same pattern as
	# test_coupon_one_use_resubmit._fabricate_submitted_usage)
	test.addCleanup(lambda: frappe.db.delete("POS Coupon", {"name": doc.name}))
	return doc


def _make_hq_code(test):
	"""An active head-office confirmation code."""
	code = _safe_code("HQ")
	doc = frappe.get_doc(
		{
			"doctype": "POS Discount Confirmation Code",
			"code": code,
			"status": "Active",
		}
	)
	doc.insert(ignore_permissions=True)
	test.addCleanup(lambda: frappe.db.delete("POS Discount Confirmation Code", {"name": doc.name}))
	return code


def _build_payload(test, discount=0, coupon_code=None, **overrides):
	"""Checkout payload on the mixin's item; the pre-discount total follows
	the items override (rate×qty) so the payment row always matches."""
	items = overrides.get("items") or test._payload()["items"]
	total = sum(flt(i.get("rate") or 0) * flt(i.get("qty") or 1) for i in items)
	payload = test._payload(
		discount_amount=discount,
		payments=[{"mode_of_payment": test.mode[0], "amount": total - discount}],
	)
	if coupon_code:
		payload["coupon_code"] = coupon_code
	payload.update(overrides)
	return payload


def _add_stock(test, qty):
	"""Extra Material Receipt (same pattern as the mixin's _make_stock) — the
	mixin mints only 5 units; the 15.000 cart spends 150."""
	se = frappe.get_doc(
		{
			"doctype": "Stock Entry",
			"stock_entry_type": "Material Receipt",
			"purpose": "Material Receipt",
			"company": test.profile.company,
			"items": [
				{
					"item_code": test.item,
					"qty": qty,
					"t_warehouse": test.profile.warehouse,
					"allow_zero_valuation_rate": 1,
				}
			],
		}
	)
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()

	def _drop():
		doc = frappe.get_doc("Stock Entry", se.name)
		if doc.docstatus == 1:
			doc.flags.ignore_permissions = True
			doc.cancel()
		frappe.db.delete("Stock Entry", {"name": se.name})  # hard delete: no enqueue

	test.addCleanup(_drop)


def _relax_enqueue_guard_for_this_process():
	"""This shared dev bench runs no queue worker, so delete_doc's
	enqueue_after_commit cleanup jobs (frappe v16 enqueues one per delete)
	accumulate across every test run and saturate frappe's enqueue guard —
	past 600 any tearDown's delete_doc throws QueueOverloaded. Lift the cap
	for THIS test process only: no site config file, no redis state — the
	process dies with the value, and the extra jobs are the same no-op
	delete_dynamic_links cleanups every integration test here already adds."""
	frappe.conf.max_queued_jobs = 10**9


class TestCouponRecomputeCalculator(unittest.TestCase):
	"""Pure calculator: pre-discount basis, percentage/amount, caps."""

	@staticmethod
	def _coupon(**overrides):
		coupon = frappe._dict(
			{
				"apply_on": "Grand Total",
				"discount_type": "Percentage",
				"discount_percentage": 10,
				"discount_amount": 0,
				"min_amount": 0,
				"max_amount": 0,
			}
		)
		coupon.update(overrides)
		return coupon

	@staticmethod
	def _doc(**overrides):
		doc = frappe._dict({"grand_total": 90, "net_total": 90, "discount_amount": 10})
		doc.update(overrides)
		return doc

	def test_grand_total_basis_reconstructs_pre_discount(self):
		"""Stored totals are post-discount: basis = grand_total + discount."""
		self.assertEqual(coupon_base_amount(self._coupon(), self._doc()), 100.0)

	def test_net_total_basis_reconstructs_pre_discount(self):
		self.assertEqual(
			coupon_base_amount(self._coupon(apply_on="Net Total"), self._doc(grand_total=99)), 100.0
		)

	def test_percentage_and_amount(self):
		self.assertEqual(expected_coupon_discount(self._coupon(), self._doc()), 10.0)
		self.assertEqual(
			expected_coupon_discount(
				self._coupon(discount_type="Amount", discount_amount=15), self._doc()
			),
			15.0,
		)

	def test_max_amount_caps_expected(self):
		self.assertEqual(
			expected_coupon_discount(
				self._coupon(discount_type="Amount", discount_amount=50, max_amount=30), self._doc()
			),
			30.0,
		)

	def test_unknown_type_grants_nothing(self):
		self.assertEqual(expected_coupon_discount(self._coupon(discount_type=""), self._doc()), 0.0)

	def test_expected_never_exceeds_basis(self):
		self.assertEqual(
			expected_coupon_discount(
				self._coupon(discount_type="Amount", discount_amount=500), self._doc()
			),
			100.0,
		)


class TestStampedCouponEnforcement(POSInvoiceModeMixin, FrappeTestCase):
	"""Skenario a-e end to end through update_invoice/submit_invoice."""

	def setUp(self):
		_relax_enqueue_guard_for_this_process()
		super().setUp()
		if not frappe.get_meta("POS Invoice").has_field("pos_coupon_code"):
			self.skipTest("no pos_coupon_code custom field on POS Invoice")

	# (a) ------------------------------------------------------------------

	def test_draft_without_apply_discount_on_saves_with_grand_total(self):
		"""Header discount without any basis: the default lands on the saved
		draft and the calculation no longer throws "Please select Apply
		Discount On" (the 417)."""
		echo = update_invoice(
			json.dumps(
				_build_payload(self, discount=10, discount_confirmation_code=_make_hq_code(self))
			)
		)
		self._created.append(echo["name"])
		self.assertEqual(echo.get("apply_discount_on"), "Grand Total")

	def test_coupon_apply_on_decides_basis(self):
		"""A coupon that applies on Net Total pins the basis without the client
		sending anything."""
		coupon = _make_coupon(self, apply_on="Net Total")
		echo = update_invoice(
			json.dumps(_build_payload(self, discount=10, coupon_code=coupon.coupon_code))
		)
		self._created.append(echo["name"])
		self.assertEqual(echo.get("apply_discount_on"), "Net Total")

	def test_invalid_apply_discount_on_rejected(self):
		"""The field is not stripped from the payload — the whitelist here is
		what keeps it from being a mass-assignment vector (coupon-less lane)."""
		with self.assertRaises(frappe.ValidationError):
			update_invoice(
				json.dumps(
					_build_payload(
						self,
						discount=10,
						apply_discount_on="Cart Total",
						discount_confirmation_code=_make_hq_code(self),
					)
				)
			)

	# (b) ------------------------------------------------------------------

	def test_exact_coupon_discount_submits_without_hq_code(self):
		"""10% coupon, 15.000 cart, client sends 1.500: stamp lands, submit
		succeeds with no head-office code, quota burns exactly one use."""
		coupon = _make_coupon(self)
		_add_stock(self, 150)  # the cart spends 150 units
		echo = update_invoice(
			json.dumps(
				_build_payload(
					self,
					discount=1500,
					coupon_code=coupon.coupon_code,
					items=[
						{"item_code": self.item, "qty": 150, "rate": 100, "warehouse": self.profile.warehouse}
					],
				)
			)
		)
		self._created.append(echo["name"])
		self.assertEqual(echo.get("pos_coupon_code"), coupon.coupon_code)

		result = submit_invoice(invoice=echo)
		self._created.append(result["name"])
		self.assertEqual(result["status"], 1)  # docstatus rides `status` here
		self.assertEqual(flt(result["grand_total"]), 13500.0)
		self.assertEqual(
			flt(frappe.db.get_value("POS Invoice", result["name"], "discount_amount")), 1500.0
		)
		self.assertEqual(frappe.db.get_value("POS Coupon", coupon.name, "used"), 1)

	# (c) — server-own: the overclaim is CLAMPED, not trusted ----------------

	def test_overclaim_is_clamped_to_server_math(self):
		"""Same coupon, client sends 5.000: nothing throws — the checkout
		writes the coupon's own 1.500 and no path carries the 5.000."""
		coupon = _make_coupon(self)
		_add_stock(self, 150)
		echo = update_invoice(
			json.dumps(
				_build_payload(
					self,
					discount=5000,
					coupon_code=coupon.coupon_code,
					payments=[{"mode_of_payment": self.mode[0], "amount": 10000}],
					items=[
						{"item_code": self.item, "qty": 150, "rate": 100, "warehouse": self.profile.warehouse}
					],
				)
			)
		)
		self._created.append(echo["name"])
		self.assertEqual(flt(echo["discount_amount"]), 1500.0)
		self.assertEqual(flt(echo["grand_total"]), 13500.0)

		# the cashier settles the (corrected) bill: submit succeeds on 1.500
		echo["payments"] = [{"mode_of_payment": self.mode[0], "amount": 13500}]
		result = submit_invoice(invoice=echo)
		self._created.append(result["name"])
		self.assertEqual(result["status"], 1)
		self.assertEqual(
			flt(frappe.db.get_value("POS Invoice", result["name"], "discount_amount")), 1500.0
		)
		self.assertEqual(flt(result["grand_total"]), 13500.0)

	# (d) ------------------------------------------------------------------

	def test_min_amount_enforced_server_side(self):
		"""The CouponDialog checks min_amount in the UI; the server must not
		trust that it ran: 100.000 minimum on a 15.000 cart is refused."""
		coupon = _make_coupon(self, min_amount=100000)
		with self.assertRaises(frappe.ValidationError) as cm:
			update_invoice(
				json.dumps(
					_build_payload(
						self,
						discount=1500,
						coupon_code=coupon.coupon_code,
						items=[
							{"item_code": self.item, "qty": 150, "rate": 100, "warehouse": self.profile.warehouse}
						],
					)
				)
			)
		self.assertIn("minimum spend", str(cm.exception))

	# (e) ------------------------------------------------------------------

	def test_discount_without_coupon_or_code_needs_hq_code(self):
		"""Legacy gate intact: a bare header discount with neither coupon nor
		confirmation code still fails with the head-office message."""
		with self.assertRaises(frappe.ValidationError) as cm:
			update_invoice(
				json.dumps(
					_build_payload(
						self,
						discount=1500,
						items=[
							{"item_code": self.item, "qty": 150, "rate": 100, "warehouse": self.profile.warehouse}
						],
					)
				)
			)
		self.assertIn("head office", str(cm.exception))

	def test_capped_amount_coupon_passes_at_cap(self):
		coupon = _make_coupon(self, discount_type="Amount", discount_amount=50, max_amount=30)
		echo = update_invoice(
			json.dumps(_build_payload(self, discount=30, coupon_code=coupon.coupon_code))
		)
		self._created.append(echo["name"])
		self.assertEqual(flt(echo["discount_amount"]), 30.0)

	# RECHECK #1 — basis inflation is dead ---------------------------------

	def test_inflated_basis_craft_is_ignored(self):
		"""The recheck craft: Net-Total 30% coupon, client sends
		apply_discount_on="Grand Total" + is_cash_or_non_trade_discount=1 +
		discount = net·30/70 (the figure that would pass an inflated-basis
		recompute). Server-own: the coupon's basis wins, the amount is 30% of
		the server's pre-discount net, and the cash flag stays off."""
		coupon = _make_coupon(self, apply_on="Net Total", discount_percentage=30)
		echo = update_invoice(
			json.dumps(
				_build_payload(
					self,
					discount=85.71,  # 200 × 30/70 — the inflated-basis figure
					coupon_code=coupon.coupon_code,
					apply_discount_on="Grand Total",
					is_cash_or_non_trade_discount=1,
					items=[{"item_code": self.item, "qty": 2, "rate": 100, "warehouse": self.profile.warehouse}],
				)
			)
		)
		self._created.append(echo["name"])
		self.assertEqual(flt(echo["discount_amount"]), 60.0)  # 30% × 200 net
		self.assertEqual(echo.get("apply_discount_on"), "Net Total")
		self.assertEqual(flt(echo["net_total"]), 140.0)  # money actually moved
		self.assertEqual(flt(echo["grand_total"]), 140.0)
		self.assertFalse(flt(echo.get("is_cash_or_non_trade_discount") or 0))

	# RECHECK #2 — gate state is server-owned ------------------------------

	def test_consolidated_flag_cannot_disarm_gate(self):
		"""is_consolidated=1 in the draft payload used to short-circuit the
		gate on every save — it is stripped now, so a discounted draft still
		needs the head-office code."""
		with self.assertRaises(frappe.ValidationError) as cm:
			update_invoice(
				json.dumps(_build_payload(self, discount=10, is_consolidated=1))
			)
		self.assertIn("head office", str(cm.exception))

	def test_pos_off_and_consolidated_cannot_disarm_existing_submit(self):
		"""The existing-draft submit loads the payload via doc.update(): a
		smuggled is_pos=0 muted the whole gate there. Stripped now — the same
		submit with its code removed still demands the head-office code."""
		echo = update_invoice(
			json.dumps(_build_payload(self, discount=10, discount_confirmation_code=_make_hq_code(self)))
		)
		self._created.append(echo["name"])
		payload = dict(echo)
		payload["is_pos"] = 0
		payload["is_consolidated"] = 1
		payload["discount_confirmation_code"] = ""  # code removed by the client
		with self.assertRaises(frappe.ValidationError) as cm:
			submit_invoice(invoice=payload)
		self.assertIn("head office", str(cm.exception))

	# (B2) — the existing-draft branch is covered too ----------------------

	def test_manipulated_existing_draft_resubmit_is_clamped(self):
		"""The existing-draft branch never re-runs update_invoice: its
		save-before-submit re-derives the coupon discount server-side, so a
		bumped 20 lands back on the coupon's 10."""
		coupon = _make_coupon(self)
		echo = update_invoice(
			json.dumps(_build_payload(self, discount=10, coupon_code=coupon.coupon_code))
		)
		echo["discount_amount"] = 20
		echo["payments"] = [{"mode_of_payment": self.mode[0], "amount": 90}]
		result = submit_invoice(invoice=echo)
		self._created.append(result["name"])
		self.assertEqual(result["status"], 1)
		self.assertEqual(flt(result["grand_total"]), 90.0)
		self.assertEqual(
			flt(frappe.db.get_value("POS Invoice", result["name"], "discount_amount")), 10.0
		)

	# Fail closed — returns ------------------------------------------------

	def test_stamped_return_is_refused(self):
		"""Any stamped return that still exists throws in the hook (the
		checkout refuses coupon claims on returns and drops copied stamps)."""
		coupon = _make_coupon(self)
		doc = frappe._dict(
			{
				"pos_coupon_code": coupon.coupon_code,
				"discount_amount": 10,
				"grand_total": -90,
				"net_total": -90,
				"is_return": 1,
			}
		)
		with self.assertRaises(frappe.ValidationError) as cm:
			enforce_stamped_coupon_discount(doc)
		self.assertIn("return", str(cm.exception))
		self.assertFalse(stamped_coupon_explains_header(doc))

	def test_coupon_claim_on_return_refused(self):
		"""Claiming a coupon in a return payload is refused outright."""
		coupon = _make_coupon(self)
		_add_stock(self, 150)
		echo = update_invoice(
			json.dumps(
				_build_payload(
					self,
					discount=1500,
					coupon_code=coupon.coupon_code,
					items=[
						{"item_code": self.item, "qty": 150, "rate": 100, "warehouse": self.profile.warehouse}
					],
				)
			)
		)
		self._created.append(echo["name"])
		result = submit_invoice(invoice=echo)
		self._created.append(result["name"])

		return_payload = _build_payload(
			self,
			is_return=1,
			return_against=result["name"],
			discount_confirmation_code=_make_hq_code(self),
			items=[{"item_code": self.item, "qty": -1, "rate": 100, "warehouse": self.profile.warehouse}],
			payments=[{"mode_of_payment": self.mode[0], "amount": -100}],
		)
		return_payload["coupon_code"] = coupon.coupon_code
		with self.assertRaises(frappe.ValidationError) as cm:
			update_invoice(json.dumps(return_payload))
		self.assertIn("return", str(cm.exception))

	def test_return_of_coupon_invoice_drops_stamp(self):
		"""make_sales_return copies every mapped field — including the stamp.
		The server drops it: a legitimate return of a coupon invoice stays on
		the refund lane and can never release coupon quota via cancel."""
		coupon = _make_coupon(self)
		_add_stock(self, 150)
		echo = update_invoice(
			json.dumps(
				_build_payload(
					self,
					discount=1500,
					coupon_code=coupon.coupon_code,
					items=[
						{"item_code": self.item, "qty": 150, "rate": 100, "warehouse": self.profile.warehouse}
					],
				)
			)
		)
		self._created.append(echo["name"])
		result = submit_invoice(invoice=echo)
		self._created.append(result["name"])

		ret = update_invoice(
			json.dumps(
				_build_payload(
					self,
					is_return=1,
					return_against=result["name"],
					discount_confirmation_code=_make_hq_code(self),
					items=[
						{"item_code": self.item, "qty": -1, "rate": 100, "warehouse": self.profile.warehouse}
					],
					payments=[{"mode_of_payment": self.mode[0], "amount": -100}],
				)
			)
		)
		self._created.append(ret["name"])
		self.assertFalse(ret.get("pos_coupon_code"))

	# Verification helpers on a fabricated doc ------------------------------

	def test_verification_is_exact_match(self):
		"""Server math on the doc passes; anything else on a stamped doc is a
		fabrication and throws — no tolerance band."""
		coupon = _make_coupon(self)
		doc = frappe._dict(
			{
				"pos_coupon_code": coupon.coupon_code,
				"discount_amount": 10,
				"grand_total": 90,
				"net_total": 90,
			}
		)
		enforce_stamped_coupon_discount(doc)  # exact server figure: no throw
		self.assertTrue(stamped_coupon_explains_header(doc))

		doc.discount_amount = 999
		with self.assertRaises(frappe.ValidationError) as cm:
			enforce_stamped_coupon_discount(doc)
		self.assertIn("does not match coupon", str(cm.exception))
		self.assertFalse(stamped_coupon_explains_header(doc))


class TestCouponHeaderExemptButItemManual(POSInvoiceModeMixin, FrappeTestCase):
	"""Skenario f: the header ≤ the coupon's math, but a row-level manual
	discount keeps the invoice on the head-office code lane.

	The manual row rides the SEC-04 rate-edit lane, which needs
	allow_user_to_edit_rate/max_discount_allowed on the profile's POS
	Settings row — backed up and restored per test; the row itself is
	created when the schedule-safe profile has none."""

	def setUp(self):
		_relax_enqueue_guard_for_this_process()
		super().setUp()
		# The manual row rides the SEC-04 rate-edit lane — it needs
		# allow_user_to_edit_rate/max_discount_allowed on the profile's POS
		# Settings row. The row itself is test-owned fixture: created here
		# when the schedule-safe profile has none (the mixin owns the profile
		# choice), patched when it exists, restored/deleted in tearDown.
		self.settings_row = frappe.db.get_value(
			"POS Settings", {"pos_profile": self.profile.name, "enabled": 1}, "name"
		)
		self.settings_created = False
		if self.settings_row:
			self.settings_backup = frappe.db.get_value(
				"POS Settings",
				self.settings_row,
				["price_replay_audit_only", "allow_user_to_edit_rate", "max_discount_allowed"],
				as_dict=True,
			)
		else:
			self.settings_row = (
				frappe.get_doc(
					{
						"doctype": "POS Settings",
						"enabled": 1,
						"pos_profile": self.profile.name,
						"allow_user_to_edit_rate": 0,
						"max_discount_allowed": 0,
						"price_replay_audit_only": 0,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
			self.settings_created = True
		frappe.db.set_value(
			"POS Settings",
			self.settings_row,
			{"price_replay_audit_only": 0, "allow_user_to_edit_rate": 1, "max_discount_allowed": 20},
			update_modified=False,
		)

	def tearDown(self):
		if getattr(self, "settings_created", False):
			# fixture of this test — hard delete, no delete_doc enqueue
			frappe.db.delete("POS Settings", {"name": self.settings_row})
		elif getattr(self, "settings_row", None) and getattr(self, "settings_backup", None):
			frappe.db.set_value(
				"POS Settings",
				self.settings_row,
				{
					"price_replay_audit_only": self.settings_backup.price_replay_audit_only or 0,
					"allow_user_to_edit_rate": self.settings_backup.allow_user_to_edit_rate or 0,
					"max_discount_allowed": self.settings_backup.max_discount_allowed or 0,
				},
				update_modified=False,
			)
		# the mixin's tearDown ends with frappe.db.commit(), so the
		# delete/restore above commits with it — nothing class-level is
		# deleted here, hence no tearDownClass commit dance.
		super().tearDown()

	def _manual_row_payload(self, coupon_code, confirmation_code=None):
		"""20% rate edit on the mixin item's list price (list 20 → rate 16,
		exactly at the cap patched in setUp), plus the 10% coupon's exact
		1.6 on the 16 cart."""
		payload = _build_payload(
			self,
			discount=1.6,
			coupon_code=coupon_code,
			items=[
				{
					"item_code": self.item,
					"qty": 1,
					"rate": 16,
					"is_rate_manually_edited": 1,
					"warehouse": self.profile.warehouse,
				}
			],
		)
		if confirmation_code:
			payload["discount_confirmation_code"] = confirmation_code
		return payload

	def test_item_manual_discount_still_needs_hq_code(self):
		"""Header 1.6 = 10% × basis 16 (≤ cap, exempt) — but the manually
		edited row is a manual discount: the head-office code is still
		demanded."""
		coupon = _make_coupon(self)
		with self.assertRaises(frappe.ValidationError) as cm:
			update_invoice(json.dumps(self._manual_row_payload(coupon.coupon_code)))
		self.assertIn("head office", str(cm.exception))

	def test_same_cart_with_hq_code_passes(self):
		"""Positive control: the identical cart WITH a code passes — what was
		refused above is the missing code, not the payload's shape."""
		coupon = _make_coupon(self)
		echo = update_invoice(
			json.dumps(self._manual_row_payload(coupon.coupon_code, _make_hq_code(self)))
		)
		self._created.append(echo["name"])
		self.assertEqual(flt(echo["discount_amount"]), 1.6)
