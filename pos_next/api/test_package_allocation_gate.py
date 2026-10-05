# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Fase 3-4 tests: SEC-23 package-allocation gate, offer exclusion, return
doctype links, invoice-level discount distribution, parent-row tax/stock,
tax-inclusive extraction, IDR 0-decimal precision and the shared
Python<->JS allocator expectation table.

Run via pos_next/_pn_run_tests.py
pos_next.api.test_package_allocation_gate
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

import pos_next
from pos_next.api.invoices import (
	_is_package_row,
	_validate_item_rates,
	_verify_package_allocation_totals,
)
from pos_next.api.packages import (
	_rate_precision,
	_validate_return_packages,
	allocate_package_rates,
	get_packages,
	quote,
	validate_invoice_packages,
)
from pos_next.api.test_packages import (
	BACKPACK as COMPONENT_A,
)

# Reuse the package fixture namespace from test_packages.
from pos_next.api.test_packages import (
	BASE_PRICE,
	COMPANY,
	COMPONENT_ROLE,
	HEADPHONE,
	PACKAGE,
	PARENT_ITEM,
	PARENT_ROLE,
	PROFILE,
	_ensure_inr_price_list,
	_ensure_item,
	_ensure_package,
	_option_id,
)
from pos_next.api.test_packages import (
	LAPTOP as COMPONENT_B,
)
from pos_next.tests._posi_test_utils import _set_invoice_type

ALLOCATION_FLAG = "enable_pos_package_allocation"

# One expectation table for both implementations: read here and by
# POS/src/utils/packageAllocation.test.js, so the server allocator and the
# offline mirror are pinned to identical rates for every brief case.
SHARED_CASES_PATH = (
	Path(__file__).resolve().parents[2] / "POS" / "src" / "utils" / "packageAllocation.cases.json"
)


def _set_allocation(value):
	frappe.db.set_single_value("POS Next Global Settings", ALLOCATION_FLAG, 1 if value else 0)
	frappe.clear_cache(doctype="POS Next Global Settings")


def _allocation_snapshot(total, selections=None):
	return json.dumps(
		{
			"package": PACKAGE,
			"package_name": PACKAGE,
			"base_price": total,
			"total": total,
			"selections": selections or [],
			"allocation": {"mode": "proportional", "precision": 2},
		}
	)


def _invoice_rows(parent_rate, component_rates, snapshot, instance="pkg-gate-1"):
	rows = [
		frappe._dict(
			{
				"item_code": PARENT_ITEM,
				"qty": 1,
				"rate": parent_rate,
				"price_list_rate": parent_rate,
				"pos_package": PACKAGE,
				"pos_package_instance": instance,
				"pos_package_role": PARENT_ROLE,
				"pos_package_snapshot": snapshot,
			}
		)
	]
	for item_code, rate, qty in component_rates:
		rows.append(
			frappe._dict(
				{
					"item_code": item_code,
					"qty": qty,
					"rate": rate,
					"price_list_rate": rate,
					"pos_package": PACKAGE,
					"pos_package_instance": instance,
					"pos_package_role": COMPONENT_ROLE,
				}
			)
		)
	return rows


def _doc(rows, **fields):
	doc = frappe._dict({"is_return": 0, "pos_profile": PROFILE, **fields})
	doc["items"] = rows
	return doc


class TestAllocatePackageRates(unittest.TestCase):
	"""Pure unit tests of ``allocate_package_rates``, driven by the shared
	expectation table at POS/src/utils/packageAllocation.cases.json — the same
	file POS/src/utils/packageAllocation.test.js reads. Both implementations
	are pinned to identical rates for every brief case (23k over 20k+10k, qty 2,
	3-item rounding, zero-priced child, IDR precision 0) so the server
	allocator and the offline preview cannot drift."""

	@classmethod
	def setUpClass(cls):
		with SHARED_CASES_PATH.open(encoding="utf-8") as handle:
			cls.cases = json.load(handle)["cases"]

	def test_shared_expectation_table_matches_server_allocator(self):
		for case in self.cases:
			with self.subTest(case=case["name"]):
				rates = allocate_package_rates(
					case["package_price"],
					case["children"],
					case["package_qty"],
					case["precision"],
				)
				self.assertEqual(rates, case["expected_rates"])
				# A negative component rate must never be booked, even on
				# fail-closed shapes (the sum check below rejects those).
				self.assertTrue(all(rate >= 0 for rate in rates), rates)

	def test_shared_table_satisfies_the_exact_sum_invariant(self):
		for case in self.cases:
			with self.subTest(case=case["name"]):
				rates = allocate_package_rates(
					case["package_price"],
					case["children"],
					case["package_qty"],
					case["precision"],
				)
				allocated = sum(
					rate * child["qty_per_package"] * case["package_qty"]
					for rate, child in zip(rates, case["children"], strict=True)
				)
				expected = case["package_price"] * case["package_qty"]
				if case.get("exact_sum", True):
					self.assertEqual(flt(allocated, case["precision"]), flt(expected, case["precision"]))
				else:
					# Pinned fail-closed shape: no qty-1 line with weight > 0
					# exists, so the remainder cannot divide exactly — the
					# invoice sum check must reject this (never silently sell).
					self.assertNotEqual(
						flt(allocated, case["precision"]), flt(expected, case["precision"])
					)

	def test_zero_weight_qty1_line_never_carries_the_remainder(self):
		"""User repro: [qty3 @5000, qty1 @0] @20000 used to yield [6667, -1].
		The zero-weight qty-1 line must not carry the leftover as a negative
		rate — it is clamped to 0 and the sum misses, failing closed."""
		rates = allocate_package_rates(
			20000,
			[
				{"qty_per_package": 3, "price_list_rate": 5000},
				{"qty_per_package": 1, "price_list_rate": 0},
			],
			precision=0,
		)

		self.assertEqual(rates, [6667, 0])
		self.assertTrue(all(rate >= 0 for rate in rates), rates)
		self.assertNotEqual(sum(rate * qty for rate, qty in zip(rates, (3, 1), strict=True)), 20000)

	def test_b1_qty1_carrier_absorbs_the_remainder(self):
		"""Review repro: [qty1 @20000, qty2 @5000] @25000 used to yield
		[16667, 4166] (sum 24999) because the qty>1 last line carried the
		remainder. The qty-1 line must carry it instead."""
		children = [
			{"qty_per_package": 1, "price_list_rate": 20000},
			{"qty_per_package": 2, "price_list_rate": 5000},
		]
		rates = allocate_package_rates(25000, children, precision=0)

		self.assertEqual(rates, [16666, 4167])
		self.assertEqual(
			sum(rate * child["qty_per_package"] for rate, child in zip(rates, children, strict=True)), 25000
		)

	def test_without_a_qty1_carrier_the_sum_can_miss(self):
		"""Documented boundary: with every line a multiple and an odd remainder
		no line can absorb, the last line carries it and the sum can miss the
		package price. validate_invoice_packages fails closed on this (and
		POS Package's own validate rejects such definitions when allocation is
		on), so the allocator itself stays pure and non-throwing."""
		rates = allocate_package_rates(
			25001,
			[
				{"qty_per_package": 2, "price_list_rate": 5000},
				{"qty_per_package": 2, "price_list_rate": 5000},
			],
			precision=0,
		)

		self.assertEqual(rates, [6250, 6250])
		self.assertNotEqual(sum(rate * 2 for rate in rates), 25001)


