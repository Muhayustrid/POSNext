# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

"""Regression test for the Promotion Selection Fact link retarget (port audit gate D).

`pos_next/pos_next/doctype/promotion_selection_fact/` stores the submitting
Sales Invoice's name in two Link fields, `pos_invoice` (required) and
`return_against` (optional). The ported `pos_next/promotions/facts.py` writes
Sales Invoice names into both — it always did, since pos_next runs on a
Sales-Invoice-only tree — but the DocType JSON originally shipped with
`options: "POS Invoice"`. Every promotion-bearing submit therefore failed with
`LinkValidationError: Could not find POS Invoice: <name>` inside
`Document._validate_links`, so the projection (design section 11 / I14) was
unwritable end-to-end.

This test locks the fix from two independent angles:

1. Structural: the live DocType meta declares both link fields pointing at
   "Sales Invoice". This is the direct assertion on the JSON retarget and does
   not depend on the submit pipeline.
2. End-to-end: a promotion sale driven through the *real* whitelisted path
   (`update_invoice` -> `submit_invoice`) with a pending-promotion payload
   actually writes `Promotion Selection Fact` rows whose `pos_invoice` is the
   submitted Sales Invoice name. Pre-fix this submit raised and rolled back, so
   zero fact rows survived; the assertion fails loudly there.

Field name `pos_invoice` is intentionally kept (not renamed): five SQL
projections in `facts.py` reference the column by that name.

Conventions (mirrors pos_next/tests/test_promotion_facts.py):
- `IntegrationTestCase`, self-cleanup via `frappe.db.rollback` registered first
  in `setUp`; the API submit path performs no `frappe.db.commit`.
- `_PNXT_TEST_`-prefixed, per-run-suffixed fixtures constructed here rather than
  assumed, so the test is self-contained and never reads another suite's rows.
"""

import json
import uuid

import frappe
from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from pos_next.api.invoices import submit_invoice, update_invoice

PARENT_ITEM = "_PNXT_TEST_FACT_PARENT"
BREAD_A = "_PNXT_TEST_FACT_BREAD_A"  # fixed component
BREAD_B = "_PNXT_TEST_FACT_BREAD_B"  # chosen option (price_adjustment 0)
BREAD_C = "_PNXT_TEST_FACT_BREAD_C"  # alternate option (unused by the sale)
CUSTOMER = "_PNXT_TEST_FACT_CUSTOMER"

PENDING_FIELD = "pos_pending_promotions"
SELECTIONS_FIELD = "pos_promotion_selections"
FACT_DOCTYPE = "Promotion Selection Fact"

# base_price; the chosen option carries a 0 adjustment so one instance = base_price.
BASE_PRICE = 20000.0


def _resolve_company():
	if frappe.db.exists("Company", "_Test Company"):
		return "_Test Company"
	default = frappe.defaults.get_global_default("company")
	if default:
		return default
	return frappe.db.get_value("Company", {"name": ["!=", ""]}, "name")


def _resolve_warehouse(company):
	wh = frappe.db.get_value(
		"Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name", order_by="creation asc"
	)
	if not wh:
		frappe.throw(f"No warehouse available for company {company}.")
	return wh


def _resolve_price_list(company):
	if frappe.db.exists("Price List", "Standard Selling"):
		return "Standard Selling"
	return frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")


def _resolve_currency(company):
	return frappe.get_cached_value("Company", company, "default_currency")


def _resolve_item_group():
	for candidate in ("_Test Item Group", "Products"):
		if frappe.db.exists("Item Group", candidate):
			ig = frappe.get_cached_doc("Item Group", candidate)
			if not ig.is_group:
				return candidate
	leaf = frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="creation asc")
	if leaf:
		return leaf
	return frappe.get_doc(
		{
			"doctype": "Item Group",
			"item_group_name": "_PNXT_TEST_FACT_IG",
			"parent_item_group": "All Item Groups",
			"is_group": 0,
		}
	).insert(ignore_permissions=True).name


def _resolve_customer_group():
	if frappe.db.exists("Customer Group", "_Test Customer Group"):
		return "_Test Customer Group"
	leaf = frappe.db.get_value("Customer Group", {"is_group": 0}, "name", order_by="creation asc")
	if leaf:
		return leaf
	return frappe.get_doc(
		{
			"doctype": "Customer Group",
			"customer_group_name": "_PNXT_TEST_FACT_CG",
			"parent_customer_group": "All Customer Groups",
			"is_group": 0,
		}
	).insert(ignore_permissions=True).name


def _resolve_territory():
	if frappe.db.exists("Territory", "_Test Territory"):
		return "_Test Territory"
	leaf = frappe.db.get_value("Territory", {"is_group": 0}, "name", order_by="creation asc")
	if leaf:
		return leaf
	return frappe.get_doc(
		{
			"doctype": "Territory",
			"territory_name": "_PNXT_TEST_FACT_TERR",
			"parent_territory": "All Territories",
			"is_group": 0,
		}
	).insert(ignore_permissions=True).name


