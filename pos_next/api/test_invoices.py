# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

"""Integration tests for POS Next's buyer-identity API surface.

Covers OpenSpec tasks 2.1 and 2.2 of `add-bakery-pos-capabilities`:

* 2.1 — `pos_next.api.invoices.sanitize_buyer_name` rejects over-length (60 chars)
  and control-character buyer names, stores whitespace-only input as absent, and
  honours the `enable_buyer_identity` / `require_buyer_name` gates in POS Settings.
  All of it happens before any write, so a rejected name leaves no draft behind.
* 2.2 — the API-level proof that a buyer name only applies to the POS Profile's
  default customer: the payload drives `update_invoice` / `submit_invoice` and the
  rejection comes from `pos_next.walk_in.validate_walk_in_customer_name`, wired as a
  Sales Invoice `validate` doc_event, firing inside the save. The document-level
  version of this rule is already covered in `pos_next/tests/test_walk_in.py`.

Group 3 (tasks 3.1-3.2, the change's one BREAKING item) is covered at the end of the
class: an unknown `customer` is rejected instead of auto-creating a bare Individual
Customer, and the counter-cases prove the retirement is scoped to unknown values only
(the walk-in default and a deliberately selected customer both still book unchanged).

Two adjacent paths were probed and need no test of their own, because Frappe rejects
them before `_validate_customer_exists` is reached and neither provisions a Customer:
a draft whose stored customer no longer exists fails link validation on re-save
(`LinkValidationError`), and an empty `customer` fails `MandatoryError` on the draft.

Also locks in two adjacent contracts from the same design decisions:
* `queue_number` is server-managed, so a client echo is stripped (allocation itself
  is task 2.3).
* A name-only walk-in sale never provisions a Customer (D1).

Fixture and payload patterns mirror `pos_next/test_promotions.py` — the same minimal
cart that passes `update_invoice` + `submit_invoice` on this bench without a POS
Opening Shift. All test data is prefixed `_PNXT_API_TEST_` and is built from scratch
rather than assuming existing records.
"""

import json
from types import SimpleNamespace
from unittest import mock

import frappe
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry
from frappe.database.database import savepoint
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

from pos_next.api.invoices import (
	BUYER_NAME_MAX_LENGTH,
	_build_invoice_search_clause,
	get_invoices,
	submit_invoice,
	update_invoice,
)

ITEM_CODE = "_PNXT_API_TEST_ITEM"
ITEM_PRICE = 50.0
DEFAULT_CUSTOMER = "_PNXT_API_TEST_WALKIN_CUSTOMER"
OTHER_CUSTOMER = "_PNXT_API_TEST_OTHER_CUSTOMER"


def _resolve_company():
	if frappe.db.exists("Company", "_Test Company"):
		return "_Test Company"
	return (
		frappe.defaults.get_global_default("company")
		or frappe.db.get_value("Company", {"name": ["!=", ""]}, "name")
	)


def _resolve_warehouse(company):
	if company == "_Test Company" and frappe.db.exists("Warehouse", "_Test Warehouse - _TC"):
		return "_Test Warehouse - _TC"
	wh = frappe.db.get_value(
		"Warehouse",
		{"company": company, "is_group": 0, "disabled": 0},
		"name",
		order_by="creation asc",
	)
	if not wh:
		frappe.throw(f"No warehouse for company {company}.")
	return wh


def _resolve_price_list(company):
	if frappe.db.exists("Price List", "Standard Selling"):
		return "Standard Selling"
	return frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")


def _resolve_currency(company):
	return frappe.get_cached_value("Company", company, "default_currency")


def _resolve_cost_center(company):
	return frappe.db.get_value(
		"Cost Center",
		{"company": company, "is_group": 0, "disabled": 0},
		"name",
		order_by="creation asc",
	)


def _resolve_leaf(group_doctype, name_field, parent_field, root_label, test_fixture):
	"""Pick a non-group node; group roots are rejected by Customer/Item validation."""
	for candidate in (test_fixture, "Products" if group_doctype == "Item Group" else None):
		if candidate and frappe.db.exists(group_doctype, candidate):
			if not frappe.get_cached_value(group_doctype, candidate, "is_group"):
				return candidate
	leaf = frappe.db.get_value(
		group_doctype, {"is_group": 0}, "name", order_by="creation asc"
	)
	if leaf:
		return leaf
	return frappe.get_doc(
		{
			"doctype": group_doctype,
			name_field: "_PNXT_API_TEST_" + group_doctype.upper(),
			parent_field: root_label,
			"is_group": 0,
		}
	).insert(ignore_permissions=True).name


