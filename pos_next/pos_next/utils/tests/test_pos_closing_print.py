from __future__ import annotations

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from pos_next.pos_next.utils.pos_closing_print import (
	_collect_parent_targets,
	_fetch_items_for_targets,
	format_rupiah,
	get_items_sold,
	get_sales_recap,
)


class TestPOSClosingPrint(FrappeTestCase):
	@patch("pos_next.pos_next.utils.pos_closing_print.frappe.get_all")
	def test_collect_parent_targets_prefers_sales_invoice(self, mock_get_all):
		mock_get_all.return_value = [{"name": "POSINV-0002", "consolidated_invoice": None}]

		targets = _collect_parent_targets(
			[
				{"sales_invoice": "SINV-0001", "pos_invoice": "POSINV-0001"},
				{"pos_invoice": "POSINV-0002"},
				{"sales_invoice": "SINV-0002"},
			]
		)

		self.assertEqual(
			targets,
			{
				("SINV-0001", "Sales Invoice"),
				("POSINV-0002", "POS Invoice"),
				("SINV-0002", "Sales Invoice"),
			},
		)
		mock_get_all.assert_called_once()

	@patch("pos_next.pos_next.utils.pos_closing_print.frappe.get_all")
	def test_collect_parent_targets_follows_consolidated_invoice(self, mock_get_all):
		mock_get_all.return_value = [{"name": "POSINV-0001", "consolidated_invoice": "SINV-0999"}]

		targets = _collect_parent_targets([{"pos_invoice": "POSINV-0001"}])

		self.assertEqual(targets, {("SINV-0999", "Sales Invoice")})

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_items_for_targets")
	def test_get_items_sold_returns_float_values(self, mock_fetch):
		mock_fetch.return_value = [
			{
				"item_code": "ITEM-001",
				"item_name": "Latte",
				"qty": "2",
				"amount": "150.50",
			}
		]

		doc = {"pos_transactions": [{"sales_invoice": "SINV-0001"}]}
		result = get_items_sold(doc)

		self.assertEqual(
			result,
			[
				{
					"item_code": "ITEM-001",
					"item_name": "Latte",
					"qty": 2.0,
					"amount": 150.5,
				}
			],
		)
		mock_fetch.assert_called_once_with({("SINV-0001", "Sales Invoice")})

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_items_for_targets")
	def test_get_items_sold_returns_empty_when_no_transactions(self, mock_fetch):
		doc = {"pos_transactions": []}
		result = get_items_sold(doc)

		self.assertEqual(result, [])
		mock_fetch.assert_not_called()


class TestFormatRupiah(FrappeTestCase):
	def test_positive_values_use_dot_thousands_separator(self):
		self.assertEqual(format_rupiah(1200), "Rp1.200")
		self.assertEqual(format_rupiah(3384000), "Rp3.384.000")

	def test_negative_sign_comes_before_rp(self):
		self.assertEqual(format_rupiah(-500), "-Rp500")

	def test_zero(self):
		self.assertEqual(format_rupiah(0), "Rp0")

	def test_garbage_and_none_become_zero(self):
		self.assertEqual(format_rupiah(None), "Rp0")
		self.assertEqual(format_rupiah("abc"), "Rp0")

	def test_rounds_half_away_from_zero(self):
		self.assertEqual(format_rupiah(1200.5), "Rp1.201")
		self.assertEqual(format_rupiah(1200.4), "Rp1.200")
		self.assertEqual(format_rupiah(-0.5), "-Rp1")

	def test_numeric_strings_are_accepted(self):
		self.assertEqual(format_rupiah("1200"), "Rp1.200")


