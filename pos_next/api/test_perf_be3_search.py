# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""PERF-09 acceptance tests: hybrid FULLTEXT item search in get_items.

The word predicate must use MATCH ... AGAINST('+word*' IN BOOLEAN MODE)
against the ft_item_pos_search FULLTEXT index for words of 3+ characters
(prefix match at token start) and keep the substring LIKE only for shorter
words (below innodb_ft_min_token_size=3, not tunable on Frappe Cloud). The
exact-barcode condition must keep matching scanned barcodes, and relevance
ordering must still put an exact name ahead of a partial one.

Run inside the container (serial only):

    ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_perf_be3_search
"""

import unittest
from unittest import mock

import frappe
from frappe.utils import now

from pos_next.api.items import build_item_search_condition, get_items

MATCH_AGAINST = "MATCH(i.name, i.item_name, i.item_group, i.description) AGAINST(%s IN BOOLEAN MODE)"
ITEM_PREFIX = "_PERFBE3-"
BARCODE = "PNXT-PERFBE3-BC"


class TestItemSearchPredicateShape(unittest.TestCase):
	"""Pure unit: the helper builds the hybrid word predicate."""

	def test_long_words_use_fulltext_prefix_match(self):
		sql, params = build_item_search_condition(["nasi", "gor"])
		self.assertNotIn("LIKE", sql)
		self.assertEqual(sql.count(MATCH_AGAINST), 2)
		self.assertIn(" AND ", sql)
		self.assertEqual(params, ["+nasi*", "+gor*"])

	def test_short_words_fall_back_to_substring_like(self):
		sql, params = build_item_search_condition(["es", "mi"])
		self.assertNotIn("MATCH(", sql)
		self.assertEqual(sql.count("LIKE %s"), 2)
		self.assertIn(" AND ", sql)
		self.assertEqual(params, ["%es%", "%mi%"])

	def test_mixed_lengths_split_between_fulltext_and_like(self):
		sql, params = build_item_search_condition(["es", "goreng"])
		self.assertEqual(sql.count(MATCH_AGAINST), 1)
		self.assertEqual(sql.count("LIKE %s"), 1)
		self.assertEqual(params, ["%es%", "+goreng*"])

	def test_boolean_mode_specials_are_stripped_from_long_words(self):
		# a typed "-teh" must search, not exclude
		sql, params = build_item_search_condition(["-teh"])
		self.assertEqual(params, ["+teh*"])

	def test_purely_special_word_keeps_literal_like(self):
		sql, params = build_item_search_condition(["***"])
		self.assertNotIn("MATCH(", sql)
		self.assertEqual(params, ["%***%"])


def _profile():
	"""A POS Profile with no item-group restriction so test items are visible."""
	for row in frappe.get_all("POS Profile", pluck="name", limit=50):
		doc = frappe.get_cached_doc("POS Profile", row)
		if not doc.get("item_groups"):
			return doc
	return None


class TestItemSearchOnSite(unittest.TestCase):
	"""Integration on the dev site with a handful of dedicated items."""

	@classmethod
	def setUpClass(cls):
		cls.profile = _profile()
		if not cls.profile:
			raise unittest.SkipTest("no POS Profile without item_groups restriction")
		cls.item_group = frappe.db.get_value(
			"Item Group", {"is_group": 0}, "name", order_by="creation asc"
		)
		if not cls.item_group:
			raise unittest.SkipTest("no non-group Item Group on site")
		cls._seeded = not frappe.db.exists("Item", ITEM_PREFIX + "NG")
		if cls._seeded:
			# direct SQL: doc inserts enqueue background jobs and the shared
			# dev site can have its queue near the overflow limit
			ts = now()
			for code, name in [
				("NG", "Nasi Goreng Spesial"),
				("ET", "Es Teh Manis"),
				("UB", "Udang Bakar Madu"),
				("WJ", "Wedang Jahe"),
				("AG", "Ayam Goreng"),
				("AGL", "Ayam Goreng Lengkap"),
				("KSB", "Kopi Susu Botol"),
			]:
				frappe.db.sql(
					"""INSERT INTO `tabItem`
					(name, item_code, item_name, item_group, stock_uom,
					is_sales_item, is_stock_item, disabled, docstatus,
					owner, modified_by, creation, modified)
					VALUES (%s, %s, %s, %s, 'Nos', 1, 0, 0, 0,
					'Administrator', 'Administrator', %s, %s)""",
					(ITEM_PREFIX + code, ITEM_PREFIX + code, name, cls.item_group, ts, ts),
				)
			frappe.db.sql(
				"""INSERT INTO `tabItem Barcode`
				(name, barcode, parent, parenttype, parentfield, idx, docstatus)
				VALUES (%s, %s, %s, 'Item', 'barcodes', 1, 0)""",
				(frappe.generate_hash(length=10), BARCODE, ITEM_PREFIX + "KSB"),
			)
			frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		for table in ("`tabItem Barcode`", "`tabItem`"):
			frappe.db.sql(
				f"DELETE FROM {table} WHERE "
				+ ("parent" if "Barcode" in table else "name")
				+ " LIKE %s",
				("\\_PERFBE3-%",),
			)
		frappe.db.commit()

	def _search(self, term):
		return get_items(self.profile.name, search_term=term, limit=20)

	def test_prefix_word_finds_item_by_token_start(self):
		# "gor" is a prefix of the token "Goreng" -> FT index path
		codes = [r["item_code"] for r in self._search("nasi gor")]
		self.assertIn(ITEM_PREFIX + "NG", codes)

	def test_short_words_still_match_by_substring(self):
		# "es teh": "es" < 3 chars keeps substring LIKE, "teh" goes through FT
		codes = [r["item_code"] for r in self._search("es teh")]
		self.assertIn(ITEM_PREFIX + "ET", codes)

	def test_single_short_word_does_not_error(self):
		rows = self._search("es")
		self.assertIsInstance(rows, list)
		self.assertIn(ITEM_PREFIX + "ET", [r["item_code"] for r in rows])

	def test_exact_barcode_still_finds_item(self):
		rows = self._search(BARCODE)
		self.assertEqual([r["item_code"] for r in rows], [ITEM_PREFIX + "KSB"])

	def test_exact_name_ranks_before_partial(self):
		rows = self._search("ayam goreng")
		codes = [r["item_code"] for r in rows]
		self.assertIn(ITEM_PREFIX + "AG", codes)
		self.assertLess(codes.index(ITEM_PREFIX + "AG"), codes.index(ITEM_PREFIX + "AGL"))


class TestSearchUsesFulltextIndex(unittest.TestCase):
	"""The whole point of PERF-09: long-word queries must go through
	ft_item_pos_search, not a per-row CONCAT LIKE evaluation."""

	@classmethod
	def setUpClass(cls):
		if not frappe.db.sql(
			"""SELECT 1 FROM information_schema.STATISTICS
			WHERE table_schema = DATABASE()
			AND table_name = 'tabItem' AND index_name = 'ft_item_pos_search'"""
		):
			raise unittest.SkipTest("ft_item_pos_search not installed yet (run the patch)")
		cls.profile = _profile()
		if not cls.profile:
			raise unittest.SkipTest("no POS Profile without item_groups restriction")

	def test_explain_shows_fulltext_access_for_long_words(self):
		captured = []
		orig = frappe.db.sql

		def cap(query, *args, **kwargs):
			captured.append((query, args))
			return orig(query, *args, **kwargs)

		with mock.patch("frappe.db.sql", side_effect=cap):
			get_items(self.profile.name, search_term="udang bakar", limit=20)

		query, args = next(c for c in captured if "MATCH(" in c[0] and "GROUP BY" in c[0])

		# word predicate is a top-level conjunct of MATCH terms
		self.assertIn("MATCH(i.name, i.item_name, i.item_group, i.description)", query)
		params = [str(a) for a in args[0]]
		self.assertIn("+udang*", params)
		self.assertIn("+bakar*", params)

		# and the planner really uses the FULLTEXT index
		explain = orig("EXPLAIN " + query, args[0], as_dict=1)
		item_row = next(r for r in explain if r.table == "i")
		self.assertEqual(item_row.type, "fulltext")
		self.assertEqual(item_row.key, "ft_item_pos_search")