def _ensure_mode_of_payment(company):
	"""A Mode of Payment that actually has an account for this company."""
	rows = frappe.db.sql(
		"SELECT DISTINCT parent FROM `tabMode of Payment Account` WHERE company = %s LIMIT 1",
		(company,),
	)
	if rows:
		return rows[0][0]

	if not frappe.db.exists("Mode of Payment", "Cash"):
		frappe.get_doc(
			{"doctype": "Mode of Payment", "mode_of_payment": "Cash", "type": "Cash", "enabled": 1}
		).insert(ignore_permissions=True)

	cash_account = frappe.get_cached_value("Company", company, "default_cash_account") or (
		frappe.db.get_value(
			"Account",
			{"company": company, "is_group": 0},
			"name",
			order_by="creation asc",
		)
	)
	mop = frappe.get_doc("Mode of Payment", "Cash")
	mop.append("accounts", {"company": company, "default_account": cash_account})
	mop.save(ignore_permissions=True)
	return "Cash"


def _ensure_item_and_stock(company, warehouse, price_list):
	if not frappe.db.exists("Item", ITEM_CODE):
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM_CODE,
				"item_name": "PNXT API Test Item",
				"item_group": _resolve_leaf(
					"Item Group", "item_group_name", "parent_item_group", "All Item Groups", "_Test Item Group"
				),
				"stock_uom": "Nos",
				"is_stock_item": 1,
			}
		)
		item.flags.from_integration = True
		item.insert(ignore_permissions=True)

	if not frappe.db.exists("Item Price", {"item_code": ITEM_CODE, "price_list": price_list}):
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": ITEM_CODE,
				"price_list": price_list,
				"price_list_rate": ITEM_PRICE,
			}
		).insert(ignore_permissions=True)

	if (frappe.db.get_value("Bin", {"item_code": ITEM_CODE, "warehouse": warehouse}, "actual_qty") or 0) < 50:
		try:
			make_stock_entry(
				item_code=ITEM_CODE,
				target=warehouse,
				qty=100,
				rate=ITEM_PRICE / 2,
				company=company,
			)
		except Exception:
			frappe.db.rollback()


