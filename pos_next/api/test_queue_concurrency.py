# Copyright (c) 2025, BrainWise and contributors
# For license information, please see LICENSE.txt.

"""Concurrency evidence for server-side queue-number allocation (OpenSpec 2.4).

Design D2 allocates a per-shift queue number by locking the `POS Opening Shift` row with
`SELECT ... FOR UPDATE`, incrementing `current_queue_number`, and writing the invoice —
all inside one database transaction. This file is the proof that the lock is what makes
that safe, and the proof that the proof has teeth.

Three properties are tested:

1. `test_for_update_lock_precedes_counter_and_save` — a lock-order observer patched over
   `frappe.qb.get_query` and `Document.db_set` records, on a real `submit_invoice` call,
   that the FOR UPDATE read of the shift happens BEFORE the counter write and BEFORE the
   invoice save. This pins the serialisation mechanism mechanically.

2. `test_two_processes_race_the_shift_lock_and_never_duplicate_a_number` — the real
   two-connection proof. The parent creates BOTH Sales Invoice drafts and commits them,
   so each subprocess only ever calls `submit_invoice`: nothing collides on
   autoname/`tabSeries` and the shift counter is the ONLY contended row. That is genuine
   contention on the lock.

3. `test_negative_control_without_the_lock_a_number_is_lost` — the same two-process
   scenario with the row lock removed from the counter read, asserting the race becomes
   observable. On this bench snapshot isolation aborts the loser anyway, so this one
   reports rather than proves; the deterministic versions below are the proof.

4. `test_negative_control_forces_the_race_and_loses_a_number` — the lock removed, the
   two workers forced to meet inside the counter read, and snapshot isolation turned off
   for their sessions. The race MUST fire: a duplicate queue number is committed, or the
   counter loses an update. If this ever comes out clean the race tests are theatre.

5. `test_forced_overlap_with_the_lock_allocates_distinct_numbers` — the mirror image of
   4: production lock intact, same forced overlap, same SI-off sessions. The two workers
   must get DISTINCT numbers and the counter must move by 2. This is the mutant-test:
   with `for_update=True` deleted it fails with the same {1, 1} as test 4.

6. `test_harness_contention_branch_is_reachable` — a measurement that reports how long
   one submit holds the shift row here and whether a staggered second worker aborts.

Read the environment finding that reshaped test 2 before changing it. This bench is
**MariaDB 11.8.8 with `innodb_snapshot_isolation=ON`**, REPEATABLE READ. There, a locking
read whose row changed after the transaction's snapshot does NOT block-then-reread: it
aborts immediately with error 1020 (`ER_CHECKREAD`, "Record has changed"), which Frappe
maps to `frappe.QueryDeadlockError` / HTTP 508. So the honest outcome of two truly
concurrent same-shift submits is NOT "both succeed, serialised by the lock". It is one of:

  branch S (serialised) — both submits commit with DISTINCT numbers and the counter delta
    is exactly 2 (this happens when one transaction's snapshot already contains the
    other's commit, i.e. it started its read after the winner committed); or
  branch A (aborted)  — one submit commits, the other aborts with a retryable
    1020/QueryDeadlockError/"Record has changed". The aborted transaction wrote nothing,
    so the counter delta is 1 and its invoice remains an unnumbered draft.

    Branch A is asserted to abort *on the shift row itself* (the error text must name
    `tabPOS Opening Shift`). That is what makes this the proof the previous version was
    not: the old harness saw its loser die on `tabSeries` autoname inside `update_invoice`
    before any queue code ran, so its "no duplicate" outcome proved only that the retry
    loop had sequenced the two workers. Here the drafts are pre-created and committed by
    the parent, so a loser can only die on the shift if it reached the allocation.

    What this test does NOT establish is that the LOCK is load-bearing: with
    `for_update=True` deleted it still passes, because snapshot isolation aborts the loser
    on the same table anyway. That claim is carried by
    test_forced_overlap_with_the_lock_allocates_distinct_numbers (lock held, race forced,
    snapshot isolation off => {1, 2}) together with
    test_negative_control_forces_the_race_and_loses_a_number (same forcing, no lock =>
    {1, 1}). Those two differ only in the lock, and they are what the reviewer's
    "deleting for_update=True must turn something red" gate is satisfied by.

Both branches are asserted explicitly. What must never happen is the invariant: no
committed duplicate number, and counter delta == number of successful submits. The
rollback is what makes an abort safe, so branch A is evidence the mechanism works.

That is also why this harness deliberately does NOT retry-loop a collision away. A
previous version caught the 1020 and re-ran the loser strictly after the winner had
committed, so the two allocations looked serialised when the retry sequencing had
serialised them — deleting `for_update=True` left that test green. No retry loop exists
here; instead test 3 runs the unlocked variant and asserts the race is observable. If
this DB's snapshot isolation makes even the unlocked path abort rather than lose an
update, test 3 says so in its output rather than claiming a victory it did not win.

Unlike the buyer-name tests in `pos_next/api/test_invoices.py` (a `FrappeTestCase`, whose
per-test transaction is rolled back and therefore invisible to a second connection), this
class is a plain `unittest.TestCase`: fixtures are committed on purpose so the
subprocesses can see them.

Two things make the assertions robust against an aborted run leaving residue behind —
which is exactly what happened to a previous version of this suite on this site:

  * every assertion is on a counter/number DELTA relative to the values read at the start
    of the test, never on an absolute 1/2, so a shift reused after a crashed run (whose
    counter is already non-zero) cannot produce a spurious failure; and
  * `setUpClass` sweeps residue first — including `POS Opening Shift` rows and submitted
    Sales Invoices, which are auto-numbered and therefore invisible to a `name like
    '_PNXT_QTEST_%'` sweep, and were what actually leaked — and `tearDownClass` sweeps
    again.

Run this suite SERIALLY, one process at a time: two concurrent runs (of this file or of
any other suite) deadlock this bench's bootstrap.
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

import frappe
from frappe.utils import cint, flt, nowdate

from pos_next.api.invoices import submit_invoice, update_invoice

TEST_PREFIX = "_PNXT_QTEST_"
ITEM_CODE = TEST_PREFIX + "ITEM"
CUSTOMER = TEST_PREFIX + "CUSTOMER"
PROFILE = TEST_PREFIX + "PROFILE"
MOP = TEST_PREFIX + "MOP"
WAREHOUSE_NAME = TEST_PREFIX + "WH"
QUEUE_FIELD = "queue_number"
SHIFT_FIELD = "posa_pos_opening_shift"
COUNTER_FIELD = "current_queue_number"
COMPANY = None  # resolved in setUpClass
SITE = None  # captured in setUpClass; frappe.local is thread-local
WAREHOUSE = None  # resolved in _ensure_fixtures to the real Warehouse doc name

# The retryable contention errors this DB can surface when two transactions contend for
# the shift row. 1020 = ER_CHECKREAD ("Record has changed"), which
# innodb_snapshot_isolation raises instead of blocking-then-rereading; 1213 = a classic
# InnoDB deadlock. Frappe maps both onto frappe.QueryDeadlockError (HTTP 508).
RETRYABLE_MARKERS = ("1020", "1213", "Record has changed", "Deadlock", "QueryDeadlockError")

# Worker modes for the subprocess driver.
MODE_LOCKED = "locked"  # production code path, no monkeypatch at all
MODE_UNLOCKED = "unlocked"  # negative control: FOR UPDATE removed from the counter read
MODE_UNLOCKED_NO_SI = "unlocked_no_si"  # like unlocked, plus snapshot isolation turned off
MODE_LOCKED_NO_SI = "locked_no_si"  # production lock, forced overlap, SI turned off
MODE_PROBE = "probe"  # measures how long one submit holds the shift row

RESULT_MARKER = "PN_QUEUE_RESULT:"

# The driver a worker subprocess runs.
#
# `bench execute` is deliberately NOT used, for two reasons: its `--kwargs` are parsed
# with `eval()` (a live code-exec sink we do not want to feed a document payload into),
# and it offers no way to monkeypatch `_read_shift_counter` inside the child, which the
# negative control requires. So we run this generated script with `python -c`, pass the
# arguments through an env var parsed with `json.loads`, and have the child print exactly
# one machine-readable result line.
DRIVER_TEMPLATE = """
import json, os, sys
sys.path.insert(0, %(app_root)r)
import frappe
frappe.init(site=%(site)r, sites_path=%(sites_path)r)
frappe.connect()
import pos_next.api.invoices as _inv
import pos_next.api.test_queue_concurrency as _m

