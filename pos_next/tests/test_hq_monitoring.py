# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Tests for the HQ Sales Monitoring API, scope enforcement, targets doctype
and the Sales vs Shifts Report company filter.

Fixture dataset (all posted today, server tz):

Company A (currency CA), profile A1:
  inv1  item_x  qty  2 @ 1000 -> 2000
  inv2  item_y  qty  1 @  500 ->  500
  inv3  RETURN of item_x -1 @ 1000
  inv4  package parent qty 1 @ 3000 (role Package) + component qty 5 @ 0
        (role Package Item)
Company B (currency CB, different from CA), profile B1:
  inv5  item_x qty 1 @ 100
"""

from datetime import date, timedelta

import frappe
from frappe.tests import IntegrationTestCase

from pos_next.api.hq_monitoring import get_sales_monitoring, growth_pct, previous_weekday, ratio
from pos_next.tests.price_group_helpers import (
	get_default_company,
	get_default_currency,
	make_test_company,
	make_test_item,
	make_test_pos_profile,
	make_test_warehouse,
)

ADMIN = "Administrator"
USER_ALL = "hq.mon.all@example.com"
USER_CO_A = "hq.mon.co.a@example.com"
USER_NO_ROLE = "hq.mon.nobody@example.com"


def _make_hq_user(email, roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "HQ", "roles": [{"role": r} for r in roles]}
		).insert(ignore_permissions=True)
	return email


def _grant_company(user, company):
	# Reset first: earlier committed runs may have left stray company perms.
	for name in frappe.get_all("User Permission", filters={"user": user, "allow": "Company"}, pluck="name"):
		frappe.delete_doc("User Permission", name, ignore_permissions=True)
	frappe.get_doc(
		{"doctype": "User Permission", "user": user, "allow": "Company", "for_value": company}
	).insert(ignore_permissions=True)


class TestHQMonitoring(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		# Dedicated companies (the shared default company carries unrelated POS
		# invoices from other tests / the site). Nothing here is committed, so
		# the class-end rollback removes every artifact.
		cls.currency_a = "IDR"
		cls.currency_b = next((c for c in ("USD", "EUR", "SGD") if c != cls.currency_a), "USD")
		cls.company_a = cls._make_hq_company("_Test HQ Co A", cls.currency_a)
		cls.company_b = cls._make_hq_company("_Test HQ Co B", cls.currency_b)
		# Wipe invoices of every HQ test company (covers leftovers of earlier
		# runs under slightly different company names) so counts are exact.
		for name in frappe.get_all("Company", filters={"name": ["like", "_Test HQ Co %"]}, pluck="name"):
			cls._wipe_company_invoices(name)

		cls.profile_a = make_test_pos_profile(
			"HQMA", cls.company_a, make_test_warehouse("HQMA", cls.company_a)
		)
		cls.profile_b = make_test_pos_profile(
			"HQMB", cls.company_b, make_test_warehouse("HQMB", cls.company_b)
		)
		# refund code gate fails closed; disable it for these test profiles
		for profile in (cls.profile_a, cls.profile_b):
			if not frappe.db.exists("POS Settings", {"pos_profile": profile}):
				frappe.get_doc(
					{"doctype": "POS Settings", "pos_profile": profile, "require_refund_code": 0}
				).insert(ignore_permissions=True)
		cls.customer = frappe.db.get_value("Customer", {}, "name")
		cls.cash_a = cls._cash_mode(cls.company_a)
		cls.cash_b = cls._cash_mode(cls.company_b)

		cls.item_x = make_test_item("HQMON X")
		cls.item_y = cls._item_in_other_group("HQMON Y")
		cls.pkg_parent = make_test_item("HQMON PKG")
		cls.pkg_component = make_test_item("HQMON PKGC")

		cls._make_invoice(
			cls.company_a,
			cls.profile_a,
			cls.cash_a,
			[{"item": cls.item_x, "qty": 2, "rate": 1000}],
			paid=2000,
		)
		cls._make_invoice(
			cls.company_a, cls.profile_a, cls.cash_a, [{"item": cls.item_y, "qty": 1, "rate": 500}], paid=500
		)
		cls._make_invoice(
			cls.company_a,
			cls.profile_a,
			cls.cash_a,
			[{"item": cls.item_x, "qty": -1, "rate": 1000}],
			paid=-1000,
			is_return=True,
		)
		cls._make_invoice(
			cls.company_a,
			cls.profile_a,
			cls.cash_a,
			[
				{"item": cls.pkg_parent, "qty": 1, "rate": 3000, "role": "Package"},
				{"item": cls.pkg_component, "qty": 5, "rate": 0, "role": "Package Item"},
			],
			paid=3000,
		)
		cls._make_invoice(
			cls.company_b,
			cls.profile_b,
			cls.cash_b,
			[{"item": cls.item_x, "qty": 1, "rate": 100}],
			paid=100,
			currency=cls.currency_b,
		)

		cls._make_target(cls.company_a)
		cls._make_target(cls.company_b)

		cls.user_all = _make_hq_user(USER_ALL, ["Sales Manager"])
		cls.user_co_a = _make_hq_user(USER_CO_A, ["Sales Manager"])
		_grant_company(cls.user_co_a, cls.company_a)
		cls.user_no_role = _make_hq_user(USER_NO_ROLE, ["Sales User"])

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		super().tearDownClass()

	# ------------------------------------------------------------------
	# fixtures
	# ------------------------------------------------------------------

	@classmethod
	def _make_hq_company(cls, name, currency):
		if frappe.db.exists("Company", name):
			return name
		abbr = "HQ" + "".join(c for c in name if c.isalpha())[-3:]
		doc = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": name,
				"abbr": abbr,
				"default_currency": currency,
				"country": "Indonesia",
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	@classmethod
	def _wipe_company_invoices(cls, company):
		"""Drop leftover invoices (and their ledgers) of a test-only company.

		Earlier failed runs committed class fixtures, so a rerun could double
		the dataset. Scoped strictly to throwaway `_Test HQ Co *` companies.
		"""
		names = frappe.get_all("Sales Invoice", filters={"company": company}, pluck="name")
		if not names:
			return
		frappe.db.delete("Sales Invoice Payment", {"parent": ["in", names]})
		frappe.db.delete("Sales Invoice Item", {"parent": ["in", names]})
		frappe.db.delete("Sales Invoice", {"company": company})
		frappe.db.delete("GL Entry", {"company": company})
		frappe.db.delete("Payment Ledger Entry", {"company": company})

	@classmethod
	def _cash_mode(cls, company):
		mode = frappe.db.get_value("Mode of Payment", {"enabled": 1, "type": "Cash"}, "name")
		if not mode:
			mode = "Cash"
		if not frappe.db.exists("Mode of Payment Account", {"parent": mode, "company": company}):
			doc = frappe.get_doc("Mode of Payment", mode)
			doc.append(
				"accounts",
				{
					"company": company,
					"default_account": frappe.get_cached_value("Company", company, "default_cash_account"),
				},
			)
			doc.save(ignore_permissions=True)
		return mode

	@classmethod
	def _item_in_other_group(cls, suffix):
		code = make_test_item(suffix)
		group = frappe.db.get_value(
			"Item Group",
			{"is_group": 0, "name": ("!=", frappe.db.get_value("Item", code, "item_group"))},
			"name",
		)
		if group:
			frappe.db.set_value("Item", code, "item_group", group)
		return code

	@classmethod
	def _make_target(cls, company):
		if frappe.db.exists(
			"POS Monthly Target",
			{"company": company, "month_start": frappe.utils.get_first_day(frappe.utils.nowdate())},
		):
			return
		frappe.get_doc(
			{
				"doctype": "POS Monthly Target",
				"company": company,
				"month_start": frappe.utils.get_first_day(frappe.utils.nowdate()),
				"target_sales": 100000,
				"target_transactions": 10,
			}
		).insert(ignore_permissions=True)

	@classmethod
	def _make_invoice(cls, company, profile, cash_mode, rows, paid, is_return=False, currency=None):
		inv = frappe.new_doc("Sales Invoice")
		inv.company = company
		inv.customer = cls.customer
		inv.is_pos = 1
		if currency:
			# non-site-currency company: keep document currency on the company's
			# own currency (price list would otherwise pull the site default)
			inv.currency = currency
			inv.conversion_rate = 1
		if is_return:
			inv.is_return = 1
		for row in rows:
			inv.append(
				"items",
				{
					"item_code": row["item"],
					"qty": row["qty"],
					"rate": row["rate"],
					"price_list_rate": row["rate"],
					"pos_package_role": row.get("role"),
				},
			)
		inv.append("payments", {"mode_of_payment": cash_mode, "amount": paid, "base_amount": paid})
		inv.insert(ignore_permissions=True)
		inv.submit()
		return inv.name

	# ------------------------------------------------------------------
	# pure helpers
	# ------------------------------------------------------------------

	def test_pure_helpers(self):
		self.assertIsNone(ratio(1, 0))
		self.assertIsNone(growth_pct(100, 0))  # prior 0 -> never fake 0%
		self.assertEqual(growth_pct(150, 100), 50.0)
		self.assertEqual(ratio(50, 200), 25.0)

		monday = date.today() - timedelta(days=date.today().weekday())
		friday = monday - timedelta(days=3)
		self.assertEqual(previous_weekday(monday), friday)
		# Tuesday -> Monday
		self.assertEqual(previous_weekday(monday + timedelta(days=1)), monday)

	# ------------------------------------------------------------------
	# endpoint metrics
	# ------------------------------------------------------------------

	def _payload(self, user=ADMIN, **overrides):
		frappe.set_user(user)
		args = {"company": self.company_a, "page_size": 10}
		args.update(overrides)
		return get_sales_monitoring(**args)

	def test_scope_metrics_company_a(self):
		data = self._payload()
		m = data["monthly"]
		self.assertEqual(m["orders"], 3)  # refund invoice is not a new order
		self.assertEqual(m["refund_orders"], 1)
		self.assertEqual(m["net_tax_incl"]["by_currency"][self.currency_a], 4500.0)
		self.assertEqual(m["gross"]["by_currency"][self.currency_a], 5500.0)
		self.assertEqual(m["refunds"]["by_currency"][self.currency_a], 1000.0)
		# default currency labeled explicitly, never a raw mixed sum
		self.assertEqual(m["net_tax_incl"]["default_currency"], self.currency_a)

	def test_refunds_negative_qty_and_package_component_excluded(self):
		data = self._payload()
		fav = data["favorite_product"]
		# qty ranking: parent pkg (qty1, 3000) wins the qty/net tie-break;
		# the component (qty 5 @ 0) must never appear anywhere.
		self.assertEqual(fav["item_code"], self.pkg_parent)
		codes = {r["item_code"] for r in data["product_ranking"]["rows"]}
		self.assertNotIn(self.pkg_component, codes)
		self.assertEqual(data["product_ranking"]["total"], 3)  # x, y, pkg

		x_row = next(r for r in data["product_ranking"]["rows"] if r["item_code"] == self.item_x)
		self.assertEqual(x_row["qty"], 1)  # 2 sold - 1 returned

	def test_ranking_sort_tie_and_pagination(self):
		data = self._payload(page_size=2)
		pr = data["product_ranking"]
		self.assertEqual(pr["page"], 1)
		self.assertEqual(pr["page_size"], 2)
		self.assertEqual(pr["total"], 3)
		# tie on net_amount (1000): qty 2... x net=1000 qty1, y net=500 -> order by net desc
		nets = [r["net_amount"] for r in pr["rows"]]
		self.assertEqual(nets, sorted(nets, reverse=True))
		self.assertEqual(pr["rows"][0]["item_code"], self.pkg_parent)  # 3000
		# stable tie-break: same net -> higher qty first
		data2 = self._payload(page_size=10)
		rows = data2["product_ranking"]["rows"]
		x = next(r for r in rows if r["item_code"] == self.item_x)
		y = next(r for r in rows if r["item_code"] == self.item_y)
		self.assertLess(rows.index(x), rows.index(y))  # 1000 > 500 sorts first
		self.assertEqual(x["share_pct"], round(1000 / 4500 * 100, 2))

	def test_category_filter_scope(self):
		data = self._payload(category=frappe.db.get_value("Item", self.item_y, "item_group"))
		pr = data["product_ranking"]
		self.assertEqual({r["item_code"] for r in pr["rows"]}, {self.item_y})

	def test_mixed_currency_never_raw_summed(self):
		data = self._payload(company=None)  # all companies (admin unrestricted)
		net = data["monthly"]["net_tax_incl"]["by_currency"]
		self.assertIn(self.currency_a, net)
		self.assertIn(self.currency_b, net)
		self.assertEqual(net[self.currency_b], 100.0)
		self.assertEqual(len(data["scope"]["currency_map"]), len(data["scope"]["companies"]))
		# outlet rows carry their own currency
		outlets = {o["pos_profile"]: o for o in data["outlet_ranking"]}
		self.assertEqual(outlets[self.profile_b]["currency"], self.currency_b)
		self.assertEqual(outlets[self.profile_b]["orders"], 1)

	def test_targets_available_and_achievement(self):
		data = self._payload()
		t = data["targets"]
		self.assertTrue(t["available"])
		self.assertEqual(t["target_sales"]["by_currency"][self.currency_a], 100000.0)
		self.assertEqual(t["target_transactions"], 10)
		expected = round(4500 / 100000 * 100, 2)
		self.assertEqual(t["achievement_sales_pct"][self.currency_a], expected)
		# daily target is pro-rata monthly / days-in-month, not invented
		dim = data["windows"]["days_in_month"]
		self.assertEqual(t["daily_target_sales"][self.currency_a], round(100000 / dim, 2))
		self.assertEqual(t["apc_target"][self.currency_a], 10000.0)

	def test_targets_missing_marked_unavailable(self):
		# drop company A's target: aggregate over A scope must go N/A, not partial
		frappe.db.delete("POS Monthly Target", {"company": self.company_a})
		data = self._payload()
		self.assertFalse(data["targets"]["available"])
		self.assertIn(self.company_a, data["targets"]["missing_companies"])
		self._make_target(self.company_a)  # restore for sibling tests

	# ------------------------------------------------------------------
	# permission enforcement
	# ------------------------------------------------------------------

	def test_no_role_denied(self):
		with self.assertRaises(frappe.PermissionError):
			self._payload(user=self.user_no_role)

	def test_forged_company_denied(self):
		with self.assertRaises(frappe.PermissionError):
			self._payload(user=self.user_co_a, company=self.company_b)

	def test_nonowner_scopes_to_permitted_companies_only(self):
		data = self._payload(user=self.user_co_a, company=None)
		self.assertEqual(data["scope"]["companies"], [self.company_a])
		self.assertEqual(data["monthly"]["net_tax_incl"]["by_currency"].get(self.currency_b), None)
		self.assertEqual(data["monthly"]["net_tax_incl"]["by_currency"][self.currency_a], 4500.0)

	def test_report_company_filter_forged_denied(self):
		from pos_next.pos_next.report.sales_vs_shifts_report.sales_vs_shifts_report import execute

		frappe.set_user(self.user_co_a)
		with self.assertRaises(frappe.PermissionError):
			execute({"company": self.company_b, "from_date": "2026-01-01", "to_date": frappe.utils.nowdate()})
		# blank company is also scoped to permitted companies (no explosion)
		columns, rows = execute({"from_date": "2026-01-01", "to_date": frappe.utils.nowdate()})[:2]
		self.assertIsInstance(rows, list)


class TestPOSMonthlyTarget(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		cls.company = get_default_company()

	def test_month_start_must_be_first_day(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Monthly Target",
				"company": self.company,
				"month_start": "2026-08-15",
				"target_sales": 1000,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)

	def test_duplicate_company_month_denied(self):
		# (company, month) uniqueness comes from the deterministic autoname PK
		month = frappe.utils.get_first_day(frappe.utils.nowdate())
		values = {"company": self.company, "month_start": month, "target_sales": 1000}
		if not frappe.db.exists("POS Monthly Target", {"company": self.company, "month_start": month}):
			frappe.get_doc({"doctype": "POS Monthly Target", **values}).insert(ignore_permissions=True)
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc({"doctype": "POS Monthly Target", **values}).insert(ignore_permissions=True)

	def test_negative_target_denied(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Monthly Target",
				"company": self.company,
				"month_start": "2026-08-01",
				"target_sales": -5,
			}
		)
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)