class TestAllocationGate(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		_ensure_package()
		frappe.db.commit()

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)

	def test_honest_allocation_group_passes(self):
		rows = _invoice_rows(
			0, [(COMPONENT_A, 5_000_000, 1), (COMPONENT_B, 5_000_000, 1)], _allocation_snapshot(BASE_PRICE)
		)

		_verify_package_allocation_totals(_doc(rows), 2)

	def test_tampered_component_rate_is_rejected(self):
		rows = _invoice_rows(
			0, [(COMPONENT_A, 4_999_999, 1), (COMPONENT_B, 5_000_000, 1)], _allocation_snapshot(BASE_PRICE)
		)

		with self.assertRaises(frappe.ValidationError):
			_verify_package_allocation_totals(_doc(rows), 2)

	def test_legacy_group_without_marker_is_untouched(self):
		"""Legacy components at 0 with a marker-less parent snapshot stay on the
		old path — no invariant, no throw."""
		snapshot = json.dumps({"package": PACKAGE, "total": BASE_PRICE, "selections": []})
		rows = _invoice_rows(BASE_PRICE, [(COMPONENT_A, 0, 1), (COMPONENT_B, 0, 1)], snapshot)

		_verify_package_allocation_totals(_doc(rows), 2)

	def test_zero_rate_allocation_component_is_not_flagged_as_suspicious(self):
		"""A component the allocation legitimately rounds to 0 must not trip the
		rate-0 suspicion — the group sum carries the check instead."""
		rows = _invoice_rows(
			0,
			[(COMPONENT_A, 0, 1), (COMPONENT_B, BASE_PRICE, 1)],
			_allocation_snapshot(BASE_PRICE),
		)
		doc = _doc(rows)

		# No throw from either the allocation gate or the SEC-23 replay loop.
		_verify_package_allocation_totals(doc, 2)
		_validate_item_rates(doc, None, None)

	def test_offer_claim_on_package_row_is_rejected_explicitly(self):
		rows = _invoice_rows(
			0, [(COMPONENT_A, 5_000_000, 1), (COMPONENT_B, 5_000_000, 1)], _allocation_snapshot(BASE_PRICE)
		)
		rows[1]["pricing_rules"] = "PRLE-0001"

		with self.assertRaises(frappe.ValidationError) as ctx:
			_validate_item_rates(_doc(rows), None, None)

		self.assertIn("package lines", str(ctx.exception))

	def test_allocation_marker_without_total_is_rejected(self):
		snapshot = json.dumps({"package": PACKAGE, "selections": [], "allocation": {"mode": "proportional"}})
		rows = _invoice_rows(0, [(COMPONENT_A, 5_000_000, 1), (COMPONENT_B, 5_000_000, 1)], snapshot)

		with self.assertRaises(frappe.ValidationError):
			_verify_package_allocation_totals(_doc(rows), 2)

	def test_package_row_detection_covers_instance_only_rows(self):
		self.assertTrue(_is_package_row(frappe._dict({"pos_package": PACKAGE})))
		self.assertTrue(_is_package_row(frappe._dict({"pos_package_instance": "pkg-1"})))
		self.assertFalse(_is_package_row(frappe._dict({"item_code": "X", "rate": 5})))


