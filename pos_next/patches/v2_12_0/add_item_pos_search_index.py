# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Add a FULLTEXT index ft_item_pos_search on Item (name, item_name,
item_group, description).

Serves the POS item search in pos_next/api/items.py get_items: words of 3+
characters are matched with MATCH ... AGAINST('+word*' IN BOOLEAN MODE) over
these four columns (build_item_search_condition), which a full scan of
CONCAT(...columns...) LIKE '%word%' cannot serve. InnoDB FULLTEXT only indexes
tokens of innodb_ft_min_token_size (3 in production) and up; shorter search
words keep the old substring condition, so the index only needs these four
text columns. Production (Frappe Cloud) cannot change server config, hence a
site-level DDL rather than an app schema change.

Runs in [post_model_sync]: the table must exist.
"""

import frappe

INDEX_NAME = "ft_item_pos_search"


def execute():
	# DDL directly (frappe.db.add_index cannot create FULLTEXT indexes); the
	# guard keeps the patch safe to re-run
	exists = frappe.db.sql(
		f"""
		SELECT 1 FROM information_schema.STATISTICS
		WHERE table_schema = DATABASE()
		AND table_name = 'tabItem'
		AND index_name = '{INDEX_NAME}'
		"""
	)
	if not exists:
		frappe.db.sql_ddl(
			f"CREATE FULLTEXT INDEX {INDEX_NAME} ON `tabItem`"
			" (name, item_name, item_group, description)"
		)