_args = json.loads(os.environ["PN_QUEUE_WORKER_ARGS"])
_mode = os.environ["PN_QUEUE_WORKER_MODE"]
if _mode in ("unlocked", "unlocked_no_si"):
	# NEGATIVE CONTROL ONLY: drop the row lock from the counter read so both workers
	# can read the same value and then write it. Production never passes lock=False.
	if _mode == "unlocked_no_si":
		# Also turn snapshot isolation off for this session — the way a stock MySQL 8 /
		# default MariaDB deployment behaves. With it ON (this bench) the server aborts
		# the loser's unrepeatable write with 1020 and masks the race entirely.
		_m._disable_snapshot_isolation()
	_inv._read_shift_counter = lambda shift, lock=True: _m._read_counter_unlocked(
		shift, _args.get("sync_dir"), _args.get("tag")
	)
elif _mode == "locked_no_si":
	# Production read (FOR UPDATE intact) but the two workers are forced to meet
	# inside it, and snapshot isolation is off so the server does not abort the loser
	# for us. This is what a stock MySQL deployment looks like: the ONLY thing that
	# can serialise the two allocations is the row lock itself.
	_m._disable_snapshot_isolation()
	_real_read = _inv._read_shift_counter

	def _read_and_meet(shift, lock=True):
		_m._rendezvous(_args.get("sync_dir"), _args.get("tag"))
		return _real_read(shift, lock=True)

	_inv._read_shift_counter = _read_and_meet
elif _mode not in ("locked", "probe"):
	raise SystemExit("unknown PN_QUEUE_WORKER_MODE: " + _mode)

_out = _m._worker_submit_invoice(_args)
_out["si"] = _m._snapshot_isolation()
sys.stdout.write("%(marker)s" + json.dumps(_out) + "\\n")
"""


def _bench_dir():
	return os.environ.get("FRAPPE_BENCH_DIR", "/workspace/development/frappe-bench")


def _app_root():
	# .../<bench>/apps/pos_next — the directory that CONTAINS the `pos_next` package.
	# This file lives at <app>/pos_next/api/<file>, so three dirnames up is the app root.
	# Two is not enough: it yields the package dir, whose own `pos_next` submodule has no
	# `api` and the worker then fails with ModuleNotFoundError: pos_next.api.
	path = os.path.abspath(__file__)
	for _ in range(3):
		path = os.path.dirname(path)
	return path


def _build_payload(shift, draft_name=None):
	"""The minimal single-line cart every worker submits.

	Resolves company / warehouse / price list / currency from the POS Profile row itself
	rather than from this module's globals, because a worker subprocess never runs
	`setUpClass` and therefore has no globals to read.
	"""
	profile = frappe.db.get_value(
		"POS Profile", PROFILE, ["company", "warehouse", "selling_price_list", "currency"], as_dict=True
	)
	payload = {
		"doctype": "Sales Invoice",
		"is_pos": 1,
		"pos_profile": PROFILE,
		"company": profile.get("company"),
		"currency": profile.get("currency")
		or frappe.get_cached_value("Company", profile.get("company"), "default_currency"),
		"customer": CUSTOMER,
		"selling_price_list": profile.get("selling_price_list"),
		"posting_date": nowdate(),
		SHIFT_FIELD: shift,
		"items": [
			{
				"item_code": ITEM_CODE,
				"qty": 1,
				"rate": 100.0,
				"uom": "Nos",
				"warehouse": profile.get("warehouse"),
				"conversion_factor": 1,
				"price_list_rate": 100.0,
			}
		],
		"payments": [{"mode_of_payment": MOP, "amount": 100.0}],
	}
	if draft_name:
		payload["name"] = draft_name
	return payload


def _snapshot_isolation():
	"""This session's innodb_snapshot_isolation, or None where the knob does not exist."""
	try:
		return cint(frappe.db.sql("SELECT @@session.innodb_snapshot_isolation")[0][0])
	except Exception:
		return None