class TestInvoiceLevelDiscount(FrappeTestCase):
	"""The approved split: package price remains the allocation basis and an
	invoice-level discount (manual or coupon) is distributed proportionally by
	ERPNext's own apply_discount_amount over the row net amounts — the parent
	rate-0 row gets a zero share, components share the discount by value."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_package()
		frappe.db.commit()
		cls.company = COMPANY
		cls.warehouse = frappe.db.get_value("POS Profile", PROFILE, "warehouse")

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)

	def _allocation_invoice(self):
		from pos_next.api.packages import get_packages, quote

		pkg = next(p for p in get_packages(PROFILE) if p["name"] == PACKAGE)
		result = quote(
			PACKAGE,
			[
				{
					"group_key": "accessory",
					"options": [{"option_id": _option_id(pkg, COMPONENT_A), "qty": 1}],
				}
			],
			PROFILE,
			allocate=True,
		)
		self.assertTrue(result["snapshot"].get("allocation"), "fixture quote must be allocation mode")

		inv = frappe.new_doc("Sales Invoice")
		inv.customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		inv.company = self.company
		inv.pos_profile = PROFILE
		inv.is_pos = 0
		inv.set_posting_time = 1
		inv.currency = frappe.db.get_value("Company", self.company, "default_currency")
		inv.selling_price_list = frappe.db.get_value("POS Profile", PROFILE, "selling_price_list")
		for line in result["lines"]:
			inv.append(
				"items",
				{
					"item_code": line["item_code"],
					"qty": line["qty"],
					"rate": line["rate"],
					"uom": line.get("uom") or "Nos",
					"warehouse": self.warehouse,
					"pos_package": PACKAGE,
					"pos_package_instance": "pkg-disc-gate",
					"pos_package_role": line["role"],
					"pos_package_snapshot": json.dumps(result["snapshot"])
					if line["role"] == PARENT_ROLE
					else None,
				},
			)
		inv.set_missing_values()
		return inv, result["total"]

	@staticmethod
	def _by_role(inv, role):
		return [row for row in inv.items if row.pos_package_role == role]

	def test_header_discount_is_shared_by_components_parent_stays_zero(self):
		inv, package_total = self._allocation_invoice()
		inv.discount_amount = 100.0
		inv.apply_discount_on = "Grand Total"

		inv.run_method("validate")

		parent = self._by_role(inv, PARENT_ROLE)[0]
		components = self._by_role(inv, COMPONENT_ROLE)
		self.assertEqual(parent.rate, 0)
		self.assertEqual(flt(parent.net_amount), 0)
		self.assertEqual(flt(parent.get("distributed_discount_amount") or 0), 0)

		distributed = [flt(c.get("distributed_discount_amount") or 0) for c in components]
		self.assertTrue(all(share > 0 for share in distributed), distributed)
		# Proportional split: equal-valued lines get equal shares. The absolute
		# sum follows ERPNext's own discount basis (tax template from
		# set_missing_values can shrink the distributable base), so only the
		# proportionality and the untouched allocation basis are asserted.
		self.assertAlmostEqual(distributed[0], distributed[1], places=2)
		rate_money = sum(flt(c.rate) * flt(c.qty) for c in components)
		self.assertAlmostEqual(rate_money, package_total, places=2)

	def test_percentage_header_discount_keeps_the_allocation_basis(self):
		"""A percentage invoice discount (the coupon lane's companion: a coupon
		is stamped as a header discount and distributed by the same ERPNext
		apply_discount_amount) gives the zero parent a zero share, splits the
		discount proportionally across the components and leaves the allocation
		basis itself untouched."""
		inv, package_total = self._allocation_invoice()
		inv.additional_discount_percentage = 5
		inv.apply_discount_on = "Grand Total"

		inv.run_method("validate")

		self.assertGreater(flt(inv.discount_amount), 0)
		parent = self._by_role(inv, PARENT_ROLE)[0]
		components = self._by_role(inv, COMPONENT_ROLE)
		self.assertEqual(parent.rate, 0)
		self.assertEqual(flt(parent.net_amount), 0)
		self.assertEqual(flt(parent.get("distributed_discount_amount") or 0), 0)

		distributed = [flt(c.get("distributed_discount_amount") or 0) for c in components]
		self.assertTrue(all(share > 0 for share in distributed), distributed)
		# Proportional split: equal-valued lines get equal shares, up to
		# ERPNext's per-row rounding of the distributed amount (a cent).
		self.assertLess(abs(distributed[0] - distributed[1]), 0.02)
		rate_money = sum(flt(c.rate) * flt(c.qty) for c in components)
		self.assertAlmostEqual(rate_money, package_total, places=2)


class TestParentRowTaxAndStock(FrappeTestCase):
	"""The allocation-mode parent is a zero-rate group header: it must not be
	taxed (value-based taxes) and must not move stock (non-stock item).
	'On Item Quantity' taxes would charge the header as one extra unit — the
	allocation re-quote fails closed instead of silently over-taxing."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_package()
		frappe.db.commit()
		cls.company = COMPANY
		cls.warehouse = frappe.db.get_value("POS Profile", PROFILE, "warehouse")
		cls.tax_account = frappe.db.get_value(
			"Account", {"company": COMPANY, "account_type": "Tax", "is_group": 0}, "name"
		)

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)

	def _allocation_invoice(self, doctype="Sales Invoice"):
		from pos_next.api.packages import get_packages, quote

		pkg = next(p for p in get_packages(PROFILE) if p["name"] == PACKAGE)
		result = quote(
			PACKAGE,
			[{"group_key": "accessory", "options": [{"option_id": _option_id(pkg, COMPONENT_A), "qty": 1}]}],
			PROFILE,
			allocate=True,
		)
		inv = frappe.new_doc(doctype)
		inv.customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		inv.company = self.company
		inv.pos_profile = PROFILE
		inv.is_pos = 0
		inv.set_posting_time = 1
		inv.currency = frappe.db.get_value("Company", self.company, "default_currency")
		inv.selling_price_list = frappe.db.get_value("POS Profile", PROFILE, "selling_price_list")
		for line in result["lines"]:
			inv.append(
				"items",
				{
					"item_code": line["item_code"],
					"qty": line["qty"],
					"rate": line["rate"],
					"uom": line.get("uom") or "Nos",
					"warehouse": self.warehouse,
					"pos_package": PACKAGE,
					"pos_package_instance": "pkg-tax-gate",
					"pos_package_role": line["role"],
					"pos_package_snapshot": json.dumps(result["snapshot"])
					if line["role"] == PARENT_ROLE
					else None,
				},
			)
		inv.set_missing_values()
		# Test-site artifact: set_missing_values pulls a default Item Tax
		# Template and (for the zero-rate parent) a stray price_list_rate,
		# which ERPNext's calculate_item_rate turns into rate 1. Production
		# never sees this: update_invoice sets explicit rates/price_list_rate
		# and validate_invoice_packages re-quotes before calculate runs.
		for row in inv.items:
			row.item_tax_template = None
			if row.pos_package_role == PARENT_ROLE:
				row.rate = 0
				row.price_list_rate = 0
		inv.flags.ignore_validate = True
		return inv, result["total"]

	def test_value_based_tax_leaves_parent_at_zero(self):
		for doctype in ("Sales Invoice", "POS Invoice"):
			inv, _total = self._allocation_invoice(doctype)
			if not self.tax_account:
				self.skipTest("no tax account on site")
			inv.append(
				"taxes",
				{
					"charge_type": "On Net Total",
					"account_head": self.tax_account,
					"rate": 10,
					"description": "VAT",
				},
			)
			inv.calculate_taxes_and_totals()

			parent = next(r for r in inv.items if r.pos_package_role == PARENT_ROLE)
			components = [r for r in inv.items if r.pos_package_role == COMPONENT_ROLE]
			# item_tax_amount stays 0 for exclusive taxes; the per-row tax
			# breakup is the transient item_wise_tax_details list (v16).
			details = inv.get("_item_wise_tax_details") or []
			parent_tax = flt(
				sum(
					d.get("amount") or 0
					for d in details
					if d.get("item") is not None and d["item"].item_code == parent.item_code
				)
			)
			component_codes = {c.item_code for c in components}
			component_tax = flt(
				sum(
					d.get("amount") or 0
					for d in details
					if d.get("item") is not None and d["item"].item_code in component_codes
				)
			)
			self.assertEqual(parent_tax, 0, doctype)
			self.assertGreater(component_tax, 0, doctype)

	def test_on_item_quantity_tax_with_allocation_fails_closed(self):
		if not self.tax_account:
			self.skipTest("no tax account on site")
		from pos_next.api.packages import validate_invoice_packages

		for doctype in ("Sales Invoice", "POS Invoice"):
			inv, _total = self._allocation_invoice(doctype)
			inv.append(
				"taxes",
				{
					"charge_type": "On Item Quantity",
					"account_head": self.tax_account,
					"rate": 5,
					"description": "Levy",
				},
			)

			# Both hooks route here (Sales Invoice doc_events and
			# pos_invoice_events.validate), so calling it directly proves the
			# gate itself is doctype-agnostic without needing a live shift.
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_invoice_packages(inv)

			self.assertIn("On Item Quantity", str(ctx.exception), doctype)

	def test_inclusive_value_tax_keeps_grand_total_at_package_price(self):
		"""Tax-inclusive ON: the package price is the gross amount, so the tax
		must be extracted from it rather than added on top — grand_total equals
		the package price exactly (no double tax) and the zero-rate parent stays
		untaxed."""
		if not self.tax_account:
			self.skipTest("no tax account on site")
		for doctype in ("Sales Invoice", "POS Invoice"):
			inv, total = self._allocation_invoice(doctype)
			inv.append(
				"taxes",
				{
					"charge_type": "On Net Total",
					"account_head": self.tax_account,
					"rate": 11,
					"description": "VAT included",
					"included_in_print_rate": 1,
				},
			)
			inv.calculate_taxes_and_totals()

			parent = next(r for r in inv.items if r.pos_package_role == PARENT_ROLE)
			self.assertEqual(parent.rate, 0, doctype)
			self.assertGreater(flt(inv.total_taxes_and_charges), 0, doctype)
			self.assertLess(flt(inv.net_total), flt(total), doctype)
			self.assertEqual(flt(inv.grand_total, 2), flt(total, 2), doctype)

			details = inv.get("_item_wise_tax_details") or []
			parent_tax = flt(
				sum(
					d.get("amount") or 0
					for d in details
					if d.get("item") is not None and d["item"].item_code == parent.item_code
				)
			)
			self.assertEqual(parent_tax, 0, doctype)

	def test_parent_moves_no_stock(self):
		"""The parent item is non-stock and the ledger loop gates on
		is_stock_item — submit a real invoice and count the SLEs."""
		self.assertEqual(frappe.db.get_value("Item", PARENT_ITEM, "is_stock_item"), 0)

		inv, _total = self._allocation_invoice()
		inv.update_stock = 1
		for item_code in (COMPONENT_A, COMPONENT_B):
			try:
				se = frappe.get_doc(
					{
						"doctype": "Stock Entry",
						"stock_entry_type": "Material Receipt",
						"purpose": "Material Receipt",
						"company": self.company,
						"items": [
							{
								"item_code": item_code,
								"qty": 1,
								"t_warehouse": self.warehouse,
								"allow_zero_valuation_rate": 1,
							}
						],
					}
				)
				se.flags.ignore_permissions = True
				se.insert()
				se.submit()
			except Exception:
				frappe.db.rollback()
				self.skipTest("cannot seed component stock")

		try:
			# The invoice was built before the receipts, so its default
			# posting_time predates them; stamp a later time or the ledger
			# sorts the sale first and reports negative stock.
			inv.set_posting_time = 1
			inv.posting_time = frappe.utils.nowtime()
			inv.flags.ignore_validate = False
			inv.flags.ignore_permissions = True
			inv.run_method("validate")
			inv.insert()
			inv.submit()

			for row in inv.items:
				count = frappe.db.count(
					"Stock Ledger Entry",
					{
						"voucher_type": "Sales Invoice",
						"voucher_no": inv.name,
						"voucher_detail_no": row.name,
					},
				)
				if row.pos_package_role == PARENT_ROLE:
					self.assertEqual(count, 0, "parent header must not move stock")
				else:
					self.assertEqual(count, 1, row.item_code)
		finally:
			frappe.db.rollback()


