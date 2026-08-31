"""Promotion master model and validation tests (Task 2).

One RED case per validation guard; each names its exact error so the guard and
the test stay paired. Mutation rule: removing any single validation line in
promotion.py must fail exactly the test that targets it — two guards never
share one test.

All validations live in the parent Promotion controller: measured framework
behaviour (frappe/model/document.py) is that child rows persist via
db_insert/db_update without running child DocType hooks, so child controllers
carry no validation.

Fixture conventions: rollback-first cleanup, unique per-run names, no commit.
"""

import json
import uuid

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

from pos_next.tests.helpers import (
	get_default_account,
	get_default_cost_center,
	get_default_mode_of_payment,
)


def get_unique_suffix():
	return uuid.uuid4().hex[:8]


class TestPromotionMasterValidations(IntegrationTestCase):
	"""One guard test per master-save validation (design section 4.1, plan Task 2)."""

	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.suffix = get_unique_suffix()
		self.root_company = self._make_company("_Test PM Root Co", is_group=1)
		self.outlet_company = self._make_company("_Test PM Outlet Co", parent=self.root_company)
		self.outlet_warehouse = self._make_warehouse("_Test PM Outlet WH", self.outlet_company)
		self._setup_items()

	# --- fixture helpers -------------------------------------------------

	def _make_company(self, prefix, parent=None, currency="IDR", is_group=0):
		company_name = f"{prefix} {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company_name,
				"is_group": is_group,
				"parent_company": parent,
				"default_currency": currency,
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)
		return company_name

	def _make_warehouse(self, prefix, company):
		# Warehouse autoname appends the company abbr ("- ABC"); the document
		# name, not warehouse_name, is what Link fields must reference.
		doc = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": f"{prefix} {self.suffix}",
				"company": company,
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _make_item(self, code, **overrides):
		values = {
			"doctype": "Item",
			"item_code": code,
			"item_name": code,
			"item_group": "All Item Groups",
			"is_stock_item": 1,
			"is_sales_item": 1,
			"stock_uom": "Nos",
		}
		values.update(overrides)
		frappe.get_doc(values).insert(ignore_permissions=True)
		return code

	def _setup_items(self):
		self.parent_item = self._make_item(f"_Test PM Parent {self.suffix}", is_stock_item=0, is_sales_item=1)
		self.bread_a = self._make_item(f"_Test PM Bread A {self.suffix}")
		self.bread_b = self._make_item(f"_Test PM Bread B {self.suffix}")
		self.bread_c = self._make_item(f"_Test PM Bread C {self.suffix}")
		self.non_sellable_item = self._make_item(f"_Test PM NonSellable {self.suffix}", is_sales_item=0)
		self.non_sellable_parent = self._make_item(
			f"_Test PM NonSellableParent {self.suffix}", is_stock_item=0, is_sales_item=0
		)
		self.batch_item = self._make_item(
			f"_Test PM Batch {self.suffix}", has_batch_no=1, batch_number_series="BATCH-.#####"
		)
		self.serial_item = self._make_item(
			f"_Test PM Serial {self.suffix}", has_serial_no=1, serial_no_series="SN-.#####"
		)

	def _base_promotion(self, **overrides):
		"""A fully valid baseline master; every guard test overrides exactly one thing."""
		group_key = f"grp_{self.suffix}"
		doc = {
			"doctype": "Promotion",
			"promotion_name": f"Promo Master {self.suffix}",
			"root_company": self.root_company,
			"parent_item": self.parent_item,
			"base_price": 20000.0,
			"currency": "IDR",
			"enabled": 1,
			"components": [{"item_code": self.bread_a, "qty": 1.0}],
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
					"price_adjustment": 2000.0,
					"max_per_option": 0,
				},
			],
			"outlets": [{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1}],
		}
		doc.update(overrides)
		return frappe.get_doc(doc)

	def _make_tax_template(self, prefix):
		# Item Tax Template.validate_tax_accounts only accepts Tax / Chargeable /
		# Income Account / Expense Account / Expenses Included In Valuation rows;
		# the standard chart ships the last one for every company.
		account = frappe.get_all(
			"Account",
			filters={
				"company": self.root_company,
				"is_group": 0,
				"account_type": [
					"in",
					[
						"Tax",
						"Chargeable",
						"Income Account",
						"Expense Account",
						"Expenses Included In Valuation",
					],
				],
			},
			pluck="name",
			limit=1,
		)[0]
		return (
			frappe.get_doc(
				{
					"doctype": "Item Tax Template",
					"title": f"{prefix} {self.suffix}",
					"company": self.root_company,
					"taxes": [{"tax_type": account, "tax_rate": 10.0}],
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _assign_item_tax_template(self, item_code, template):
		item = frappe.get_doc("Item", item_code)
		item.append("taxes", {"item_tax_template": template})
		item.save(ignore_permissions=True)

	@staticmethod
	def _message_log_texts():
		texts = []
		for entry in frappe.local.message_log or []:
			if isinstance(entry, dict):
				texts.append(str(entry.get("message", "")))
			else:
				texts.append(str(entry))
		return texts

	def _ensure_standard_selling_price_list(self):
		if not frappe.db.exists("Price List", "Standard Selling"):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": "Standard Selling",
					"selling": 1,
					"currency": "IDR",
				}
			).insert(ignore_permissions=True)
		return frappe.db.get_value("Price List", "Standard Selling", "currency") or "IDR"

	def _ensure_buying_price_list(self):
		# Buying Item Price uses a buying Price List; the fixture chooses the
		# Price List from its selling/buying intent, because ItemPrice
		# validates that a buying row sits on a buying-enabled Price List.
		if not frappe.db.exists("Price List", "Standard Buying"):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": "Standard Buying",
					"buying": 1,
					"currency": "IDR",
				}
			).insert(ignore_permissions=True)
		return frappe.db.get_value("Price List", "Standard Buying", "currency") or "IDR"

	def _make_item_price(self, item_code, price_list, selling, buying):
		self._ensure_standard_selling_price_list()
		self._ensure_buying_price_list()
		if price_list == "Standard Selling":
			pl_currency = frappe.db.get_value("Price List", "Standard Selling", "currency") or "IDR"
		elif price_list == "Standard Buying":
			pl_currency = frappe.db.get_value("Price List", "Standard Buying", "currency") or "IDR"
		else:
			pl_currency = self._ensure_standard_selling_price_list()
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": item_code,
				"price_list": price_list,
				"selling": selling,
				"buying": buying,
				"currency": pl_currency,
				"price_list_rate": 15000.0,
			}
		).insert(ignore_permissions=True)

	# --- positive baselines ----------------------------------------------

	def test_valid_master_saves(self):
		promo = self._base_promotion()
		promo.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Promotion", promo.name))

	def test_max_instances_per_invoice_defaults_to_zero(self):
		promo = self._base_promotion()
		promo.insert(ignore_permissions=True)
		self.assertEqual(promo.max_instances_per_invoice, 0)

	def test_group_key_survives_resave_and_label_edit(self):
		promo = self._base_promotion()
		promo.insert(ignore_permissions=True)
		original_key = promo.choice_groups[0].group_key
		promo.choice_groups[0].label = "Pilih Roti Baru"
		promo.save(ignore_permissions=True)
		self.assertEqual(promo.choice_groups[0].group_key, original_key)

	# --- structure guards (plan item 1) ----------------------------------

	def test_master_requires_component_or_choice_group(self):
		promo = self._base_promotion(components=[], choice_groups=[], options=[])
		with self.assertRaisesRegex(frappe.ValidationError, r"At least one component or choice group"):
			promo.insert(ignore_permissions=True)

	def test_choice_group_requires_at_least_two_options(self):
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			options=[
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				}
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"must have at least two options"):
			promo.insert(ignore_permissions=True)

	def test_option_group_key_must_resolve_within_document(self):
		group_key = f"grp_{self.suffix}"
		options = [
			{
				"choice_group_key": group_key,
				"item_code": self.bread_b,
				"price_adjustment": 0.0,
				"max_per_option": 0,
			},
			{
				"choice_group_key": group_key,
				"item_code": self.bread_c,
				"price_adjustment": 0.0,
				"max_per_option": 0,
			},
			{
				"choice_group_key": "grp_unknown_key",
				"item_code": self.bread_a,
				"price_adjustment": 0.0,
				"max_per_option": 0,
			},
		]
		promo = self._base_promotion(options=options)
		with self.assertRaisesRegex(frappe.ValidationError, r"which does not exist in this Promotion"):
			promo.insert(ignore_permissions=True)

	def test_duplicate_group_keys_rejected(self):
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			choice_groups=[
				{"group_key": group_key, "label": "Grup Satu", "pick_count": 1},
				{"group_key": group_key, "label": "Grup Dua", "pick_count": 1},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"Duplicate choice group key"):
			promo.insert(ignore_permissions=True)

	def test_pick_count_must_be_at_least_one(self):
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			choice_groups=[{"group_key": group_key, "label": "Pilih Roti", "pick_count": 0}]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"Pick count must be at least one"):
			promo.insert(ignore_permissions=True)

	# --- choice-group repeat states (Task 4.1 / design D3) ------------------

	def test_allow_repeats_defaults_to_off(self):
		# Unset allow_repeats must land as 0 (distinct-by-default), not None.
		promo = self._base_promotion()
		promo.insert(ignore_permissions=True)
		self.assertEqual(int(promo.choice_groups[0].allow_repeats or 0), 0)

	def test_pick_count_one_group_saves(self):
		# State 1: single choice — pick_count 1, two options, repeats irrelevant.
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			choice_groups=[{"group_key": group_key, "label": "Pilih Satu", "pick_count": 1}]
		)
		promo.insert(ignore_permissions=True)
		self.assertEqual(int(promo.choice_groups[0].pick_count), 1)

	def test_pick_many_distinct_group_saves(self):
		# State 2: pick-many-distinct — allow_repeats off, pick_count equals the
		# number of options so a full distinct selection is satisfiable.
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			choice_groups=[
				{
					"group_key": group_key,
					"label": "Pilih Dua Beda",
					"pick_count": 2,
					"allow_repeats": 0,
				}
			]
		)
		promo.insert(ignore_permissions=True)
		self.assertEqual(int(promo.choice_groups[0].allow_repeats or 0), 0)

	def test_pick_many_with_repeats_group_saves(self):
		# State 3: pick-many-any — allow_repeats on, same option may repeat.
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			choice_groups=[
				{
					"group_key": group_key,
					"label": "Pilih Dua Boleh Sama",
					"pick_count": 2,
					"allow_repeats": 1,
				}
			]
		)
		promo.insert(ignore_permissions=True)
		self.assertEqual(int(promo.choice_groups[0].allow_repeats or 0), 1)

	def test_distinct_group_rejects_unsatisfiable_pick_count(self):
		# Distinct guard: allow_repeats off but pick_count exceeds the number of
		# options, so no selection could ever satisfy the group — reject at save.
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			choice_groups=[
				{
					"group_key": group_key,
					"label": "Pilih Tiga Beda",
					"pick_count": 3,
					"allow_repeats": 0,
				}
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"cannot be satisfied"):
			promo.insert(ignore_permissions=True)

	def test_repeats_group_allows_pick_count_exceeding_options(self):
		# Counter-case to the guard above: the same oversubscribed pick_count is
		# legal once repeats are allowed.
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			choice_groups=[
				{
					"group_key": group_key,
					"label": "Pilih Tiga Boleh Sama",
					"pick_count": 3,
					"allow_repeats": 1,
				}
			]
		)
		promo.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Promotion", promo.name))

	# --- physical item guards (plan item 1, I12/D13) ---------------------

	def test_component_item_must_be_stock(self):
		promo = self._base_promotion(components=[{"item_code": self.parent_item, "qty": 1.0}])
		with self.assertRaisesRegex(frappe.ValidationError, r"Component item .* must be a stock item"):
			promo.insert(ignore_permissions=True)

	def test_option_item_must_be_stock(self):
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			options=[
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.parent_item,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"Option item .* must be a stock item"):
			promo.insert(ignore_permissions=True)

	def test_component_item_must_be_sellable(self):
		promo = self._base_promotion(components=[{"item_code": self.non_sellable_item, "qty": 1.0}])
		with self.assertRaisesRegex(frappe.ValidationError, r"must be a sales item"):
			promo.insert(ignore_permissions=True)

	def test_missing_component_item_fails_closed_under_ignore_links(self):
		# item_code is a Link field and Document.insert runs _validate_links
		# before validate (frappe/model/document.py), so the controller's named
		# error is only reachable when link validation is bypassed with
		# ignore_links — a programmatic path (framework-internal callers and
		# patches). Without the guard that path crashes with AttributeError on
		# the missing Item instead of failing closed.
		promo = self._base_promotion(components=[{"item_code": f"No Such Item {self.suffix}", "qty": 1.0}])
		with self.assertRaisesRegex(frappe.ValidationError, r"Item .* does not exist"):
			promo.insert(ignore_permissions=True, ignore_links=True)

	def test_option_item_must_not_track_batches(self):
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			options=[
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.batch_item,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"must not track batches"):
			promo.insert(ignore_permissions=True)

	def test_option_item_must_not_track_serial_numbers(self):
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			options=[
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.serial_item,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"must not track serial numbers"):
			promo.insert(ignore_permissions=True)

	def test_component_qty_must_be_positive(self):
		promo = self._base_promotion(components=[{"item_code": self.bread_a, "qty": 0.0}])
		with self.assertRaisesRegex(frappe.ValidationError, r"Component quantity must be greater than zero"):
			promo.insert(ignore_permissions=True)

	def test_component_qty_must_be_whole(self):
		promo = self._base_promotion(components=[{"item_code": self.bread_a, "qty": 1.5}])
		with self.assertRaisesRegex(frappe.ValidationError, r"Component quantity must be a whole number"):
			promo.insert(ignore_permissions=True)

	# --- pricing guards (plan item 1) -------------------------------------

	def test_base_price_must_be_positive(self):
		promo = self._base_promotion(base_price=0.0)
		with self.assertRaisesRegex(frappe.ValidationError, r"Base price must be greater than zero"):
			promo.insert(ignore_permissions=True)

	def test_option_adjusted_total_must_not_be_negative(self):
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			options=[
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.bread_c,
					"price_adjustment": -25000.0,
					"max_per_option": 0,
				},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"adjusted total must not be negative"):
			promo.insert(ignore_permissions=True)

	def test_valid_from_must_not_exceed_valid_to(self):
		promo = self._base_promotion(valid_from="2026-09-01", valid_to="2026-08-01")
		with self.assertRaisesRegex(frappe.ValidationError, r"Valid From must not be after Valid To"):
			promo.insert(ignore_permissions=True)

	# --- instance cap guard (plan item 7, D19) ----------------------------

	def test_max_instances_per_invoice_must_not_be_negative(self):
		promo = self._base_promotion(max_instances_per_invoice=-1)
		with self.assertRaisesRegex(
			frappe.ValidationError, r"Max instances per invoice must not be negative"
		):
			promo.insert(ignore_permissions=True)

	# --- parent item guards (plan item 2, I11/D12) ------------------------

	def test_parent_item_must_not_be_stock(self):
		promo = self._base_promotion(parent_item=self.bread_a)
		with self.assertRaisesRegex(frappe.ValidationError, r"Parent item .* must not be a stock item"):
			promo.insert(ignore_permissions=True)

	def test_parent_item_must_be_sellable(self):
		promo = self._base_promotion(parent_item=self.non_sellable_parent)
		with self.assertRaisesRegex(frappe.ValidationError, r"Parent item .* must be a sales item"):
			promo.insert(ignore_permissions=True)

	def test_parent_item_must_not_be_fixed_asset(self):
		frappe.db.set_value("Item", self.parent_item, "is_fixed_asset", 1)
		promo = self._base_promotion()
		with self.assertRaisesRegex(frappe.ValidationError, r"Parent item .* must not be a fixed asset"):
			promo.insert(ignore_permissions=True)

	def test_missing_parent_item_fails_closed_under_ignore_links(self):
		# Same ignore_links reasoning as the component case: _validate_links
		# normally rejects the missing Link first, and without the guard the
		# controller would crash with AttributeError instead of the named error.
		promo = self._base_promotion(parent_item=f"No Such Parent {self.suffix}")
		with self.assertRaisesRegex(frappe.ValidationError, r"Parent item .* does not exist"):
			promo.insert(ignore_permissions=True, ignore_links=True)

	def test_parent_item_must_not_have_selling_item_price(self):
		self._make_item_price(self.parent_item, "Standard Selling", selling=1, buying=0)
		promo = self._base_promotion()
		with self.assertRaisesRegex(frappe.ValidationError, r"must not have any selling Item Price"):
			promo.insert(ignore_permissions=True)

	def test_parent_item_with_buying_price_only_is_allowed(self):
		self._make_item_price(self.parent_item, "Standard Buying", selling=0, buying=1)
		promo = self._base_promotion()
		promo.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Promotion", promo.name))

	def test_single_enabled_promotion_per_parent_item(self):
		first = self._base_promotion(promotion_name=f"Promo First {self.suffix}")
		first.insert(ignore_permissions=True)
		second = self._base_promotion(promotion_name=f"Promo Second {self.suffix}")
		with self.assertRaisesRegex(frappe.ValidationError, r"already used by enabled Promotion"):
			second.insert(ignore_permissions=True)

	def test_disabled_promotion_may_share_parent_item(self):
		first = self._base_promotion(promotion_name=f"Promo First {self.suffix}")
		first.insert(ignore_permissions=True)
		second = self._base_promotion(promotion_name=f"Promo Second {self.suffix}", enabled=0)
		second.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Promotion", second.name))

	# --- outlet guards (plan item 3, D4/D5) -------------------------------

	def test_duplicate_outlet_pair_rejected(self):
		promo = self._base_promotion(
			outlets=[
				{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 1},
				{"company": self.outlet_company, "warehouse": self.outlet_warehouse, "enabled": 0},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"Duplicate outlet"):
			promo.insert(ignore_permissions=True)

	def test_outlet_warehouse_must_belong_to_row_company(self):
		foreign_company = self._make_company("_Test PM Foreign Co")
		foreign_warehouse = self._make_warehouse("_Test PM Foreign WH", foreign_company)
		promo = self._base_promotion(
			outlets=[{"company": self.outlet_company, "warehouse": foreign_warehouse, "enabled": 1}]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"belongs to company"):
			promo.insert(ignore_permissions=True)

	def test_outlet_company_must_be_within_root_company(self):
		foreign_company = self._make_company("_Test PM Foreign Co")
		foreign_warehouse = self._make_warehouse("_Test PM Foreign WH", foreign_company)
		promo = self._base_promotion(
			outlets=[{"company": foreign_company, "warehouse": foreign_warehouse, "enabled": 1}]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"is not within root company"):
			promo.insert(ignore_permissions=True)

	def test_root_company_itself_is_valid_outlet(self):
		root_warehouse = self._make_warehouse("_Test PM Root WH", self.root_company)
		promo = self._base_promotion(
			outlets=[{"company": self.root_company, "warehouse": root_warehouse, "enabled": 1}]
		)
		promo.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Promotion", promo.name))

	def test_outlet_company_currency_must_match_promotion_currency(self):
		usd_child = self._make_company("_Test PM USD Child Co", parent=self.root_company, currency="USD")
		usd_child_warehouse = self._make_warehouse("_Test PM USD Child WH", usd_child)
		promo = self._base_promotion(
			outlets=[{"company": usd_child, "warehouse": usd_child_warehouse, "enabled": 1}]
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"uses currency"):
			promo.insert(ignore_permissions=True)

	# --- tax template advisory (plan item 4, frozen decision 1) -----------

	def test_option_tax_template_mismatch_warns_without_blocking(self):
		parent_template = self._make_tax_template("_Test PM Tax A")
		option_template = self._make_tax_template("_Test PM Tax B")
		self._assign_item_tax_template(self.parent_item, parent_template)
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			options=[
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.bread_c,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
			]
		)
		self._assign_item_tax_template(self.bread_b, option_template)
		self._assign_item_tax_template(self.bread_c, parent_template)
		frappe.local.message_log = []
		promo.insert(ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Promotion", promo.name))
		self.assertTrue(
			any("Item Tax Template" in text for text in self._message_log_texts()),
			msg="A mismatching option tax template must raise a warning",
		)

	def test_matching_tax_templates_do_not_warn(self):
		parent_template = self._make_tax_template("_Test PM Tax A")
		self._assign_item_tax_template(self.parent_item, parent_template)
		group_key = f"grp_{self.suffix}"
		promo = self._base_promotion(
			options=[
				{
					"choice_group_key": group_key,
					"item_code": self.bread_b,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
				{
					"choice_group_key": group_key,
					"item_code": self.bread_c,
					"price_adjustment": 0.0,
					"max_per_option": 0,
				},
			]
		)
		self._assign_item_tax_template(self.bread_b, parent_template)
		self._assign_item_tax_template(self.bread_c, parent_template)
		frappe.local.message_log = []
		promo.insert(ignore_permissions=True)
		self.assertFalse(
			any("Item Tax Template" in text for text in self._message_log_texts()),
			msg="Matching tax templates must not warn",
		)

	# --- lifecycle (plan item 5, D15) -------------------------------------

	def test_unused_promotion_deletes(self):
		promo = self._base_promotion()
		promo.insert(ignore_permissions=True)
		frappe.delete_doc("Promotion", promo.name, ignore_permissions=True)
		self.assertFalse(frappe.db.exists("Promotion", promo.name))


class TestPromotionLifecycle(IntegrationTestCase):
	"""D15 trash guard and D3 snapshot stability against real materialized sales."""

	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		# Environment pin (port mechanics, no assertion touched): this site's Stock Settings
		# enable auto_insert_price_list_rate_if_missing, which makes ERPNext's
		# get_item_details.insert_item_price write a selling Item Price row for the promotion
		# parent item when the combo line is priced at invoice submit. That row violates the
		# D12 precondition ("the promotion engine is the only writer of the parent row's
		# rate"), so the post-sale promo.save() below would throw. The source bench ran with
		# this setting off; pin it off for this class and restore the site value on cleanup.
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
		self.suffix = get_unique_suffix()
		self._setup_companies_and_warehouses()
		self._setup_items()
		self._setup_pos_profile()
		self._setup_promotion_master()

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

		self.root_company_name = f"_Test PL Root Co {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": self.root_company_name,
				"is_group": 1,
				"default_currency": "IDR",
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)

		self.outlet_company_name = f"_Test PL Outlet Co {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": self.outlet_company_name,
				"parent_company": self.root_company_name,
				"default_currency": "IDR",
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)

		self.warehouse_name = f"_Test PL Outlet WH {self.suffix}"
		self.warehouse = frappe.get_doc(
			{
				"doctype": "Warehouse",
				"warehouse_name": self.warehouse_name,
				"company": self.outlet_company_name,
			}
		).insert(ignore_permissions=True)

	def _setup_items(self):
		self.parent_item_code = f"_Test PL Parent {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.parent_item_code,
				"item_name": "Paket Combo Lifecycle",
				"item_group": "All Item Groups",
				"is_stock_item": 0,
				"is_sales_item": 1,
				"stock_uom": "Nos",
			}
		).insert(ignore_permissions=True)

		self.bread_b_code = f"_Test PL Bread B {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.bread_b_code,
				"item_name": "Roti Lifecycle B",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
				"standard_rate": 12000.0,
				"valuation_rate": 6000.0,
			}
		).insert(ignore_permissions=True)

		self.bread_c_code = f"_Test PL Bread C {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": self.bread_c_code,
				"item_name": "Roti Lifecycle C",
				"item_group": "All Item Groups",
				"is_stock_item": 1,
				"is_sales_item": 1,
				"stock_uom": "Nos",
				"standard_rate": 15000.0,
				"valuation_rate": 8000.0,
			}
		).insert(ignore_permissions=True)

		stock_entry = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"company": self.outlet_company_name,
				"items": [
					{
						"item_code": self.bread_b_code,
						"qty": 50,
						"t_warehouse": self.warehouse.name,
						"basic_rate": 6000.0,
					},
					{
						"item_code": self.bread_c_code,
						"qty": 50,
						"t_warehouse": self.warehouse.name,
						"basic_rate": 8000.0,
					},
				],
			}
		).insert(ignore_permissions=True)
		stock_entry.submit()

	def _setup_pos_profile(self):
		self.customer_name = f"_Test PL Customer {self.suffix}"
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": self.customer_name,
				"customer_group": "Individual",
				"territory": "All Territories",
				"company": self.outlet_company_name,
			}
		).insert(ignore_permissions=True)

		self.pos_profile_name = f"_Test PL POS Profile {self.suffix}"
		self.mop_name = get_default_mode_of_payment(self.outlet_company_name)
		write_off_account = get_default_account(self.outlet_company_name, "Expense")
		write_off_cc = get_default_cost_center(self.outlet_company_name)
		income_account = (
			frappe.db.get_value(
				"Account",
				{"company": self.outlet_company_name, "root_type": "Income", "is_group": 0},
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

		frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": self.pos_profile_name,
				"company": self.outlet_company_name,
				"warehouse": self.warehouse.name,
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

	def _setup_promotion_master(self):
		self.group_key = f"grp_{self.suffix}"
		self.promo = frappe.get_doc(
			{
				"doctype": "Promotion",
				"promotion_name": f"Promo Lifecycle {self.suffix}",
				"root_company": self.root_company_name,
				"parent_item": self.parent_item_code,
				"base_price": 20000.0,
				"currency": "IDR",
				"enabled": 1,
				"max_instances_per_invoice": 0,
				"components": [],
				"choice_groups": [
					{"group_key": self.group_key, "label": "Pilih Varian Roti", "pick_count": 1}
				],
				"options": [
					{
						"choice_group_key": self.group_key,
						"item_code": self.bread_b_code,
						"price_adjustment": 0.0,
						"max_per_option": 0,
					},
					{
						"choice_group_key": self.group_key,
						"item_code": self.bread_c_code,
						"price_adjustment": 5000.0,
						"max_per_option": 0,
					},
				],
				"outlets": [
					{
						"company": self.outlet_company_name,
						"warehouse": self.warehouse.name,
						"enabled": 1,
					}
				],
			}
		).insert(ignore_permissions=True)
		self.option_b = self.promo.options[0].name

	def _make_promo_invoice(self):
		payload = {
			"instances": [
				{
					"promotion": self.promo.name,
					"selections": [
						{
							"group_key": self.group_key,
							"picks": [
								{"option_row": self.option_b, "item_code": self.bread_b_code, "qty": 1}
							],
						}
					],
				}
			]
		}
		return frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"is_pos": 1,
				"company": self.outlet_company_name,
				"pos_profile": self.pos_profile_name,
				"customer": self.customer_name,
				"posting_date": nowdate(),
				"pos_pending_promotions": json.dumps(payload),
				"items": [],
				"payments": [{"mode_of_payment": self.mop_name, "amount": 20000.0}],
			}
		)

	def test_referenced_promotion_delete_is_blocked(self):
		"""D15: a submitted POS Promotion Selection referencing this Promotion blocks trash."""
		pos_inv = self._make_promo_invoice()
		pos_inv.insert()
		pos_inv.submit()
		self.assertEqual(pos_inv.docstatus, 1)
		self.assertEqual(len(pos_inv.pos_promotion_selections), 1)

		with self.assertRaisesRegex(frappe.ValidationError, r"cannot be deleted"):
			frappe.delete_doc("Promotion", self.promo.name, ignore_permissions=True)
		self.assertTrue(frappe.db.exists("Promotion", self.promo.name))

	def test_label_edit_preserves_group_key_and_selection_snapshots(self):
		"""D3/D7: editing a group label never moves group_key or stored snapshots."""
		pos_inv = self._make_promo_invoice()
		pos_inv.insert()
		pos_inv.submit()

		reloaded_before = frappe.get_doc("Sales Invoice", pos_inv.name)
		snapshots_before = [
			s.snapshot for s in reloaded_before.pos_promotion_selections
		]
		totals_before = [
			flt(s.total_amount) for s in reloaded_before.pos_promotion_selections
		]

		promo = frappe.get_doc("Promotion", self.promo.name)
		key_before = promo.choice_groups[0].group_key
		promo.choice_groups[0].label = "Pilih Varian Roti Baru"
		promo.save()
		self.assertEqual(promo.choice_groups[0].group_key, key_before)

		reloaded_after = frappe.get_doc("Sales Invoice", pos_inv.name)
		snapshots_after = [s.snapshot for s in reloaded_after.pos_promotion_selections]
		self.assertEqual(snapshots_after, snapshots_before)
		self.assertEqual(
			[flt(s.total_amount) for s in reloaded_after.pos_promotion_selections],
			totals_before,
		)
