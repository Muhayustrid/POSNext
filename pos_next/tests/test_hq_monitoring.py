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

from pos_next.api.hq_monitoring import (
	get_outlet_targets,
	get_sales_monitoring,
	growth_pct,
	previous_weekday,
	ratio,
	set_outlet_target,
)
from pos_next.api.invoices import submit_invoice
from pos_next.install import sync_custom_fields
from pos_next.target_basis import (
	GROSS_PROFIT,
	NET_PROFIT,
	NET_SALES,
	TARGET_BASIS_LABELS,
	get_target_basis,
)
from pos_next.tests.price_group_helpers import (
	get_default_company,
	get_default_currency,
	get_default_account,
	get_default_cost_center,
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
		# The overall-target Company custom fields must exist for this suite —
		# a fresh checkout runs tests before its first migrate.
		sync_custom_fields(quiet=True)
		# Dedicated companies (the shared default company carries unrelated POS
		# invoices from other tests / the site). Nothing here is committed, so
		# the class-end rollback removes every artifact.
		cls.currency_a = "IDR"
		cls.currency_b = next((c for c in ("USD", "EUR", "SGD") if c != cls.currency_a), "USD")
		cls.company_a = cls._make_hq_company("_Test HQ Co A", cls.currency_a)
		cls.company_b = cls._make_hq_company("_Test HQ Co B", cls.currency_b)
		# Ad-hoc test companies need an active Fiscal Year covering today — the
		# site's real FY lists its outlets explicitly and covers nothing else.
		cls._ensure_fiscal_year(cls.company_a)
		cls._ensure_fiscal_year(cls.company_b)
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
		# The site's real customers restrict "Allowed To Transact With" to their
		# outlet companies; a fresh unrestricted customer transacts with any of
		# the ad-hoc test companies.
		cls.customer = cls._make_customer()
		cls.cash_a = cls._cash_mode(cls.company_a)
		cls.cash_b = cls._cash_mode(cls.company_b)

		cls.item_x = make_test_item("HQMON X")
		cls.item_y = cls._item_in_other_group("HQMON Y")
		cls.pkg_parent = make_test_item("HQMON PKG")
		cls.pkg_component = make_test_item("HQMON PKGC")
		# The site manages selling price lists itself (price groups) and has no
		# global default, so invoices bind an explicit per-currency list.
		cls.price_list_a = cls._make_price_list(cls.currency_a)
		cls.price_list_b = cls._make_price_list(cls.currency_b)

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
	def _make_price_list(cls, currency):
		name = f"_Test HQ PL {currency}"
		if not frappe.db.exists("Price List", name):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": name,
					"currency": currency,
					"selling": 1,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		return name

	@classmethod
	def _make_customer(cls):
		name = "_Test HQ Customer"
		existing = frappe.db.exists("Customer", {"customer_name": name})
		if existing:
			return existing
		# naming may be by series — the doc's name, not customer_name, is the link
		return (
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": name, "customer_type": "Individual"}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def _ensure_fiscal_year(cls, company):
		year = frappe.utils.nowdate()[:4]
		name = f"_Test HQ FY {year}"
		if not frappe.db.exists("Fiscal Year", name):
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": name,
					"year_start_date": f"{year}-01-01",
					"year_end_date": f"{year}-12-31",
					"companies": [{"company": company}],
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.exists("Fiscal Year Company", {"parent": name, "company": company}):
			doc = frappe.get_doc("Fiscal Year", name)
			doc.append("companies", {"company": company})
			doc.save(ignore_permissions=True)

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
		inv.selling_price_list = cls.price_list_b if currency else cls.price_list_a
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

	def test_targets_by_company_rows(self):
		data = self._payload()
		rows = data["targets"]["by_company"]
		self.assertEqual([r["company"] for r in rows], [self.company_a])
		row = rows[0]
		self.assertFalse(row["missing"])
		self.assertEqual(row["currency"], self.currency_a)
		self.assertEqual(row["target_sales"], 100000.0)
		self.assertEqual(row["target_transactions"], 10)
		self.assertEqual(row["mtd_net_tax_incl"], 4500.0)
		self.assertEqual(row["mtd_orders"], 3)
		self.assertEqual(row["mtd_apc"], 1500.0)
		self.assertEqual(row["achievement_sales_pct"], round(4500 / 100000 * 100, 2))
		self.assertEqual(row["achievement_transactions_pct"], 30.0)
		elapsed = data["windows"]["days_elapsed"]
		dim = data["windows"]["days_in_month"]
		self.assertEqual(row["projected_sales"], round(4500 / elapsed * dim, 2))

	def test_overall_target_absent_when_unset(self):
		self.assertFalse(self._payload()["targets"]["overall"]["available"])

	def test_overall_target_cumulative_and_from_date(self):
		frappe.set_user(ADMIN)
		frappe.db.set_value(
			"Company",
			self.company_a,
			{"pos_overall_sales_target": 10000, "pos_overall_target_from": None},
		)
		data = self._payload()  # scope = company A only
		overall = data["targets"]["overall"]
		self.assertTrue(overall["available"])
		row = overall["by_company"][self.company_a]
		self.assertEqual(row["overall_target"], 10000.0)
		self.assertIsNone(row["from_date"])
		self.assertEqual(row["cumulative_net_tax_incl"], 4500.0)
		self.assertEqual(row["cumulative_orders"], 3)
		self.assertEqual(row["achievement_pct"], 45.0)
		self.assertEqual(row["remaining"], 5500.0)

		# each outlet pins its own lower bound: counting from tomorrow drops
		# today's invoices from the cumulative sum
		frappe.db.set_value(
			"Company",
			self.company_a,
			"pos_overall_target_from",
			frappe.utils.add_days(frappe.utils.nowdate(), 1),
		)
		row = self._payload()["targets"]["overall"]["by_company"][self.company_a]
		self.assertEqual(row["cumulative_net_tax_incl"], 0.0)
		self.assertEqual(row["achievement_pct"], 0.0)

	def test_set_outlet_target_updates_and_creates(self):
		frappe.set_user(ADMIN)
		month = frappe.utils.get_first_day(frappe.utils.nowdate())
		out = set_outlet_target(
			company=self.company_a,
			month_start=month,
			target_sales=200000,
			target_transactions=20,
			overall_target=500000,
			overall_from="",
		)
		self.assertTrue(out["monthly"])
		self.assertTrue(out["overall_updated"])
		doc = frappe.get_doc("POS Monthly Target", out["monthly"])
		self.assertEqual(doc.target_sales, 200000.0)
		self.assertEqual(doc.target_transactions, 20)
		self.assertEqual(
			frappe.db.get_value("Company", self.company_a, "pos_overall_sales_target"), 500000.0
		)

		data = self._payload()
		row = data["targets"]["by_company"][0]
		self.assertEqual(row["target_sales"], 200000.0)
		self.assertFalse(row["missing"])
		self.assertEqual(
			data["targets"]["overall"]["by_company"][self.company_a]["overall_target"], 500000.0
		)

		# blank monthly fields keep the stored values; overall 0 clears it
		set_outlet_target(company=self.company_a, overall_target=0, overall_from="")
		self.assertEqual(
			frappe.db.get_value("Company", self.company_a, "pos_overall_sales_target"), 0.0
		)
		row = self._payload()["targets"]["by_company"][0]
		self.assertEqual(row["target_sales"], 200000.0)

		# missing month row is created (create permission path)
		frappe.db.delete("POS Monthly Target", {"company": self.company_a, "month_start": month})
		out = set_outlet_target(company=self.company_a, target_sales=80000)
		doc = frappe.get_doc("POS Monthly Target", out["monthly"])
		self.assertEqual(doc.target_sales, 80000.0)
		self.assertEqual(doc.target_transactions, 0)

		# restore the class fixture for sibling tests (method changes persist)
		frappe.db.delete("POS Monthly Target", {"company": self.company_a, "month_start": month})
		self._make_target(self.company_a)

	def test_set_outlet_target_validation_and_permission(self):
		frappe.set_user(ADMIN)
		with self.assertRaises(frappe.ValidationError):
			set_outlet_target(company=self.company_a, target_sales=-100)
		with self.assertRaises(frappe.ValidationError):
			set_outlet_target(company=self.company_a, month_start="2026-08-15", target_sales=100)
		with self.assertRaises(frappe.ValidationError):
			set_outlet_target(company=self.company_a)  # nothing to save
		with self.assertRaises(frappe.ValidationError):
			set_outlet_target(company="_Test HQ Missing Co", target_sales=100)

		# Sales Manager reads the dashboard but cannot move targets
		frappe.set_user(self.user_all)
		with self.assertRaises(frappe.PermissionError):
			set_outlet_target(company=self.company_a, target_sales=100)
		with self.assertRaises(frappe.PermissionError):
			set_outlet_target(company=self.company_a, overall_target=100)

		frappe.set_user(self.user_no_role)
		with self.assertRaises(frappe.PermissionError):
			set_outlet_target(company=self.company_a, target_sales=100)

	def test_targets_missing_marked_unavailable(self):
		# drop company A's target: aggregate over A scope must go N/A, not partial
		frappe.db.delete("POS Monthly Target", {"company": self.company_a})
		data = self._payload()
		self.assertFalse(data["targets"]["available"])
		self.assertIn(self.company_a, data["targets"]["missing_companies"])
		self._make_target(self.company_a)  # restore for sibling tests

	def test_get_outlet_targets_lists_all_outlets(self):
		frappe.set_user(ADMIN)
		month = frappe.utils.get_first_day(frappe.utils.nowdate())
		# drop B's monthly target: B must still be listed, flagged missing
		frappe.db.delete("POS Monthly Target", {"company": self.company_b, "month_start": month})
		try:
			out = get_outlet_targets()
			rows = {r["company"]: r for r in out["rows"]}
			self.assertIn(self.company_a, rows)
			self.assertIn(self.company_b, rows)
			self.assertEqual(out["month_start"], str(month))
			self.assertEqual(out["month_end"], str(frappe.utils.get_last_day(month)))
			self.assertGreaterEqual(out["days_elapsed"], 1)

			a = rows[self.company_a]
			self.assertFalse(a["monthly"]["missing"])
			self.assertEqual(a["monthly"]["target_sales"], 100000.0)
			self.assertEqual(a["monthly"]["target_transactions"], 10)
			self.assertEqual(a["currency"], self.currency_a)
			self.assertEqual(a["mtd_net_tax_incl"], 4500.0)
			self.assertEqual(a["mtd_orders"], 3)
			self.assertEqual(a["achievement_sales_pct"], round(4500 / 100000 * 100, 2))
			# linear projection from elapsed days to month end
			dim = frappe.utils.get_last_day(month).day
			self.assertEqual(a["projected_sales"], round(4500 / out["days_elapsed"] * dim, 2))

			b = rows[self.company_b]
			self.assertTrue(b["monthly"]["missing"])
			self.assertIsNone(b["monthly"]["target_sales"])
			# no target never hides the outlet: its month sales still show
			self.assertEqual(b["mtd_net_tax_incl"], 100.0)
			self.assertEqual(b["mtd_orders"], 1)
			self.assertIsNone(b["achievement_sales_pct"])
			self.assertIsNone(b["projected_sales"])
			self.assertIsNone(b["overall"])  # no payback target configured for B
		finally:
			self._make_target(self.company_b)  # restore for sibling tests

	def test_get_outlet_targets_future_month(self):
		frappe.set_user(ADMIN)
		month = frappe.utils.get_first_day(frappe.utils.add_months(frappe.utils.nowdate(), 1))
		out = get_outlet_targets(month_start=month)
		self.assertEqual(out["month_start"], str(month))
		self.assertEqual(out["days_elapsed"], 0)
		rows = {r["company"]: r for r in out["rows"]}
		self.assertIn(self.company_a, rows)
		a = rows[self.company_a]
		self.assertEqual(a["mtd_net_tax_incl"], 0.0)
		self.assertEqual(a["mtd_orders"], 0)
		self.assertIsNone(a["projected_sales"])
		self.assertTrue(a["monthly"]["missing"])  # fixture only targets this month

	def test_get_outlet_targets_month_start_validation(self):
		frappe.set_user(ADMIN)
		with self.assertRaises(frappe.ValidationError):
			get_outlet_targets(month_start="2026-08-15")

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


class TestTargetBasis(IntegrationTestCase):
	"""Configurable target basis (POS Settings): default parity, the
	setter/sync/validation path, and the Gross Profit (Stock Ledger) and
	Net Profit (GL) numerators.

	Fixture dataset (all posted today, server tz):

	GP company (currency IDR), profile TGP, POS Next sale through the real
	submit pipeline (SLEs + GL posted by CustomPOSInvoice):
	  Material Receipt  item_valued qty 10 @ basic_rate 400
	  Material Receipt  item_costless qty 5, zero valuation allowed
	  POS Invoice       item_valued qty 2 @ 1000 + item_costless qty 1 @ 500
	    -> omzet 2500, HPP 800, one costless SLE row
	NP company (currency IDR), plain is_pos Sales Invoice (GL income only):
	  inv1  item_plain qty 1 @ 2000 -> GL income 2000

	Two separate companies on purpose: the GP books carry stock-ledger noise
	(a Material Receipt credits an expense-rooted Stock Adjustment account),
	so Net Profit would not be a clean income-minus-expense figure there.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		# Company custom fields (overall target) must exist before the first
		# migrate of a fresh checkout, same as the monitoring suite.
		sync_custom_fields(quiet=True)
		cls.currency = "IDR"
		cls.company_gp = cls._make_company("_Test TB GP Co", "TBGP")
		cls.company_np = cls._make_company("_Test TB NP Co", "TBNP")
		for company in (cls.company_gp, cls.company_np):
			cls._ensure_fiscal_year(company)
			cls._wipe_company_books(company)

		cls.warehouse_gp = make_test_warehouse("TBGP", cls.company_gp)
		cls.profile_gp = make_test_pos_profile("TBGP", cls.company_gp, cls.warehouse_gp)
		cls.profile_np = make_test_pos_profile(
			"TBNP", cls.company_np, make_test_warehouse("TBNP", cls.company_np)
		)
		for profile in (cls.profile_gp, cls.profile_np):
			if not frappe.db.exists("POS Settings", {"pos_profile": profile}):
				frappe.get_doc(
					{"doctype": "POS Settings", "pos_profile": profile, "require_refund_code": 0}
				).insert(ignore_permissions=True)
		cls.customer = cls._make_customer()
		cls.price_list = cls._make_price_list()
		cls.mode_gp = cls._profile_mode(cls.profile_gp)
		cls.mode_np = cls._profile_mode(cls.profile_np)

		cls.item_valued = make_test_item("TBGP VAL", is_stock_item=1)
		cls.item_costless = make_test_item("TBGP FREE", is_stock_item=1)
		cls.item_plain = make_test_item("TBNP PLAIN")

		# GP books: valued stock + costless stock, then one POS Next sale
		cls._make_receipt(cls.item_valued, qty=10, rate=400)
		cls._make_receipt(cls.item_costless, qty=5, rate=None)
		cls.shift = cls._open_shift()
		cls.gp_invoice = cls._sell_pos(
			[
				{"item": cls.item_valued, "qty": 2, "rate": 1000},
				{"item": cls.item_costless, "qty": 1, "rate": 500},
			]
		)
		# NP books: one plain POS sale, GL income only
		cls.np_invoice = cls._sell_si(
			cls.company_np, cls.profile_np, [{"item": cls.item_plain, "qty": 1, "rate": 2000}]
		)

		for company in (cls.company_gp, cls.company_np):
			cls._make_target(company)
		# The basis cache lives on frappe.local, which survives every class in
		# this process; never leak a non-default basis into sibling classes.
		cls.addClassCleanup(cls._restore_default_basis)

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		super().tearDownClass()

	# ------------------------------------------------------------------
	# fixtures
	# ------------------------------------------------------------------

	@classmethod
	def _make_company(cls, name, abbr):
		if frappe.db.exists("Company", name):
			return name
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": name,
				"abbr": abbr,
				"default_currency": cls.currency,
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)
		return name

	@classmethod
	def _ensure_fiscal_year(cls, company):
		year = frappe.utils.nowdate()[:4]
		name = f"_Test TB FY {year}"
		if not frappe.db.exists("Fiscal Year", name):
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": name,
					"year_start_date": f"{year}-01-01",
					"year_end_date": f"{year}-12-31",
					"companies": [{"company": company}],
				}
			).insert(ignore_permissions=True)
		elif not frappe.db.exists("Fiscal Year Company", {"parent": name, "company": company}):
			doc = frappe.get_doc("Fiscal Year", name)
			doc.append("companies", {"company": company})
			doc.save(ignore_permissions=True)

	@classmethod
	def _wipe_company_books(cls, company):
		"""Drop leftover books of earlier runs that crashed after a stray
		commit. Scoped strictly to the throwaway `_Test TB *` companies."""
		for doctype in ("POS Invoice", "Sales Invoice"):
			names = frappe.get_all(doctype, filters={"company": company}, pluck="name")
			if names:
				frappe.db.delete(f"{doctype} Item", {"parent": ["in", names]})
				frappe.db.delete(f"{doctype} Payment", {"parent": ["in", names]})
				frappe.db.delete(doctype, {"company": company})
		names = frappe.get_all("Stock Entry", filters={"company": company}, pluck="name")
		if names:
			frappe.db.delete("Stock Entry Detail", {"parent": ["in", names]})
			frappe.db.delete("Stock Entry", {"company": company})
		names = frappe.get_all("Journal Entry", filters={"company": company}, pluck="name")
		if names:
			frappe.db.delete("Journal Entry Account", {"parent": ["in", names]})
			frappe.db.delete("Journal Entry", {"company": company})
		for doctype in ("GL Entry", "Payment Ledger Entry", "Stock Ledger Entry", "POS Opening Shift"):
			frappe.db.delete(doctype, {"company": company})

	@classmethod
	def _make_customer(cls):
		name = "_Test TB Customer"
		existing = frappe.db.exists("Customer", {"customer_name": name})
		if existing:
			return existing
		return (
			frappe.get_doc(
				{"doctype": "Customer", "customer_name": name, "customer_type": "Individual"}
			)
			.insert(ignore_permissions=True)
			.name
		)

	@classmethod
	def _make_price_list(cls):
		name = "_Test TB PL"
		if not frappe.db.exists("Price List", name):
			frappe.get_doc(
				{
					"doctype": "Price List",
					"price_list_name": name,
					"currency": cls.currency,
					"selling": 1,
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		return name

	@classmethod
	def _profile_mode(cls, profile):
		return frappe.db.get_value("POS Payment Method", {"parent": profile}, "mode_of_payment")

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
	def _make_receipt(cls, item, qty, rate):
		row = {"item_code": item, "qty": qty, "t_warehouse": cls.warehouse_gp}
		if rate is None:
			row["allow_zero_valuation_rate"] = 1
		else:
			row["basic_rate"] = rate
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"purpose": "Material Receipt",
				"company": cls.company_gp,
				"items": [row],
			}
		)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()

	@classmethod
	def _open_shift(cls):
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": cls.profile_gp,
				"company": cls.company_gp,
				"user": ADMIN,
				"posting_date": frappe.utils.nowdate(),
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": [{"mode_of_payment": cls.mode_gp, "amount": 0}],
			}
		).insert(ignore_permissions=True)
		# a concurrent writer can bump `modified` between insert and submit
		shift.reload()
		shift.submit()
		return shift.name

	@classmethod
	def _sell_pos(cls, rows):
		"""One POS Next sale through the real submit pipeline: CustomPOSInvoice
		posts its own SLEs (voucher_type = the configured invoice doctype)
		and GL entries at submit."""
		total = sum(row["qty"] * row["rate"] for row in rows)
		result = submit_invoice(
			invoice={
				"pos_profile": cls.profile_gp,
				"posa_pos_opening_shift": cls.shift,
				"customer": cls.customer,
				"selling_price_list": cls.price_list,
				"items": [
					{
						"item_code": row["item"],
						"qty": row["qty"],
						"rate": row["rate"],
						"warehouse": cls.warehouse_gp,
					}
					for row in rows
				],
				"payments": [{"mode_of_payment": cls.mode_gp, "amount": total}],
			}
		)
		return result["name"]

	@classmethod
	def _sell_si(cls, company, profile, rows):
		inv = frappe.new_doc("Sales Invoice")
		inv.company = company
		inv.customer = cls.customer
		inv.is_pos = 1
		inv.selling_price_list = cls.price_list
		paid = 0
		for row in rows:
			inv.append(
				"items",
				{
					"item_code": row["item"],
					"qty": row["qty"],
					"rate": row["rate"],
					"price_list_rate": row["rate"],
				},
			)
			paid += row["qty"] * row["rate"]
		inv.append(
			"payments",
			{"mode_of_payment": cls._profile_mode(profile), "amount": paid, "base_amount": paid},
		)
		inv.insert(ignore_permissions=True)
		inv.submit()
		return inv.name

	@classmethod
	def _make_expense_je(cls, company, amount, posting_date):
		expense = get_default_account(company, "Expense")
		cash = frappe.get_cached_value("Company", company, "default_cash_account")
		cost_center = get_default_cost_center(company)
		je = frappe.get_doc(
			{
				"doctype": "Journal Entry",
				"voucher_type": "Journal Entry",
				"posting_date": posting_date,
				"company": company,
				"accounts": [
					{
						"account": expense,
						"cost_center": cost_center,
						"debit_in_account_currency": amount,
						"debit": amount,
					},
					{
						"account": cash,
						"cost_center": cost_center,
						"credit_in_account_currency": amount,
						"credit": amount,
					},
				],
			}
		)
		je.insert(ignore_permissions=True)
		je.submit()
		return je.name

	# ------------------------------------------------------------------
	# helpers
	# ------------------------------------------------------------------

	@staticmethod
	def _clear_basis_cache():
		frappe.local._pos_next_target_bases = {}

	@classmethod
	def _set_basis(cls, monthly=None, overall=None):
		for fieldname, value in (
			("monthly_target_basis", monthly),
			("overall_target_basis", overall),
		):
			if value:
				frappe.db.set_value("POS Settings", {}, fieldname, value, update_modified=False)
		cls._clear_basis_cache()

	@classmethod
	def _restore_default_basis(cls):
		for fieldname in ("monthly_target_basis", "overall_target_basis"):
			frappe.db.set_value("POS Settings", {}, fieldname, NET_SALES, update_modified=False)
		cls._clear_basis_cache()

	def _dashboard(self, company):
		frappe.set_user(ADMIN)
		return get_sales_monitoring(company=company, page_size=10)

	def _targets_row(self, company):
		rows = self._dashboard(company)["targets"]["by_company"]
		return next(r for r in rows if r["company"] == company)

	# ------------------------------------------------------------------
	# default basis
	# ------------------------------------------------------------------

	def test_default_basis_reports_net_sales_and_legacy_numbers(self):
		self._restore_default_basis()
		data = self._dashboard(self.company_np)
		basis = data["target_basis"]
		self.assertEqual(basis["monthly"], NET_SALES)
		self.assertEqual(basis["overall"], NET_SALES)
		self.assertEqual(basis["monthly_label"], frappe._(TARGET_BASIS_LABELS[NET_SALES]))
		self.assertEqual(basis["overall_label"], frappe._(TARGET_BASIS_LABELS[NET_SALES]))

		# numbers identical to the pre-switch behaviour: the neutral key
		# mirrors the legacy sales key on the default basis
		row = self._targets_row(self.company_np)
		self.assertEqual(row["mtd_value"], 2000.0)
		self.assertEqual(row["mtd_value"], row["mtd_net_tax_incl"])
		self.assertEqual(row["target_value"], row["target_sales"])
		self.assertEqual(row["projected_value"], row["projected_sales"])
		self.assertIsNone(row["zero_cost_rows"])
		self.assertEqual(row["achievement_sales_pct"], round(2000 / 100000 * 100, 2))
		self.assertEqual(
			data["targets"]["mtd_value_by_currency"]["by_currency"][self.currency], 2000.0
		)

		# the outlet target sheet carries the same neutral keys
		frappe.set_user(ADMIN)
		out = get_outlet_targets()
		self.assertEqual(out["target_basis"]["monthly"], NET_SALES)
		sheet = next(r for r in out["rows"] if r["company"] == self.company_np)
		self.assertEqual(sheet["mtd_value"], 2000.0)
		self.assertEqual(sheet["mtd_net_tax_incl"], 2000.0)
		self.assertEqual(sheet["target_value"], sheet["monthly"]["target_sales"])
		self.assertEqual(sheet["projected_value"], sheet["projected_sales"])
		self.assertIsNone(sheet["zero_cost_rows"])

	# ------------------------------------------------------------------
	# setter / sync / validation
	# ------------------------------------------------------------------

	def test_target_basis_setter_sync_and_validation(self):
		frappe.set_user(ADMIN)
		try:
			row = frappe.db.get_value("POS Settings", {"pos_profile": self.profile_np}, "name")
			doc = frappe.get_doc("POS Settings", row)
			doc.monthly_target_basis = GROSS_PROFIT
			doc.overall_target_basis = NET_PROFIT
			doc.save(ignore_permissions=True)
			self._clear_basis_cache()
			self.assertEqual(get_target_basis("monthly"), GROSS_PROFIT)
			self.assertEqual(get_target_basis("overall"), NET_PROFIT)

			# on_update synced the global switch onto every other row
			for name in frappe.get_all(
				"POS Settings", filters={"name": ["!=", row]}, pluck="name"
			):
				self.assertEqual(
					frappe.db.get_value("POS Settings", name, "monthly_target_basis"), GROSS_PROFIT
				)
				self.assertEqual(
					frappe.db.get_value("POS Settings", name, "overall_target_basis"), NET_PROFIT
				)

			# invalid values are rejected by the controller
			bad = frappe.get_doc("POS Settings", row)
			bad.monthly_target_basis = "Bogus"
			with self.assertRaises(frappe.ValidationError):
				bad.save(ignore_permissions=True)

			# junk that slipped into the DB reads back as the Net Sales default
			frappe.db.set_value(
				"POS Settings", row, "overall_target_basis", "Junk", update_modified=False
			)
			self._clear_basis_cache()
			self.assertEqual(get_target_basis("overall"), NET_SALES)

			with self.assertRaises(ValueError):
				get_target_basis("bogus-slot")
		finally:
			self._restore_default_basis()

	# ------------------------------------------------------------------
	# gross profit basis
	# ------------------------------------------------------------------

	def test_gross_profit_basis_value_is_omzet_minus_hpp(self):
		frappe.set_user(ADMIN)
		try:
			self._set_basis(monthly=GROSS_PROFIT)
			data = self._dashboard(self.company_gp)
			self.assertEqual(data["target_basis"]["monthly"], GROSS_PROFIT)
			self.assertEqual(
				data["target_basis"]["monthly_label"], frappe._(TARGET_BASIS_LABELS[GROSS_PROFIT])
			)
			row = self._targets_row(self.company_gp)
			# omzet 2500 (2x1000 + 1x500) minus HPP 800 (2x400, valued FIFO)
			# = 1700; the costless row adds sales but no cost
			self.assertAlmostEqual(row["mtd_value"], 1700.0, places=2)
			# sales columns keep their sales meaning on every basis
			self.assertEqual(row["mtd_net_tax_incl"], 2500.0)
			self.assertEqual(row["mtd_orders"], 1)
			self.assertEqual(row["zero_cost_rows"], 1)  # the costless row is flagged
			self.assertEqual(row["achievement_sales_pct"], round(1700 / 100000 * 100, 2))
			self.assertEqual(
				data["targets"]["mtd_value_by_currency"]["by_currency"][self.currency], 1700.0
			)
		finally:
			self._restore_default_basis()

	# ------------------------------------------------------------------
	# net profit basis
	# ------------------------------------------------------------------

	def test_net_profit_basis_income_minus_expense_per_window(self):
		frappe.set_user(ADMIN)
		try:
			self._set_basis(monthly=NET_PROFIT)
			row = self._targets_row(self.company_np)
			self.assertAlmostEqual(row["mtd_value"], 2000.0, places=2)  # GL income only so far

			# a simple expense Journal Entry today: income minus expense
			self._make_expense_je(self.company_np, 300, frappe.utils.nowdate())
			row = self._targets_row(self.company_np)
			self.assertAlmostEqual(row["mtd_value"], 1700.0, places=2)
			self.assertEqual(row["mtd_net_tax_incl"], 2000.0)  # sales stays sales

			# window: last month's expense is outside the MTD window
			prev_month = frappe.utils.get_first_day(
				frappe.utils.add_months(frappe.utils.nowdate(), -1)
			)
			self._make_expense_je(self.company_np, 111, prev_month)
			row = self._targets_row(self.company_np)
			self.assertAlmostEqual(row["mtd_value"], 1700.0, places=2)

			# overall (payback) basis: same books, all-time window, no orders
			self._set_basis(overall=NET_PROFIT)
			frappe.db.set_value(
				"Company",
				self.company_np,
				{"pos_overall_sales_target": 5000, "pos_overall_target_from": None},
			)
			overall = self._dashboard(self.company_np)["targets"]["overall"]
			self.assertTrue(overall["available"])
			orow = overall["by_company"][self.company_np]
			self.assertAlmostEqual(orow["cumulative_value"], 1589.0, places=2)  # all time
			self.assertIsNone(orow["cumulative_orders"])  # no order concept in the GL
			self.assertEqual(orow["achievement_pct"], round(1589 / 5000 * 100, 2))
		finally:
			self._set_basis(monthly=NET_SALES, overall=NET_SALES)
			frappe.db.set_value(
				"Company",
				self.company_np,
				{"pos_overall_sales_target": 0, "pos_overall_target_from": None},
			)
			self._clear_basis_cache()