class TestReturnAllocationRates(FrappeTestCase):
	"""Allocation returns mirror the original component rates (full and
	partial), legacy returns mirror the parent price with components at 0, and
	both invoice doctypes resolve their own row-link column."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_package()
		frappe.db.commit()
		cls.company = COMPANY

	def _insert_invoice(self, doctype, rows, instance="pkg-ret-gate", grand_total=None):
		parent_fields = {
			"doctype": doctype,
			"company": self.company,
			"customer": frappe.db.get_value("Customer", {"disabled": 0}, "name"),
			"pos_profile": PROFILE,
			"is_pos": 1,
			"update_stock": 0,
			"posting_date": frappe.utils.nowdate(),
		}
		if grand_total is not None:
			parent_fields["grand_total"] = grand_total
			parent_fields["net_total"] = grand_total
		parent = frappe.get_doc(parent_fields)
		parent.db_insert()
		inserted = []
		for idx, vals in enumerate(rows, start=1):
			row = frappe.get_doc(
				{
					"doctype": f"{doctype} Item",
					"parent": parent.name,
					"parenttype": doctype,
					"parentfield": "items",
					"idx": idx,
					"item_name": vals["item_code"],
					"conversion_factor": 1,
					"amount": flt(vals["rate"]) * flt(vals["qty"]),
					"uom": "Nos",
					**vals,
				}
			)
			row.db_insert()
			inserted.append(row)
		return parent, inserted

	def _return_doc(self, doctype, original_rows, link_field, fraction=1.0):
		doc = frappe._dict(
			{
				"doctype": doctype,
				"is_return": 1,
				"return_against": original_rows[0].parent,
				"pos_profile": PROFILE,
			}
		)
		doc["items"] = [
			frappe._dict(
				{
					"item_code": row.item_code,
					"qty": -flt(row.qty) * fraction,
					"rate": 0,
					link_field: row.name,
				}
			)
			for row in original_rows
		]
		return doc

	def test_allocation_return_full_and_partial_mirror_component_rates(self):
		for doctype in ("Sales Invoice", "POS Invoice"):
			link_field = "sales_invoice_item" if doctype == "Sales Invoice" else "pos_invoice_item"
			rows = [
				{
					"item_code": PARENT_ITEM,
					"qty": 1,
					"rate": 0,
					"pos_package": PACKAGE,
					"pos_package_instance": f"pkg-ret-full-{doctype[:2]}",
					"pos_package_role": PARENT_ROLE,
					"pos_package_snapshot": _allocation_snapshot(BASE_PRICE),
				},
				{
					"item_code": COMPONENT_A,
					"qty": 1,
					"rate": 5_000_000,
					"pos_package": PACKAGE,
					"pos_package_instance": f"pkg-ret-full-{doctype[:2]}",
					"pos_package_role": COMPONENT_ROLE,
				},
				{
					"item_code": COMPONENT_B,
					"qty": 1,
					"rate": 5_000_000,
					"pos_package": PACKAGE,
					"pos_package_instance": f"pkg-ret-full-{doctype[:2]}",
					"pos_package_role": COMPONENT_ROLE,
				},
			]
			_parent, inserted = self._insert_invoice(doctype, rows)
			try:
				full = self._return_doc(doctype, inserted, link_field)
				_validate_return_packages(full)
				by_code = {r.item_code: r for r in full["items"]}
				self.assertEqual(by_code[COMPONENT_A].rate, 5_000_000, doctype)
				self.assertEqual(by_code[COMPONENT_B].rate, 5_000_000, doctype)

				partial = self._return_doc(doctype, inserted, link_field, fraction=0.5)
				_validate_return_packages(partial)
				by_code = {r.item_code: r for r in partial["items"]}
				self.assertEqual(by_code[COMPONENT_A].rate, 5_000_000, doctype)
				self.assertEqual(by_code[PARENT_ITEM].rate, 0, doctype)
			finally:
				frappe.db.rollback()

	def test_legacy_return_keeps_parent_price_and_zero_components(self):
		for doctype in ("Sales Invoice", "POS Invoice"):
			link_field = "sales_invoice_item" if doctype == "Sales Invoice" else "pos_invoice_item"
			instance = f"pkg-ret-legacy-{doctype[:2]}"
			rows = [
				{
					"item_code": PARENT_ITEM,
					"qty": 1,
					"rate": BASE_PRICE,
					"pos_package": PACKAGE,
					"pos_package_instance": instance,
					"pos_package_role": PARENT_ROLE,
					"pos_package_snapshot": json.dumps({"package": PACKAGE, "total": BASE_PRICE}),
				},
				{
					"item_code": COMPONENT_A,
					"qty": 1,
					"rate": 0,
					"pos_package": PACKAGE,
					"pos_package_instance": instance,
					"pos_package_role": COMPONENT_ROLE,
				},
				{
					"item_code": COMPONENT_B,
					"qty": 1,
					"rate": 0,
					"pos_package": PACKAGE,
					"pos_package_instance": instance,
					"pos_package_role": COMPONENT_ROLE,
				},
			]
			_parent, inserted = self._insert_invoice(doctype, rows)
			try:
				return_doc = self._return_doc(doctype, inserted, link_field)
				_validate_return_packages(return_doc)

				by_code = {r.item_code: r for r in return_doc["items"]}
				self.assertEqual(by_code[PARENT_ITEM].rate, BASE_PRICE, doctype)
				self.assertEqual(by_code[COMPONENT_A].rate, 0, doctype)
				self.assertEqual(by_code[COMPONENT_B].rate, 0, doctype)
			finally:
				frappe.db.rollback()

	def test_duplicate_component_full_return_mirrors_each_origin_rate(self):
		"""H2: two component rows of the SAME item_code carry independently
		rounded rates (3333 and 3334). The return must refund each row at its
		origin row's rate — averaging per item_code (3333.5 → 3334) over-refunds.
		Asserts grand_total(return) == -grand_total(original)."""
		for doctype in ("Sales Invoice", "POS Invoice"):
			link_field = "sales_invoice_item" if doctype == "Sales Invoice" else "pos_invoice_item"
			instance = f"pkg-ret-dup-{doctype[:2]}"
			rows = [
				{
					"item_code": PARENT_ITEM,
					"qty": 1,
					"rate": 0,
					"pos_package": PACKAGE,
					"pos_package_instance": instance,
					"pos_package_role": PARENT_ROLE,
				},
				{
					"item_code": COMPONENT_A,
					"qty": 1,
					"rate": 3333,
					"pos_package": PACKAGE,
					"pos_package_instance": instance,
					"pos_package_role": COMPONENT_ROLE,
				},
				{
					"item_code": COMPONENT_A,
					"qty": 1,
					"rate": 3334,
					"pos_package": PACKAGE,
					"pos_package_instance": instance,
					"pos_package_role": COMPONENT_ROLE,
				},
			]
			original, inserted = self._insert_invoice(doctype, rows, grand_total=6667)
			try:
				return_doc = frappe.get_doc(
					{
						"doctype": doctype,
						"company": self.company,
						"customer": frappe.db.get_value("Customer", {"disabled": 0}, "name"),
						"pos_profile": PROFILE,
						"is_return": 1,
						"return_against": original.name,
						"currency": frappe.db.get_value("Company", self.company, "default_currency"),
					}
				)
				for row in inserted:
					return_doc.append(
						"items",
						{
							"item_code": row.item_code,
							"qty": -flt(row.qty),
							"rate": 0,
							"uom": "Nos",
							"conversion_factor": 1,
							link_field: row.name,
						},
					)

				_validate_return_packages(return_doc)

				rates = [flt(row.rate) for row in return_doc.items]
				self.assertEqual(rates, [0, 3333, 3334], doctype)
				self.assertEqual(flt(return_doc.grand_total), -6667, doctype)
			finally:
				frappe.db.rollback()


class TestAllocationThroughUpdateInvoice(FrappeTestCase):
	"""End-to-end: the frontend-shaped allocation payload (quote output with
	pos_package linkage) survives update_invoice with the new gate active, and
	a rate-tampered variant is rejected with the allocation message."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_package()
		cls.company = COMPANY
		cls.warehouse = frappe.db.get_value("POS Profile", PROFILE, "warehouse")
		cls.baseline_invoice_type = frappe.db.get_single_value("POS Next Global Settings", "invoice_type")

	@classmethod
	def tearDownClass(cls):
		_set_invoice_type(cls.baseline_invoice_type or "POS Invoice")
		super().tearDownClass()

	def setUp(self):
		_set_allocation(1)
		_set_invoice_type("Sales Invoice")

	def tearDown(self):
		_set_allocation(0)

	def _seed_stock(self):
		for item_code in (COMPONENT_A, COMPONENT_B):
			se = frappe.get_doc(
				{
					"doctype": "Stock Entry",
					"stock_entry_type": "Material Receipt",
					"purpose": "Material Receipt",
					"company": self.company,
					"items": [
						{
							"item_code": item_code,
							"qty": 2,
							"t_warehouse": self.warehouse,
							"allow_zero_valuation_rate": 1,
						}
					],
				}
			)
			se.flags.ignore_permissions = True
			se.insert()
			se.submit()

	def _payload(self):
		from pos_next.api.packages import get_packages, quote

		pkg = next(p for p in get_packages(PROFILE) if p["name"] == PACKAGE)
		result = quote(
			PACKAGE,
			[{"group_key": "accessory", "options": [{"option_id": _option_id(pkg, COMPONENT_A), "qty": 1}]}],
			PROFILE,
			allocate=True,
		)
		items = [
			{
				"item_code": line["item_code"],
				"item_name": line["item_name"],
				"qty": line["qty"],
				"rate": line["rate"],
				"price_list_rate": line["rate"],
				"uom": line.get("uom") or "Nos",
				"warehouse": self.warehouse,
				"conversion_factor": 1,
				"pos_package": PACKAGE,
				"pos_package_instance": "pkg-update-gate",
				"pos_package_role": line["role"],
				"pos_package_snapshot": json.dumps(result["snapshot"])
				if line["role"] == PARENT_ROLE
				else None,
			}
			for line in result["lines"]
		]
		return {
			"doctype": "Sales Invoice",
			"pos_profile": PROFILE,
			"customer": frappe.db.get_value("Customer", {"disabled": 0}, "name"),
			"company": self.company,
			"currency": frappe.db.get_value("Company", self.company, "default_currency"),
			"selling_price_list": frappe.db.get_value("POS Profile", PROFILE, "selling_price_list"),
			"posting_date": frappe.utils.nowdate(),
			"items": items,
			"is_pos": 1,
			"update_stock": 1,
		}

	def test_honest_allocation_payload_is_saved_and_repriced(self):
		from pos_next.api.invoices import update_invoice

		self._seed_stock()
		try:
			created = update_invoice(json.dumps(self._payload()))
			name = created.get("name")
			self.assertTrue(name)
			doc = frappe.get_doc("Sales Invoice", name)
			by_code = {row.item_code: row for row in doc.items}
			parent = next(r for r in doc.items if r.pos_package_role == PARENT_ROLE)
			self.assertEqual(parent.rate, 0)
			self.assertEqual(flt(by_code[COMPONENT_A].rate) + flt(by_code[COMPONENT_B].rate), BASE_PRICE)
			self.assertEqual(doc.net_total, BASE_PRICE)
		finally:
			frappe.db.rollback()

	def test_tampered_component_rate_is_rejected(self):
		from pos_next.api.invoices import update_invoice

		self._seed_stock()
		try:
			payload = self._payload()
			for item in payload["items"]:
				if item["item_code"] == COMPONENT_A:
					item["rate"] = flt(item["rate"]) - 1

			with self.assertRaises(frappe.ValidationError) as ctx:
				update_invoice(json.dumps(payload))
			self.assertIn("component rates total", str(ctx.exception))
		finally:
			frappe.db.rollback()