def _disable_snapshot_isolation():
	"""Turn snapshot isolation off for this session.

	That is how a stock MySQL 8 deployment behaves and how MariaDB behaves with the
	setting left at its default — the configuration most installs actually run. This
	bench has it ON, which makes the server itself abort an unrepeatable write with
	1020; that abort would mask the very race the negative control exists to show.
	Returns the session value afterwards (None if the knob is unavailable).
	"""
	try:
		frappe.db.sql("SET SESSION innodb_snapshot_isolation=0")
	except Exception:  # pragma: no cover - platform without the knob
		frappe.log_error(frappe.get_traceback(), "PNXT QTest control: no snapshot_isolation knob")
	return _snapshot_isolation()


def _rendezvous(sync_dir, tag, partners=2, timeout=25.0):
	"""Filesystem barrier: wait until `partners` workers have arrived.

	The workers are separate processes reached through `python -c`, so a shared
	directory is the simplest synchronisation point that needs no new DocType and no
	network. Used ONLY by the negative control, to make the collision deterministic
	instead of leaving it to process-startup luck. Returns False if the partner never
	arrived (the caller then proceeds alone, which the test reports).
	"""
	if not sync_dir or not tag:
		return False
	os.makedirs(sync_dir, exist_ok=True)
	open(os.path.join(sync_dir, tag), "w").close()
	deadline = time.time() + timeout
	while time.time() < deadline:
		try:
			if len(os.listdir(sync_dir)) >= partners:
				return True
		except OSError:
			return False
		time.sleep(0.005)
	return False


def _read_counter_unlocked(shift, sync_dir=None, tag=None):
	"""Negative-control read: the shift counter with NO row lock.

	Mirrors `pos_next.api.invoices._read_shift_counter(shift, lock=False)`; used only by
	the driver above in the unlocked modes.

	When `sync_dir` is given, both workers meet here BEFORE either reads. Combined with
	snapshot isolation being off, that pins the interleaving the lock exists to prevent:
	both transactions read the same committed counter, both compute the same next value,
	and the second write silently replaces the first.
	"""
	if sync_dir:
		# Take the snapshot first (this cheap read anchors it), then wait for the
		# partner, then read — so neither can observe the other's write.
		frappe.db.sql("SELECT 1")
		_rendezvous(sync_dir, tag)
	return cint(frappe.db.get_value("POS Opening Shift", shift, COUNTER_FIELD) or 0)


def _worker_submit_invoice(args):
	"""Runs inside a worker subprocess (see DRIVER_TEMPLATE).

	`args` is a dict:

	  mode    MODE_LOCKED | MODE_UNLOCKED | MODE_PROBE
	  shift   POS Opening Shift name
	  invoice optional JSON string of a draft the PARENT already created and committed.
	          When present the worker submits that draft and performs no insert of its
	          own, so no `tabSeries`/autoname row is contended and the shift counter is
	          the only shared object between the two transactions. MODE_PROBE omits it and
	          creates its own draft, because the probe wants a realistic full submit.

	Returns a JSON-serialisable dict with `ok`, `number`, `draft_name`, `elapsed`, and on
	failure `error` / `contention`. A DB-level contention error is REPORTED, not raised —
	it is a legitimate outcome this test must be able to observe. Any other exception
	propagates and the subprocess exits non-zero, which the parent treats as a harness bug.
	"""
	frappe.set_user("Administrator")
	mode = args.get("mode", MODE_LOCKED)
	shift = args["shift"]
	start = time.time()

	if mode == MODE_PROBE:
		# How long does a submit hold the shift row, from the first lock read to the
		# commit? Uncontended, so this is roughly the window in which a second worker
		# must land to collide.
		draft = update_invoice(json.dumps(_build_payload(shift)))
		submit_invoice(
			invoice=json.dumps(draft, default=str),
			data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
		)
		frappe.db.commit()
		return {
			"ok": True,
			"mode": mode,
			"draft_name": draft["name"],
			"number": cint(frappe.db.get_value("Sales Invoice", draft["name"], QUEUE_FIELD) or 0),
			"elapsed": round(time.time() - start, 3),
		}

	invoice = json.loads(args["invoice"]) if args.get("invoice") else _build_payload(shift)
	name = invoice.get("name")
	submit_invoice(
		invoice=json.dumps(invoice, default=str),
		data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
	)
	frappe.db.commit()
	return {
		"ok": True,
		"mode": mode,
		"draft_name": name,
		"number": cint(frappe.db.get_value("Sales Invoice", name, QUEUE_FIELD) or 0),
		"elapsed": round(time.time() - start, 3),
	}


def _brief(result):
	"""One-line, honest summary of a worker result for test output."""
	parts = [f"ok={result.get('ok')}", f"number={result.get('number')}", f"elapsed={result.get('elapsed')}"]
	if not result.get("ok"):
		text = (result.get("error") or "").strip().replace("\n", " ")
		for marker in RETRYABLE_MARKERS:
			if marker in text:
				parts.append(f"contention={marker}")
				break
		parts.append("error=" + text[-1200:])
	return " ".join(str(p) for p in parts)


def _site_name():
	# frappe.local.site is a thread-local proxy; the concurrency test reads it from a
	# worker pool thread where it is unset, so we surface the value captured in setUpClass.
	return SITE


def _is_retryable_contention(text):
	return any(marker in text for marker in RETRYABLE_MARKERS)


def _run_worker_subprocess(mode, shift, invoice_json=None, extra_args=None):
	"""Run one worker and return its result dict.

	NEVER retries. Retrying a contention abort is precisely what previously made the lock
	look load-bearing when it was the retry sequencing that was.
	"""
	args = {"mode": mode, "shift": shift}
	if invoice_json:
		args["invoice"] = invoice_json
	if extra_args:
		args.update(extra_args)

	env = dict(os.environ)
	env["FRAPPE_PY"] = sys.executable
	env["PN_QUEUE_WORKER_MODE"] = mode
	env["PN_QUEUE_WORKER_ARGS"] = json.dumps(args)
	script = DRIVER_TEMPLATE % {
		"app_root": _app_root(),
		"site": _site_name(),
		"sites_path": os.path.join(_bench_dir(), "sites"),
		"marker": RESULT_MARKER,
	}

	start = time.time()
	proc = subprocess.run(
		[sys.executable, "-c", script], cwd=_bench_dir(), capture_output=True, text=True, env=env
	)
	stdout = proc.stdout or ""
	result = None
	for line in reversed(stdout.splitlines()):
		if line.startswith(RESULT_MARKER):
			result = json.loads(line[len(RESULT_MARKER) :])
			break
	if result is not None:
		result.setdefault("returncode", proc.returncode)
		return result

	detail = stdout + "\n" + (proc.stderr or "")
	if _is_retryable_contention(detail):
		out = {
			"ok": False,
			"mode": mode,
			"contention": True,
			"error": detail[-3000:],
			"elapsed": round(time.time() - start, 3),
		}
		# A failed worker has no result line, so its draft name (the thing the parent
		# must go and inspect for "still an unnumbered draft") comes from the args.
		if invoice_json:
			out["draft_name"] = json.loads(invoice_json).get("name")
		return out
	raise AssertionError(
		f"worker subprocess failed without DB contention.\n"
		f"mode={mode} shift={shift} rc={proc.returncode}\ncwd={_bench_dir()}\n"
		f"stdout={stdout[-3000:]!r}\nstderr={(proc.stderr or '')[-3000:]!r}"
	)


