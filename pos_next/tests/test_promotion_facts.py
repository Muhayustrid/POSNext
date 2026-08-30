"""Task 6 — Reporting projection (design section 11 / D16, invariant I14, gate G5).

Covers, in the plan's proof order:
- Sale and return facts with signs and ``return_against``, including the
  one-instance-out-of-two return whose copied selections must not over-count.
- Cancellation lifecycle in the measured order: return cancel first, sale
  cancel second (the framework blocks cancelling a sale that a submitted
  return still references).
- ``facts.rebuild()`` canonical semantic equality, row count, no duplicates,
  idempotent repeat, and cancelled sources contributing nothing. Assertions
  are scoped to the test's own invoice names, never global row counts.
- One query per section 11 bullet returning expected numbers and excluding
  cancelled transactions.
- I14 source contract: no module outside ``facts.py`` (and tests) touches the
  fact doctype; permission rows are read-only per design section 18.

Conventions:
- self.addCleanup(frappe.db.rollback) registered FIRST in setUp.
- Zero frappe.db.commit().
- Unique suffix per test run (promotions are undeletable once referenced).
"""

import json
import uuid
from pathlib import Path

import frappe
from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, getdate, nowdate

from pos_next.promotions import facts
from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)

INSTANCE_FIELD = "pos_promotion_instance"
ROLE_FIELD = "pos_promotion_role"
PENDING_FIELD = "pos_pending_promotions"
SELECTIONS_FIELD = "pos_promotion_selections"

FACT_DOCTYPE = "Promotion Selection Fact"

# Canonical semantic equality (G5): every business-significant fact field, in
# the design section 11 order. Framework-managed metadata (name, creation,
# modified) is deliberately absent — identity is a random hash (facts.py).
CANONICAL_FIELDS = [
	"pos_invoice",
	"instance_id",
	"promotion",
	"posting_date",
	"company",
	"warehouse",
	"pos_profile",
	"is_return",
	"return_against",
	"kind",
	"group_key",
	"group_label",
	"option",
	"item_code",
	"item_name",
	"qty",
	"price_adjustment",
	"promotion_total",
]

# Design section 18: the fact table is read-only for reporting roles and is
# written only by system/doc-events, so no role may hold a writing right.
FACT_ROLE_RIGHTS = {
	"System Manager": {"read": 1, "report": 1},
	"Administrator": {"read": 1, "report": 1},
	"Nexus POS Manager": {"read": 1, "report": 1},
	# The cashier reads facts in-app but holds no report right, matching the
	# shipped promotion_selection_fact.json.
	"POSNext Cashier": {"read": 1, "report": 0},
}
FACT_FORBIDDEN_RIGHTS = ("create", "write", "delete", "submit", "cancel", "amend")


