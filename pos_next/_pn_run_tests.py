#!/usr/bin/env python
"""Bootstrap runner for pos_next tests.

`python -m unittest` imports test modules before `frappe.init`, which crashes,
and `bench run-tests` dies on ERPNext bootstrap (DuplicateEntryError on
'Standard Buying'). This inits frappe first, then loads the named modules.

Usage (inside the container, serial only -- parallel runs deadlock on
Stock Settings/tabSingles with error 1213):

    ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_packages ...
"""

import os
import sys
import unittest

import frappe

SITE = "erpnext16.localhost"
# frappe.init resolves sites/ relative to the cwd, so anchor at the bench root.
# In the main checkout that is three levels up; in a worktree under
# .worktrees/<name>/ the depth differs, so walk up until a sites/ dir appears.
_APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def _find_bench_root(start):
	cur = start
	while cur != os.path.dirname(cur):
		if os.path.isdir(os.path.join(cur, "sites")) and os.path.isdir(os.path.join(cur, "apps")):
			return cur
		cur = os.path.dirname(cur)
	raise SystemExit("could not locate bench root above %s" % start)


BENCH_ROOT = _find_bench_root(_APP_DIR)
APP_ROOT = _APP_DIR
SITES_PATH = os.environ.get("SITES_PATH") or os.path.join(BENCH_ROOT, "sites")


def main(module_names, sync=False):
	if not module_names:
		print(__doc__, file=sys.stderr)
		return 2

	os.chdir(BENCH_ROOT)

	script_dir = os.path.dirname(os.path.abspath(__file__))
	sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != script_dir]
	if APP_ROOT not in sys.path:
		sys.path.insert(0, APP_ROOT)

	frappe.init(site=SITE, sites_path=SITES_PATH)
	frappe.connect()
	frappe.flags.in_test = True

	if sync:
		from frappe.model.sync import sync_for

		sync_for("pos_next", force=1)

	try:
		loader = unittest.TestLoader()
		suite = unittest.TestSuite()
		for name in module_names:
			# loadTestsFromName on a package silently collects 0 tests, so
			# modules must always be listed explicitly.
			suite.addTests(loader.loadTestsFromName(name))

		result = unittest.TextTestRunner(verbosity=2).run(suite)
		return 0 if result.wasSuccessful() else 1
	finally:
		frappe.destroy()


if __name__ == "__main__":
	args = [a for a in sys.argv[1:] if a != "--sync"]
	raise SystemExit(main(args, sync="--sync" in sys.argv[1:]))
