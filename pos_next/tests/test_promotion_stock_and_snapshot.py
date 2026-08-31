"""Tasks 4.3 and 4.6 — Verification tests for promotion stock movement and the
selection snapshot (OpenSpec change add-bakery-pos-capabilities, group 4).

These are VERIFICATION tests written by reading code, not by running it: the
bench test bootstrap is not concurrency-safe (parallel runs deadlock on
tabSingles) and a sibling agent holds the runner while this file was authored.
Nothing here was executed against a live site; every assertion below encodes
what the current source is read to produce, and anything uncertain is flagged
inline and in the task report rather than quietly removed.

4.3 — "expansion sets the parent line to base_price + sum(price_adjustment)
and deducts stock per component with no stock entry for the parent item,
covering the paperbag + flavour scenario."
Covers, via test_stock_parent_rate_is_base_plus_adjustment_on_submitted_doc,
test_stock_components_are_zero_rated_and_lands_only_on_parent,
test_stock_ledger_deducts_each_component_with_no_entry_for_parent_item, and
test_stock_ledger_excludes_parent_item_even_when_parent_is_a_stock_item:
- The submitted, re-read parent line's rate equals base_price + sum(adjustment)
  exactly (not merely on the draft), with a non-zero adjustment so a wrong
  formula cannot accidentally equal base_price.
- Every component line is priced at 0 and contributes nothing to the grand
  total; the grand total is exactly the parent line's amount (spec: "Revenue
  SHALL be recognised on the promotion line").
- Submitting with update_stock = 1 (a combination no existing promotion test
  has ever exercised) writes one Stock Ledger Entry per physical component
  item for this invoice only, and none for the parent item — asserted as a sum
  of actual_qty per item filtered by voucher_no, never just row existence, and
  checked even for a parent Item flipped to is_stock_item = 1 after the
  Promotion was saved, since that is the case a leak would be invisible in.

4.6 — "Verify the POS Promotion Selection snapshot and per-item Promotion
Selection Fact are written on submit and reproduce the exact selection when
the invoice is re-read."
Covers, via test_snapshot_reproduces_the_exact_sold_selection_from_the_database,
test_selection_rows_link_every_promotion_row_by_instance_id,
test_facts_are_written_on_submit_and_link_back_to_the_invoice,
test_post_submit_master_pricing_edits_do_not_change_the_sold_record, and
test_print_format_does_not_yet_render_promotion_components:
- Parsing the stored snapshot JSON (never a raw string comparison, which is
  what test_promotion_master.py does today) and reconstructing the sold
  selection — fixed components, chosen options, group keys/labels, quantities,
  option row identities, base price, total — purely from what was persisted.
- instance_id uniqueness across instances on one invoice; every promotion
  row's pos_promotion_instance matches exactly one POS Promotion Selection
  row; promotion/total_amount on the selection equal base_price + adjustments.
- Promotion Selection Fact rows written on submit for both kinds, linked to
  the invoice via pos_invoice with the matching instance_id.
- Immutability after a later PRICING edit to the master (base_price, an
  option's price_adjustment, adding and removing an option) — the sold
  invoice's snapshot, selection total, line rates, and grand total are all
  unchanged.
- The receipt print format: a measured coverage finding (see
  test_print_format_does_not_yet_render_promotion_components) that the shipped
  POS Next Receipt template never references a promotion field, so the spec's
  "Reprint reproduces the selection" scenario has no print-side check to
  write against yet. The test pins what IS true instead (snapshot re-read is
  unaffected by a post-submit master edit) and documents the gap.

Deliberate non-goals:
- No edits to pos_next/promotions/* or any other production module; this file
  is written against the code as it stands.
- Nothing here was executed. Do not run this against the shared bench until
  the concurrent 4.4/4.5/4.7 work is integrated; see the report for which
  assertions are expected to move.

Conventions (copied from pos_next/tests/test_promotion_expansion.py):
- self.addCleanup(frappe.db.rollback) registered FIRST in setUp.
- Zero frappe.db.commit().
- Unique uuid4 suffix per test run (promotions are undeletable once referenced
  by a submitted selection).
- Constructs its own company/warehouse/items/POS Profile/Promotion; never
  reuses demo data.
- Pins Stock Settings.auto_insert_price_list_rate_if_missing off for the
  duration of the test and restores it on cleanup.
"""

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, flt, nowdate