def _resolve_mode_of_payment(company):
	"""An enabled Mode of Payment with an account row for `company`, else create one."""
	for mop_name in frappe.get_all("Mode of Payment", filters={"enabled": 1}, pluck="name", order_by="creation asc"):
		if frappe.db.exists("Mode of Payment Account", {"parent": mop_name, "company": company}):
			return mop_name

	mop_name = "_PNXT_TEST_FACT_MOP"
	if not frappe.db.exists("Mode of Payment", mop_name):
		default_account = frappe.db.get_value(
			"Account",
			{"company": company, "account_type": ("in", ["Cash", "Bank"]), "is_group": 0},
			"name",
			order_by="creation asc",
		) or frappe.db.get_value("Account", {"company": company, "is_group": 0}, "name", order_by="creation asc")
		accounts = [{"company": company, "default_account": default_account}] if default_account else []
		frappe.get_doc(
			{"doctype": "Mode of Payment", "mode_of_payment": mop_name, "enabled": 1, "type": "Cash", "accounts": accounts}
		).insert(ignore_permissions=True)
	return mop_name


def _account(company, **filters):
	filters.setdefault("company", company)
	filters.setdefault("is_group", 0)
	return frappe.db.get_value("Account", filters, "name", order_by="creation asc")


class TestPromotionFactLinks(IntegrationTestCase):
	"""Promotion Selection Fact must accept the Sales Invoice it is written for."""

	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		frappe.set_user("Administrator")
		self.suffix = uuid.uuid4().hex[:6]

		self.company = _resolve_company()
		self.warehouse = _resolve_warehouse(self.company)
		self.price_list = _resolve_price_list(self.company)
		self.currency = _resolve_currency(self.company)
		self.mode_of_payment = _resolve_mode_of_payment(self.company)

		self._setup_items()
		self._setup_customer()
		self._setup_pos_profile()
		self._setup_promotion()

	# --- fixtures ------------------------------------------------------------

	def _make_item(self, code, *, is_stock_item):
		if frappe.db.exists("Item", code):
			return code
		doc = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": code,
				"item_name": code.replace("_PNXT_TEST_FACT_", ""),
				"item_group": _resolve_item_group(),
				"stock_uom": "Nos",
				"is_stock_item": is_stock_item,
				"is_sales_item": 1,
			}
		)
		doc.flags.from_integration = True
		doc.insert(ignore_permissions=True)
		return code

	def _setup_items(self):
		self.parent_item = self._make_item(PARENT_ITEM, is_stock_item=0)
		self.bread_a = self._make_item(BREAD_A, is_stock_item=1)
		self.bread_b = self._make_item(BREAD_B, is_stock_item=1)
		self.bread_c = self._make_item(BREAD_C, is_stock_item=1)

		for code in (self.bread_a, self.bread_b, self.bread_c):
			bin_qty = frappe.db.get_value("Bin", {"item_code": code, "warehouse": self.warehouse}, "actual_qty") or 0
			if flt(bin_qty) < 50:
				make_stock_entry(
					item_code=code, target=self.warehouse, qty=100, rate=5000.0, company=self.company
				)

	def _setup_customer(self):
		if not frappe.db.exists("Customer", CUSTOMER):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": CUSTOMER,
					"customer_group": _resolve_customer_group(),
					"territory": _resolve_territory(),
					"customer_type": "Individual",
				}
			).insert(ignore_permissions=True)

	def _setup_pos_profile(self):
		write_off_account = _account(self.company, root_type="Expense")
		cost_center = frappe.db.get_value(
			"Cost Center", {"company": self.company, "is_group": 0}, "name", order_by="creation asc"
		)
		income_account = _account(self.company, root_type="Income")
		expense_account = write_off_account
		self.pos_profile = f"_PNXT_TEST_FACT_PROFILE_{self.suffix}"
		frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": self.pos_profile,
				"company": self.company,
				"warehouse": self.warehouse,
				"currency": self.currency,
				"selling_price_list": self.price_list,
				"customer": CUSTOMER,
				"payments": [{"mode_of_payment": self.mode_of_payment, "default": 1, "amount": 0}],
				"write_off_account": write_off_account,
				"write_off_cost_center": cost_center,
				"income_account": income_account,
				"expense_account": expense_account,
				"cost_center": cost_center,
				"write_off_limit": 1.0,
				"disable_rounded_total": 1,
				"ignore_pricing_rule": 0,
				"disabled": 0,
			}
		).insert(ignore_permissions=True)

	def _setup_promotion(self):
		group_key = f"grp_{self.suffix}"
		promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"_PNXT_TEST FACT PROMO {self.suffix}",
				"root_company": self.company,
				"parent_item": self.parent_item,
				"base_price": BASE_PRICE,
				"currency": self.currency,
				"enabled": 1,
				"max_instances_per_invoice": 0,
				"components": [{"item_code": self.bread_a, "qty": 2.0}],
				"choice_groups": [{"group_key": group_key, "label": "Pick Bread", "pick_count": 1}],
				"options": [
					{"choice_group_key": group_key, "item_code": self.bread_b, "price_adjustment": 0.0, "max_per_option": 0},
					{"choice_group_key": group_key, "item_code": self.bread_c, "price_adjustment": 3000.0, "max_per_option": 0},
				],
				"outlets": [{"company": self.company, "warehouse": self.warehouse, "enabled": 1}],
			}
		)
		promo.insert(ignore_permissions=True)
		self.promotion = promo.name
		self.group_key = group_key
		self.option_b = promo.options[0].name

	# --- helpers -------------------------------------------------------------

	def _sell_via_api(self):
		"""Drive one promotion sale through update_invoice -> submit_invoice."""
		pending = json.dumps(
			{
				"instances": [
					{
						"promotion": self.promotion,
						"selections": [{"group_key": self.group_key, "picks": [{"option_row": self.option_b, "qty": 1}]}],
					}
				]
			}
		)
		payload = {
			"doctype": "Sales Invoice",
			"is_pos": 1,
			"pos_profile": self.pos_profile,
			"company": self.company,
			"currency": self.currency,
			"customer": CUSTOMER,
			"selling_price_list": self.price_list,
			"posting_date": nowdate(),
			"items": [],
			"payments": [{"mode_of_payment": self.mode_of_payment, "amount": 0}],
			PENDING_FIELD: pending,
		}
		draft = update_invoice(json.dumps(payload))
		invoice_name = draft["name"]
		# Pay exactly the materialized total so submit reconciles.
		draft["payments"] = [{"mode_of_payment": self.mode_of_payment, "amount": flt(draft["grand_total"])}]
		submit_invoice(
			invoice=json.dumps(draft, default=str),
			data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
		)
		return frappe.get_doc("Sales Invoice", invoice_name)

	def _facts_for(self, invoice_name):
		return frappe.get_all(
			FACT_DOCTYPE, filters={"pos_invoice": invoice_name}, fields=["pos_invoice", "return_against", "promotion"]
		)

	# --- 1. structural: the DocType links must target Sales Invoice ----------

	def test_fact_link_fields_point_to_sales_invoice(self):
		"""`pos_invoice` and `return_against` options must be "Sales Invoice".

		Direct assertion on the retargeted DocType JSON, independent of the submit
		path: if the link options drift back to "POS Invoice", every projection
		write dies in `Document._validate_links`.
		"""
		meta = frappe.get_meta(FACT_DOCTYPE)
		for fieldname in ("pos_invoice", "return_against"):
			df = meta.get_field(fieldname)
			self.assertIsNotNone(df, f"{FACT_DOCTYPE} is missing field {fieldname}")
			self.assertEqual(df.fieldtype, "Link", f"{fieldname} must remain a Link field")
			self.assertEqual(
				df.options,
				"Sales Invoice",
				f"{fieldname}.options must point at Sales Invoice (pos_next has no POS Invoice)",
			)

	def test_fact_fieldname_pos_invoice_is_preserved(self):
		"""The physical column name stays `pos_invoice` (facts.py SQL depends on it)."""
		meta = frappe.get_meta(FACT_DOCTYPE)
		self.assertIsNotNone(meta.get_field("pos_invoice"), "fieldname must NOT be renamed to sales_invoice")

	# --- 2. end-to-end: a real promotion sale writes facts -------------------

	def test_promotion_sale_writes_facts_with_sales_invoice_name(self):
		"""Pre-fix: submit raised LinkValidationError and rolled back -> zero facts."""
		invoice = self._sell_via_api()

		self.assertEqual(invoice.docstatus, 1, "promotion-bearing invoice must reach submitted state")
		self.assertTrue(
			invoice.get(SELECTIONS_FIELD),
			"pending payload should have materialized POS Promotion Selection rows",
		)

		facts = self._facts_for(invoice.name)
		self.assertGreaterEqual(
			len(facts),
			2,
			"expected one fact row per fixed component + chosen option of the instance",
		)
		for row in facts:
			self.assertEqual(
				row.pos_invoice, invoice.name, "fact.pos_invoice must carry the Sales Invoice name"
			)
			self.assertIsNone(row.return_against, "a sale fact must not set return_against")

		# The whole point of the fix: the stored name resolves as a Sales Invoice,
		# not a POS Invoice. This lookup would raise/return nothing if the Link
		# target were still the (nonexistent in pos_next) POS Invoice.
		self.assertEqual(
			frappe.db.get_value("Sales Invoice", invoice.name, "name"),
			invoice.name,
			"the value written into pos_invoice must be a real Sales Invoice",
		)