def _suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionFacts(IntegrationTestCase):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.suffix = _suffix()
		self._company_counter = 0
		self._setup_companies_and_warehouses()
		self._setup_items()
		self._setup_pos_profile()
		self._setup_promotion()

	# --- fixtures (same shape as test_promotion_returns) --------------------

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
		return (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"{prefix} {self.suffix}",
					"company": company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

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

		self.root_company = self._make_company("_Test Fact Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test Fact Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test Fact Outlet WH", self.outlet_company)

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

	def _setup_items(self):
		self.parent_item = self._make_item(f"_Test Fact Parent {self.suffix}", is_stock_item=0)
		self.bread_a = self._make_item(f"_Test Fact Bread A {self.suffix}", is_stock_item=1)
		self.bread_b = self._make_item(f"_Test Fact Bread B {self.suffix}", is_stock_item=1)
		self.bread_c = self._make_item(f"_Test Fact Bread C {self.suffix}", is_stock_item=1)

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
					for code in (self.bread_a, self.bread_b, self.bread_c)
				],
			}
		).insert(ignore_permissions=True)
		stock_entry.submit()

	def _setup_pos_profile(self):
		self.customer_name = f"_Test Fact Customer {self.suffix}"
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

		self.pos_profile_name = f"_Test Fact POS Profile {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": self.pos_profile_name,
				"company": self.outlet_company,
				"warehouse": self.outlet_warehouse,
				"customer": self.customer_name,
				"currency": "IDR",
				"selling_price_list": "Standard Selling",
				"payments": [{"mode_of_payment": self.mop_name, "default": 1}],
				"write_off_account": write_off_account,
				"write_off_cost_center": write_off_cc,
				"income_account": income_account,
				"expense_account": write_off_account,
				"cost_center": write_off_cc,
				"write_off_limit": 1.0,
			}
		).insert(ignore_permissions=True)

	def _setup_promotion(self):
		group_key = f"grp_{self.suffix}"
		self.promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"Promo Fact {self.suffix}",
				"root_company": self.root_company,
				"parent_item": self.parent_item,
				"base_price": 20000.0,
				"currency": "IDR",
				"enabled": 1,
				"max_instances_per_invoice": 0,
				"components": [{"item_code": self.bread_a, "qty": 2.0}],
				"choice_groups": [{"group_key": group_key, "label": "Pilih Roti", "pick_count": 1}],
				"options": [
					{
						"choice_group_key": group_key,
						"item_code": self.bread_b,
						"price_adjustment": 0.0,
						"max_per_option": 0,
					},
					{
						"choice_group_key": group_key,
						"item_code": self.bread_c,
						"price_adjustment": 3000.0,
						"max_per_option": 0,
					},
				],
				"outlets": [
					{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}
				],
			}
		).insert(ignore_permissions=True)
		self.group_key = group_key
		self.option_b = self.promo.options[0].name
		self.option_c = self.promo.options[1].name

	def _instance(self, option_row):
		return {
			"promotion": self.promo.name,
			"selections": [{"group_key": self.group_key, "picks": [{"option_row": option_row, "qty": 1}]}],
		}

	def _new_invoice(self, pending=None, items=None):
		doc = {
			"doctype": "Sales Invoice",
			"is_pos": 1,
			"company": self.outlet_company,
			"pos_profile": self.pos_profile_name,
			"customer": self.customer_name,
			"posting_date": nowdate(),
			"currency": "IDR",
			"items": items if items is not None else [],
			"payments": [{"mode_of_payment": self.mop_name, "amount": 0}],
		}
		if pending is not None:
			doc[PENDING_FIELD] = pending
		return frappe.get_doc(doc)

	def _submit_paid(self, inv):
		inv.payments[0].amount = flt(inv.grand_total)
		inv.save()
		inv.submit()
		return inv

	def _repay(self, ret, amount):
		"""Restate the refund after rows were removed from a mapped return."""
		ret.payments[0].amount = amount
		ret.paid_amount = amount
		return ret

	def _sell(self, options=(None,)):
		"""Submit one paid Sales Invoice carrying one instance per given option row."""
		option_rows = [opt or self.option_b for opt in options]
		pending = json.dumps({"instances": [self._instance(opt) for opt in option_rows]})
		inv = self._new_invoice(pending=pending)
		inv.insert()
		return self._submit_paid(inv)

	def _sell_with_standalone_row(self):
		"""One promotion instance plus one ordinary standalone row on the same invoice."""
		pending = json.dumps({"instances": [self._instance(self.option_b)]})
		inv = self._new_invoice(
			pending=pending,
			items=[
				{"item_code": self.bread_c, "qty": 3, "rate": 10000.0, "warehouse": self.outlet_warehouse}
			],
		)
		inv.insert()
		return self._submit_paid(inv)

	def _partial_set_return(self, sale, instance_total):
		"""Return only the instance whose selection totals instance_total."""
		selections = {s.instance_id: s for s in sale.get(SELECTIONS_FIELD)}
		kept = next(iid for iid, s in selections.items() if flt(s.total_amount) == instance_total)
		ret = make_sales_return(sale.name)
		ret.items = [row for row in ret.items if row.get(INSTANCE_FIELD) == kept]
		self._repay(ret, -instance_total)
		ret.insert()
		ret.submit()
		return ret, kept

	# --- fact readers --------------------------------------------------------

	def _fact_rows(self, invoice_name):
		return frappe.get_all(FACT_DOCTYPE, filters={"pos_invoice": invoice_name}, fields=CANONICAL_FIELDS)

	def _canonical(self, invoice_names):
		"""Sorted multiset of canonical string tuples across the given invoices.

		String projection keeps None, dates, and floats sortable in one rule;
		equality of these multisets is G5's canonical semantic equality (equal
		values, equal counts, therefore no duplicates) with framework-managed
		metadata ignored.
		"""
		rows = []
		for name in invoice_names:
			rows.extend(self._fact_rows(name))
		return sorted(
			tuple("" if row[field] is None else str(row[field]) for field in CANONICAL_FIELDS) for row in rows
		)

	def _instance_of_total(self, sale, instance_total):
		selections = {s.instance_id: s for s in sale.get(SELECTIONS_FIELD)}
		return next(iid for iid, s in selections.items() if flt(s.total_amount) == instance_total)

	# --- 1. sale and return facts with signs ---------------------------------

	def test_sale_and_partial_instance_set_return_write_signed_facts(self):
		sale = self._sell(options=(self.option_b, self.option_c))
		inst_b = self._instance_of_total(sale, 20000.0)
		inst_c = self._instance_of_total(sale, 23000.0)

		rows = self._fact_rows(sale.name)
		self.assertEqual(len(rows), 4)
		by_key = {(row["kind"], row["item_code"], row["instance_id"]): row for row in rows}

		expected_common = {
			"pos_invoice": sale.name,
			"promotion": self.promo.name,
			# get_all returns posting_date as a date object while the document
			# carries the string form; compare as dates, not as mixed types.
			"posting_date": getdate(sale.posting_date),
			"company": self.outlet_company,
			"warehouse": self.outlet_warehouse,
			"pos_profile": self.pos_profile_name,
			"is_return": 0,
			"return_against": None,
		}
		for (kind, item_code, instance_id), row in by_key.items():
			for field, expected in expected_common.items():
				value = getdate(row[field]) if field == "posting_date" else row[field]
				self.assertEqual(value, expected, msg=f"{instance_id} {kind} {item_code}: {field}")

		fixed_b = by_key[("Fixed Component", self.bread_a, inst_b)]
		self.assertEqual(flt(fixed_b["qty"]), 2.0)
		self.assertEqual(flt(fixed_b["price_adjustment"]), 0.0)
		self.assertIsNone(fixed_b["group_key"])
		self.assertIsNone(fixed_b["group_label"])
		self.assertIsNone(fixed_b["option"])
		self.assertEqual(fixed_b["item_name"], frappe.db.get_value("Item", self.bread_a, "item_name"))
		self.assertEqual(flt(fixed_b["promotion_total"]), 20000.0)

		option_b_row = by_key[("Option", self.bread_b, inst_b)]
		self.assertEqual(flt(option_b_row["qty"]), 1.0)
		self.assertEqual(option_b_row["group_key"], self.group_key)
		self.assertEqual(option_b_row["group_label"], "Pilih Roti")
		self.assertEqual(option_b_row["option"], self.option_b)
		self.assertEqual(option_b_row["item_name"], frappe.db.get_value("Item", self.bread_b, "item_name"))
		self.assertEqual(flt(option_b_row["price_adjustment"]), 0.0)
		self.assertEqual(flt(option_b_row["promotion_total"]), 20000.0)

		option_c_row = by_key[("Option", self.bread_c, inst_c)]
		self.assertEqual(flt(option_c_row["price_adjustment"]), 3000.0)
		self.assertEqual(flt(option_c_row["promotion_total"]), 23000.0)

		# The return repays only inst_b. Its facts must describe exactly the
		# repaid instance — never the copied selections of inst_c (which the
		# return still carries wholesale).
		ret, kept = self._partial_set_return(sale, 20000.0)
		self.assertEqual(kept, inst_b)

		return_rows = self._fact_rows(ret.name)
		self.assertEqual(len(return_rows), 2)
		return_by_key = {(row["kind"], row["item_code"]): row for row in return_rows}
		self.assertEqual(set(return_by_key), {("Fixed Component", self.bread_a), ("Option", self.bread_b)})

		for row in return_rows:
			self.assertEqual(row["is_return"], 1)
			self.assertEqual(row["return_against"], sale.name)
			self.assertEqual(row["instance_id"], inst_b)
			self.assertEqual(flt(row["promotion_total"]), -20000.0)

		self.assertEqual(flt(return_by_key[("Fixed Component", self.bread_a)]["qty"]), -2.0)
		self.assertEqual(flt(return_by_key[("Option", self.bread_b)]["qty"]), -1.0)

	def test_return_fact_price_adjustment_is_not_negated(self):
		"""A unit-price attribute keeps its sign; only qty and totals flip."""
		sale = self._sell(options=(self.option_c,))
		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()

		rows = self._fact_rows(ret.name)
		self.assertEqual(len(rows), 2)
		option_row = next(row for row in rows if row["kind"] == "Option")
		self.assertEqual(flt(option_row["qty"]), -1.0)
		self.assertEqual(flt(option_row["promotion_total"]), -23000.0)
		self.assertEqual(flt(option_row["price_adjustment"]), 3000.0)

	# --- 2. cancellation lifecycle -------------------------------------------

	def test_cancel_lifecycle_plain_sale(self):
		sale = self._sell()
		self.assertEqual(len(self._fact_rows(sale.name)), 2)

		sale.cancel()

		self.assertEqual(frappe.db.get_value("Sales Invoice", sale.name, "docstatus"), 2)
		self.assertEqual(self._fact_rows(sale.name), [])

	def test_cancel_lifecycle_return_first_then_sale(self):
		"""Measured order: a sale with a live submitted return cannot cancel."""
		sale = self._sell()
		ret = make_sales_return(sale.name)
		ret.insert()
		ret.submit()
		self.assertEqual(len(self._fact_rows(sale.name)), 2)
		self.assertEqual(len(self._fact_rows(ret.name)), 2)

		ret.cancel()
		self.assertEqual(self._fact_rows(ret.name), [])
		self.assertEqual(len(self._fact_rows(sale.name)), 2)

		# The submitted return stamps the source invoice's outstanding/modified via
		# update_voucher_outstanding(voucher_no=return_against) (erpnext/accounts/
		# doctype/sales_invoice/sales_invoice.py:1600-1625 → erpnext/accounts/
		# utils.py:2171-2187), so the in-memory `sale` is stale by the time it is
		# cancelled. Reload first; the lifecycle assertion (facts cleared on sale
		# cancel) is unchanged. POS Invoice returns never stamped their source, so
		# the source app could cancel the held object directly.
		sale = frappe.get_doc("Sales Invoice", sale.name)
		sale.cancel()
		self.assertEqual(self._fact_rows(sale.name), [])

	# --- 3. rebuild -----------------------------------------------------------

	def test_rebuild_reproduces_the_projection(self):
		sale_a = self._sell(options=(self.option_b, self.option_c))
		ret, _kept = self._partial_set_return(sale_a, 20000.0)
		sale_b = self._sell()
		sale_b.cancel()
		self.assertEqual(self._fact_rows(sale_b.name), [])

		own_invoices = [sale_a.name, ret.name, sale_b.name]
		before = self._canonical(own_invoices)
		self.assertEqual(len(before), 6)

		facts.rebuild()

		self.assertEqual(self._canonical(own_invoices), before)
		self.assertEqual(self._fact_rows(sale_b.name), [])

		# Idempotent repeat: a second rebuild changes nothing.
		facts.rebuild()
		self.assertEqual(self._canonical(own_invoices), before)

	# --- 4. section 11 queries -------------------------------------------------

	def _sell_two_and_return_one(self):
		sale = self._sell(options=(self.option_b, self.option_c))
		ret, _kept = self._partial_set_return(sale, 20000.0)
		return sale, ret

	def test_query_promotion_units(self):
		self._sell_two_and_return_one()

		self.assertEqual(
			facts.promotion_units().get(self.promo.name),
			{"gross": 2, "returned": 1, "net": 1},
		)

	def test_query_promotion_revenue(self):
		self._sell_two_and_return_one()

		self.assertEqual(
			facts.promotion_revenue().get(self.promo.name),
			{"gross": 43000.0, "returned": 20000.0, "net": 23000.0},
		)

	def test_query_item_quantities_by_kind(self):
		self._sell_two_and_return_one()

		quantities = facts.item_quantities()
		self.assertEqual(
			quantities[self.bread_a]["Fixed Component"],
			{"gross": 4.0, "returned": 2.0, "net": 2.0},
		)
		self.assertEqual(quantities[self.bread_b]["Option"], {"gross": 1.0, "returned": 1.0, "net": 0.0})
		self.assertEqual(quantities[self.bread_c]["Option"], {"gross": 1.0, "returned": 0.0, "net": 1.0})
		self.assertNotIn("Option", quantities[self.bread_a])
		self.assertNotIn("Fixed Component", quantities[self.bread_b])

	def test_query_option_frequency(self):
		self._sell_two_and_return_one()

		frequency = facts.option_frequency()
		self.assertEqual(frequency[self.bread_b], {"gross": 1.0, "returned": 1.0, "net": 0.0})
		self.assertEqual(frequency[self.bread_c], {"gross": 1.0, "returned": 0.0, "net": 1.0})
		# Option frequency covers chosen options only: the fixed component and
		# the promotion parent item never appear.
		self.assertNotIn(self.bread_a, frequency)
		self.assertNotIn(self.parent_item, frequency)

	def test_query_outlet_totals(self):
		self._sell_two_and_return_one()

		# Scoped to this run's POS Profile: outlet_totals() aggregates the whole
		# site, so a whole-list equality would break the moment any other
		# committed promotion data exists on the site.
		rows = [row for row in facts.outlet_totals() if row["pos_profile"] == self.pos_profile_name]
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			rows[0],
			{
				"company": self.outlet_company,
				"pos_profile": self.pos_profile_name,
				"warehouse": self.outlet_warehouse,
				"gross_units": 2,
				"returned_units": 1,
				"net_units": 1,
				"gross_revenue": 43000.0,
				"returned_revenue": 20000.0,
				"net_revenue": 23000.0,
			},
		)

	def test_queries_exclude_cancelled_transactions(self):
		_sale, ret = self._sell_two_and_return_one()

		ret.cancel()

		self.assertEqual(
			facts.promotion_units().get(self.promo.name),
			{"gross": 2, "returned": 0, "net": 2},
		)
		self.assertEqual(
			facts.promotion_revenue().get(self.promo.name),
			{"gross": 43000.0, "returned": 0.0, "net": 43000.0},
		)
		self.assertEqual(
			facts.item_quantities()[self.bread_b]["Option"],
			{"gross": 1.0, "returned": 0.0, "net": 1.0},
		)

	def test_query_standalone_split(self):
		self._sell_with_standalone_row()

		split = facts.standalone_split()
		self.assertEqual(split[self.bread_c]["standalone"], 3.0)
		self.assertEqual(split[self.bread_a]["in_promotion"], 2.0)
		self.assertEqual(split[self.bread_b]["in_promotion"], 1.0)
		self.assertEqual(split[self.bread_c]["in_promotion"], 0.0)
		self.assertEqual(split[self.bread_a]["standalone"], 0.0)
		# The non-stock promotion parent row is neither standalone nor component.
		self.assertNotIn(self.parent_item, split)

	# --- 5. contracts (I14, design section 18) ---------------------------------

	def _fact_doctype_json(self) -> dict:
		path = (
			Path(frappe.get_app_path("pos_next"))
			/ "pos_next"
			/ "doctype"
			/ "promotion_selection_fact"
			/ "promotion_selection_fact.json"
		)
		self.assertTrue(path.is_file(), msg=f"DocType JSON not found: {path}")
		return json.loads(path.read_text(encoding="utf-8"))

	def test_fact_permissions_are_read_only_for_reporting_roles(self):
		"""The shipped JSON, not the database: this is the definition the migrate imports."""
		perms = self._fact_doctype_json()["permissions"]

		self.assertEqual(
			[row["role"] for row in perms],
			list(FACT_ROLE_RIGHTS),
			msg=f"Fact permission roles mismatch: {perms}",
		)
		for row in perms:
			for right, expected in FACT_ROLE_RIGHTS[row["role"]].items():
				self.assertEqual(
					int(row.get(right) or 0),
					expected,
					msg=f"Fact permission {row['role']}.{right} is {row.get(right)!r}, expected {expected}",
				)
			for right in FACT_FORBIDDEN_RIGHTS:
				self.assertEqual(
					int(row.get(right) or 0),
					0,
					msg=f"Fact permission {row['role']}.{right} must stay 0, got {row.get(right)!r}",
				)

	def test_fact_permissions_are_live_on_this_site(self):
		"""The imported definition, so a JSON that never synced still fails."""
		live = frappe.get_all(
			"DocPerm",
			filters={"parent": FACT_DOCTYPE, "parenttype": "DocType"},
			fields=["role", "read", "report", *FACT_FORBIDDEN_RIGHTS],
		)
		self.assertEqual(
			{row["role"] for row in live},
			set(FACT_ROLE_RIGHTS),
			msg=f"live fact DocPerm roles mismatch: {live}",
		)
		for row in live:
			for right, expected in FACT_ROLE_RIGHTS[row["role"]].items():
				self.assertEqual(
					int(row.get(right) or 0),
					expected,
					msg=f"live fact DocPerm {row['role']}.{right} is {row.get(right)!r}, expected {expected}",
				)
			for right in FACT_FORBIDDEN_RIGHTS:
				self.assertEqual(
					int(row.get(right) or 0),
					0,
					msg=f"live fact DocPerm {row['role']}.{right} must stay 0",
				)

	def test_no_module_outside_facts_touches_the_fact_table(self):
		"""I14: only the projection writer (and the doctype definition and tests) may name it."""
		# The scan walks the whole app repo; paths inside it are relative to the
		# repo root, so the allowances carry the package prefix. Detection covers
		# the writer module too, not just the doctype names: a breach via
		# ``from pos_next.promotions import facts`` + ``facts.FACT_DOCTYPE``
		# names no doctype literal, so a name-only scan would stay green.
		app_root = Path(frappe.get_app_path("pos_next")).parent
		allowed_exact = {
			"pos_next/promotions/facts.py",
			# hooks.py wires the doc-event handlers by dotted path; it is writer
			# registration, never a reader.
			"pos_next/hooks.py",
		}
		allowed_prefixes = (
			"pos_next/tests/",
			"pos_next/pos_next/doctype/promotion_selection_fact/",
		)

		offending = []
		for py_file in sorted(app_root.rglob("*.py")):
			rel = py_file.relative_to(app_root).as_posix()
			if rel in allowed_exact or rel.startswith(allowed_prefixes):
				continue
			if any(part.startswith(".") or part == "node_modules" for part in py_file.parts):
				continue
			# pos_next also ships test modules at the package root
			# (test_promotions.py, test_promotion_fact_links.py, ...); the
			# design's "tests may name it" allowance covers them too.
			if py_file.name.startswith("test_"):
				continue
			source = py_file.read_text(encoding="utf-8")
			if (
				"Promotion Selection Fact" in source
				or "promotion_selection_fact" in source
				or "promotions.facts" in source
				or "promotions import facts" in source
			):
				offending.append(rel)

		self.assertEqual(
			offending,
			[],
			msg="I14 breach — modules outside facts.py reference the fact projection: "
			+ ", ".join(offending),
		)