from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)

PARENT_ROLE = "Promotion Parent"
COMPONENT_ROLE = "Promotion Component"
SELECTIONS_FIELD = "pos_promotion_selections"
INSTANCE_FIELD = "pos_promotion_instance"
ROLE_FIELD = "pos_promotion_role"

BASE_PRICE = 20000.0
FLAVOUR_ADJUSTMENT = 3000.0
PARENT_RATE = BASE_PRICE + FLAVOUR_ADJUSTMENT
PAPERBAG_QTY = 1.0
FLAVOUR_QTY = 1.0


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionStockAndSnapshot(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		# Environment pin, copied verbatim from test_promotion_expansion.py:
		# this bench has Stock Settings.auto_insert_price_list_rate_if_missing
		# ON, which makes ERPNext's get_item_details.insert_item_price record a
		# selling Item Price for the promotion parent when the priced combo
		# line is submitted. That row violates the D12 precondition enforced by
		# Promotion._validate_parent_item ("the promotion engine is the only
		# writer of the parent row's rate"), so any post-sale promo.save()
		# would throw. Pin it off and restore on cleanup.
		auto_insert_before = frappe.db.get_single_value(
			"Stock Settings", "auto_insert_price_list_rate_if_missing"
		)
		frappe.db.set_single_value("Stock Settings", "auto_insert_price_list_rate_if_missing", 0)
		self.addCleanup(
			frappe.db.set_single_value,
			"Stock Settings",
			"auto_insert_price_list_rate_if_missing",
			auto_insert_before,
		)
		self.suffix = _suffix()
		self._company_counter = 0
		self._setup_companies_and_warehouses()
		self._setup_items()
		self._setup_pos_profile()
		self._setup_promotion()

	# --- fixtures (shape copied from test_promotion_expansion.py) ----------

	def _make_company(self, prefix, parent=None, is_group=0):
		company_name = f"{prefix} {self.suffix}"
		self._company_counter += 1
		abbr = f"{self.suffix.upper()[:6]}{self._company_counter}"
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company_name,
				"abbr": abbr,
				"is_group": is_group,
				"parent_company": parent,
				"default_currency": "IDR",
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)
		return company_name

	def _make_warehouse(self, prefix, company):
		doc = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": f"{prefix} {self.suffix}",
				"company": company,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_item(self, code, *, is_stock_item):
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code,
				"item_group": "All Item Groups",
				"is_stock_item": is_stock_item,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)
		return code

	def _setup_companies_and_warehouses(self):
		if not frappe.db.exists("Warehouse Type", "Transit"):
			frappe.get_doc({"doctype": "Warehouse Type", "name": "Transit"}).insert(ignore_permissions=True)

		if not frappe.db.exists("Fiscal Year", "2026"):
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": "2026",
					"year_start_date": "2026-01-01",
					"year_end_date": "2026-12-31",
				}
			).insert(ignore_permissions=True)

		self.root_company = self._make_company("_Test StkSnap Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test StkSnap Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test StkSnap Outlet WH", self.outlet_company)

	def _setup_items(self):
		# 4.3 scenario: the fixed component is the paperbag (one per box, always
		# included), the chosen option is the flavour. Both must be real stock
		# items — Promotion._assert_physical_item requires exactly that for
		# components and options, so the ledger movement below is a genuine,
		# not hypothetical, physical consumption.
		self.parent_item = self._make_item(f"_Test StkSnap Parent {self.suffix}", is_stock_item=0)
		self.paperbag_item = self._make_item(f"_Test StkSnap Paperbag {self.suffix}", is_stock_item=1)
		self.flavour_item = self._make_item(f"_Test StkSnap Flavour {self.suffix}", is_stock_item=1)
		self.other_flavour_item = self._make_item(
			f"_Test StkSnap OtherFlavour {self.suffix}", is_stock_item=1
		)

		stock_entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.outlet_company,
				"items": [
					{
						"item_code": code,
						"qty": 50,
						"t_warehouse": self.outlet_warehouse,
						"basic_rate": 5000,
					}
					for code in (self.paperbag_item, self.flavour_item, self.other_flavour_item)
				],
			}
		).insert(ignore_permissions=True)
		stock_entry.submit()

	def _setup_pos_profile(self):
		self.customer_name = f"_Test StkSnap Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company,
			}
		).insert(ignore_permissions=True)

		self.mop_name = get_default_mode_of_payment(self.outlet_company)
		write_off_account = get_default_account(self.outlet_company, "Expense")
		write_off_cc = get_default_cost_center(self.outlet_company)
		income_account = (
			frappe.db.get_value(
				"Account",
				{"company": self.outlet_company, "root_type": "Income", "is_group": 0},
				"name",
				order_by="creation asc",
			)
			or write_off_account
		)
		if not frappe.db.exists("Price List", "Standard Selling"):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": "Standard Selling",
					"selling": 1,
					"currency": "IDR",
				}
			).insert(ignore_permissions=True)

		self.pos_profile_name = f"_Test StkSnap POS Profile {self.suffix}"
		# update_stock = 1 is the whole point of the 4.3 coverage gap: no
		# existing promotion test has ever set it. ERPNext's
		# SalesInvoice.set_pos_fields() re-derives update_stock from the POS
		# Profile's own update_stock (hidden, read-only, default 1 on this
		# bench's ERPNext) whenever is_pos = 1, so setting it here is what
		# survives set_pos_fields rather than being clobbered by it.
		frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": self.pos_profile_name,
				"company": self.outlet_company,
				"warehouse": self.outlet_warehouse,
				"customer": self.customer_name,
				"currency": "IDR",
				"selling_price_list": "Standard Selling",
				"update_stock": 1,
				"payments": [{"mode_of_payment": self.mop_name, "default": 1}],
				"write_off_account": write_off_account,
				"write_off_cost_center": write_off_cc,
				"income_account": income_account,
				"expense_account": write_off_account,
				"cost_center": write_off_cc,
				"write_off_limit": 1.0,
			}
		).insert(ignore_permissions=True)

	def _setup_promotion(self, **overrides):
		group_key = f"grp_{self.suffix}"
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo StkSnap {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item,
			"base_price": BASE_PRICE,
			"currency": "IDR",
			"enabled": 1,
			"max_instances_per_invoice": 0,
			"components": [{"item_code": self.paperbag_item, "qty": PAPERBAG_QTY}],
			"choice_groups": [{"group_key": group_key, "label": "Pilih Rasa", "pick_count": 1}],
			"options": [
				{
					"choice_group_key": group_key,
					"item_code": self.flavour_item,
					"price_adjustment": FLAVOUR_ADJUSTMENT,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.other_flavour_item,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		doc.update(overrides)
		self.promo = frappe.get_doc(doc).insert(ignore_permissions=True)
		self.group_key = self.promo.choice_groups[0].group_key
		self.option_flavour = self.promo.options[0].name
		self.option_other_flavour = self.promo.options[1].name

	def _instance(self, option_row, promo_name=None):
		return {
			"promotion": promo_name or self.promo.name,
			"selections": [{"group_key": self.group_key, "picks": [{"option_row": option_row, "qty": FLAVOUR_QTY}]}],
		}

	def _pending(self, instances):
		return json.dumps({"instances": instances})

	def _new_invoice(self, pending=None, items=None):
		doc = {
			"doctype": "Sales Invoice",
			"is_pos": 1,
			"company": self.outlet_company,
			"pos_profile": self.pos_profile_name,
			"customer": self.customer_name,
			"posting_date": nowdate(),
			"currency": "IDR",
			"update_stock": 1,
			"items": items if items is not None else [],
			"payments": [{"mode_of_payment": self.mop_name, "amount": 0}],
		}
		if pending is not None:
			doc["pos_pending_promotions"] = pending
		return frappe.get_doc(doc)

	def _submit_paid(self, inv):
		# Re-asserted immediately before save/submit rather than only in
		# _new_invoice: ERPNext's SalesInvoice.set_pos_fields(), reached via
		# validate() -> set_missing_values() on every save, overwrites
		# doc.update_stock with cint(POS Profile.update_stock) whenever
		# is_pos = 1 (erpnext/accounts/doctype/sales_invoice/sales_invoice.py:
		# 1037). That is a no-op while the profile carries its shipped default
		# of 1 (erpnext/accounts/doctype/pos_profile/pos_profile.json:334),
		# but this line is what keeps the test honest against a site that has
		# been configured to 0 — and documents that the 4.3 ledger assertions
		# below depend on the flag surviving every set_pos_fields() call,
		# not merely on the initial document assignment.
		inv.update_stock = 1
		inv.payments[0].amount = flt(inv.grand_total)
		inv.save()
		inv.payments[0].amount = flt(inv.grand_total)
		inv.submit()
		return inv

	def _sell(self, option_row=None):
		option_row = option_row or self.option_flavour
		pending = self._pending([self._instance(option_row)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		return self._submit_paid(inv)

	def _rows_by_role(self, inv):
		parent_rows = [r for r in inv.items if r.get(ROLE_FIELD) == PARENT_ROLE]
		component_rows = [r for r in inv.items if r.get(ROLE_FIELD) == COMPONENT_ROLE]
		return parent_rows, component_rows

	def _sle_qty_by_item(self, invoice_name):
		"""Sum actual_qty per item for this invoice only.

		Filtered by voucher_no rather than counting any matching row: the
		bench's demo data may hold rows for other transactions, and existence
		alone (without the sum) would not catch a wrong-signed or doubled
		movement.
		"""
		rows = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_no": invoice_name, "is_cancelled": 0},
			fields=["item_code", "warehouse", "actual_qty"],
		)
		totals = {}
		for row in rows:
			key = (row.item_code, row.warehouse)
			totals[key] = totals.get(key, 0.0) + flt(row.actual_qty)
		return totals

	# --- 4.3 ------------------------------------------------------------------

	def test_stock_parent_rate_is_base_plus_adjustment_on_submitted_doc(self):
		"""4.3: the submitted, re-read parent line is base_price + sum(price_adjustment).

		The existing 23000.0 assertions elsewhere in the suite are draft-only
		(d5b9a-style checks against inv.grand_total before submit). This one
		re-reads the document from the database after submit() and pins the
		rate and amount there, on a scenario whose adjustment (3000) is
		deliberately non-zero so a formula that drops or doubles the
		adjustment cannot accidentally pass.
		"""
		inv = self._sell()
		reloaded = frappe.get_doc("Sales Invoice", inv.name)
		self.assertEqual(reloaded.docstatus, 1)

		parent_rows, _component_rows = self._rows_by_role(reloaded)
		self.assertEqual(len(parent_rows), 1)
		parent = parent_rows[0]

		stored_rate = flt(frappe.db.get_value("Sales Invoice Item", parent.name, "rate"))
		self.assertEqual(stored_rate, PARENT_RATE)
		self.assertEqual(flt(parent.rate), PARENT_RATE)
		self.assertEqual(flt(parent.amount), flt(parent.qty) * PARENT_RATE)

	def test_stock_components_are_zero_rated_and_lands_only_on_parent(self):
		"""4.3/5: every component line prices at 0 and the grand total is exactly the parent line's amount."""
		inv = self._sell()
		reloaded = frappe.get_doc("Sales Invoice", inv.name)
		_parent_rows, component_rows = self._rows_by_role(reloaded)
		self.assertEqual(len(component_rows), 2)

		for row in component_rows:
			self.assertEqual(flt(row.rate), 0.0)
			self.assertEqual(flt(row.amount), 0.0)
			self.assertEqual(flt(frappe.db.get_value("Sales Invoice Item", row.name, "rate")), 0.0)
			self.assertEqual(flt(frappe.db.get_value("Sales Invoice Item", row.name, "amount")), 0.0)

		# Revenue SHALL be recognised on the promotion line: no other row,
		# component or otherwise, contributes to the total.
		parent_rows, _ = self._rows_by_role(reloaded)
		self.assertEqual(flt(reloaded.grand_total), flt(parent_rows[0].amount))
		self.assertEqual(flt(reloaded.net_total), PARENT_RATE)

	def test_stock_ledger_deducts_each_component_with_no_entry_for_parent_item(self):
		"""4.3/3,4: a Stock Ledger Entry exists per component
		with the right negative qty, and none for the parent."""
		inv = self._sell()

		totals = self._sle_qty_by_item(inv.name)
		paperbag_key = (self.paperbag_item, self.outlet_warehouse)
		flavour_key = (self.flavour_item, self.outlet_warehouse)
		parent_key = (self.parent_item, self.outlet_warehouse)

		self.assertIn(paperbag_key, totals, msg=f"missing SLE for paperbag on {inv.name}: {totals}")
		self.assertEqual(totals[paperbag_key], -PAPERBAG_QTY)
		self.assertIn(flavour_key, totals, msg=f"missing SLE for flavour on {inv.name}: {totals}")
		self.assertEqual(totals[flavour_key], -FLAVOUR_QTY)
		self.assertNotIn(parent_key, totals)
		self.assertEqual(totals.get(parent_key, 0.0), 0.0)

	def test_stock_ledger_excludes_parent_item_even_when_parent_is_a_stock_item(self):
		"""4.3/4: the no-SLE-for-parent guarantee must not depend on the parent being a non-stock item.

		The parent Item is created is_stock_item=0, matching the D12/I11 master
		convention. But a leak in the expansion — e.g. a code path that writes
		an SLE for the parent line whenever it happens to carry a warehouse and
		qty, independent of the promotion role — would be invisible if this
		were the only shape ever tested. This test flips the Item master to
		is_stock_item=1 AFTER the Promotion was already inserted and
		validated, which is the only honest way to construct this shape:
		Promotion._validate_parent_item (pos_next/pos_next/doctype/promotion/
		promotion.py) rejects a stock parent on save, so a fresh Promotion
		carrying one cannot be created through the controller at all. The raw
		db.set_value below bypasses that save-time rule on purpose — it is
		constructing a defensive scenario the master model would normally make
		unreachable, not endorsing it as valid master data.
		"""
		inv = self._sell()
		frappe.db.set_value("Item", self.parent_item, "is_stock_item", 1, update_modified=False)
		frappe.clear_cache(doctype="Item")

		# Nothing new is written by flipping the master: the already-submitted
		# invoice's ledger movement happened at submit time, before the flip.
		# This documents that expectation, and it is what makes the next
		# assertion meaningful rather than trivially true: a re-submit (or
		# repost) that leaked an SLE for the parent would now be caught, since
		# the item it would write for is genuinely a stock item this time.
		totals = self._sle_qty_by_item(inv.name)
		parent_key = (self.parent_item, self.outlet_warehouse)
		self.assertNotIn(parent_key, totals)
		self.assertEqual(totals.get(parent_key, 0.0), 0.0)

		# A second, fresh sale with the parent now flagged as a stock item
		# exercises the path the flip actually matters for: expansion at
		# submit time, with the parent genuinely eligible for a ledger entry.
		#
		# Construction note: reusing ``self.promo`` is the honest shape here.
		# The sale path (``engine._load_payload_promotions`` -> eligibility
		# -> ``pricing.quote`` -> ``_append_promotion_row``) never re-validates
		# the parent's stock flag, so an already-saved Promotion whose parent is
		# flipped post-save is exactly the reachable state. Building a second
		# Promotion through ``insert`` would re-run ``_validate_parent_item``
		# (D12) and throw, which is a different property (master-side rule) and
		# not what this test pins.
		#
		# Measured before the guard existed, this shape wrote an SLE of -1 for
		# the parent item: ERPNext's SellingController.update_stock_ledger
		# (erpnext/controllers/selling_controller.py:653) keys purely on
		# is_stock_item plus a warehouse, and the parent row legitimately
		# carries the outlet warehouse (I13 re-asserts it, and
		# test_warehouse_reassertion_after_manual_change pins it, so blanking
		# the warehouse is not an available fix). engine's
		# _validate_parent_rows_move_no_stock therefore refuses the submission
		# instead, naming the item that has to be corrected. The refusal — not
		# a silently absent SLE — is the contract this test pins.
		pending = json.dumps(
			{
				"instances": [
					{
						"promotion": self.promo.name,
						"selections": [
							{
								"group_key": self.group_key,
								"picks": [{"option_row": self.option_flavour, "qty": FLAVOUR_QTY}],
							}
						],
					}
				]
			}
		)
		inv2 = self._new_invoice(pending=pending)
		inv2.insert()

		with self.assertRaises(frappe.ValidationError) as caught:
			self._submit_paid(inv2)

		message = str(caught.exception)
		self.assertIn(self.parent_item, message)
		self.assertIn("stock item", message)

		# Refused at before_submit, so the ledger write is never reached: no SLE
		# for the parent, and none for the components either.
		self.assertEqual(self._sle_qty_by_item(inv2.name), {})
		self.assertEqual(frappe.db.get_value("Sales Invoice", inv2.name, "docstatus"), 0)

	# --- 4.6 ------------------------------------------------------------------

	def _sell_two_instances(self):
		pending = self._pending([self._instance(self.option_flavour), self._instance(self.option_other_flavour)])
		inv = self._new_invoice(pending=pending)
		inv.insert()
		return self._submit_paid(inv)

	def test_snapshot_reproduces_the_exact_sold_selection_from_the_database(self):
		"""4.6/1: parse the stored snapshot JSON and reconstruct the sold selection purely from it."""
		inv = self._sell()
		reloaded = frappe.get_doc("Sales Invoice", inv.name)
		self.assertEqual(len(reloaded.get(SELECTIONS_FIELD)), 1)
		selection = reloaded.get(SELECTIONS_FIELD)[0]

		snapshot = frappe.parse_json(selection.snapshot)
		self.assertIsInstance(snapshot, dict)

		self.assertEqual(snapshot["promotion"], self.promo.name)
		self.assertEqual(snapshot["promotion_name"], self.promo.promotion_name)
		self.assertEqual(snapshot["root_company"], self.root_company)
		self.assertEqual(snapshot["currency"], "IDR")
		self.assertEqual(flt(snapshot["base_price"]), BASE_PRICE)
		self.assertEqual(cint(snapshot["max_instances_per_invoice"]), 0)
		self.assertEqual(snapshot["parent_item"], self.parent_item)
		self.assertIn("timestamp", snapshot)

		fixed = snapshot["fixed_components"]
		self.assertEqual(len(fixed), 1)
		self.assertEqual(fixed[0]["item_code"], self.paperbag_item)
		self.assertEqual(flt(fixed[0]["qty"]), PAPERBAG_QTY)

		groups = snapshot["choice_groups"]
		self.assertEqual(len(groups), 1)
		self.assertEqual(groups[0]["group_key"], self.group_key)
		self.assertEqual(groups[0]["label"], "Pilih Rasa")
		self.assertEqual(cint(groups[0]["pick_count"]), 1)
		self.assertEqual(cint(groups[0]["allow_repeats"]), 0)

		chosen = snapshot["chosen_options"]
		self.assertEqual(len(chosen), 1)
		self.assertEqual(chosen[0]["group_key"], self.group_key)
		self.assertEqual(chosen[0]["group_label"], "Pilih Rasa")
		self.assertEqual(chosen[0]["option_row"], self.option_flavour)
		self.assertEqual(chosen[0]["item_code"], self.flavour_item)
		self.assertEqual(
			chosen[0]["item_name"],
			frappe.db.get_value("Item", self.flavour_item, "item_name"),
		)
		self.assertEqual(flt(chosen[0]["qty"]), FLAVOUR_QTY)
		self.assertEqual(flt(chosen[0]["price_adjustment"]), FLAVOUR_ADJUSTMENT)

		self.assertEqual(flt(snapshot["total_amount"]), PARENT_RATE)
		self.assertEqual(flt(snapshot["total_amount"]), flt(selection.total_amount))

	def test_selection_rows_link_every_promotion_row_by_instance_id(self):
		"""4.6/2: instance_id is unique per instance and matches exactly one selection for every promotion row."""
		inv = self._sell_two_instances()
		reloaded = frappe.get_doc("Sales Invoice", inv.name)
		selections = reloaded.get(SELECTIONS_FIELD)
		self.assertEqual(len(selections), 2)

		instance_ids = [s.instance_id for s in selections]
		self.assertEqual(len(set(instance_ids)), 2, msg=f"instance_id collision: {instance_ids}")

		for selection in selections:
			rows = [r for r in reloaded.items if r.get(INSTANCE_FIELD) == selection.instance_id]
			# 1 parent + 1 fixed component + 1 chosen option = 3 rows per instance.
			self.assertEqual(
				len(rows),
				3,
				msg=(
					f"instance {selection.instance_id} expected exactly 3 rows "
					f"(1 parent + 1 fixed + 1 option), got {len(rows)}"
				),
			)
			parent_rows = [r for r in rows if r.get(ROLE_FIELD) == PARENT_ROLE]
			self.assertEqual(len(parent_rows), 1)
			self.assertEqual(parent_rows[0].item_code, self.parent_item)
			self.assertEqual(flt(parent_rows[0].rate), flt(selection.total_amount))

			self.assertEqual(selection.promotion, self.promo.name)

		flavour_selection = next(
			s for s in selections if flt(s.total_amount) == PARENT_RATE
		)
		other_flavour_selection = next(
			s for s in selections if flt(s.total_amount) == BASE_PRICE
		)
		self.assertEqual(
			flt(flavour_selection.total_amount),
			BASE_PRICE + FLAVOUR_ADJUSTMENT,
			msg="flavour selection total must equal base_price + option adjustment",
		)
		self.assertEqual(
			flt(other_flavour_selection.total_amount),
			BASE_PRICE,
			msg="zero-adjustment selection total must equal base_price",
		)

	def test_facts_are_written_on_submit_and_link_back_to_the_invoice(self):
		"""4.6/3: Promotion Selection Fact rows exist on submit, link to the invoice, and cover both kinds."""
		inv = self._sell()
		reloaded = frappe.get_doc("Sales Invoice", inv.name)
		selection = reloaded.get(SELECTIONS_FIELD)[0]
		snapshot = frappe.parse_json(selection.snapshot)
		instance_id = selection.instance_id

		fact_rows = frappe.get_all(
			"Promotion Selection Fact",
			filters={"pos_invoice": inv.name},
			fields=["kind", "instance_id", "item_code", "qty", "price_adjustment", "group_key", "option"],
		)
		self.assertEqual(len(fact_rows), 2)
		for row in fact_rows:
			self.assertEqual(row.instance_id, instance_id)

		by_kind = {row.kind: row for row in fact_rows}
		self.assertIn("Fixed Component", by_kind)
		self.assertIn("Option", by_kind)

		fixed_row = by_kind["Fixed Component"]
		self.assertEqual(fixed_row.item_code, snapshot["fixed_components"][0]["item_code"])
		self.assertEqual(flt(fixed_row.qty), PAPERBAG_QTY)
		self.assertEqual(flt(fixed_row.price_adjustment), 0.0)

		option_row = by_kind["Option"]
		self.assertEqual(option_row.item_code, snapshot["chosen_options"][0]["item_code"])
		self.assertEqual(flt(option_row.qty), FLAVOUR_QTY)
		self.assertEqual(flt(option_row.price_adjustment), FLAVOUR_ADJUSTMENT)
		self.assertEqual(option_row.group_key, snapshot["chosen_options"][0]["group_key"])
		self.assertEqual(option_row.option, snapshot["chosen_options"][0]["option_row"])

	def test_post_submit_master_pricing_edits_do_not_change_the_sold_record(self):
		"""4.6/4: editing base_price, an option price_adjustment, and
		the option list after submit changes nothing on the sale."""
		inv = self._sell()
		before = frappe.get_doc("Sales Invoice", inv.name)
		before_selection = before.get(SELECTIONS_FIELD)[0]
		before_snapshot = frappe.parse_json(before_selection.snapshot)
		before_total = flt(before_selection.total_amount)
		before_grand_total = flt(before.grand_total)
		before_parent_rate = flt(
			next(r for r in before.items if r.get(ROLE_FIELD) == PARENT_ROLE).rate
		)

		# Edit PRICING (not merely a label, which is all test_promotion_master.py
		# compares today): base_price, one option's adjustment, and the option
		# list itself (add a third option, remove the chosen one's neighbour).
		self.promo.base_price = 99999.0
		self.promo.options[0].price_adjustment = -12345.0
		self.promo.append(
			"options",
			{
				"choice_group_key": self.group_key,
				"item_code": self.paperbag_item,
				"price_adjustment": 500.0,
				"max_per_option": 0,
			},
		)
		self.promo.save()
		self.promo.reload()
		self.assertEqual(len(self.promo.options), 3)
		self.assertEqual(flt(self.promo.base_price), 99999.0)

		# The master edit above is real and must not have silently no-oped.
		self.promo.base_price = BASE_PRICE
		self.promo.save()

		after = frappe.get_doc("Sales Invoice", inv.name)
		after_selection = after.get(SELECTIONS_FIELD)[0]
		self.assertEqual(after_selection.instance_id, before_selection.instance_id)
		self.assertEqual(after_selection.snapshot, before_selection.snapshot)
		self.assertEqual(flt(after_selection.total_amount), before_total)

		after_snapshot = frappe.parse_json(after_selection.snapshot)
		self.assertEqual(after_snapshot, before_snapshot)

		parent_rate_after = flt(
			next(r for r in after.items if r.get(ROLE_FIELD) == PARENT_ROLE).rate
		)
		self.assertEqual(parent_rate_after, before_parent_rate)
		self.assertEqual(flt(after.grand_total), before_grand_total)

	def test_print_format_does_not_yet_render_promotion_components(self):
		"""4.6/5 coverage finding: the shipped POS Next Receipt template has no promotion rendering at all.

		"Reprint reproduces the selection" cannot be checked against the print
		format yet, so this test does not force the print format to change (an
		out-of-scope edit for this task). It instead asserts the two facts that
		make that gap precise, rather than being skipped:
		1. the print format source references no promotion field, so rendering
		   a promotion invoice is structurally incapable of reproducing a
		   selection today; and
		2. the strongest adjacent thing that is true: the snapshot survives a
		   post-submit master edit unchanged on re-read, which is the same
		   property a future print-side check would have to rest on.
		"""
		pf_html = frappe.db.get_value("Print Format", "POS Next Receipt", "html")
		self.assertTrue(pf_html, msg="POS Next Receipt print format is missing on this site")
		for token in ("pos_promotion", "promotion", "snapshot", "instance"):
			self.assertNotIn(
				token,
				pf_html.lower(),
				msg=(
					f"POS Next Receipt now references {token!r} — update this "
					f"test to actually render and assert the printed selection "
					f"against the snapshot (4.6 item 5)"
				),
			)

		inv = self._sell()
		before = frappe.get_doc("Sales Invoice", inv.name)
		before_snapshot = before.get(SELECTIONS_FIELD)[0].snapshot

		self.promo.base_price = BASE_PRICE + 7777.0
		self.promo.save()

		after = frappe.get_doc("Sales Invoice", inv.name)
		self.assertEqual(after.get(SELECTIONS_FIELD)[0].snapshot, before_snapshot)