class TestOfferExclusionOnPackageRows(FrappeTestCase):
	"""apply_offers excludes package lines explicitly and the response reports
	the exclusion; no rule is marked applied on their strength."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		from pos_next.test_promotions import _ctx

		cls.ctx = _ctx()
		cls._rules = []

	def tearDown(self):
		for rule in self._rules:
			frappe.db.set_value("Pricing Rule", rule, "disable", 1)
		self._rules = []
		frappe.db.commit()

	def test_package_row_is_excluded_and_reported(self):
		from pos_next.api.invoices import apply_offers
		from pos_next.test_promotions import ITEM_A, _line, _make_rule

		rule = _make_rule(
			"_PNXT_F3_OfferExclusion",
			apply_on="Item Code",
			items=[{"item_code": ITEM_A}],
			rate_or_discount="Discount Percentage",
			price_or_product_discount="Price",
			discount_percentage=15,
		)
		self._rules.append(rule)

		line = _line(self.ctx, ITEM_A, qty=1)
		line.update(
			{
				"pos_package": PACKAGE,
				"pos_package_instance": "pkg-offer-gate",
				"pos_package_role": COMPONENT_ROLE,
			}
		)
		payload = {
			"doctype": "Sales Invoice",
			"pos_profile": self.ctx.pos_profile,
			"customer": self.ctx.customer,
			"items": [line],
		}

		response = apply_offers(invoice_data=json.dumps(payload), selected_offers=json.dumps([rule]))

		self.assertEqual(response.get("package_rows_excluded"), [ITEM_A])
		self.assertEqual(response.get("applied_pricing_rules"), [])
		row = response["items"][0]
		self.assertIn(flt(row.get("discount_percentage") or 0), (0.0,))
		self.assertFalse(row.get("pricing_rules"))


# --- IDR 0-decimal precision, end to end -------------------------------------

ZERO_PRECISION_PACKAGE = "_PNXT IDR Precision Package"
ZERO_PRECISION_PARENT = "_PNXT_PKG_PARENT_IDR"
ZERO_PRECISION_ROTI = "_PNXT_PKG_ROTI"
ZERO_PRECISION_TEH = "_PNXT_PKG_TEH"


def _ensure_zero_precision_package():
	"""Brief fixture: Roti 20k + Teh 10k sold as a 23k package, priced in the
	profile's own selling list, so allocation weights are 20k/10k."""
	_ensure_item(ZERO_PRECISION_PARENT, "PNXT IDR Precision Package", is_stock_item=False)
	_ensure_item(ZERO_PRECISION_ROTI, "PNXT Roti", is_stock_item=True)
	_ensure_item(ZERO_PRECISION_TEH, "PNXT Teh", is_stock_item=True)

	price_list = _ensure_inr_price_list()
	currency = frappe.db.get_value("Company", COMPANY, "default_currency") or "INR"
	for item_code, rate in ((ZERO_PRECISION_ROTI, 20_000.0), (ZERO_PRECISION_TEH, 10_000.0)):
		if frappe.db.exists("Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}):
			continue
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": item_code,
				"price_list": price_list,
				"selling": 1,
				"buying": 0,
				"currency": currency,
				"valid_from": "2020-01-01",
				"price_list_rate": rate,
			}
		).insert(ignore_permissions=True)

	if frappe.db.exists("POS Package", ZERO_PRECISION_PACKAGE):
		return
	vals = frappe.db.get_value("POS Profile", PROFILE, ["company", "warehouse"], as_dict=True)
	frappe.get_doc(
		{
			"doctype": "POS Package",
			"package_name": ZERO_PRECISION_PACKAGE,
			"company": COMPANY,
			"currency": currency,
			"parent_item": ZERO_PRECISION_PARENT,
			"base_price": 23_000.0,
			"items": [
				{"item_code": ZERO_PRECISION_ROTI, "qty": 1},
				{"item_code": ZERO_PRECISION_TEH, "qty": 1},
			],
			"outlets": [{"company": vals.company, "warehouse": vals.warehouse, "enabled": 1}],
		}
	).insert(ignore_permissions=True)