class TestItemsComeFromEachDoctypesOwnChildTable(FrappeTestCase):
	"""POS Invoice items live in `POS Invoice Item`, Sales Invoice items in
	`Sales Invoice Item`. Reading one table for both doctypes silently returns
	nothing for the other, which emptied the EOD "items sold" section (and the
	product-discount / category aggregates) for POS Invoices."""

	@patch("pos_next.pos_next.utils.pos_closing_print.frappe.db.sql")
	def test_items_are_read_per_doctype(self, mock_sql):
		mock_sql.return_value = [
			{"item_code": "RP001", "item_name": "Roti", "qty": 3.0, "amount": 30000.0}
		]

		rows = _fetch_items_for_targets({("PSINV-0001", "POS Invoice")})

		self.assertEqual(len(rows), 1)
		self.assertEqual(rows[0]["item_code"], "RP001")
		self.assertEqual(rows[0]["qty"], 3.0)
		# the POS Invoice branch must query the POS Invoice Item table
		queried = str(mock_sql.call_args[0][0])
		self.assertIn("`tabPOS Invoice Item`", queried)
		self.assertNotIn("`tabSales Invoice Item`", queried)


def _recap_stub_targets():
	"""Patched helpers so get_sales_recap never touches the database."""
	return [
		patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets"),
		patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets"),
		patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets"),
	]