def _ensure_customer(customer_name):
	if frappe.db.exists("Customer", customer_name):
		return customer_name
	return (
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": customer_name,
				"customer_group": _resolve_leaf(
					"Customer Group",
					"customer_group_name",
					"parent_customer_group",
					"All Customer Groups",
					"_Test Customer Group",
				),
				"territory": _resolve_leaf(
					"Territory", "territory_name", "parent_territory", "All Territories", "_Test Territory"
				),
				"customer_type": "Individual",
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


def _ensure_pos_profile(company, warehouse, price_list, mode_of_payment, currency):
	profile_name = "_PNXT_API_TEST_POS_PROFILE"
	payload = {
		"doctype": "POS Profile",
		"name": profile_name,
		"company": company,
		"warehouse": warehouse,
		"selling_price_list": price_list,
		"currency": currency,
		"customer": DEFAULT_CUSTOMER,
		"write_off_account": frappe.get_cached_value("Company", company, "write_off_account"),
		"write_off_cost_center": _resolve_cost_center(company),
		"ignore_pricing_rule": 0,
		"disable_rounded_total": 1,
		"disabled": 0,
	}
	if frappe.db.exists("POS Profile", profile_name):
		profile = frappe.get_doc("POS Profile", profile_name)
		profile.update({k: v for k, v in payload.items() if k != "name"})
		profile.payments = []
		profile.append("payments", {"mode_of_payment": mode_of_payment, "default": 1, "amount": 0})
		profile.save(ignore_permissions=True)
		return profile.name

	profile = frappe.get_doc(payload)
	profile.append("payments", {"mode_of_payment": mode_of_payment, "default": 1, "amount": 0})
	profile.insert(ignore_permissions=True)
	return profile.name


def _get_pos_settings(pos_profile):
	"""Fetch (creating if needed) the single POS Settings row owned by this suite."""
	name = frappe.db.get_value("POS Settings", {"pos_profile": pos_profile}, "name")
	if name:
		return frappe.get_cached_doc("POS Settings", name)
	doc = frappe.get_doc({"doctype": "POS Settings", "pos_profile": pos_profile, "enabled": 1})
	doc.insert(ignore_permissions=True)
	return doc


class TestInvoicesBuyerName(FrappeTestCase):
	"""API-level buyer-name validation through update_invoice / submit_invoice."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		company = _resolve_company()
		warehouse = _resolve_warehouse(company)
		price_list = _resolve_price_list(company)
		mode_of_payment = _ensure_mode_of_payment(company)
		currency = _resolve_currency(company)
		_ensure_customer(DEFAULT_CUSTOMER)
		_ensure_customer(OTHER_CUSTOMER)
		_ensure_item_and_stock(company, warehouse, price_list)
		pos_profile = _ensure_pos_profile(
			company, warehouse, price_list, mode_of_payment, currency
		)
		cls.ctx = SimpleNamespace(
			company=company,
			warehouse=warehouse,
			price_list=price_list,
			mode_of_payment=mode_of_payment,
			currency=currency,
			customer=DEFAULT_CUSTOMER,
			other_customer=OTHER_CUSTOMER,
			pos_profile=pos_profile,
		)

	def _payload(self, **overrides):
		ctx = self.ctx
		payload = {
			"doctype": "Sales Invoice",
			"is_pos": 1,
			"pos_profile": ctx.pos_profile,
			"company": ctx.company,
			"currency": ctx.currency,
			"customer": ctx.customer,
			"selling_price_list": ctx.price_list,
			"posting_date": nowdate(),
			"items": [
				{
					"item_code": ITEM_CODE,
					"qty": 1,
					"rate": ITEM_PRICE,
					"uom": "Nos",
					"warehouse": ctx.warehouse,
					"conversion_factor": 1,
					"price_list_rate": ITEM_PRICE,
					"discount_percentage": 0,
					"discount_amount": 0,
				}
			],
			"payments": [{"mode_of_payment": ctx.mode_of_payment, "amount": flt(ITEM_PRICE)}],
		}
		payload.update(overrides)
		return payload

	def _set_buyer_identity(self, enable, require):
		"""Mutate this profile's POS Settings, restoring the defaults afterwards."""
		settings = _get_pos_settings(self.ctx.pos_profile)
		frappe.db.set_value(
			"POS Settings",
			settings.name,
			{"enable_buyer_identity": enable, "require_buyer_name": require},
			update_modified=False,
		)
		frappe.clear_cache(doctype="POS Settings")
		self.addCleanup(
			lambda: frappe.db.set_value(
				"POS Settings",
				settings.name,
				{"enable_buyer_identity": 0, "require_buyer_name": 0},
				update_modified=False,
			)
		)
		return settings

	def _submit(self, payload):
		return self._submit_result(payload)[0]

	def _submit_result(self, payload):
		"""Submit `payload`, returning (invoice_name, submit_invoice_result_dict).

		`submit_invoice`'s return value is part of the API contract (the offline client
		reads `queue_number` and `offline_id` from it), so tests that exercise that
		contract go through here rather than through `_submit`, which only returns the
		name most assertions need.
		"""
		draft = update_invoice(json.dumps(payload))
		result = submit_invoice(
			invoice=json.dumps(draft, default=str),
			data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
		)
		return draft["name"], result

	def _invoice_count(self):
		return frappe.db.count("Sales Invoice", {"pos_profile": self.ctx.pos_profile})

	# ------------------------------------------------------------------ 2.1

	def test_update_invoice_rejects_buyer_name_over_length_limit(self):
		"""A 61-character buyer name is rejected and no draft is written."""
		too_long = "A" * (BUYER_NAME_MAX_LENGTH + 1)
		before = self._invoice_count()

		with self.assertRaises(frappe.ValidationError) as raised:
			update_invoice(json.dumps(self._payload(buyer_name=too_long)))

		message = str(raised.exception)
		self.assertIn(str(BUYER_NAME_MAX_LENGTH), message)
		self.assertEqual(self._invoice_count(), before)
		self.assertFalse(
			frappe.db.exists("Sales Invoice", {"pos_profile": self.ctx.pos_profile, "buyer_name": too_long})
		)

	def test_buyer_name_at_length_limit_is_accepted(self):
		"""The boundary is inclusive: exactly 60 characters passes."""
		name = "B" * BUYER_NAME_MAX_LENGTH
		self._set_buyer_identity(enable=1, require=0)
		draft = update_invoice(json.dumps(self._payload(buyer_name=name)))

		self.assertEqual(frappe.db.get_value("Sales Invoice", draft["name"], "buyer_name"), name)

	def test_update_invoice_rejects_control_characters(self):
		"""A control character in the buyer name is rejected with a naming message."""
		before = self._invoice_count()

		with self.assertRaises(frappe.ValidationError) as raised:
			update_invoice(json.dumps(self._payload(buyer_name="Budi\x07")))

		self.assertIn("control character", str(raised.exception).lower())
		self.assertEqual(self._invoice_count(), before)

	def test_whitespace_only_buyer_name_is_treated_as_absent(self):
		"""Whitespace-only persists as NULL, never as a blank string."""
		self._set_buyer_identity(enable=1, require=0)
		draft = update_invoice(json.dumps(self._payload(buyer_name="   ")))

		stored = frappe.db.get_value("Sales Invoice", draft["name"], "buyer_name")
		self.assertFalse(stored)
		self.assertNotEqual(stored, "   ")

	def test_valid_buyer_name_persists_and_survives_submit(self):
		"""A name-only walk-in sale keeps the name through submit and creates no Customer."""
		self._set_buyer_identity(enable=1, require=0)
		customers_before = frappe.db.count("Customer")

		name = self._submit(self._payload(buyer_name="Budi"))

		doc = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.buyer_name, "Budi")
		self.assertEqual(frappe.db.count("Customer"), customers_before)

	def test_require_buyer_name_gate_blocks_submission(self):
		"""With require_buyer_name on, submitting without a name is rejected."""
		self._set_buyer_identity(enable=1, require=1)
		before = self._invoice_count()

		with self.assertRaises(frappe.ValidationError):
			self._submit(self._payload(buyer_name=None))

		self.assertEqual(self._invoice_count(), before)

	def test_require_buyer_name_satisfied_by_valid_name(self):
		"""The same gate passes once a name is supplied."""
		self._set_buyer_identity(enable=1, require=1)

		name = self._submit(self._payload(buyer_name="Siti"))

		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "buyer_name"), "Siti")

	def test_disabled_buyer_identity_drops_client_supplied_name(self):
		"""With the feature off, a submitted invoice stores no buyer name."""
		self._set_buyer_identity(enable=0, require=0)

		name = self._submit(self._payload(buyer_name="Budi"))

		doc = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(doc.docstatus, 1)
		self.assertFalse(doc.buyer_name)

	def test_queue_number_from_client_is_stripped(self):
		"""queue_number is server-allocated; a client echo never reaches the doc."""
		self._set_buyer_identity(enable=1, require=0)

		name = self._submit(self._payload(buyer_name="Budi", queue_number=999))

		doc = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(doc.docstatus, 1)
		self.assertFalse(doc.queue_number)

	def test_submit_path_resanitises_replayed_buyer_name(self):
		"""An existing draft replayed through submit_invoice is re-validated."""
		self._set_buyer_identity(enable=1, require=0)
		draft = update_invoice(json.dumps(self._payload(buyer_name="Budi")))
		# Simulate a tampered client replaying an over-length name on submit.
		unsanitised = dict(draft, buyer_name="A" * (BUYER_NAME_MAX_LENGTH + 1))

		with self.assertRaises(frappe.ValidationError):
			submit_invoice(
				invoice=json.dumps(unsanitised, default=str),
				data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
			)

		self.assertEqual(
			frappe.db.get_value("Sales Invoice", draft["name"], "docstatus"), 0, "draft must not submit"
		)

	# ------------------------------------------------------------------ 2.2

	def test_buyer_name_rejected_for_non_default_customer(self):
		"""A buyer name on a non-default customer is rejected by the walk-in validator."""
		ctx = self.ctx
		self._set_buyer_identity(enable=1, require=0)
		before = self._invoice_count()

		with self.assertRaises(frappe.ValidationError) as raised:
			update_invoice(json.dumps(self._payload(customer=ctx.other_customer, buyer_name="Budi")))

		# Message comes from pos_next.walk_in and names the profile's default customer.
		self.assertIn(ctx.customer, str(raised.exception))
		self.assertEqual(self._invoice_count(), before)

	def test_buyer_name_accepted_for_default_customer(self):
		"""The counter-case: the same name on the default customer submits fine."""
		self._set_buyer_identity(enable=1, require=0)

		name = self._submit(self._payload(buyer_name="Budi"))

		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "buyer_name"), "Budi")

	# ------------------------------------------------------------------ 2.3 / 2.5

	def _new_shift(self):
		"""Create an Open POS Opening Shift for this suite's profile."""
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.ctx.pos_profile,
				"company": self.ctx.company,
				"user": "Administrator",
				"period_start_date": nowdate(),
				"posting_date": nowdate(),
				"status": "Open",
				"balance_details": [{"mode_of_payment": self.ctx.mode_of_payment, "amount": 0}],
			}
		)
		shift.insert(ignore_permissions=True)
		shift.submit()
		return shift.name

	def _submit_on_shift(self, shift):
		"""Submit one sale carrying `shift`, returning the invoice name."""
		return self._submit(self._payload(buyer_name="Budi", posa_pos_opening_shift=shift))

	def test_two_sequential_submissions_yield_1_then_2(self):
		"""D2: the shift counter is allocated 1 then 2 across two sequential submits."""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()

		first = self._submit_on_shift(shift)
		second = self._submit_on_shift(shift)

		self.assertEqual(frappe.db.get_value("Sales Invoice", first, "queue_number"), 1)
		self.assertEqual(frappe.db.get_value("Sales Invoice", second, "queue_number"), 2)
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift, "current_queue_number"), 2)

	def test_failed_submit_does_not_consume_queue_number(self):
		"""A submission that fails after allocation rolls the counter back with it (no gaps).

		The counter increment happens in the same transaction as the invoice write, so a
		later failure (here simulated at `submit()` — the step right after `save()`, which
		is where stock/GL posting can still blow up) must not leave the number consumed.
		Production would roll back the whole request; we reproduce that with a savepoint
		wrapper so the fixture (shift) created earlier in the test survives.
		"""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()

		real_submit = frappe.model.document.Document.submit
		calls = {"n": 0}

		def failing_submit(self_doc, *args, **kwargs):
			if self_doc.doctype == "Sales Invoice" and calls["n"] == 0:
				calls["n"] += 1
				raise frappe.ValidationError("simulated post-save submit failure")
			return real_submit(self_doc, *args, **kwargs)

		frappe.model.document.Document.submit = failing_submit
		try:
			with savepoint(catch=frappe.ValidationError):
				self._submit_on_shift(shift)
		finally:
			frappe.model.document.Document.submit = real_submit
		self.assertEqual(calls["n"], 1, "the simulated failure must actually have fired")

		# The failed attempt rolled back; the counter is still 0, so the next success is 1.
		good = self._submit_on_shift(shift)
		self.assertEqual(frappe.db.get_value("Sales Invoice", good, "queue_number"), 1)
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift, "current_queue_number"), 1)

	def test_second_shift_restarts_at_one(self):
		"""The counter is per shift: a new shift's first sale is number 1."""
		self._set_buyer_identity(enable=1, require=0)
		first_shift = self._new_shift()
		self._submit_on_shift(first_shift)
		self._submit_on_shift(first_shift)

		second_shift = self._new_shift()
		name = self._submit_on_shift(second_shift)

		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "queue_number"), 1)
		self.assertEqual(frappe.db.get_value("POS Opening Shift", second_shift, "current_queue_number"), 1)
		# The first shift's counter is untouched by the second shift's sale.
		self.assertEqual(frappe.db.get_value("POS Opening Shift", first_shift, "current_queue_number"), 2)

	def test_no_shift_sale_leaves_queue_number_empty(self):
		"""A sale without an opening shift must not be allocated (pos_next doesn't require one)."""
		self._set_buyer_identity(enable=1, require=0)

		name = self._submit(self._payload(buyer_name="Budi"))

		self.assertFalse(frappe.db.get_value("Sales Invoice", name, "queue_number"))

	def test_gate_off_shift_counter_untouched(self):
		"""With buyer identity disabled, even a shift-bearing sale leaves both empty."""
		self._set_buyer_identity(enable=0, require=0)
		shift = self._new_shift()

		name = self._submit_on_shift(shift)

		self.assertFalse(frappe.db.get_value("Sales Invoice", name, "queue_number"))
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift, "current_queue_number") or 0, 0)

	def test_disabled_settings_row_does_not_enforce_gates(self):
		"""A POS Settings row with enabled=0 is skipped, matching bootstrap/pos_profile/shifts.

		The server gate must honour the same `enabled` filter the client gate uses: with the
		only settings row disabled, `require_buyer_name=1` on it must not block a nameless
		sale and buyer identity must not be collected (same semantics as tasks 2.6/2.8).
		"""
		settings = _get_pos_settings(self.ctx.pos_profile)
		frappe.db.set_value(
			"POS Settings",
			settings.name,
			{"enabled": 0, "enable_buyer_identity": 1, "require_buyer_name": 1},
			update_modified=False,
		)
		frappe.clear_cache(doctype="POS Settings")
		self.addCleanup(
			lambda: frappe.db.set_value(
				"POS Settings",
				settings.name,
				{"enabled": 1, "enable_buyer_identity": 0, "require_buyer_name": 0},
				update_modified=False,
			)
		)

		name = self._submit(self._payload(buyer_name="Budi"))

		# The row is off, so the gate never fires: submission succeeds and the name is dropped.
		doc = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(doc.docstatus, 1)
		self.assertFalse(doc.buyer_name)

	# ------------------------------------------------- 2.5 result-dict contract

	def test_submit_result_carries_the_allocated_queue_number(self):
		"""`submit_invoice`'s result dict must publish the number it allocated (D2).

		The offline client persists this value as `server_queue_number` on its queue
		record (`POS/src/utils/buyerIdentity.js#reconcileQueueAfterSync`). Omitting the
		key makes every synced offline sale store null and silently breaks the audit
		contract, so the KEY and the VALUE are both asserted here.
		"""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()

		name, result = self._submit_result(self._payload(buyer_name="Budi", posa_pos_opening_shift=shift))

		self.assertIn("queue_number", result, f"result dict lost the key: {sorted(result)}")
		self.assertEqual(result["queue_number"], 1, f"result={result}")
		self.assertEqual(result["name"], name)
		# The published copy must agree with what is on the doc and on the shift.
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "queue_number"), 1)
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift, "current_queue_number"), 1)

	def test_submit_result_queue_number_is_none_when_allocation_is_skipped(self):
		"""Gate-off / no-shift sales must return the key with a null value, not omit it.

		Two shapes are tolerated by the client (`Number.parseInt(undefined)` and
		`Number.parseInt(null)` both fall back to null), but the contract we test is the
		stable one: the key is always present, and unallocated is represented as None so
		JSON serialises it to `null` rather than to `0` (which would read as queue zero).
		"""
		settings = self._set_buyer_identity(enable=0, require=0)
		shift = self._new_shift()

		_name, result = self._submit_result(self._payload(buyer_name="Budi", posa_pos_opening_shift=shift))

		self.assertIn("queue_number", result, f"key vanished with the gate off: {sorted(result)}")
		self.assertIsNone(result["queue_number"], f"gate-off submit must not publish a number: {result}")

		# Same shape with the gate ON but no shift on the sale.
		frappe.db.set_value(
			"POS Settings", settings.name, {"enable_buyer_identity": 1}, update_modified=False
		)
		frappe.clear_cache(doctype="POS Settings")
		_name, result = self._submit_result(self._payload(buyer_name="Budi"))
		self.assertIn("queue_number", result)
		self.assertIsNone(result["queue_number"], f"shiftless submit must not publish a number: {result}")

	def test_offline_dedup_replay_echoes_the_original_queue_number(self):
		"""The already-synced short-circuit must return the number the FIRST submit gave.

		An offline sale is often synced more than once (network retry, two tabs). The
		second call never reaches the allocation code — it returns from the dedup branch
		in `_ensure_offline_uniqueness`. That payload therefore has to read the persisted
		column off the existing invoice, or the client reconciles to null and the audit
		trail is lost on exactly the retries it exists to survive.
		"""
		import uuid

		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()
		offline_id = "pnxt-test-" + uuid.uuid4().hex

		name, first = self._submit_result(
			self._payload(buyer_name="Budi", posa_pos_opening_shift=shift)
		)
		self.assertEqual(first["queue_number"], 1)

		# Mark this invoice as already synced under `offline_id`, exactly how a completed
		# sync leaves the row behind.
		frappe.get_doc(
			{
				"doctype": "Offline Invoice Sync",
				"offline_id": offline_id,
				"sales_invoice": name,
				"status": "Synced",
				"pos_profile": self.ctx.pos_profile,
				"customer": self.ctx.customer,
			}
		).insert(ignore_permissions=True)

		replay = self._payload(
			buyer_name="Budi", posa_pos_opening_shift=shift, offline_id=offline_id, queue_number=999
		)
		result = submit_invoice(
			invoice=json.dumps(replay, default=str),
			data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
		)

		self.assertTrue(result.get("duplicate_prevented"), f"not the dedup branch: {result}")
		self.assertEqual(result.get("name"), name)
		self.assertEqual(result.get("offline_id"), offline_id)
		self.assertIn("queue_number", result, f"dedup payload lost the key: {sorted(result)}")
		self.assertEqual(
			result["queue_number"], 1, f"replay must echo the ORIGINAL number, got {result}"
		)
		# The replay must not consume a second number, and must not create a second invoice.
		self.assertEqual(frappe.db.get_value("POS Opening Shift", shift, "current_queue_number"), 1)
		self.assertEqual(
			frappe.db.count("Sales Invoice", {"posa_pos_opening_shift": shift}),
			1,
			"the dedup short-circuit must not create another invoice for the shift",
		)

	# ------------------------------------------------- 2.7 search by buyer identity

	def test_get_invoices_search_by_buyer_name(self):
		"""A staff search for a buyer name returns that sale with its identity fields.

		assertIn-not-equality on the result set: the shared POS Profile carries other
		sales from sibling tests whose auto-generated document names contain digits and
		years, so the legacy LIKE chain on `name` can also match; what the spec locks in
		is that the buyer-name match PRESENTS the right transaction (a search for
		'Budi' returns Budi's sale) rather than that no other row can ever surface.
		"""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()

		budi = self._submit(self._payload(buyer_name="Budi Saputra", posa_pos_opening_shift=shift))
		siti = self._submit(self._payload(buyer_name="Siti", posa_pos_opening_shift=shift))

		rows = get_invoices(self.ctx.pos_profile, search="Saputra", limit=100)
		names = [row["name"] for row in rows]
		self.assertIn(budi, names)
		self.assertNotIn(siti, names)

		row = next(r for r in rows if r["name"] == budi)
		self.assertEqual(row["buyer_name"], "Budi Saputra")
		self.assertEqual(row["queue_number"], 1)

	def test_get_invoices_search_by_queue_number(self):
		"""A numeric search returns the sale carrying that exact queue number.

		The exact-`=` join on queue_number is asserted positively (queue 2 is Siti's);
		the negative half — that a LIKE-substring match cannot leak queue 17 into a
		search for "1" — lives in the builder unit tests below, because the shared
		profile's other invoices have year-bearing names that the legacy name-LIKE
		clause matches independently of queue numbers.
		"""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()

		budi = self._submit(self._payload(buyer_name="Budi Saputra", posa_pos_opening_shift=shift))
		siti = self._submit(self._payload(buyer_name="Siti", posa_pos_opening_shift=shift))
		self.assertEqual(frappe.db.get_value("Sales Invoice", budi, "queue_number"), 1)
		self.assertEqual(frappe.db.get_value("Sales Invoice", siti, "queue_number"), 2)

		rows = get_invoices(self.ctx.pos_profile, search="2", limit=100)
		names = [row["name"] for row in rows]
		self.assertIn(siti, names)

		siti_row = next(r for r in rows if r["name"] == siti)
		self.assertEqual(siti_row["queue_number"], 2)
		self.assertEqual(siti_row["buyer_name"], "Siti")

	def test_get_invoices_search_unknown_queue_number_returns_nothing(self):
		"""An unmatched queue number must not return an unrelated transaction (2.7).

		'999' is chosen so it cannot substring-match anything: the fixture buyer names
		(Budi Saputra / Siti) carry no digits, and the profile's invoices never reach
		queue 999, so the exact-`=` clause yields nothing and the LIKE chain has no
		digit-free name to latch onto either.
		"""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()

		self._submit(self._payload(buyer_name="Budi Saputra", posa_pos_opening_shift=shift))
		self._submit(self._payload(buyer_name="Siti", posa_pos_opening_shift=shift))

		self.assertEqual(get_invoices(self.ctx.pos_profile, search="999", limit=100), [])

	def test_get_invoices_returns_buyer_identity_keys_without_search(self):
		"""A plain listing carries buyer_name/queue_number and every legacy key.

		The legacy key set must be a subset of the new shape so existing callers that
		read name/status/grand_total etc. keep working untouched.
		"""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()

		name = self._submit(self._payload(buyer_name="Budi Saputra", posa_pos_opening_shift=shift))

		rows = get_invoices(self.ctx.pos_profile, limit=100)
		row = next(r for r in rows if r["name"] == name)

		legacy_keys = {
			"name",
			"customer",
			"customer_name",
			"posting_date",
			"posting_time",
			"grand_total",
			"paid_amount",
			"outstanding_amount",
			"status",
			"docstatus",
			"is_return",
			"return_against",
		}
		self.assertTrue(legacy_keys.issubset(set(row)), f"lost legacy keys: {legacy_keys - set(row)}")
		self.assertEqual(row["buyer_name"], "Budi Saputra")
		self.assertEqual(row["queue_number"], 1)

	def test_get_invoices_degrades_when_custom_fields_are_missing(self):
		"""A site without the buyer-identity Custom Fields still lists invoices.

		Simulates a fresh site where after_migrate has not created the columns:
		has_column reports both absent, so buyer_name/queue_number must vanish from the
		SELECT and from the search clause instead of raising Unknown column.
		"""
		self._set_buyer_identity(enable=1, require=0)
		shift = self._new_shift()
		name = self._submit(self._payload(buyer_name="Budi Saputra", posa_pos_opening_shift=shift))

		with mock.patch.object(frappe.db, "has_column", return_value=False):
			rows = get_invoices(self.ctx.pos_profile, search="2", limit=100)

		self.assertTrue(rows, "the endpoint must still return rows without the CF columns")
		self.assertIn(name, [row["name"] for row in rows])
		row = next(r for r in rows if r["name"] == name)
		self.assertNotIn("buyer_name", row)
		self.assertNotIn("queue_number", row)

	def test_search_clause_builder_uses_exact_queue_match_only_for_integers(self):
		"""The builder locks the isdigit-gate and the exact `=` semantics (unit-level)."""
		clause, params = _build_invoice_search_clause("17", True, True)
		self.assertIn("buyer_name LIKE %(search)s", clause)
		self.assertIn("queue_number = %(queue_number)s", clause)
		self.assertNotIn("queue_number LIKE", clause)
		self.assertEqual(params["search"], "%17%")
		self.assertEqual(params["queue_number"], 17)

		# A non-integer term never touches queue_number.
		clause, params = _build_invoice_search_clause("Budi", True, True)
		self.assertIn("buyer_name LIKE %(search)s", clause)
		self.assertNotIn("queue_number", clause)
		self.assertNotIn("queue_number", params)

	def test_search_clause_builder_omits_missing_columns(self):
		"""CF-missing degradation at the clause level: absent columns stay out of SQL."""
		clause, params = _build_invoice_search_clause("17", False, False)
		self.assertNotIn("buyer_name", clause)
		self.assertNotIn("queue_number", clause)
		self.assertNotIn("queue_number", params)
		# The legacy chain is always there.
		for legacy in ("name LIKE %(search)s", "customer_name LIKE %(search)s", "customer LIKE %(search)s"):
			self.assertIn(legacy, clause)

	# ------------------------------------------------------------------ 3.1 / 3.2

	def test_unknown_customer_is_rejected_without_provisioning(self):
		"""Group 3 (BREAKING): an unknown `customer` no longer creates a Customer row.

		The retired behaviour auto-created a bare Individual Customer whenever the client
		sent a string that was not an existing Customer, swallowing failures into the error
		log. Now the sale is rejected, the Customer count is unchanged, and nothing is
		written — so a mistyped name cannot silently become master data. The message names
		`buyer_name`, which is where a name-only walk-in belongs.
		"""
		before = frappe.db.count("Customer")
		invoices = self._invoice_count()

		with self.assertRaises(frappe.ValidationError) as raised:
			update_invoice(json.dumps(self._payload(customer="Budi Santoso")))

		message = str(raised.exception)
		self.assertIn("Budi Santoso", message)
		self.assertIn("buyer name", message)
		self.assertEqual(frappe.db.count("Customer"), before, "no Customer may be provisioned")
		self.assertEqual(self._invoice_count(), invoices, "no draft may be written")

	def test_submit_path_rejects_unknown_customer(self):
		"""The submit path rejects an unknown customer on both branches (review finding).

		Branch 1: `submit_invoice` creates the draft internally, so the check in
		`update_invoice` fires. Branch 2: a draft already exists and that customer was
		since deleted - the check in `submit_invoice` itself fires, so the message stays
		the buyer-name-guided one rather than a raw link error. Both branches must leave
		the Customer count unchanged.
		"""
		before = frappe.db.count("Customer")

		with self.assertRaises(frappe.ValidationError) as raised:
			submit_invoice(
				invoice=json.dumps(self._payload(customer="Chandra Wijaya")),
				data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
			)
		self.assertIn("Chandra Wijaya", str(raised.exception))
		self.assertIn("buyer name", str(raised.exception))

		# Branch 2: an existing draft with a since-deleted customer.
		draft = update_invoice(json.dumps(self._payload()))
		frappe.db.set_value("Sales Invoice", draft["name"], "customer", "Ghost Customer")
		# Refresh, so we do not send a stale `modified` timestamp (which would raise
		# `TimestampMismatchError` before the validation is reached).
		draft = frappe.get_doc("Sales Invoice", draft["name"]).as_dict()
		with savepoint(catch=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError) as raised:
				submit_invoice(
					invoice=json.dumps(draft, default=str),
					data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
				)
			message = str(raised.exception)
		self.assertIn("buyer name", message)

		self.assertEqual(frappe.db.count("Customer"), before)

	def test_known_non_default_customer_still_books(self):
		"""3.2 scoped to a deliberately selected customer, not just the walk-in default.

		The retirement must only affect unknown values. A real Customer the cashier
		picked from the dialog loads, prices and books exactly as it did before, with no
		provisioning and no buyer-name involvement.
		"""
		customers_before = frappe.db.count("Customer")

		name = self._submit(self._payload(customer=self.ctx.other_customer))

		doc = frappe.get_doc("Sales Invoice", name)
		self.assertEqual(doc.customer, self.ctx.other_customer)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(flt(doc.grand_total), flt(ITEM_PRICE))
		self.assertFalse(doc.get("buyer_name"))
		self.assertEqual(frappe.db.count("Customer"), customers_before)

	def test_existing_walk_in_customer_still_books(self):
		"""The counter-case that makes this a scope change, not a feature removal.

		The profile's default walk-in customer is a real Customer row, so the ordinary
		name-only sale still books against exactly the same customer as before and still
		creates nothing.
		"""
		self._set_buyer_identity(enable=1, require=0)
		customers_before = frappe.db.count("Customer")

		name = self._submit(self._payload(buyer_name="Budi"))

		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "customer"), self.ctx.customer)
		self.assertEqual(frappe.db.get_value("Sales Invoice", name, "buyer_name"), "Budi")
		self.assertEqual(frappe.db.count("Customer"), customers_before)