class TestIdrZeroPrecisionAllocation(FrappeTestCase):
	"""Brief case 'presisi IDR 0 desimal': Roti 20k + Teh 10k as a 23k package,
	quoted and validated at rate precision 0. The test site runs at precision 2
	(INR), so the test flips the same two defaults an IDR site carries
	(System Settings currency_precision=0 and number_format '#,###') and
	restores them in a finally block."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_package()
		_ensure_zero_precision_package()
		frappe.db.commit()

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)

	def test_quote_and_invoice_validate_at_zero_precision(self):
		from pos_next.api.packages import quote

		original_precision = frappe.db.get_default("currency_precision")
		original_format = frappe.db.get_default("number_format")
		try:
			frappe.db.set_default("currency_precision", 0)
			frappe.db.set_default("number_format", "#,###")
			frappe.clear_cache()

			self.assertEqual(_rate_precision(), 0, "precision flip must take effect")

			result = quote(ZERO_PRECISION_PACKAGE, [], PROFILE, allocate=True)
			self.assertEqual(flt(result["total"], 0), 23_000)
			self.assertEqual(result["snapshot"]["allocation"]["precision"], 0)

			parent, *components = result["lines"]
			self.assertEqual(parent["rate"], 0)
			self.assertEqual([line["rate"] for line in components], [15_333, 7_667])
			self.assertTrue(all(float(line["rate"]).is_integer() for line in components))

			snapshot = json.dumps(result["snapshot"])
			rows = [
				frappe._dict(
					{
						"item_code": parent["item_code"],
						"qty": 1,
						"rate": 0,
						"price_list_rate": 0,
						"pos_package": ZERO_PRECISION_PACKAGE,
						"pos_package_instance": "pkg-idr-precision",
						"pos_package_role": PARENT_ROLE,
						"pos_package_snapshot": snapshot,
					}
				)
			]
			for line in components:
				rows.append(
					frappe._dict(
						{
							"item_code": line["item_code"],
							"qty": line["qty"],
							"rate": line["rate"],
							"price_list_rate": line["rate"],
							"pos_package": ZERO_PRECISION_PACKAGE,
							"pos_package_instance": "pkg-idr-precision",
							"pos_package_role": COMPONENT_ROLE,
						}
					)
				)
			doc = _doc(rows)
			validate_invoice_packages(doc)

			self.assertEqual(rows[0].rate, 0)
			component_rows = [r for r in rows if r.pos_package_role == COMPONENT_ROLE]
			self.assertEqual([r.rate for r in component_rows], [15_333.0, 7_667.0])
			allocated = sum(flt(r.rate) * flt(r.qty) for r in component_rows)
			self.assertEqual(flt(allocated, 0), 23_000)

			# The payload-side invariant gate must also accept it at precision 0.
			_verify_package_allocation_totals(doc, 0)
		finally:
			frappe.db.set_default("currency_precision", original_precision)
			frappe.db.set_default("number_format", original_format)
			frappe.clear_cache()


# --- B1: qty-1 remainder carrier + offline preview → sync ---------------------

B1_PACKAGE = "_PNXT B1 Carrier Package"
B1_NO_CARRIER_PACKAGE = "_PNXT B1 No Carrier Package"
B1_ROTI = "_PNXT_PKG_B1_ROTI"
B1_TEH = "_PNXT_PKG_B1_TEH"
B1_PARENT = "_PNXT_PKG_PARENT_B1"
B1_NO_CARRIER_PARENT = "_PNXT_PKG_PARENT_B1NC"


def _ensure_b1_packages():
	"""B1 repro fixture: Roti qty1 @20k + Teh qty2 @5k sold as a 25k package.

	The odd per-unit split (16666.67 / 4166.67) only sums to 25k when the qty-1
	Roti line carries the remainder. The no-carrier variant (every line qty 2/3,
	25k at precision 0) can never split exactly — used to pin the fail-closed
	checkout guard for definitions that slipped past save validation (e.g. the
	toggle flipped on after the package was saved).
	"""
	_ensure_item(B1_ROTI, "PNXT B1 Roti", is_stock_item=False)
	_ensure_item(B1_TEH, "PNXT B1 Teh", is_stock_item=False)
	# Non-stock on purpose: these fixture items carry 2020-dated prices on the
	# shared test site, and other suites pick the newest STOCK sales item and
	# insert their own 2020-01-01 price — which would then collide. Correct
	# items created by an earlier run of this fixture.
	frappe.db.set_value("Item", B1_ROTI, "is_stock_item", 0, update_modified=False)
	frappe.db.set_value("Item", B1_TEH, "is_stock_item", 0, update_modified=False)
	_ensure_item(B1_PARENT, "PNXT B1 Carrier Package", is_stock_item=False)
	_ensure_item(B1_NO_CARRIER_PARENT, "PNXT B1 No Carrier Package", is_stock_item=False)

	price_list = _ensure_inr_price_list()
	currency = frappe.db.get_value("Company", COMPANY, "default_currency") or "INR"
	for item_code, rate in ((B1_ROTI, 20_000.0), (B1_TEH, 5_000.0)):
		if frappe.db.exists("Item Price", {"item_code": item_code, "price_list": price_list, "selling": 1}):
			continue
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": item_code,
				"price_list": price_list,
				"selling": 1,
				"buying": 0,
				"currency": currency,
				"valid_from": "2020-01-01",
				"price_list_rate": rate,
			}
		).insert(ignore_permissions=True)

	vals = frappe.db.get_value("POS Profile", PROFILE, ["company", "warehouse"], as_dict=True)
	for name, parent, items, base_price in (
		(B1_PACKAGE, B1_PARENT, [{"item_code": B1_ROTI, "qty": 1}, {"item_code": B1_TEH, "qty": 2}], 25_000.0),
		(
			# Every line a multiple, and the leftover over the qty-3 last line
			# does not divide (25000 - 3572 = 21428; 21428/3 = 7142.67).
			B1_NO_CARRIER_PACKAGE,
			B1_NO_CARRIER_PARENT,
			[{"item_code": B1_TEH, "qty": 2}, {"item_code": B1_ROTI, "qty": 3}],
			25_000.0,
		),
	):
		if frappe.db.exists("POS Package", name):
			continue
		frappe.get_doc(
			{
				"doctype": "POS Package",
				"package_name": name,
				"company": COMPANY,
				"currency": currency,
				"parent_item": parent,
				"base_price": base_price,
				"items": items,
				"outlets": [{"company": vals.company, "warehouse": vals.warehouse, "enabled": 1}],
			}
		).insert(ignore_permissions=True)


class TestB1CarrierAndOfflineSync(FrappeTestCase):
	"""The review repro: [qty1 @20000, qty2 @5000] @25000 used to split
	[16667, 4166] (Σ 24999) because the qty>1 line carried the remainder. The
	qty-1 line carries it now, and the sync-side invariant gate
	(_verify_package_allocation_totals) accepts the exact same preview."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_set_allocation(0)
		_ensure_package()
		_ensure_zero_precision_package()
		_ensure_b1_packages()
		frappe.db.commit()

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)

	@staticmethod
	def _zero_precision():
		original_precision = frappe.db.get_default("currency_precision")
		original_format = frappe.db.get_default("number_format")
		frappe.db.set_default("currency_precision", 0)
		frappe.db.set_default("number_format", "#,###")
		frappe.clear_cache()
		return original_precision, original_format

	def _preview_rows(self, result, package):
		parent, *components = result["lines"]
		rows = [
			frappe._dict(
				{
					"item_code": parent["item_code"],
					"qty": 1,
					"rate": 0,
					"price_list_rate": 0,
					"pos_package": package,
					"pos_package_instance": "pkg-b1",
					"pos_package_role": PARENT_ROLE,
					"pos_package_snapshot": json.dumps(result["snapshot"]),
				}
			)
		]
		for line in components:
			rows.append(
				frappe._dict(
					{
						"item_code": line["item_code"],
						"qty": line["qty"],
						"rate": line["rate"],
						"price_list_rate": line["rate"],
						"pos_package": package,
						"pos_package_instance": "pkg-b1",
						"pos_package_role": COMPONENT_ROLE,
					}
				)
			)
		return rows

	def test_b1_repro_splits_exactly_and_sync_gate_accepts_the_preview(self):
		original_precision, original_format = self._zero_precision()
		try:
			result = quote(B1_PACKAGE, [], PROFILE, allocate=True)
			self.assertEqual(flt(result["total"], 0), 25_000)

			parent, *components = result["lines"]
			self.assertEqual(parent["rate"], 0)
			self.assertEqual([line["rate"] for line in components], [16_666, 4_167])
			allocated = sum(flt(line["rate"]) * flt(line["qty"]) for line in components)
			self.assertEqual(flt(allocated, 0), 25_000)

			# Offline preview → sync: the payload-side invariant gate and the
			# server re-quote must both accept the same rates (L2).
			doc = _doc(self._preview_rows(result, B1_PACKAGE))
			_verify_package_allocation_totals(doc, 0)
			validate_invoice_packages(doc)

			self.assertEqual(
				[r.rate for r in doc["items"] if r.pos_package_role == COMPONENT_ROLE],
				[16_666.0, 4_167.0],
			)
		finally:
			frappe.db.set_default("currency_precision", original_precision)
			frappe.db.set_default("number_format", original_format)
			frappe.clear_cache()

	def test_no_carrier_definition_fails_closed_at_invoice_validate(self):
		"""Boundary kept as a truthful backstop: a package whose every line is a
		multiple (saved while the toggle was off, so POS Package validate passed)
		cannot be split exactly when allocation turns on — the checkout guard
		must refuse with the carrier hint instead of booking a miscount."""
		original_precision, original_format = self._zero_precision()
		try:
			result = quote(B1_NO_CARRIER_PACKAGE, [], PROFILE, allocate=True)
			self.assertTrue(result["snapshot"].get("allocation"))
			doc = _doc(self._preview_rows(result, B1_NO_CARRIER_PACKAGE))

			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_invoice_packages(doc)
			self.assertIn("quantity 1", str(ctx.exception))
		finally:
			frappe.db.set_default("currency_precision", original_precision)
			frappe.db.set_default("number_format", original_format)
			frappe.clear_cache()


