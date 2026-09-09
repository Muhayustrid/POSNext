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
	def _make_invoice(cls, company, profile, cash_mode, rows, paid, is_return=False, currency=None, posting_date=None):
		inv = frappe.new_doc("Sales Invoice")
		inv.company = company
		inv.customer = cls.customer
		inv.is_pos = 1
		if posting_date:
			inv.set_posting_time = 1
			inv.posting_date = posting_date
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
		# outlet rows are company-level and carry their own currency
		outlets = {o["company"]: o for o in data["outlet_ranking"]}
		self.assertEqual(outlets[self.company_b]["currency"], self.currency_b)
		self.assertEqual(outlets[self.company_b]["orders"], 1)
		# POS profiles stay nested under their company (nothing hidden)
		profile_b = next(
			p for p in outlets[self.company_b]["profiles"] if p["pos_profile"] == self.profile_b
		)
		self.assertEqual(profile_b["orders"], 1)

	def test_outlet_ranking_is_company_level(self):
		data = self._payload()  # company A scope
		rows = data["outlet_ranking"]
		self.assertEqual([r["company"] for r in rows], [self.company_a])
		row = rows[0]
		self.assertEqual(row["currency"], self.currency_a)
		self.assertEqual(row["orders"], 3)
		self.assertEqual(row["share_pct"], 100.0)  # share inside one currency
		self.assertEqual([p["pos_profile"] for p in row["profiles"]], [self.profile_a])

	def test_category_top_positive_only_labeled_currency(self):
		data = self._payload()
		ct = data["category_top"]
		self.assertTrue(ct["rows"], "fixture has positive-sales groups")
		self.assertEqual(ct["currency"], self.currency_a)
		self.assertIn("net revenue", ct["method"])  # chosen method is labeled
		groups = {r["item_group"] for r in ct["rows"]}
		self.assertIn(frappe.db.get_value("Item", self.item_x, "item_group"), groups)
		for r in ct["rows"]:
			self.assertGreater(r["net_amount"], 0)  # donut segments positive-only
			self.assertGreater(r["share_pct"], 0)

	# ------------------------------------------------------------------
	# per-category "top items" cards (category_a / category_b)
	# ------------------------------------------------------------------

	def _groups(self):
		return (
			frappe.db.get_value("Item", self.item_x, "item_group"),
			frappe.db.get_value("Item", self.item_y, "item_group"),
		)

	def test_category_product_cards_isolated_with_shares(self):
		group_x, group_y = self._groups()
		data = self._payload(category_a=group_x, category_b=group_y)
		a = data["category_products"]["a"]
		b = data["category_products"]["b"]

		# isolation: each card holds only items of its own category
		self.assertEqual(a["category"], group_x)
		self.assertEqual({r["item_code"] for r in a["items"]}, {self.pkg_parent, self.item_x})
		self.assertEqual(b["category"], group_y)
		self.assertEqual([r["item_code"] for r in b["items"]], [self.item_y])

		# full-dataset aggregation over the category: pkg 3000 + x 1000 (2 sold,
		# 1 returned); shares are of the WHOLE category, "other" is the remainder
		self.assertEqual(a["category_total"], 4000.0)
		self.assertEqual([r["net_amount"] for r in a["items"]], [3000.0, 1000.0])
		self.assertEqual([r["share_pct"] for r in a["items"]], [75.0, 25.0])
		self.assertEqual(a["other_net"], 0.0)
		self.assertEqual(a["items_with_sales"], 2)
		self.assertEqual(a["currency"], self.currency_a)
		self.assertEqual(b["category_total"], 500.0)
		self.assertEqual(b["items"][0]["share_pct"], 100.0)

		# package components never appear (bundle revenue stays on the parent;
		# the SQL filter drops them entirely, so nothing non-positive remains)
		self.assertNotIn(self.pkg_component, {r["item_code"] for r in a["items"]})
		self.assertEqual(a["excluded_nonpositive"], 0)

	def test_category_product_cards_include_descendants(self):
		group_x, _ = self._groups()
		parent = self._make_group("_Test HQ Parent Group", 1)
		child = self._make_group("_Test HQ Child Group", 0, parent)
		code = make_test_item("HQMON CHILD")
		frappe.db.set_value("Item", code, "item_group", child)
		name = self._make_invoice(
			self.company_a,
			self.profile_a,
			self.cash_a,
			[{"item": code, "qty": 1, "rate": 700}],
			paid=700,
		)
		try:
			data = self._payload(category_a=parent, category_b=group_x)
			a = data["category_products"]["a"]
			self.assertIn(code, {r["item_code"] for r in a["items"]})
			self.assertEqual(a["category_total"], 700.0)  # only the child sells
			# sibling branch untouched by the parent subtree
			b = data["category_products"]["b"]
			self.assertNotIn(code, {r["item_code"] for r in b["items"]})
		finally:
			frappe.get_doc("Sales Invoice", name).cancel()

	def test_category_product_cards_currency_isolated(self):
		from pos_next.api.hq_monitoring import _category_products

		group_x, _ = self._groups()
		out = _category_products(
			[self.company_a, self.company_b],
			None,
			frappe.utils.nowdate(),
			frappe.utils.nowdate(),
			None,
			{self.company_a: self.currency_a, self.company_b: self.currency_b},
			self.currency_a,
			{"a": group_x, "b": None},
		)
		# company B sells item_x in another currency: listed, never merged in
		self.assertEqual(out["a"]["category_total"], 4000.0)
		self.assertEqual(out["a"]["excluded_currencies"], [self.currency_b])
		self.assertEqual(out["b"]["category"], None)
		self.assertEqual(out["b"]["items"], [])

	def test_category_product_cards_invalid_and_insufficient_data(self):
		group_x, group_y = self._groups()
		# stale stored category: flagged, not raised, so the page can recover
		data = self._payload(category_a="_Test HQ Missing Group", category_b=group_y)
		a = data["category_products"]["a"]
		self.assertTrue(a["invalid"])
		self.assertEqual(a["items"], [])
		self.assertFalse(data["category_products"]["b"]["invalid"])

		# category chosen but no sales in the selected range: empty, zero, N/A
		yesterday = frappe.utils.add_days(frappe.utils.nowdate(), -1)
		data = self._payload(from_date=yesterday, to_date=yesterday, category_a=group_x)
		a = data["category_products"]["a"]
		self.assertFalse(a["invalid"])
		self.assertEqual(a["items"], [])
		self.assertEqual(a["category_total"], 0.0)
		self.assertIsNone(a["other_share_pct"])

	def test_category_cards_independent_of_global_ranking_filter(self):
		group_x, group_y = self._groups()
		# the Product Ranking category filter must not touch the cards...
		data = self._payload(category=group_y, category_a=group_x, category_b=group_y)
		self.assertEqual({r["item_code"] for r in data["product_ranking"]["rows"]}, {self.item_y})
		a = data["category_products"]["a"]
		self.assertEqual({r["item_code"] for r in a["items"]}, {self.pkg_parent, self.item_x})
		# ...and item_groups for the card selects always come back
		self.assertIn(group_x, data["item_groups"])
		self.assertIn(group_y, data["item_groups"])

	@classmethod
	def _make_group(cls, name, is_group, parent=None):
		if frappe.db.exists("Item Group", name):
			return name
		if parent is None:
			parent = (
				"All Item Groups"
				if frappe.db.exists("Item Group", "All Item Groups")
				else frappe.db.get_value("Item Group", {"is_group": 1}, "name")
			)
		doc = frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": name,
				"is_group": is_group,
				"parent_item_group": parent,
			}
		)
		doc.insert(ignore_permissions=True)
		return doc.name

	def test_daily_compares_same_weekday_last_week(self):
		# An invoice posted exactly 7 days back is the ONLY sales in the
		# last-week comparator window: it must drive "Growth vs LW" while the
		# selected day (today) keeps the class fixtures only.
		last_week = frappe.utils.add_days(frappe.utils.nowdate(), -7)
		name = self._make_invoice(
			self.company_a,
			self.profile_a,
			self.cash_a,
			[{"item": self.item_x, "qty": 1, "rate": 400}],
			paid=400,
			posting_date=last_week,
		)
		try:
			data = self._payload()
			d = data["daily"]
			self.assertEqual(d["last_week_same"]["date"], last_week)
			# selected-day result: today's class fixtures, not the backdated one
			self.assertEqual(d["totals"]["orders"], 3)
			self.assertEqual(d["totals"]["net_tax_incl"]["by_currency"][self.currency_a], 4500.0)
			# comparator window holds exactly the backdated invoice
			self.assertEqual(d["last_week_same"]["orders"], 1)
			self.assertEqual(d["last_week_same"]["net_tax_incl"]["by_currency"][self.currency_a], 400.0)
			self.assertEqual(
				d["growth_vs_last_week_pct"][self.currency_a], round((4500 - 400) / 400 * 100, 2)
			)
		finally:
			# Cancel so the shared class fixture stays exact for sibling tests.
			frappe.get_doc("Sales Invoice", name).cancel()

	def test_range_metrics_follow_selected_range(self):
		# An invoice posted yesterday: the selected range (yesterday..today)
		# must include it while the daily section (today only) must not.
		yesterday = frappe.utils.add_days(frappe.utils.nowdate(), -1)
		name = self._make_invoice(
			self.company_a,
			self.profile_a,
			self.cash_a,
			[{"item": self.item_x, "qty": 1, "rate": 250}],
			paid=250,
			posting_date=yesterday,
		)
		try:
			data = self._payload(from_date=yesterday, to_date=yesterday)
			self.assertEqual(data["scope"]["from_date"], yesterday)
			r = data["range"]
			self.assertEqual(r["orders"], 1)
			self.assertEqual(r["net_tax_incl"]["by_currency"][self.currency_a], 250.0)
			self.assertEqual(r["apc"]["by_currency"][self.currency_a], 250.0)
			# daily section stays on its own window (to_date), not the range
			# (here to_date == yesterday, so the daily result IS the range day)
			self.assertEqual(data["daily"]["totals"]["orders"], 1)
		finally:
			# Cancel so the shared class fixture stays exact for sibling tests.
			frappe.get_doc("Sales Invoice", name).cancel()

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
		# TC projection = MTD orders / days elapsed x days in month
		elapsed = data["windows"]["days_elapsed"]
		self.assertEqual(t["projected_orders"], round(3 / elapsed * dim))

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