class TestGetSalesRecap(FrappeTestCase):
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print.frappe.db.get_value")
	def test_payment_amount_is_expected_minus_opening(self, mock_get_value, mock_collect, mock_discount, mock_grouped):
		mock_collect.return_value = set()
		mock_discount.return_value = 0.0
		mock_grouped.return_value = []
		mock_get_value.return_value = "General"

		doc = {
			"grand_total": 500000,
			"pos_transactions": [{"sales_invoice": "SINV-0001"}],
			"payment_reconciliation": [
				{
					"mode_of_payment": "QRIS",
					"opening_amount": 25000,
					"expected_amount": 175000,
					"closing_amount": 175000,
				}
			],
			"taxes": [],
		}
		recap = get_sales_recap(doc)

		self.assertEqual(recap["payment_methods"], [{"mode_of_payment": "QRIS", "amount": 150000.0, "is_cash": False}])
		self.assertEqual(recap["methods_grand_total"], 150000.0)
		self.assertEqual(recap["total_cash"], 0.0)
		self.assertEqual(recap["total_non_cash"], 150000.0)

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print.frappe.db.get_value")
	def test_cash_and_non_cash_split(self, mock_get_value, mock_collect, mock_discount, mock_grouped):
		mock_collect.return_value = set()
		mock_discount.return_value = 0.0
		mock_grouped.return_value = []
		mock_get_value.side_effect = lambda doctype, name, field: "Cash" if name == "Cash" else "General"

		doc = {
			"grand_total": 500000,
			"pos_transactions": [{"sales_invoice": "SINV-0001"}],
			"payment_reconciliation": [
				{
					"mode_of_payment": "Cash",
					"opening_amount": 100000,
					"expected_amount": 350000,
					"closing_amount": 300000,
				},
				{
					"mode_of_payment": "QRIS",
					"opening_amount": 0,
					"expected_amount": 150000,
					"closing_amount": 150000,
				},
			],
			"taxes": [],
		}
		recap = get_sales_recap(doc)

		self.assertEqual(recap["cash"], {
			"opening_balance": 100000.0,
			"cash_payment": 250000.0,
			"total_expense": 0.0,
			"cash_in_hand": 300000.0,
		})
		self.assertEqual(recap["total_cash"], 250000.0)
		self.assertEqual(recap["total_non_cash"], 150000.0)
		self.assertEqual(recap["methods_grand_total"], 400000.0)
		self.assertEqual([row["is_cash"] for row in recap["payment_methods"]], [True, False])

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets")
	def test_charge_classification(self, mock_collect, mock_discount, mock_grouped):
		mock_collect.return_value = set()
		mock_discount.return_value = 0.0
		mock_grouped.return_value = []

		doc = {
			"grand_total": 100000,
			"pos_transactions": [],
			"payment_reconciliation": [],
			"taxes": [
				{"account_head": "PPN Keluaran", "rate": 11, "amount": 11000},
				{"account_head": "Service Charge", "rate": 5, "amount": 5000},
				{"account_head": "Biaya Jasa Lain", "rate": 0, "amount": 2500},
				{"account_head": "Rounding", "rate": 0, "amount": 100},
			],
		}
		recap = get_sales_recap(doc)

		self.assertEqual(recap["tax_total"], 11100.0)
		self.assertEqual(recap["service_charge"], 7500.0)

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets")
	def test_refund_total_uses_negative_transactions(self, mock_collect, mock_discount, mock_grouped):
		mock_collect.return_value = set()
		mock_discount.return_value = 0.0
		mock_grouped.return_value = []

		doc = {
			"grand_total": 100000,
			"pos_transactions": [
				{"sales_invoice": "SINV-0001", "grand_total": 200000},
				{"sales_invoice": "SINV-0002", "grand_total": -50000},
				{"sales_invoice": "SINV-0003", "grand_total": 0},
			],
			"payment_reconciliation": [],
			"taxes": [],
		}
		recap = get_sales_recap(doc)

		self.assertEqual(recap["refund_total"], 50000.0)
		self.assertEqual(recap["total_order"], 3)

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets")
	def test_average_guarded_against_zero_orders(self, mock_collect, mock_discount, mock_grouped):
		mock_collect.return_value = set()
		mock_discount.return_value = 0.0
		mock_grouped.return_value = []

		doc = {"grand_total": 0, "pos_transactions": [], "payment_reconciliation": [], "taxes": []}
		recap = get_sales_recap(doc)

		self.assertEqual(recap["average_per_order"], 0.0)
		self.assertEqual(recap["total_sales"], 0.0)
		self.assertEqual(recap["total_order"], 0)

	def test_empty_doc_renders_all_zero_recap(self):
		recap = get_sales_recap({"grand_total": 0, "pos_transactions": None, "payment_reconciliation": None, "taxes": None})

		self.assertEqual(
			recap,
			{
				"total_sales": 0.0,
				"total_order": 0,
				"average_per_order": 0.0,
				"cash": {
					"opening_balance": 0.0,
					"cash_payment": 0.0,
					"total_expense": 0.0,
					"cash_in_hand": 0.0,
				},
				"payment_methods": [],
				"total_cash": 0.0,
				"total_non_cash": 0.0,
				"methods_grand_total": 0.0,
				"service_charge": 0.0,
				"tax_total": 0.0,
				"product_discount": 0.0,
				"payment_discount": 0.0,
				"refund_total": 0.0,
				"categories": [],
			},
		)

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets")
	def test_categories_grouped_and_sorted_by_total_then_amount(self, mock_collect, mock_discount, mock_grouped):
		mock_collect.return_value = {("SINV-0001", "Sales Invoice")}
		mock_discount.return_value = 0.0
		mock_grouped.return_value = [
			{"item_group": "Food", "item_code": "ITEM-003", "item_name": "Croissant", "qty": 1, "amount": 30000},
			{"item_group": "Coffee", "item_code": "ITEM-001", "item_name": "Latte", "qty": 2, "amount": 40000},
			{"item_group": "Coffee", "item_code": "ITEM-002", "item_name": "Americano", "qty": 1, "amount": 25000},
		]

		doc = {"grand_total": 95000, "pos_transactions": [{"sales_invoice": "SINV-0001"}], "payment_reconciliation": [], "taxes": []}
		recap = get_sales_recap(doc)

		self.assertEqual(
			recap["categories"],
			[
				{
					"category": "Coffee",
					"total": 65000.0,
					"items": [
						{"qty": 2.0, "item_name": "Latte", "amount": 40000.0},
						{"qty": 1.0, "item_name": "Americano", "amount": 25000.0},
					],
				},
				{
					"category": "Food",
					"total": 30000.0,
					"items": [{"qty": 1.0, "item_name": "Croissant", "amount": 30000.0}],
				},
			],
		)

	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_grouped_items_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._fetch_discount_for_targets")
	@patch("pos_next.pos_next.utils.pos_closing_print._collect_parent_targets")
	def test_product_discount_comes_from_sibling_query(self, mock_collect, mock_discount, mock_grouped):
		mock_collect.return_value = {("SINV-0001", "Sales Invoice")}
		mock_discount.return_value = 12500.0
		mock_grouped.return_value = []

		doc = {"grand_total": 95000, "pos_transactions": [{"sales_invoice": "SINV-0001"}], "payment_reconciliation": [], "taxes": []}
		recap = get_sales_recap(doc)

		self.assertEqual(recap["product_discount"], 12500.0)
		mock_collect.assert_called_once_with(doc["pos_transactions"])
		mock_discount.assert_called_once_with({("SINV-0001", "Sales Invoice")})