class TestValidateSingleToggleReadAndPostingDate(unittest.TestCase):
	"""M2: one toggle read per validate_invoice_packages however many package
	instances the invoice carries. M3: the invoice's posting_date prices the
	allocation weights (backdated invoices / delayed offline syncs)."""

	@classmethod
	def setUpClass(cls):
		_ensure_package()
		frappe.db.commit()
		cls.pkg = next(p for p in get_packages(PROFILE) if p["name"] == PACKAGE)

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)

	def _group_rows(self, instance):
		result = quote(
			PACKAGE,
			[
				{
					"group_key": "accessory",
					"options": [{"option_id": _option_id(self.pkg, COMPONENT_A), "qty": 1}],
				}
			],
			PROFILE,
			allocate=True,
		)
		rows = []
		for line in result["lines"]:
			rows.append(
				frappe._dict(
					{
						"item_code": line["item_code"],
						"qty": line["qty"],
						"rate": line["rate"],
						"price_list_rate": line["rate"],
						"pos_package": PACKAGE,
						"pos_package_instance": instance,
						"pos_package_role": line["role"],
						"pos_package_snapshot": (
							json.dumps(result["snapshot"]) if line["role"] == PARENT_ROLE else None
						),
					}
				)
			)
		return rows

	def test_toggle_is_read_once_for_two_package_instances(self):
		doc = _doc(self._group_rows("pkg-m2-a") + self._group_rows("pkg-m2-b"))
		calls = []

		def _counting_toggle():
			calls.append(1)
			return True

		with patch("pos_next.api.packages._package_allocation_enabled", side_effect=_counting_toggle):
			validate_invoice_packages(doc)

		self.assertEqual(len(calls), 1)

	def test_posting_date_flows_into_the_allocation_weights(self):
		doc = _doc(self._group_rows("pkg-m3"), posting_date="2026-01-15")
		captured = {}

		def _fetch(item_codes, price_list, transaction_date=None, selling=None):
			captured["date"] = transaction_date
			return {}

		with patch("pos_next.api.packages._fetch_uom_prices_map", side_effect=_fetch):
			validate_invoice_packages(doc)

		self.assertEqual(captured.get("date"), "2026-01-15")


