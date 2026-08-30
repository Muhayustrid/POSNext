"""Ad-hoc unittest runner for this bench.

Why this exists:
  * `python -m unittest` imports the test module *before* frappe is initialised, so
    ERPNext's module-level test bootstrap runs with no `frappe.local`.
  * `bench run-tests` initialises correctly but then dies in that same bootstrap with
    `DuplicateEntryError: ('Price List', 'Standard Buying')` on a site that already has
    demo data — `erpnext.tests.utils.BootStrapTestData.make_records` matches on the full
    filter dict (which includes `currency`), so an existing "Standard Buying" priced in
    IDR is not seen as a duplicate and the insert fails.

This runner initialises frappe first, then widens `frappe.db.exists` for the duration of
the bootstrap import so name-matching rows count as existing and get skipped.

Usage (inside the container, from the bench root):
    FRAPPE_STREAM_LOGGING=1 ./env/bin/python \
      apps/pos_next/pos_next/_pn_run_tests.py pos_next.test_foo pos_next.api.test_bar
"""

import os
import sys
import unittest

BENCH_ROOT = "/workspace/development/frappe-bench"
sys.path.insert(0, os.path.join(BENCH_ROOT, "apps", "pos_next"))

import frappe  # noqa: E402

frappe.init(site="erpnext16.localhost", sites_path=os.path.join(BENCH_ROOT, "sites"))
frappe.connect()

_real_exists = frappe.db.exists


def _exists_widening_name_match(doctype, filters=None, *args, **kwargs):
	"""Retry a dict-filter lookup on `name` so ERPNext's bootstrap skips live demo rows."""
	found = _real_exists(doctype, filters, *args, **kwargs)
	if found or not isinstance(filters, dict):
		return found
	for key in ("price_list_name", "item_name", "company_name", "warehouse_name"):
		if key in filters:
			name = frappe.db.get_value(doctype, filters.get(key))
			if name:
				return name
	return found


frappe.db.exists = _exists_widening_name_match
try:
	import erpnext.tests.utils  # noqa: F401,E402
finally:
	frappe.db.exists = _real_exists

frappe.set_user("Administrator")

loader = unittest.TestLoader()
suite = unittest.TestSuite()
for name in sys.argv[1:]:
	suite.addTests(loader.loadTestsFromName(name))

result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(0 if result.wasSuccessful() else 1)
