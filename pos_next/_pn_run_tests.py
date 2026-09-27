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

SITE = os.environ.get("PN_SITE") or "posnext.localhost"
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


def _disable_client_cache_persistence():
	"""Make frappe's ClientCache local-only for the test process.

	The real set_value pickles values into Redis; when tests patch
	frappe.get_doc / frappe.db with MagicMocks, frappe.utils.today() →
	get_system_settings() would try to pickle the mock and every such test
	dies with PicklingError. We keep the in-process cache (repeat reads —
	hooks, get_tables — stay cached; dropping it changes query patterns
	mid-flow and can deadlock MySQLdb on an unread result set), but skip
	the Redis write entirely.
	"""
	import time as _time

	from frappe.utils.redis_wrapper import CachedValue as _CachedValue

	cc = frappe.client_cache
	if not getattr(cc, "healthy", False):
		return

	def _local_only_set_value(key, val, *, shared=False):
		key = cc.redis.make_key(key, shared=shared)
		with cc.lock:
			cc.cache[key] = _CachedValue(value=val, expiry=_time.monotonic() + cc.local_ttl)

	cc.set_value = _local_only_set_value


# Modules observed to fail only inside long full sweeps (86 modules, one
# process, ~2h) while passing individually and in pairs. Investigated
# 2026-09-27 within a 2-cycle budget, no residual-state root cause found:
# - test_cashier_permissions (sweep position 3): passed 2x after running its
#   exact sweep predecessors serially (test_backdate_invoices,
#   test_block_sale_toggle, test_cashier_checkout_permissions).
# - test_uninstall_coverage (last sweep position): tests are hermetic (file +
#   mocked-deletion checks, no DB reads); passed after its 11 nearest
#   predecessors ran before it.
# State audited: invoice_type flip/restore (all _set_invoice_type variants
# invalidate frappe.local._pos_next_invoice_doctype), Custom DocPerm writers
# (test_docperm_mirror cleans up + clears cache), permission caches
# (set_user resets frappe.local.role_permissions; FrappeTestCase class
# cleanups rollback + restore thread locals). Treat their sweep failures as
# environment noise of the long shared-site sweep, not regressions.
KNOWN_FLAKY = frozenset(
    {
        "pos_next.api.test_cashier_permissions",
        "pos_next.tests.test_uninstall_coverage",
    }
)


def main(module_names, sync=False):
	if not module_names:
		print(__doc__, file=sys.stderr)
		return 2

	os.chdir(os.path.join(BENCH_ROOT, "sites"))
	# frappe resolves relative asset paths (e.g. assets/assets.json in
	# get_assets_json) against the cwd; real frappe processes (bench, wsgi)
	# always run with cwd = sites/, so mirror that here or email/asset
	# rendering during ERPNext's import-time test bootstrap crashes with
	# AttributeError: 'NoneType' object has no attribute 'get'.

	script_dir = os.path.dirname(os.path.abspath(__file__))
	sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") != script_dir]
	if APP_ROOT not in sys.path:
		sys.path.insert(0, APP_ROOT)

	frappe.init(site=SITE, sites_path=SITES_PATH)
	frappe.connect()
	_disable_client_cache_persistence()
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
		flaky = sorted(KNOWN_FLAKY & set(module_names))
		if flaky:
			print(
				"known-flaky modules in this run (fail only in long sweeps, pass individually): "
				+ ", ".join(flaky)
			)
		return 0 if result.wasSuccessful() else 1
	finally:
		frappe.destroy()


if __name__ == "__main__":
	args = [a for a in sys.argv[1:] if a != "--sync"]
	raise SystemExit(main(args, sync="--sync" in sys.argv[1:]))