class TestEodReportCashierName(FrappeTestCase):
	"""The EOD template shows the User's display name, not the login email."""

	@staticmethod
	def _clear_jinja_env_cache():
		import frappe

		# Local.__iter__ yields (key, value) pairs, so `key in frappe.local`
		# is always False — delete directly and treat absence as done. The env
		# caches the `frappe` namespace built when it is first created, so a
		# patched frappe.db.get_value is only visible after this reset.
		for key in ("jenv_restricted", "jenv_unrestricted"):
			try:
				delattr(frappe.local, key)
			except AttributeError:
				pass

	def test_cashier_row_prints_display_name_with_login_fallback(self):
		import json
		import os
		from types import SimpleNamespace
		from unittest.mock import patch

		import frappe

		template_path = os.path.join(
			os.path.dirname(__file__),
			"..",
			"..",
			"print_format",
			"pos_next_eod_report",
			"pos_next_eod_report.json",
		)
		with open(template_path) as template_file:
			template = json.load(template_file)["html"]

		doc = SimpleNamespace(
			name="POCLO-TEST-0001",
			company="Test Co",
			user="kasir@example.com",
			period_start_date="2026-10-05 08:00:00",
			period_end_date="2026-10-05 17:00:00",
		)
		recap = {
			"total_sales": 0,
			"total_order": 0,
			"average_per_order": 0,
			"cash": {
				"opening_balance": 0,
				"cash_payment": 0,
				"total_expense": 0,
				"cash_in_hand": 0,
			},
			"payment_methods": [],
			"total_cash": 0,
			"total_non_cash": 0,
			"methods_grand_total": 0,
			"service_charge": 0,
			"tax_total": 0,
			"product_discount": 0,
			"payment_discount": 0,
			"refund_total": 0,
			"categories": [],
		}
		context = {
			"doc": doc,
			"get_sales_recap": lambda _doc: recap,
			"format_rupiah": lambda value=0: f"Rp {value}",
		}

		# Happy path: the User record carries a display name. A real function
		# (not a Mock) is required — Jinja's sandbox refuses MagicMock callables.
		def get_value_with_name(*_args, **_kwargs):
			return "Kasir Contoh"

		# No User record / no full name -> the login id is the honest fallback.
		def get_value_without_name(*_args, **_kwargs):
			return None

		with patch("frappe.db.get_value", new=get_value_with_name):
			self._clear_jinja_env_cache()
			rendered = frappe.render_template(template, context)
		self.assertIn("Kasir Contoh", rendered)
		self.assertNotIn("kasir@example.com", rendered)

		with patch("frappe.db.get_value", new=get_value_without_name):
			self._clear_jinja_env_cache()
			rendered = frappe.render_template(template, context)
		self.assertIn("kasir@example.com", rendered)

		# Leave a clean env behind: the cached namespace would otherwise keep
		# the stubbed get_value alive for every later render in this process.
		self._clear_jinja_env_cache()
