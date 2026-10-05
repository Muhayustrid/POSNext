# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Fase 3 tests: SEC-23 package-allocation gate, offer exclusion, return
doctype links, invoice-level discount distribution and parent-row tax/stock.

Run via pos_next/_pn_run_tests.py
pos_next.api.test_package_allocation_gate
"""

import json
import unittest

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

import pos_next
from pos_next.api.invoices import (
	_is_package_row,
	_validate_item_rates,
	_verify_package_allocation_totals,
)
from pos_next.api.packages import _validate_return_packages
from pos_next.api.test_packages import (
	BACKPACK as COMPONENT_A,
)

# Reuse the package fixture namespace from test_packages.
from pos_next.api.test_packages import (
	BASE_PRICE,
	COMPANY,
	COMPONENT_ROLE,
	PACKAGE,
	PARENT_ITEM,
	PARENT_ROLE,
	PROFILE,
	_ensure_package,
	_option_id,
)
from pos_next.api.test_packages import (
	LAPTOP as COMPONENT_B,
)
from pos_next.tests._posi_test_utils import _set_invoice_type

ALLOCATION_FLAG = "enable_pos_package_allocation"


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

	def _insert_invoice(self, doctype, rows, instance="pkg-ret-gate"):
		parent = frappe.get_doc(
			{
				"doctype": doctype,
				"company": self.company,
				"customer": frappe.db.get_value("Customer", {"disabled": 0}, "name"),
				"pos_profile": PROFILE,
				"is_pos": 1,
				"update_stock": 0,
				"posting_date": frappe.utils.nowdate(),
			}
		)
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
			parent, inserted = self._insert_invoice(doctype, rows)
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


if __name__ == "__main__":
	unittest.main()