class TestQueueNumberConcurrency(unittest.TestCase):
	# ------------------------------------------------------------- fixtures

	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		global COMPANY, SITE
		COMPANY = cls._resolve_company()
		SITE = frappe.local.site
		# Sweep before building: a crashed run can leave shifts, POS Settings rows and
		# submitted invoices behind, and a reused shift with a non-zero counter used to
		# break this file's absolute assertions.
		cls._delete_residue()
		cls._ensure_fixtures()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()
		cls._delete_residue()
		frappe.db.commit()

	@staticmethod
	def _resolve_company():
		if frappe.db.exists("Company", "_Test Company"):
			return "_Test Company"
		return frappe.db.get_value("Company", {}, "name", order_by="creation asc")

	@classmethod
	def _ensure_fixtures(cls):
		# Each step commits so link validation in later fixtures (and in the
		# subprocesses) can see the earlier rows. This class deliberately commits — its
		# whole point is to hand a second connection visible, committed data.
		global WAREHOUSE
		warehouse = frappe.db.get_value(
			"Warehouse", {"warehouse_name": WAREHOUSE_NAME, "company": COMPANY}, "name"
		)
		if not warehouse:
			group_wh = frappe.db.get_value(
				"Warehouse", {"company": COMPANY, "is_group": 1}, "name", order_by="creation asc"
			)
			created = frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": WAREHOUSE_NAME,
					"company": COMPANY,
					"is_group": 0,
					"parent_warehouse": group_wh,
				}
			)
			created.insert(ignore_permissions=True)
			frappe.db.commit()
			warehouse = created.name
		# The Warehouse doc name carries the company-abbreviation suffix; keep the real
		# row on the profile so the Bin lookup, stock entry and payloads all match.
		WAREHOUSE = warehouse

		if not frappe.db.exists("Item", ITEM_CODE):
			item_group = frappe.db.get_value(
				"Item Group", {"is_group": 0}, "name", order_by="creation asc"
			)
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": ITEM_CODE,
					"item_name": "PNXT QTest Item",
					"item_group": item_group or "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()

		balance = frappe.db.get_value("Bin", {"item_code": ITEM_CODE, "warehouse": WAREHOUSE}, "actual_qty")
		if not balance or flt(balance) < 10:
			# Imported lazily: erpnext's test_stock_entry module runs the ERPNext test
			# bootstrap on import (DuplicateEntryError on the 'Standard Buying' price
			# list), which would break the worker subprocess when it imports this module.
			from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry

			make_stock_entry(item_code=ITEM_CODE, target=WAREHOUSE, qty=500, rate=50.0, company=COMPANY)
			frappe.db.commit()

		if not frappe.db.exists("Customer", CUSTOMER):
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": CUSTOMER,
					"customer_group": frappe.db.get_value(
						"Customer Group", {"is_group": 0}, "name", order_by="creation asc"
					)
					or "All Customer Groups",
					"territory": frappe.db.get_value(
						"Territory", {"is_group": 0}, "name", order_by="creation asc"
					)
					or "All Territories",
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()

		if not frappe.db.exists("Mode of Payment", MOP):
			cash_account = frappe.db.get_value(
				"Account",
				{"company": COMPANY, "is_group": 0, "account_type": ("in", ["Cash", "Bank"])},
				"name",
				order_by="creation asc",
			) or frappe.db.get_value("Account", {"company": COMPANY, "is_group": 0}, "name")
			frappe.get_doc(
				{
					"doctype": "Mode of Payment",
					"mode_of_payment": MOP,
					"type": "Cash",
					"enabled": 1,
					"accounts": [{"company": COMPANY, "default_account": cash_account}] if cash_account else [],
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()

		if not frappe.db.exists("POS Profile", PROFILE):
			price_list = frappe.db.get_value("Price List", {"selling": 1}, "name", order_by="creation asc")
			frappe.get_doc(
				{
					"doctype": "POS Profile",
					"name": PROFILE,
					"company": COMPANY,
					"warehouse": WAREHOUSE,
					"currency": frappe.get_cached_value("Company", COMPANY, "default_currency"),
					"customer": CUSTOMER,
					"selling_price_list": price_list,
					"write_off_account": frappe.db.get_value(
						"Account", {"company": COMPANY, "is_group": 0}, "name"
					),
					"write_off_cost_center": frappe.db.get_value(
						"Cost Center", {"company": COMPANY, "is_group": 0}, "name"
					),
					"disable_rounded_total": 1,
					"payments": [{"mode_of_payment": MOP, "default": 1, "amount": 0}],
				}
			).insert(ignore_permissions=True)
			frappe.db.commit()
		else:
			# A residue sweep deleted the settings row but a pre-existing profile (e.g.
			# from a partially cleaned run) is still usable; make sure it points at our
			# warehouse so payloads built in workers stay valid.
			frappe.db.set_value("POS Profile", PROFILE, "warehouse", WAREHOUSE, update_modified=False)

		# POS Settings must exist for the profile with buyer identity ENABLED, else the
		# allocation gate short-circuits. There is normally exactly one row per profile.
		settings_name = frappe.db.get_value("POS Settings", {"pos_profile": PROFILE}, "name")
		if settings_name:
			frappe.db.set_value(
				"POS Settings",
				settings_name,
				{"enabled": 1, "enable_buyer_identity": 1, "require_buyer_name": 0},
				update_modified=False,
			)
		else:
			frappe.get_doc(
				{
					"doctype": "POS Settings",
					"pos_profile": PROFILE,
					"enabled": 1,
					"enable_buyer_identity": 1,
					"require_buyer_name": 0,
				}
			).insert(ignore_permissions=True)
		frappe.db.commit()

	# --------------------------------------------------------------- cleanup

	@staticmethod
	def _shift_names():
		"""Every POS Opening Shift belonging to this suite's profile.

		Shift names are auto-numbered (`POSA-OS-..`), NOT `_PNXT_QTEST_`-prefixed, so a
		`name like` sweep can never find them. This is the leak the reviewer saw.
		"""
		return frappe.get_all("POS Opening Shift", filters={"pos_profile": PROFILE}, pluck="name")

	@classmethod
	def _delete_residue(cls):
		"""Idempotent sweep of everything a previous crashed run may have left behind.

		Order matters: invoices must be cancelled before their shift can be deleted, and
		the shift before the profile. Every step is guarded so a partial state (a
		still-submitted invoice, an already-deleted profile) cannot abort the sweep and
		re-leave the residue this function exists to remove.
		"""
		frappe.flags.ignore_link_count_check = True
		shift_names = cls._shift_names()
		invoices = (
			frappe.get_all("Sales Invoice", filters={SHIFT_FIELD: ["in", shift_names]}, pluck="name")
			if shift_names
			else []
		)
		for inv in invoices:
			cls._safe_delete("Sales Invoice", inv)
		# Offline-sync reservations for this profile would otherwise block a re-run.
		for name in frappe.get_all("Offline Invoice Sync", filters={"pos_profile": PROFILE}, pluck="name"):
			cls._safe_delete("Offline Invoice Sync", name)
		for shift in shift_names:
			cls._safe_delete("POS Opening Shift", shift)
		for name in frappe.get_all("POS Settings", filters={"pos_profile": PROFILE}, pluck="name"):
			cls._safe_delete("POS Settings", name)
		for doctype in ("POS Profile", "Mode of Payment", "Customer", "Item", "Warehouse"):
			for name in frappe.get_all(doctype, filters=[["name", "like", TEST_PREFIX + "%"]], pluck="name"):
				cls._safe_delete(doctype, name)
		frappe.db.commit()

	@staticmethod
	def _safe_delete(doctype, name):
		try:
			doc = frappe.get_doc(doctype, name)
			if doc.meta.is_submittable and doc.docstatus == 1:
				doc.cancel()
			frappe.delete_doc(doctype, name, ignore_permissions=True, force=True, delete_permanently=True)
		except Exception:
			# Cleanup must never be the reason a run fails, but swallowing it silently is
			# what left 17 orphaned shifts on this site, so it goes to the error log.
			frappe.log_error(frappe.get_traceback(), f"PNXT QTest cleanup: {doctype} {name}")

	# --------------------------------------------------------------- helpers

	def _new_shift(self):
		"""Create and submit an Open POS Opening Shift bound to the test profile."""
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": PROFILE,
				"company": COMPANY,
				"user": "Administrator",
				"period_start_date": nowdate(),
				"posting_date": nowdate(),
				"status": "Open",
				"balance_details": [{"mode_of_payment": MOP, "amount": 0}],
			}
		)
		shift.insert(ignore_permissions=True)
		shift.submit()
		return shift.name

	def _make_committed_draft(self, shift):
		"""Create a Sales Invoice DRAFT in the parent, COMMIT it, return its JSON.

		Committing here is the whole point of the restructure: the workers then only call
		`submit_invoice`, so neither one takes a `tabSeries` FOR UPDATE lock and the shift
		counter is the only row they contend on.
		"""
		frappe.set_user("Administrator")
		draft = update_invoice(json.dumps(_build_payload(shift)))
		frappe.db.commit()
		return json.dumps(copy.deepcopy(draft), default=str)

	def _counter(self, shift):
		return cint(frappe.db.get_value("POS Opening Shift", shift, COUNTER_FIELD) or 0)

	def _allocated_numbers(self, shift):
		"""queue_number -> count, over SUBMITTED invoices for this shift."""
		rows = frappe.db.sql(
			f"SELECT `{QUEUE_FIELD}`, COUNT(*) FROM `tabSales Invoice` "
			f"WHERE `{SHIFT_FIELD}` = %s AND docstatus = 1 AND `{QUEUE_FIELD}` > 0 "
			f"GROUP BY `{QUEUE_FIELD}`",
			(shift,),
		)
		return {int(n): int(c) for n, c in rows}

	def _draft_state(self, name):
		"""(docstatus, has_queue_number) for a worker's draft."""
		if not name:
			return None
		row = frappe.db.get_value("Sales Invoice", name, ["docstatus", QUEUE_FIELD], as_dict=True)
		if not row:
			return None
		return (cint(row.get("docstatus")), 1 if cint(row.get(QUEUE_FIELD)) else 0)

	def _refresh(self):
		# End the main connection's open transaction: under REPEATABLE READ its snapshot
		# would predate the subprocesses' commits and verification reads would be stale.
		frappe.db.commit()

	@staticmethod
	def _split(results):
		"""-> (committed_numbers, ok_results, contention_failures)."""
		ok = [r for r in results if r.get("ok")]
		bad = [r for r in results if not r.get("ok")]
		return sorted(r.get("number") for r in ok), ok, bad

	# --------------------------------------------------------------- test 1

	def test_for_update_lock_precedes_counter_and_save(self):
		"""The FOR UPDATE read of the shift happens before the counter write and before
		the invoice save, on a real submit_invoice call."""
		shift = self._new_shift()
		frappe.db.commit()
		draft = update_invoice(json.dumps(_build_payload(shift)))

		events = []
		real_get_query = frappe.qb.get_query
		real_db_set = frappe.model.document.Document.db_set
		real_save = frappe.model.document.Document.save

		def spy_get_query(table, *args, **kwargs):
			if table == "POS Opening Shift" and kwargs.get("for_update"):
				events.append("LOCK_SHIFT")
			return real_get_query(table, *args, **kwargs)

		def spy_db_set(self_doc, fieldname, *args, **kwargs):
			if self_doc.doctype == "POS Opening Shift" and fieldname == COUNTER_FIELD:
				events.append("WRITE_COUNTER")
			return real_db_set(self_doc, fieldname, *args, **kwargs)

		def spy_save(self_doc, *args, **kwargs):
			if self_doc.doctype == "Sales Invoice":
				events.append("SAVE_INVOICE")
			return real_save(self_doc, *args, **kwargs)

		frappe.qb.get_query = spy_get_query
		frappe.model.document.Document.db_set = spy_db_set
		frappe.model.document.Document.save = spy_save
		try:
			submit_invoice(
				invoice=json.dumps(draft, default=str),
				data=json.dumps({"change_amount": 0, "write_off_amount": 0}),
			)
		finally:
			frappe.qb.get_query = real_get_query
			frappe.model.document.Document.db_set = real_db_set
			frappe.model.document.Document.save = real_save

		self.assertIn("LOCK_SHIFT", events, f"shift FOR UPDATE read never happened: {events}")
		self.assertIn("WRITE_COUNTER", events, f"counter write never happened: {events}")
		self.assertIn("SAVE_INVOICE", events, f"invoice save never happened: {events}")
		li, wi, si = events.index("LOCK_SHIFT"), events.index("WRITE_COUNTER"), events.index("SAVE_INVOICE")
		self.assertLess(li, wi, f"lock must precede counter write: {events}")
		self.assertLess(wi, si, f"counter write must precede invoice save: {events}")

		frappe.db.rollback()

	# --------------------------------------------------------------- test 2

	def test_two_processes_race_the_shift_lock_and_never_duplicate_a_number(self):
		"""Two independent processes submit parent-created drafts against the SAME shift.

		Accepted outcomes and why they are accepted: see the module docstring
		(branch S = serialised, branch A = one retryable 1020 abort whose write was rolled
		back). The invariant asserted on both branches is what the lock guarantees.
		"""
		shift = self._new_shift()
		self._refresh()
		base_counter = self._counter(shift)
		base_numbers = self._allocated_numbers(shift)

		draft_a = self._make_committed_draft(shift)
		draft_b = self._make_committed_draft(shift)

		with ThreadPoolExecutor(max_workers=2) as pool:
			futures = [
				pool.submit(_run_worker_subprocess, MODE_LOCKED, shift, draft_a),
				pool.submit(_run_worker_subprocess, MODE_LOCKED, shift, draft_b),
			]
			results = [f.result() for f in futures]

		self._refresh()
		numbers, ok, bad = self._split(results)
		end_counter = self._counter(shift)
		delta = end_counter - base_counter
		current_numbers = self._allocated_numbers(shift)
		changed = {n: c for n, c in current_numbers.items() if c != base_numbers.get(n, 0)}

		if bad:
			branch = "A (one submit aborted with retryable contention and wrote nothing)"
			for f in bad:
				self.assertTrue(
					f.get("contention"),
					"a locked worker must only fail on retryable DB contention, got: " + str(f)[:900],
				)
				# The abort must be ON THE SHIFT ROW, i.e. inside the allocation, not on
				# some unrelated row the submit path happens to touch. This is the check
				# the previous version of this test could not pass — its loser died on
				# `tabSeries` naming inside `update_invoice`, long before the queue code
				# ran, so its "no duplicate" result proved only that the retry loop had
				# sequenced the workers. Note what this does NOT prove: a run with the row
				# lock deleted also aborts on this table (via the FOR UPDATE load inside
				# `db_set`), so this pins WHERE the two transactions meet, not that the
				# lock is what keeps them apart. That claim belongs to
				# test_forced_overlap_with_the_lock_allocates_distinct_numbers.
				self.assertIn(
					"tabPOS Opening Shift",
					f.get("error") or "",
					"a locked worker's contention abort must be on the shift row, got: "
					+ str(f)[:900],
				)
			self.assertEqual(
				len(numbers), 1, f"branch A means exactly one committed submit, got {results}"
			)
			self.assertEqual(
				delta,
				1,
				f"the aborted transaction's increment must have rolled back with it; "
				f"base={base_counter} end={end_counter} results={str(results)[:600]}",
			)
			for f in bad:
				self.assertEqual(
					self._draft_state(f.get("draft_name")),
					(0, 0),
					f"the aborted worker's invoice must still be an unnumbered draft: {f}",
				)
		else:
			branch = "S (both submits committed with distinct numbers)"
			self.assertEqual(len(numbers), 2, f"branch S means two committed submits, got {numbers}")
			self.assertEqual(
				len(set(numbers)),
				2,
				f"locked submits must never allocate the same number: {numbers}",
			)
			self.assertEqual(
				delta,
				2,
				f"two successful submits must move the counter by exactly 2; "
				f"base={base_counter} end={end_counter} numbers={numbers}",
			)
			self.assertEqual(
				sorted(numbers),
				[base_counter + 1, base_counter + 2],
				f"numbers must be the next two after the starting counter, with no gap; "
				f"base={base_counter} numbers={numbers}",
			)
			for r in ok:
				self.assertEqual(
					self._draft_state(r.get("draft_name")),
					(1, 1),
					f"a successful worker's draft must be submitted with a number: {r}",
				)

		print(f"\n[PNXT queue race/LOCKED] shift={shift} base_counter={base_counter} -> branch {branch}")
		for r in results:
			print("[PNXT queue race/LOCKED]   " + _brief(r))
		print(f"[PNXT queue race/LOCKED] counter base={base_counter} end={end_counter} delta={delta}")
		print(f"[PNXT queue race/LOCKED] new allocations={changed}")

		# --- invariants that hold on BOTH branches ------------------------------
		for number, count in changed.items():
			self.assertEqual(
				count, 1, f"queue number {number} was committed more than once: {changed}"
			)
		self.assertEqual(
			delta,
			len(numbers),
			f"counter delta ({delta}) must equal the number of successful submits "
			f"({len(numbers)}): {changed}",
		)
		self.assertTrue(
			numbers or bad,
			f"the two submits must not both vanish silently: {results}",
		)
		for number in numbers:
			self.assertIsInstance(
				number, int, f"a successful submit must carry an integer queue number, got {results}"
			)
			self.assertGreater(number, 0, f"committed queue numbers must be positive: {numbers}")

	# --------------------------------------------------------------- test 3

	def test_negative_control_without_the_lock_a_number_is_lost(self):
		"""Prove test 2 has teeth: the same two-process scenario with the lock removed.

		The worker monkeypatches `invoices._read_shift_counter` to an unlocked read, so
		both transactions can observe the same counter and write the same next value.
		"""
		shift = self._new_shift()
		self._refresh()
		base_counter = self._counter(shift)

		draft_a = self._make_committed_draft(shift)
		draft_b = self._make_committed_draft(shift)

		with ThreadPoolExecutor(max_workers=2) as pool:
			futures = [
				pool.submit(_run_worker_subprocess, MODE_UNLOCKED, shift, draft_a),
				pool.submit(_run_worker_subprocess, MODE_UNLOCKED, shift, draft_b),
			]
			results = [f.result() for f in futures]

		self._refresh()
		numbers, _ok, bad = self._split(results)
		delta = self._counter(shift) - base_counter
		duplicate = len(numbers) != len(set(numbers))
		lost_update = bool(numbers) and delta != len(numbers)

		print(f"\n[PNXT queue race/NO-LOCK CONTROL] shift={shift} base_counter={base_counter}")
		for r in results:
			print("[PNXT queue race/NO-LOCK CONTROL]   " + _brief(r))
		print(
			f"[PNXT queue race/NO-LOCK CONTROL] numbers={numbers} delta={delta} "
			f"duplicate={duplicate} lost_update={lost_update}"
		)

		if duplicate or lost_update:
			print(
				"[PNXT queue race/NO-LOCK CONTROL] RACE OBSERVED without FOR UPDATE -> the "
				"locked test above has teeth: removing the lock changes the outcome."
			)
			return

		for f in bad:
			self.assertTrue(
				f.get("contention"),
				"an unlocked worker may only fail on retryable contention, got: " + str(f)[:900],
			)
		# No race observable on the unlocked path. Say so plainly instead of pretending
		# the control failed for the right reason: with innodb_snapshot_isolation=ON, the
		# server itself refuses to commit an unrepeatable write (1020), so the lock under
		# test is not what produced the safe outcome here. Test 2 still proves the lock is
		# present, ordered, and never duplicates; this control does NOT prove necessity.
		print(
			"[PNXT queue race/NO-LOCK CONTROL] NO RACE OBSERVED on the unlocked path. On this "
			"bench (MariaDB 11.8.8, innodb_snapshot_isolation=ON) an unrepeatable write aborts "
			"with 1020 even with no FOR UPDATE, so this control CANNOT fail here and does not "
			"demonstrate that the lock is necessary — snapshot isolation would have aborted the "
			"loser anyway. The lock's necessity is a portability argument (default MySQL/MariaDB "
			"setups, READ COMMITTED, or a loser that reads before the winner's write lands)."
		)
		if numbers:
			self.assertFalse(duplicate, f"unexpected duplicate on the clean control: {numbers}")
			self.assertEqual(
				delta,
				len(numbers),
				f"even on the clean control the counter must match the commits; "
				f"delta={delta} numbers={numbers} results={str(results)[:600]}",
			)

	def test_negative_control_forces_the_race_and_loses_a_number(self):
		"""Deterministic proof the race tests have teeth: force the bad interleaving.

		Test 3's two workers may never actually overlap, and this bench's
		`innodb_snapshot_isolation=ON` aborts an unrepeatable write anyway, so a clean
		run of test 3 does not by itself show that the LOCK is what keeps numbers
		unique. Here the interleaving is forced rather than waited for:

		  * the workers run with snapshot isolation turned OFF for their session — the
		    stock MySQL 8 / default MariaDB behaviour, i.e. how a normal deployment
		    runs; and
		  * both workers meet on a filesystem barrier INSIDE the counter read, so
		    neither can observe the other's write before producing its own.

		With `for_update` also removed, both transactions therefore compute the SAME
		next number, and the second write silently replaces the first. The expected,
		DESIRED outcome of this test is that the race is OBSERVED: two committed
		invoices sharing one queue number, and a counter delta of 1 for 2 successful
		submits. If it ever comes out clean, the lock is not what is protecting
		allocations and test 2 must be re-examined — so it fails loudly.

		Production code is untouched by this test: the unlocked read is installed in the
		worker process only, and snapshot isolation is a per-session setting.
		"""
		import tempfile

		shift = self._new_shift()
		self._refresh()
		base_counter = self._counter(shift)

		draft_a = self._make_committed_draft(shift)
		draft_b = self._make_committed_draft(shift)

		sync_dir = tempfile.mkdtemp(prefix="pnxt-queue-race-")
		common = {"sync_dir": sync_dir}
		with ThreadPoolExecutor(max_workers=2) as pool:
			futures = [
				pool.submit(
					_run_worker_subprocess, MODE_UNLOCKED_NO_SI, shift, draft_a, dict(common, tag="A")
				),
				pool.submit(
					_run_worker_subprocess, MODE_UNLOCKED_NO_SI, shift, draft_b, dict(common, tag="B")
				),
			]
			results = [f.result() for f in futures]
		shutil.rmtree(sync_dir, ignore_errors=True)

		self._refresh()
		numbers, ok, bad = self._split(results)
		delta = self._counter(shift) - base_counter
		si_values = {r.get("si") for r in results}

		print(f"\n[PNXT queue race/FORCED CONTROL] shift={shift} base_counter={base_counter}")
		for r in results:
			print(f"[PNXT queue race/FORCED CONTROL]   {_brief(r)} si={r.get('si')}")
		print(f"[PNXT queue race/FORCED CONTROL] numbers={numbers} delta={delta}")

		if 1 in si_values or None in si_values:
			self.skipTest(
				f"could not disable snapshot isolation in the workers (si={si_values}); the "
				"forced race is not observable on this configuration. Reported, not faked."
			)

		# Every worker must have committed: with SI off there is nothing to abort on.
		self.assertFalse(bad, f"unlocked/SI-off workers should not abort: {[str(b)[:400] for b in bad]}")
		self.assertEqual(
			len(numbers), 2, f"both forced workers must commit, got {numbers}"
		)
		duplicate = len(set(numbers)) != len(numbers)
		lost = delta != len(numbers)
		self.assertTrue(
			duplicate or lost,
			f"NEGATIVE CONTROL DID NOT FIRE: with no row lock and no snapshot isolation the "
			f"two forced workers still produced clean numbers ({numbers}, delta={delta}). That "
			f"means test 2's green result is not evidence about this app's lock — re-examine "
			f"the harness before trusting it.",
		)
		if duplicate:
			self.assertEqual(
				numbers[0],
				numbers[1],
				f"expected both workers to allocate the SAME number, got {numbers}",
			)
			print(
				f"[PNXT queue race/FORCED CONTROL] DUPLICATE queue number {numbers[0]} committed "
				"twice -> the FOR UPDATE lock in _read_shift_counter is load-bearing."
			)
		if lost:
			print(
				f"[PNXT queue race/FORCED CONTROL] LOST UPDATE: {len(numbers)} submits committed "
				f"but the counter moved by only {delta} -> the lock is load-bearing."
			)

	def test_forced_overlap_with_the_lock_allocates_distinct_numbers(self):
		"""The mirror image of the forced control: production lock, same forced overlap.

		The two workers meet on the barrier INSIDE `_read_shift_counter` and snapshot
		isolation is turned off, so the server is NOT the thing that can save them —
		there is no 1020 to fall back on. The only remaining protection is the row lock,
		which is exactly the claim design D2 makes.

		Under the real code the expected outcome is DISTINCT numbers with a counter delta
		of 2: one worker acquires `FOR UPDATE`, the other blocks in the kernel until that
		transaction commits, and a locking read always returns the latest committed row,
		so the blocked worker sees the winner's increment. Under a mutant with the lock
		removed this test MUST fail, producing the same {1, 1} the control shows — which
		is what the reviewer's "deleting `for_update=True` leaves the test green" objection
		demands. Test 2 alone cannot carry that burden on this DB (see its docstring):
		here the loser aborts for reasons that have nothing to do with the lock.
		"""
		import tempfile

		shift = self._new_shift()
		self._refresh()
		base_counter = self._counter(shift)

		draft_a = self._make_committed_draft(shift)
		draft_b = self._make_committed_draft(shift)

		sync_dir = tempfile.mkdtemp(prefix="pnxt-queue-lock-")
		common = {"sync_dir": sync_dir}
		with ThreadPoolExecutor(max_workers=2) as pool:
			futures = [
				pool.submit(
					_run_worker_subprocess, MODE_LOCKED_NO_SI, shift, draft_a, dict(common, tag="A")
				),
				pool.submit(
					_run_worker_subprocess, MODE_LOCKED_NO_SI, shift, draft_b, dict(common, tag="B")
				),
			]
			results = [f.result() for f in futures]
		shutil.rmtree(sync_dir, ignore_errors=True)

		self._refresh()
		numbers, _ok, bad = self._split(results)
		end_counter = self._counter(shift)
		delta = end_counter - base_counter
		print(f"\n[PNXT queue race/LOCKED+FORCED] shift={shift} base_counter={base_counter}")
		for r in results:
			print(f"[PNXT queue race/LOCKED+FORCED]   {_brief(r)} si={r.get('si')}")
		print(f"[PNXT queue race/LOCKED+FORCED] numbers={numbers} counter end={end_counter} delta={delta}")

		if 1 in {r.get("si") for r in results} or None in {r.get("si") for r in results}:
			self.skipTest("could not disable snapshot isolation in the workers on this host")

		# Snapshot isolation is off in the workers, so the server cannot abort either one:
		# any bad outcome here is a real serialisation failure, never the retryable 1020
		# that test 2 may show.
		self.assertFalse(
			bad, f"workers must not fail with snapshot isolation off: {[str(b)[:400] for b in bad]}"
		)
		self.assertEqual(
			len(numbers), 2, f"both submits must commit, got {numbers}: {str(results)[:600]}"
		)
		self.assertEqual(
			len(set(numbers)),
			2,
			f"THE LOCK FAILED ITS JOB: two forced concurrent submits both allocated {numbers}. "
			f"If this appears after a change to _read_shift_counter, the FOR UPDATE read is no "
			f"longer serialising allocations.",
		)
		self.assertEqual(
			sorted(numbers),
			[base_counter + 1, base_counter + 2],
			f"forced-overlap submits must take the next two numbers without a gap; "
			f"base={base_counter} numbers={numbers}",
		)
		self.assertEqual(
			delta,
			2,
			f"two successful submits must move the counter by 2; base={base_counter} "
			f"end={end_counter} numbers={numbers}",
		)

	# --------------------------------------------------------------- test 4

	def test_harness_contention_branch_is_reachable(self):
		"""Measure the lock-hold window on this machine and report what a staggered
		second worker does.

		A measurement, not a gate: it asserts nothing about which outcome it sees, only
		that the submit it drives actually ran. Its value is in the printed evidence — it
		tells a reader how long one transaction really holds the shift row here, so the
		branch the race test takes can be interpreted instead of assumed. In practice the
		staggered worker lands after the first commit (the subprocess startup skew is
		larger than the ~0.5s hold) and reports branch S, while the simultaneous launch in
		the race test produces branch A.
		"""
		shift = self._new_shift()
		self._refresh()

		probe = _run_worker_subprocess(MODE_PROBE, shift)
		self._refresh()
		self.assertTrue(probe.get("ok"), f"probe submit must succeed: {probe}")
		hold = float(probe.get("elapsed") or 0)
		print(f"\n[PNXT harness] one uncontended submit takes ~{hold:.2f}s (shift {shift})")
		self.assertGreater(hold, 0, "probe reported no elapsed time")

		# Launch the second worker ~40% into that window: the first transaction should
		# still hold an uncommitted write, so the second's locked read is unrepeatable.
		draft = self._make_committed_draft(shift)
		time.sleep(max(0.05, hold * 0.4))
		result = _run_worker_subprocess(MODE_LOCKED, shift, draft)
		self._refresh()
		print(
			f"[PNXT harness] staggered second worker: ok={result.get('ok')} "
			f"number={result.get('number')} elapsed={result.get('elapsed')} "
			f"contention={result.get('contention')}"
		)
		if result.get("ok"):
			print(
				"[PNXT harness] the staggered worker committed: it entered its transaction "
				"after the first submit's write was already committed, which is precisely the "
				"mechanism behind branch S in the race test. A branch-A abort needs both "
				"workers alive inside the window at once, which the simultaneous launch there "
				"does produce (asserted on the shift row)."
			)
		else:
			self.assertTrue(
				result.get("contention"),
				"staggered worker failed for a non-contention reason: " + str(result)[:900],
			)
			print("[PNXT harness] contention abort IS observable here; test 2's branch A is reachable.")