class TestPricingRuleCannotTouchPackageRows(FrappeTestCase):
	"""H3: an item-level Min/Max Pricing Rule (whose bulk pass
	apply_min_max_price_discounts runs as a validate hook AFTER
	validate_invoice_packages) must not re-price a component row. The fix
	clears the rows' pricing_rules attribution, so the later hook finds no
	target on package rows — while a standalone row under the same rule is
	still discounted, proving the rule itself is live."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_package()
		frappe.db.commit()
		cls.company = COMPANY
		cls.warehouse = frappe.db.get_value("POS Profile", PROFILE, "warehouse")
		cls.pkg = next(p for p in get_packages(PROFILE) if p["name"] == PACKAGE)

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)
		for name in frappe.get_all(
			"Pricing Rule", filters={"title": "_PNXT H3 Component Rule"}, pluck="name"
		):
			frappe.delete_doc("Pricing Rule", name, force=True, ignore_permissions=True)
		frappe.db.commit()

	def _seed_rule(self):
		existing = frappe.db.get_value("Pricing Rule", {"title": "_PNXT H3 Component Rule"}, "name")
		if existing:
			frappe.delete_doc("Pricing Rule", existing, force=True, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": "_PNXT H3 Component Rule",
				"selling": 1,
				"company": self.company,
				"currency": frappe.db.get_value("Company", self.company, "default_currency"),
				"apply_on": "Item Code",
				"items": [{"item_code": COMPONENT_A}, {"item_code": HEADPHONE}],
				"price_or_product_discount": "Price",
				"rate_or_discount": "Discount Percentage",
				"apply_discount_on_price": "Max",
				"discount_percentage": 10,
				"valid_from": frappe.utils.nowdate(),
				"min_qty": 1,
			}
		).insert(ignore_permissions=True)
		frappe.db.commit()

	def test_component_rows_survive_an_item_pricing_rule(self):
		self._seed_rule()

		result = quote(
			PACKAGE,
			[
				{
					"group_key": "accessory",
					"options": [{"option_id": _option_id(self.pkg, COMPONENT_A), "qty": 1}],
				}
			],
			PROFILE,
			allocate=True,
		)
		self.assertTrue(result["snapshot"].get("allocation"))

		inv = frappe.new_doc("Sales Invoice")
		inv.customer = frappe.db.get_value("Customer", {"disabled": 0}, "name")
		inv.company = self.company
		inv.pos_profile = PROFILE
		inv.is_pos = 0
		inv.set_posting_time = 1
		inv.currency = frappe.db.get_value("Company", self.company, "default_currency")
		inv.selling_price_list = frappe.db.get_value("POS Profile", PROFILE, "selling_price_list")
		for line in result["lines"]:
			inv.append(
				"items",
				{
					"item_code": line["item_code"],
					"qty": line["qty"],
					"rate": line["rate"],
					"price_list_rate": line["rate"],
					"uom": line.get("uom") or "Nos",
					"warehouse": self.warehouse,
					"pos_package": PACKAGE,
					"pos_package_instance": "pkg-h3-gate",
					"pos_package_role": line["role"],
					"pos_package_snapshot": (
						json.dumps(result["snapshot"]) if line["role"] == PARENT_ROLE else None
					),
				},
			)
		# A standalone row under the same rule: proof the rule fires at all.
		inv.append(
			"items",
			{
				"item_code": HEADPHONE,
				"qty": 1,
				"rate": 500_000,
				"price_list_rate": 500_000,
				"uom": "Nos",
				"warehouse": self.warehouse,
			},
		)
		inv.set_missing_values()
		# The leak exists exactly on documents where ERPNext's own engine runs.
		inv.ignore_pricing_rule = 0
		inv.flags.ignore_validate = False

		inv.run_method("validate")

		for row in inv.items:
			if row.get("pos_package_role"):
				self.assertFalse(row.get("pricing_rules"), row.item_code)
		component_rows = [r for r in inv.items if r.pos_package_role == COMPONENT_ROLE]
		allocated = sum(flt(r.rate) * flt(r.qty) for r in component_rows)
		self.assertEqual(flt(allocated, 2), flt(result["total"], 2))
		# The rule stayed live: the standalone row carries its discount.
		self.assertEqual(flt(inv.items[-1].discount_percentage), 10.0)


class TestAllocationCarrierValidation(FrappeTestCase):
	"""B1 guard on the POS Package doctype itself (save-time, not checkout):
	while allocation is on, a definition whose every line is a multiple is
	rejected with a clear message; a qty-1 line (included item or option with
	Qty Per Unit 1) makes it saveable. With the toggle off the legacy shape
	stays untouched."""

	PACKAGE_NAME = "_PNXT Carrier Guard Package"
	PARENT = "_PNXT_PKG_PARENT_CARRIER_GUARD"

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		_ensure_package()
		_ensure_item(cls.PARENT, "PNXT Carrier Guard Package", is_stock_item=False)
		frappe.db.commit()

	def setUp(self):
		_set_allocation(1)

	def tearDown(self):
		_set_allocation(0)
		frappe.db.delete("POS Package", {"package_name": self.PACKAGE_NAME})
		frappe.db.commit()

	def _make(self, items=None, groups=None, options=None):
		vals = frappe.db.get_value("POS Profile", PROFILE, ["company", "warehouse"], as_dict=True)
		return frappe.get_doc(
			{
				"doctype": "POS Package",
				"package_name": self.PACKAGE_NAME,
				"company": COMPANY,
				"currency": frappe.db.get_value("Company", COMPANY, "default_currency"),
				"parent_item": self.PARENT,
				"base_price": 1_000.0,
				"items": items or [],
				"groups": groups or [],
				"options": options or [],
				"outlets": [{"company": vals.company, "warehouse": vals.warehouse, "enabled": 1}],
			}
		)

	def test_all_multiples_are_rejected_with_a_carrier_hint(self):
		doc = self._make(items=[{"item_code": COMPONENT_A, "qty": 2}])

		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.insert(ignore_permissions=True)

		self.assertIn("quantity 1", str(ctx.exception))

	def test_a_qty1_included_item_makes_it_saveable(self):
		doc = self._make(
			items=[{"item_code": COMPONENT_A, "qty": 2}, {"item_code": COMPONENT_B, "qty": 1}]
		)
		doc.insert(ignore_permissions=True)

		self.assertTrue(doc.name)

	def test_qty_per_unit_one_option_is_a_carrier(self):
		doc = self._make(
			groups=[{"group_key": "carrier_group", "label": "Carrier", "min_qty": 1, "max_qty": 1}],
			options=[
				{"group_key": "carrier_group", "item_code": COMPONENT_A, "qty_per_unit": 1}
			],
		)
		doc.insert(ignore_permissions=True)

		self.assertTrue(doc.name)

	def test_toggle_off_keeps_all_multiples_saveable(self):
		_set_allocation(0)
		doc = self._make(items=[{"item_code": COMPONENT_A, "qty": 2}])
		doc.insert(ignore_permissions=True)

		self.assertTrue(doc.name)


if __name__ == "__main__":
	unittest.main()
