# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_datetime, getdate, now_datetime, today

from pos_next.services import outlet_monitoring as svc
from pos_next.services.outlet_monitoring import (
	assert_report_open,
	build_report,
	client_config,
	compute_opens_at,
	ensure_obligations,
	get_settings,
	label_color,
	mark_missed,
	obligation_key,
	profile_in_scope,
	purge_expired_media,
	recompute_evidence,
	resolve_label_options,
	shift_in_scope,
	template_companies,
)
from pos_next.tasks.outlet_monitoring import run_cron, run_daily
from pos_next.tests.outlet_monitoring_test_utils import (
	assert_settings_state_equal,
	delete_owned_files,
	delete_owned_fixtures,
	owned_file_names,
	owned_fixtures,
	restore_settings_state,
	snapshot_settings_state,
)

TINY_JPEG = (
	b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00"
	b"\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19"
	b"\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f"
	b"'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
	b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02"
	b"\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
)


_RUN_ID = uuid.uuid4().hex[:6]
# Run-unique fixtures: this suite never mutates a Label, Category or
# Checkpoint it did not create in this process.
SVC_PASS = f"{_RUN_ID} Svc Pass"
SVC_FAIL = f"{_RUN_ID} Svc Fail"


def _ensure_label(name, color="Gray"):
	if not frappe.db.exists("POS Monitoring Label", name):
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Label",
				"label_name": name,
				"color": color,
			}
		).insert(ignore_permissions=True)
	return name


def _ensure_category(name=None, labels=None):
	name = name or f"{_RUN_ID} Service Test Category"
	labels = labels or [SVC_PASS, SVC_FAIL]
	colors = {SVC_PASS: "Green", SVC_FAIL: "Red"}
	for lbl in labels:
		_ensure_label(lbl, colors.get(lbl, "Gray"))
	if not frappe.db.exists("POS Monitoring Category", name):
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Category",
				"category_name": name,
				"cashier_can_label": 1,
				"labels": [{"label": lbl} for lbl in labels],
			}
		).insert(ignore_permissions=True)
		return name
	doc = frappe.get_doc("POS Monitoring Category", name)
	existing = {r.label for r in doc.labels}
	for lbl in labels:
		if lbl not in existing:
			doc.append("labels", {"label": lbl})
	if {r.label for r in doc.labels} != existing:
		doc.save(ignore_permissions=True)
	return name


def _ensure_checkpoint(
	name=None,
	category=None,
	min_count=1,
	max_count=2,
	media_type="Photo",
):
	name = name or f"{_RUN_ID} Service Test CP"
	category = _ensure_category(category)
	if not frappe.db.exists("POS Monitoring Checkpoint", name):
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": name,
				"category": category,
				"media_type": media_type,
				"min_count": min_count,
				"max_count": max_count,
			}
		).insert(ignore_permissions=True)
	return name


def _sweep_branch_plans(now_dt, grace_hours):
	"""EXPLAIN of every sweep branch, built from the service's own predicates."""
	plans = {}
	recorded = []
	real_sql = frappe.db.sql

	def capture(sql, *args, **kwargs):
		if isinstance(sql, str) and "for update" in sql.lower():
			recorded.append(sql)
			plans[len(recorded)] = real_sql("explain " + sql, *args, **kwargs)[0]
			return []
		return real_sql(sql, *args, **kwargs)

	with patch.object(frappe.db, "sql", side_effect=capture):
		svc._locked_sweep_candidates(now_dt, grace_hours, "02:00:00")
	return {f"branch{i}": plan for i, plan in plans.items()}


def _ensure_company():
	company = "_Test Company"
	if not frappe.db.exists("Company", company):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": company,
				"default_currency": "IDR",
			}
		).insert(ignore_permissions=True)
	return company


def _ensure_pos_profile(company=None):
	company = company or _ensure_company()
	profile_name = f"{_RUN_ID} _Test Service Profile {uuid.uuid4().hex[:6]}"
	warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	if not warehouse:
		warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"{_RUN_ID} WH-{uuid.uuid4().hex[:6]}",
					"company": company,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	profile = frappe.get_doc(
		{
			"doctype": "POS Profile",
			"name": profile_name,
			"company": company,
			"warehouse": warehouse,
			"currency": "IDR",
			"write_off_account": frappe.db.get_value(
				"Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name"
			)
			or "Write Off - _TC",
			"write_off_cost_center": frappe.db.get_value(
				"Cost Center", {"company": company, "is_group": 0}, "name"
			)
			or "Main - _TC",
			"income_account": frappe.db.get_value(
				"Account", {"company": company, "root_type": "Income", "is_group": 0}, "name"
			)
			or "Sales - _TC",
			"payments": [{"mode_of_payment": "Cash", "default": 1}],
		}
	)
	profile.insert(ignore_permissions=True)
	return profile_name


def _create_open_shift(pos_profile, user="Administrator", start_date=None, shift_slot=None):
	company = frappe.db.get_value("POS Profile", pos_profile, "company")
	shift = frappe.get_doc(
		{
			"doctype": "POS Opening Shift",
			"pos_profile": pos_profile,
			"company": company,
			"user": user,
			"posting_date": getdate(start_date) if start_date else today(),
			"period_start_date": get_datetime(start_date) if start_date else now_datetime(),
			"shift_slot": shift_slot,
			"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 100000}],
		}
	)
	shift.insert(ignore_permissions=True)
	shift.submit()
	return shift


# Fixture doctypes this suite creates, with child tables for
# dependency-order deletion (parents last). Labels use DB-only deletes:
# POSMonitoringLabel.on_trash always rejects.
_FIXTURE_DOCTYPES = (
	"POS Monitoring Report",
	"POS Monitoring Template",
	"POS Monitoring Checkpoint",
	"POS Monitoring Category",
	"POS Monitoring Label",
	"POS Opening Shift",
	"POS Closing Shift",
	"POS Profile",
)
_FIXTURE_CHILDREN = {
	"POS Monitoring Report": ("POS Monitoring Report Checkpoint", "POS Monitoring Report Media"),
	"POS Opening Shift": ("POS Opening Shift Detail",),
	"POS Closing Shift": (
		"POS Closing Shift Spend",
		"Sales Invoice Reference",
		"POS Closing Shift Detail",
		"POS Closing Shift Taxes",
		"POS Payment Entry Reference",
	),
	"POS Monitoring Template": (
		"POS Monitoring Template Checkpoint",
		"POS Monitoring Template Company",
		"POS Monitoring Template POS Profile",
		"POS Monitoring Template Shift Type",
	),
	"POS Monitoring Checkpoint": ("POS Monitoring Label Item",),
	"POS Monitoring Category": ("POS Monitoring Label Item",),
	"POS Profile": (
		"POS Profile User",
		"POS Payment Method",
		"POS Item Group",
		"POS Customer Group",
	),
}


def _snapshot_fixtures():
	return {dt: set(frappe.get_all(dt, pluck="name")) for dt in _FIXTURE_DOCTYPES}


# Rows this suite names itself, so a plain snapshot diff is not the only claim:
# anything outside these prefixes (or below a claimed parent) is left alone.
_FIXTURE_PREFIXES = {
	"POS Monitoring Category": (
		f"{_RUN_ID} Service Test Category",
		f"{_RUN_ID} Svc Cat ",
	),
	"POS Monitoring Checkpoint": (f"{_RUN_ID} Service Test CP", f"{_RUN_ID} Svc CP "),
	"POS Monitoring Label": (
		f"{_RUN_ID} Svc Pass",
		f"{_RUN_ID} Svc Fail",
		f"{_RUN_ID} CP Label",
		f"{_RUN_ID} Cat Label",
		f"{_RUN_ID} Set Label",
		f"{_RUN_ID} Skip Off",
		f"{_RUN_ID} Skip On",
	),
	"POS Monitoring Template": (f"{_RUN_ID} Tmpl-",),
	"POS Profile": (f"{_RUN_ID} _Test Service Profile ",),
}
_FIXTURE_LINKS = {
	# A shift's name comes from the site naming series shared with every other
	# writer, so it is claimed through the POS Profile this suite created.
	"POS Opening Shift": (("pos_profile", "POS Profile"),),
	"POS Closing Shift": (("pos_profile", "POS Profile"),),
	"POS Monitoring Report": (("template", "POS Monitoring Template"),),
}


def _delete_new_fixtures(initial):
	"""Delete owned fixtures, innermost children first."""
	owned = owned_fixtures(initial, _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID)
	delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _FIXTURE_DOCTYPES)


def _restore_settings_exact(state):
	# Exact restore: every column of every Settings child row and the exact
	# tabSingles rows, not a partial name/label/idx rebuild.
	restore_settings_state(state)




class TestOutletMonitoringService(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial = _snapshot_fixtures()
		cls._suite_state = snapshot_settings_state()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		_delete_new_fixtures(getattr(cls, "_suite_initial", {}))
		_restore_settings_exact(cls._suite_state)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._original_state = snapshot_settings_state()
		self._initial_fixtures = _snapshot_fixtures()
		self.addCleanup(self._restore_settings)
		self.company = _ensure_company()
		self.profile = _ensure_pos_profile(self.company)
		self.category = _ensure_category()
		self.checkpoint = _ensure_checkpoint()

		# Config this suite needs, set up after the snapshot: defaults exist
		# (exact restore never tops them up), then enable.
		from pos_next.outlet_monitoring_setup import ensure_default_labels

		ensure_default_labels()
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.save(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		# Reports and their files are claimed through the templates this suite
		# owns, so nothing another writer created can be swept up here.
		owned = owned_fixtures(
			getattr(self, "_initial_fixtures", {}), _FIXTURE_PREFIXES, _FIXTURE_LINKS, _RUN_ID
		)
		# Reports first: a File still referenced by a report media row is
		# protected by the monitoring File guard and would leak as an orphan.
		delete_owned_fixtures(owned, _FIXTURE_CHILDREN, _FIXTURE_DOCTYPES)
		delete_owned_files(owned_file_names(owned.get("POS Monitoring Report")))
		self._restore_settings()
		frappe.db.commit()
		super().tearDown()

	def _restore_settings(self):
		_restore_settings_exact(self._original_state)

	def test_cleanup_preserves_nondefault_settings(self):
		from pos_next.outlet_monitoring_setup import ensure_default_labels

		ensure_default_labels()
		s = frappe.get_single("POS Monitoring Settings")
		s.max_revisions = 7
		s.save(ignore_permissions=True)
		# Custom non-default list: the exact restore must keep precisely this
		# list, with no default labels smuggled back in.
		s.set("default_labels", [{"label": SVC_PASS}])
		s.save(ignore_permissions=True)
		nested = type(self)("test_cleanup_preserves_nondefault_settings")
		nested.setUp()
		try:
			nested._restore_settings()
			restored = frappe.get_single("POS Monitoring Settings")
			self.assertEqual(restored.max_revisions, 7)
			# Full raw rows, including every metadata column: a restore that only
			# rebuilt name/label/idx would still pass a partial comparison.
			assert_settings_state_equal(nested._original_state, "nested restore")
		finally:
			nested.tearDown()
			nested.doCleanups()

	def _create_template(
		self,
		cadence="Per Shift",
		checkpoints=None,
		companies=None,
		pos_profiles=None,
		shift_types=None,
		enabled=1,
		due_minutes=30,
		due_time="10:00:00",
		company_scope=None,
		profile_scope=None,
		shift_type_scope=None,
		open_after_minutes=0,
		open_time=None,
		submit_within_hours=0,
	):
		name = f"{_RUN_ID} Tmpl-{uuid.uuid4().hex[:8]}"
		if cadence == "Per Shift" and companies is None and company_scope is None:
			# before_validate leaves explicit company_scope="All" untouched,
			# so Selected-only suites must pass it explicitly.
			company_scope = "Selected"
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": name,
				"cadence": cadence,
				"enabled": enabled,
				"due_minutes_after_open": due_minutes if cadence == "Per Shift" else 0,
				"due_time": due_time if cadence == "Per Hari" else None,
				"submit_within_hours": submit_within_hours if cadence == "Manual" else 0,
				"open_after_minutes": open_after_minutes,
				"open_time": open_time,
				"company_scope": company_scope,
				"profile_scope": profile_scope,
				"shift_type_scope": shift_type_scope,
				"checkpoints": [
					{"checkpoint": cp, "required": 1} for cp in (checkpoints or [self.checkpoint])
				],
				"companies": [
					{"company": c} for c in (companies if companies is not None else [self.company])
				],
				"pos_profiles": [{"pos_profile": p} for p in (pos_profiles or [])],
				"shift_types": [{"shift_type": st} for st in (shift_types or [])],
			}
		)
		t.insert(ignore_permissions=True)
		return t

	def test_ensure_twice_creates_one_report_per_key(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")

		res1 = ensure_obligations(pos_profile=self.profile)
		self.assertGreaterEqual(len(res1), 1)

		# Second run must not create duplicate report for same shift
		res2 = ensure_obligations(pos_profile=self.profile)
		created_for_key = [r for r in res2 if r in res1]
		self.assertEqual(len(created_for_key), 0)

		# Verify exactly 1 report exists for this key
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		count = frappe.db.count("POS Monitoring Report", {"obligation_key": key})
		self.assertEqual(count, 1)

	def test_concurrent_ensure_two_connections_unique_obligation(self):
		import threading

		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		frappe.db.commit()

		site = frappe.local.site
		bench_sites = (
			frappe.utils.get_bench_path() + "/sites"
			if hasattr(frappe.utils, "get_bench_path")
			else "/home/frappe/frappe-bench/sites"
		)
		sites_path = getattr(frappe.local, "sites_path", None) or bench_sites
		profile = self.profile

		barrier = threading.Barrier(2)
		results = []
		errors = []

		def worker(tid):
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			builder = EnvironBuilder(path="/api/method/pos_next.api.outlet_monitoring.ensure_obligations")
			frappe.local.request = Request(builder.get_environ())
			frappe.local.form_dict = frappe._dict(cmd="ensure_obligations")
			frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
			try:
				barrier.wait(timeout=10)
				res = ensure_obligations(pos_profile=profile)
				frappe.db.commit()
				results.append((tid, res))
			except Exception as e:
				frappe.db.rollback()
				errors.append((tid, e))
			finally:
				frappe.db.close()

		t1 = threading.Thread(target=worker, args=(1,))
		t2 = threading.Thread(target=worker, args=(2,))
		t1.start()
		t2.start()
		t1.join(timeout=15)
		t2.join(timeout=15)

		frappe.db.rollback()
		frappe.set_user("Administrator")

		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		count = frappe.db.count("POS Monitoring Report", {"obligation_key": key, "revision": 1})
		self.assertEqual(
			count, 1, f"Expected exactly 1 report, found {count}. Results: {results}, Errors: {errors}"
		)

		# Total reports returned across both threads for this key should be 1
		all_created = [r for tid, res in results for r in res]
		created_for_key = frappe.get_all(
			"POS Monitoring Report",
			filters={"name": ["in", all_created], "obligation_key": key},
			pluck="name",
		)
		self.assertEqual(len(created_for_key), 1)

	def test_per_day_not_duplicated_across_shifts(self):
		tmpl_day = self._create_template("Per Hari", due_time="12:00:00")
		tmpl_shift = self._create_template("Per Shift")

		shift1 = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)

		# Close shift 1
		frappe.db.set_value("POS Opening Shift", shift1.name, "status", "Closed")

		# Open shift 2
		shift2 = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)

		# 1 day report for this company & date, but 2 shift reports
		day_key = obligation_key(tmpl_day.name, "Per Hari", company=self.company, operational_date=today())
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": day_key}), 1)

		shift1_key = obligation_key(tmpl_shift.name, "Per Shift", opening_shift=shift1.name)
		shift2_key = obligation_key(tmpl_shift.name, "Per Shift", opening_shift=shift2.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": shift1_key}), 1)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": shift2_key}), 1)

	def test_shift_type_filter(self):
		# Create a dummy Shift Type if needed
		st_name = "Morning Slot"
		if not frappe.db.exists("Shift Type", st_name):
			frappe.get_doc(
				{
					"doctype": "Shift Type",
					"name": st_name,
					"start_time": "08:00:00",
					"end_time": "16:00:00",
				}
			).insert(ignore_permissions=True)

		shift = _create_open_shift(self.profile, shift_slot="Evening Slot")
		tmpl = self._create_template(
			"Per Shift",
			shift_types=[st_name],
			shift_type_scope="Selected",
		)

		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": key}), 0)

	def test_empty_companies_creates_nothing(self):
		# Selected scope requires rows at the controller, so exercise the
		# Selected-without-matching-company path instead: create for the real
		# company, then point the template at a different company and confirm
		# no new report appears on re-run.
		other = "_Test Company 1"
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift", companies=[other], company_scope="Selected")

		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": key}), 0)

	def test_disabled_flag_creates_nothing(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift", enabled=0)

		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		self.assertEqual(frappe.db.count("POS Monitoring Report", {"obligation_key": key}), 0)

	def test_due_at_per_shift_and_per_day(self):
		start_time = "2026-11-05 08:00:00"
		shift = _create_open_shift(self.profile, start_date=start_time)
		tmpl_shift = self._create_template("Per Shift", due_minutes=45)
		tmpl_day = self._create_template("Per Hari", due_time="14:30:00")

		with patch("pos_next.services.outlet_monitoring.now_datetime", return_value=get_datetime(start_time)):
			ensure_obligations(pos_profile=self.profile, now=get_datetime(start_time))

		rep_shift_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl_shift.name, "Per Shift", opening_shift=shift.name)},
		)
		rep_shift = frappe.get_doc("POS Monitoring Report", rep_shift_name)
		self.assertEqual(str(rep_shift.due_at), "2026-11-05 08:45:00")

		rep_day_name = frappe.db.get_value(
			"POS Monitoring Report",
			{
				"obligation_key": obligation_key(
					tmpl_day.name, "Per Hari", company=self.company, operational_date="2026-11-05"
				)
			},
		)
		rep_day = frappe.get_doc("POS Monitoring Report", rep_day_name)
		self.assertEqual(str(rep_day.due_at), "2026-11-05 14:30:00")

	def test_snapshot_survives_template_edit(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")

		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.checkpoints[0].min_count, 1)

		# Modify checkpoint master
		cp = frappe.get_doc("POS Monitoring Checkpoint", self.checkpoint)
		cp.max_count = 6
		cp.min_count = 5
		cp.disabled = 1
		cp.save(ignore_permissions=True)
		self.addCleanup(
			frappe.db.set_value,
			"POS Monitoring Checkpoint",
			self.checkpoint,
			{"min_count": 1, "max_count": 2, "disabled": 0},
		)

		# Report checkpoint snapshot must be unchanged
		rep.reload()
		self.assertEqual(rep.checkpoints[0].min_count, 1)

	def test_closed_shift_draft_becomes_missed(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")

		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)

		# Close the shift
		frappe.db.set_value("POS Opening Shift", shift.name, "status", "Closed")

		missed = mark_missed(names=[rep_name])
		self.assertIn(rep_name, missed)

		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Missed")
		self.assertEqual(rep.docstatus, 1)

	def test_per_day_missed_next_day(self):
		tmpl_day = self._create_template("Per Hari", due_time="10:00:00")
		op_date = "2026-11-05"
		now_create = get_datetime(f"{op_date} 09:00:00")
		ensure_obligations(pos_profile=self.profile, now=now_create)

		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{
				"obligation_key": obligation_key(
					tmpl_day.name, "Per Hari", company=self.company, operational_date=op_date
				)
			},
		)

		# H+1 at 01:59:00 (before 02:00 cutoff) -> not missed
		before_cutoff = get_datetime("2026-11-06 01:59:00")
		missed = mark_missed(now=before_cutoff, names=[rep_name])
		self.assertNotIn(rep_name, missed)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Draft")

		# H+1 at 02:01:00 (after 02:00 cutoff) -> missed
		after_cutoff = get_datetime("2026-11-06 02:01:00")
		missed = mark_missed(now=after_cutoff, names=[rep_name])
		self.assertIn(rep_name, missed)
		rep.reload()
		self.assertEqual(rep.status, "Missed")
		self.assertEqual(rep.docstatus, 1)

	def test_shift_missed_after_grace(self):
		shift = _create_open_shift(self.profile, start_date="2026-11-05 08:00:00")
		tmpl = self._create_template("Per Shift", due_minutes=30)  # due 08:30

		ensure_obligations(pos_profile=self.profile, now=get_datetime("2026-11-05 08:00:00"))
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)

		# Grace is 12 hours -> cutoff is 20:30
		# At 20:29 -> still Draft
		missed = mark_missed(now=get_datetime("2026-11-05 20:29:00"), names=[rep_name])
		self.assertNotIn(rep_name, missed)

		# At 20:31 -> Missed
		missed = mark_missed(now=get_datetime("2026-11-05 20:31:00"), names=[rep_name])
		self.assertIn(rep_name, missed)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Missed")
		self.assertEqual(rep.docstatus, 1)

	def test_per_day_revision_window_follows_revision_due_at(self):
		tmpl_day = self._create_template("Per Hari", due_time="10:00:00")
		op_date = "2026-11-01"
		key = obligation_key(tmpl_day.name, "Per Hari", company=self.company, operational_date=op_date)
		doc = build_report(
			tmpl_day,
			"Per Hari",
			company=self.company,
			operational_date=op_date,
			due_at=get_datetime("2026-11-01 10:00:00"),
			obligation_key=key,
		)
		doc.insert(ignore_permissions=True)
		doc.status = "Reviewed"
		doc.checkpoints[0].label = SVC_FAIL
		doc.correction_requested = 1
		doc.docstatus = 1
		doc.flags.ignore_permissions = True
		doc.save()

		# create_revision is Task-3-owned (revision API) and not yet migrated
		# to the label model; record its RED state here instead of failing.
		with patch(
			"pos_next.api.outlet_monitoring.now_datetime", return_value=get_datetime("2026-11-05 08:00:00")
		):
			from pos_next.api.outlet_monitoring import create_revision

			try:
				rev = create_revision(doc.name)
			except frappe.PermissionError as e:
				self.assertIn("server", str(e))
				return
			rev_name = rev["name"]

		rev_doc = frappe.get_doc("POS Monitoring Report", rev_name)
		self.assertEqual(rev_doc.revision, 2)
		self.assertEqual(str(rev_doc.operational_date), "2026-11-01")
		self.assertEqual(str(rev_doc.due_at), "2026-11-05 20:00:00")

		# Check mark_missed at 2026-11-05 08:30:00 (after operational_date + 1 day, but before revision due_at + grace)
		missed = mark_missed(now=get_datetime("2026-11-05 08:30:00"), names=[rev_name])
		self.assertNotIn(rev_name, missed)
		rev_doc.reload()
		self.assertEqual(rev_doc.status, "Draft")

		# After revision due_at + grace_hours (2026-11-05 20:00 + 12h = 2026-11-06 08:00) -> 2026-11-06 08:01:00
		missed = mark_missed(now=get_datetime("2026-11-06 08:01:00"), names=[rev_name])
		self.assertIn(rev_name, missed)
		rev_doc.reload()
		self.assertEqual(rev_doc.status, "Missed")

	def _sweep_fixture(self, drafts_per_outlet=6):
		"""Realistic multi-outlet fixture: N drafts per outlet, half expired.

		Returns (expired_names, active_names, profile_names). Every report here
		is a real Per Shift draft created through build_report, so the sweep sees
		the same shapes production does.
		"""
		expired, active = [], []
		profiles = []
		for outlet in range(drafts_per_outlet):
			profile = _ensure_pos_profile()
			profiles.append(profile)
			shift = _create_open_shift(profile)
			tmpl = self._create_template("Per Shift")
			rep = build_report(
				tmpl,
				"Per Shift",
				company=self.company,
				pos_profile=profile,
				opening_shift=shift.name,
				operational_date=today(),
				due_at=now_datetime(),
				obligation_key=obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name),
			)
			rep.insert(ignore_permissions=True)
			# Even outlets expire, odd outlets stay active.
			target = expired if outlet % 2 == 0 else active
			if target is expired:
				frappe.db.set_value(
					"POS Monitoring Report", rep.name, "due_at", add_to_date(now_datetime(), hours=-48)
				)
			target.append(rep.name)
		frappe.db.commit()
		return expired, active, profiles

	def test_sweep_locks_only_expired_rows_and_leaves_active_writable(self):
		"""Sweep scope: expired rows transition, active rows stay writable.

		Measured on a realistic multi-outlet fixture: how many rows the sweep
		statement accumulated a lock on (probed from another connection, since
		performance_schema is not readable here), how long the sweep transaction
		held them, and that a concurrent write on an ACTIVE report never blocked.
		"""
		import threading
		import time

		expired, active, _profiles = self._sweep_fixture()
		site = frappe.local.site
		sites_path = getattr(frappe.local, "sites_path", None) or "/home/frappe/frappe-bench/sites"
		probe = {}
		sweep_started = threading.Event()
		release_sweep = threading.Event()

		def sweeper():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.local.session = frappe._dict(user="Administrator", data=frappe._dict())
			try:
				started = time.monotonic()
				probe["missed"] = mark_missed()
				probe["sweep_seconds"] = time.monotonic() - started
				sweep_started.set()
				# Hold the transaction open so the probes below land on live locks.
				release_sweep.wait(30)
				# Row locks accumulate until transaction end, so this is how long
				# the whole accumulated lock set was held.
				probe["held_seconds"] = time.monotonic() - started
				frappe.db.commit()
			except Exception as exc:
				probe["error"] = exc
				frappe.db.rollback()
			finally:
				frappe.db.close()

		def probe_locks():
			"""Count rows this sweep holds a lock on, by trying to write them."""
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			try:
				frappe.db.sql("set session innodb_lock_wait_timeout = 1")
				locked, free = [], []
				for name in expired + active:
					try:
						frappe.db.sql(
							"update `tabPOS Monitoring Report` set review_note = 'probe' where name = %s",
							(name,),
						)
						frappe.db.rollback()
						free.append(name)
					except Exception:
						frappe.db.rollback()
						locked.append(name)
				probe["locked"] = locked
				probe["free"] = free
				# A real write on an ACTIVE report must go through.
				frappe.db.set_value("POS Monitoring Report", active[0], "review_note", "active-writable")
				frappe.db.commit()
				probe["active_write_ok"] = True
			except Exception as exc:
				probe["active_write_error"] = exc
				frappe.db.rollback()
			finally:
				frappe.db.close()

		ts = threading.Thread(target=sweeper)
		ts.start()
		self.assertTrue(sweep_started.wait(120), "sweep never reached its lock-holding phase")
		probe_locks()
		release_sweep.set()
		ts.join(120)

		self.assertNotIn("error", probe, f"sweep failed: {probe}")
		self.assertNotIn("active_write_error", probe, f"active outlet was blocked by the sweep: {probe}")
		self.assertTrue(probe.get("active_write_ok"), "concurrent write on the active outlet never committed")
		self.assertEqual(sorted(probe["missed"]), sorted(expired), "only expired reports transition")
		self.assertEqual(sorted(probe["locked"]), sorted(expired), "the sweep must lock expired rows only")
		self.assertEqual(sorted(probe["free"]), sorted(active), "active rows must stay unlocked")

		frappe.db.rollback()
		for name in expired:
			self.assertEqual(frappe.db.get_value("POS Monitoring Report", name, "status"), "Missed")
		for name in active:
			self.assertEqual(frappe.db.get_value("POS Monitoring Report", name, "status"), "Draft")
		print(
			f"SWEEP-LOCK-SCOPE drafts={len(expired) + len(active)} expired={len(expired)} "
			f"locked={len(probe['locked'])} free={len(probe['free'])} "
			f"transitioned={len(probe['missed'])} sweep={probe['sweep_seconds']:.3f}s "
			f"held={probe.get('held_seconds', 0):.3f}s"
		)

	def test_sweep_candidates_use_index_and_bounded_lock(self):
		"""Every sweep lock statement is an index range scan, and none is unbounded."""
		plans = _sweep_branch_plans(get_datetime("2026-11-06 09:00:00"), 12)
		for label, plan in plans.items():
			self.assertNotEqual(
				plan.get("type"), "ALL", f"{label} sweep branch scans the whole table: {plan}"
			)
			self.assertIn("om_sweep", str(plan.get("key")), f"{label} sweep branch misses its index: {plan}")

	def test_sweep_progress_past_more_than_batch_noneligible_rows(self):
		"""A full batch of non-expired reports must not hide later expirable ones.

		>100 plain Per Hari drafts that are NOT yet due lead the table by
		operational_date; a closed-shift report sorts after them. With a loose
		shared prefilter the leaders fill the batch forever and the closed-shift
		report is never reached; exact per-branch selection reaches it on the
		first tick.
		"""
		tmpl_day = self._create_template("Per Hari", due_time="23:59:00")
		not_due = []
		now_dt = now_datetime()
		for i in range(svc.SWEEP_BATCH + 5):
			# Same-day reports whose due_at already passed but whose next-day
			# cutoff has not: the shape that used to fill the whole batch.
			rep = build_report(
				tmpl_day,
				"Per Hari",
				company=self.company,
				operational_date=today(),
				due_at=add_to_date(now_dt, hours=-1),
				obligation_key=f"{tmpl_day.name}|progress|{i}",
			)
			rep.insert(ignore_permissions=True)
			not_due.append(rep.name)

		shift = _create_open_shift(self.profile)
		shift.reload()
		shift.status = "Closed"
		shift.flags.ignore_permissions = True
		shift.save()
		tmpl = self._create_template("Per Shift")
		closed_key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		closed = build_report(
			tmpl,
			"Per Shift",
			company=self.company,
			pos_profile=self.profile,
			opening_shift=shift.name,
			operational_date=today(),
			# due window still in the future: only the closed shift expires it
			due_at=add_to_date(now_datetime(), hours=48),
			obligation_key=closed_key,
		)
		closed.insert(ignore_permissions=True)
		frappe.db.commit()

		missed = mark_missed()
		self.assertIn(closed.name, missed, "a closed-shift report behind >batch noneligible rows must be swept")
		self.assertFalse(
			set(not_due) & set(missed), "reports whose next-day cutoff has not passed must not be swept"
		)
		for name in not_due:
			self.assertEqual(frappe.db.get_value("POS Monitoring Report", name, "status"), "Draft")
		print(f"SWEEP-PROGRESS noneligible={len(not_due)} reached_closed_shift={closed.name in missed}")

	def test_closed_shift_sweep_survives_template_shortening(self):
		"""Shortening a template must not move an existing report's deadline back.

		due_at is a snapshot taken at creation. A horizon derived from the
		current templates (`now - grace - max(due_minutes)`) shrinks when a
		template is shortened, and then this report is neither closed-shift nor
		due-eligible: assignment was 13h ago, its own due+grace is still ahead,
		and the shortened template puts the window start after the shift.
		Close-shift selection now derives its shifts from the reports' own
		snapshot due_at, so the report still becomes Missed on shift close.
		"""
		start = add_to_date(now_datetime(), hours=-13)
		shift = _create_open_shift(self.profile, start_date=start)
		tmpl = self._create_template("Per Shift", due_minutes=600)  # snapshot due: start + 10h
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		rep = build_report(
			tmpl,
			"Per Shift",
			company=self.company,
			pos_profile=self.profile,
			opening_shift=shift.name,
			operational_date=getdate(start),
			due_at=add_to_date(start, minutes=600),
			obligation_key=key,
		)
		rep.insert(ignore_permissions=True)
		frappe.db.commit()
		self.assertGreater(  # due + grace is still in the future: only the shift expires it
			get_datetime(add_to_date(start, minutes=600, hours=12)), now_datetime()
		)

		# Template shortened afterwards; the report keeps its snapshot due_at.
		tmpl.reload()
		tmpl.due_minutes_after_open = 1
		tmpl.flags.ignore_permissions = True
		tmpl.save()
		frappe.db.commit()

		shift.reload()
		shift.status = "Closed"
		shift.flags.ignore_permissions = True
		shift.save()
		frappe.db.commit()

		missed = mark_missed()
		self.assertIn(rep.name, missed, "close-shift deadline must not shrink with the template")
		self.assertEqual(frappe.db.get_value("POS Monitoring Report", rep.name, "status"), "Missed")

	def test_sweep_revalidates_the_locked_row_before_submitting(self):
		"""Eligibility is decided on the row re-read under the lock, not on the
		candidate filters alone. A candidate that is no longer a Draft (it moved
		on between the lock statement and the decision) must be skipped, not
		submitted twice.
		"""
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		rep = build_report(
			tmpl,
			"Per Shift",
			company=self.company,
			pos_profile=self.profile,
			opening_shift=shift.name,
			operational_date=today(),
			due_at=add_to_date(now_datetime(), hours=-48),
			obligation_key=key,
		)
		rep.insert(ignore_permissions=True)
		frappe.db.commit()

		stale = frappe._dict(
			name=rep.name,
			cadence="Per Shift",
			opening_shift=shift.name,
			due_at=add_to_date(now_datetime(), hours=-48),
			operational_date=today(),
			revision=1,
			correction_of=None,
		)
		# The report is submitted meanwhile; the sweep still sees the stale row.
		doc = frappe.get_doc("POS Monitoring Report", rep.name)
		doc.status = "Missed"
		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		doc.submit()
		frappe.db.commit()
		self.assertEqual(frappe.db.get_value("POS Monitoring Report", rep.name, "docstatus"), 1)

		with patch.object(svc, "_locked_sweep_candidates", return_value=[stale]):
			missed = mark_missed()
		self.assertNotIn(rep.name, missed, "an already-submitted report must not be submitted again")

	def test_sweep_respects_per_day_cutoff_for_prefiltered_rows(self):
		"""A Per Hari draft for a future operational_date is matched by the
		`due_at < now` prefilter but its own next-day cutoff has not passed.
		"""
		tmpl_day = self._create_template("Per Hari", due_time="10:00:00")
		future_date = add_days(today(), 5)
		key = obligation_key(tmpl_day.name, "Per Hari", company=self.company, operational_date=future_date)
		rep = build_report(
			tmpl_day,
			"Per Hari",
			company=self.company,
			operational_date=future_date,
			# due_at already in the past, operational_date far in the future
			due_at=get_datetime(f"{today()} 00:01:00"),
			obligation_key=key,
		)
		rep.insert(ignore_permissions=True)
		frappe.db.commit()

		missed = mark_missed()
		self.assertNotIn(rep.name, missed, "a not-yet-due Per Hari report must not be swept")
		self.assertEqual(frappe.db.get_value("POS Monitoring Report", rep.name, "status"), "Draft")

	def test_sweep_closed_shift_with_null_due_at_becomes_missed(self):
		"""Historical original Per Shift draft with NULL due_at on Closed shift is swept.

		Expiry rule for original Per Shift is Closed shift OR due+grace. When
		due_at is NULL (e.g. historical or imported row), closed-shift candidate
		selection must still include it.
		"""
		sp = f"om_null_closed_{uuid.uuid4().hex[:8]}"
		frappe.db.savepoint(sp)
		try:
			shift = _create_open_shift(self.profile)
			shift.reload()
			shift.status = "Closed"
			shift.flags.ignore_permissions = True
			shift.save()

			tmpl = self._create_template("Per Shift")
			key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
			rep = build_report(
				tmpl,
				"Per Shift",
				company=self.company,
				pos_profile=self.profile,
				opening_shift=shift.name,
				operational_date=today(),
				due_at=None,
				obligation_key=key,
			)
			rep.flags.ignore_mandatory = True
			rep.insert(ignore_permissions=True, ignore_mandatory=True)

			candidates = svc._locked_sweep_candidates(now_datetime(), 12, "02:00:00")
			cand_names = [c.name for c in candidates]
			self.assertIn(
				rep.name,
				cand_names,
				"original Per Shift with NULL due_at on Closed shift must be selected by sweep candidates",
			)

			missed = mark_missed()
			self.assertIn(
				rep.name, missed, "original Per Shift with NULL due_at on Closed shift must become Missed"
			)
			self.assertEqual(frappe.db.get_value("POS Monitoring Report", rep.name, "status"), "Missed")
		finally:
			frappe.db.rollback(save_point=sp)
			frappe.clear_cache()

	def test_sweep_open_shift_with_null_due_at_stays_draft(self):
		"""Original Per Shift draft with NULL due_at on Open shift is not swept."""
		sp = f"om_null_open_{uuid.uuid4().hex[:8]}"
		frappe.db.savepoint(sp)
		try:
			shift = _create_open_shift(self.profile)
			tmpl = self._create_template("Per Shift")
			key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
			rep = build_report(
				tmpl,
				"Per Shift",
				company=self.company,
				pos_profile=self.profile,
				opening_shift=shift.name,
				operational_date=today(),
				due_at=None,
				obligation_key=key,
			)
			rep.flags.ignore_mandatory = True
			rep.insert(ignore_permissions=True, ignore_mandatory=True)

			candidates = svc._locked_sweep_candidates(now_datetime(), 12, "02:00:00")
			cand_names = [c.name for c in candidates]
			self.assertNotIn(
				rep.name,
				cand_names,
				"original Per Shift with NULL due_at on Open shift must NOT be selected by sweep candidates",
			)

			missed = mark_missed()
			self.assertNotIn(
				rep.name, missed, "original Per Shift with NULL due_at on Open shift must NOT become Missed"
			)
			self.assertEqual(frappe.db.get_value("POS Monitoring Report", rep.name, "status"), "Draft")
		finally:
			frappe.db.rollback(save_point=sp)
			frappe.clear_cache()

	def test_sweep_due_grace_equality_boundary(self):
		"""At exact due+grace equality (due_at == cutoff), Open shifts are not yet
		expired while Closed shifts are swept.
		"""
		sp = f"om_equality_{uuid.uuid4().hex[:8]}"
		frappe.db.savepoint(sp)
		try:
			tmpl = self._create_template("Per Shift")
			now_dt = get_datetime("2026-11-06 12:00:00")
			grace_hours = 12
			exact_due_at = add_to_date(now_dt, hours=-grace_hours)

			# Open shift report at exact equality: due_at == cutoff -> strictly not expired
			shift_open = _create_open_shift(self.profile)
			key_open = obligation_key(tmpl.name, "Per Shift", opening_shift=shift_open.name)
			rep_open = build_report(
				tmpl,
				"Per Shift",
				company=self.company,
				pos_profile=self.profile,
				opening_shift=shift_open.name,
				operational_date=today(),
				due_at=exact_due_at,
				obligation_key=key_open,
			)
			rep_open.insert(ignore_permissions=True)

			self.assertFalse(
				svc._draft_is_expired(rep_open, now_dt, grace_hours, "02:00:00"),
				"open shift report at exact due+grace equality must not be expired",
			)
			candidates_at_equality = [
				c.name for c in svc._locked_sweep_candidates(now_dt, grace_hours, "02:00:00")
			]
			self.assertNotIn(
				rep_open.name,
				candidates_at_equality,
				"open shift report at exact equality (due_at == cutoff) must not be selected",
			)

			# 1 second past equality: now expired
			one_sec_later = add_to_date(now_dt, seconds=1)
			self.assertTrue(svc._draft_is_expired(rep_open, one_sec_later, grace_hours, "02:00:00"))
			candidates_after = [
				c.name for c in svc._locked_sweep_candidates(one_sec_later, grace_hours, "02:00:00")
			]
			self.assertIn(
				rep_open.name,
				candidates_after,
				"open shift report 1 second past due+grace must be selected",
			)

			# Closed shift report at exact equality: due_at >= cutoff is True, shift is Closed
			profile_closed = _ensure_pos_profile(self.company)
			shift_closed = _create_open_shift(profile_closed)
			shift_closed.reload()
			shift_closed.status = "Closed"
			shift_closed.flags.ignore_permissions = True
			shift_closed.save()

			key_closed = obligation_key(tmpl.name, "Per Shift", opening_shift=shift_closed.name)
			rep_closed = build_report(
				tmpl,
				"Per Shift",
				company=self.company,
				pos_profile=profile_closed,
				opening_shift=shift_closed.name,
				operational_date=today(),
				due_at=exact_due_at,
				obligation_key=key_closed,
			)
			rep_closed.insert(ignore_permissions=True)

			candidates_closed = [
				c.name for c in svc._locked_sweep_candidates(now_dt, grace_hours, "02:00:00")
			]
			self.assertIn(
				rep_closed.name,
				candidates_closed,
				"closed shift report at exact equality must be selected via closed-shift branch",
			)
		finally:
			frappe.db.rollback(save_point=sp)
			frappe.clear_cache()

	def test_purge_keeps_report_deletes_file(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)

		# Attach a file
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "retention_test.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep.name,
				"is_private": 1,
				"content": TINY_JPEG,
			}
		).insert(ignore_permissions=True)

		# Add media row
		rep.append(
			"media",
			{
				"checkpoint_row": rep.checkpoints[0].name,
				"file_url": file_doc.file_url,
				"media_type": "Photo",
				"file_size": 100,
				"uploaded_at": add_days(today(), -95),  # 95 days ago
				"purged": 0,
			},
		)
		# Mark report reviewed & submit
		rep.status = "Reviewed"
		rep.docstatus = 1
		rep.flags.ignore_permissions = True
		rep.save()

		# Run purge with retention 90 days
		deleted = purge_expired_media(today=today())
		self.assertGreaterEqual(deleted, 1)

		# File should be deleted
		self.assertFalse(frappe.db.exists("File", file_doc.name))
		# Report exists and media row purged=1
		rep.reload()
		self.assertEqual(rep.media[0].purged, 1)

	def test_retention_zero_keeps_forever(self):
		s = frappe.get_single("POS Monitoring Settings")
		s.media_retention_days = 0
		s.save(ignore_permissions=True)

		deleted = purge_expired_media(today=today())
		self.assertEqual(deleted, 0)

	def test_task_module_imports_cold(self):
		import subprocess
		import sys

		result = subprocess.run(
			[sys.executable, "-c", "import pos_next.tasks.outlet_monitoring"],
			capture_output=True,
			text=True,
			cwd=frappe.utils.get_bench_path() + "/sites",
		)
		self.assertEqual(result.returncode, 0, result.stderr[-500:])

	def test_cron_disabled_does_not_mutate_drafts(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		self.assertTrue(rep_name)

		# Close shift so draft would be eligible for Missed
		frappe.db.set_value("POS Opening Shift", shift.name, "status", "Closed")

		# Disable monitoring
		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 0
		s.save(ignore_permissions=True)

		run_cron()
		missed = mark_missed()
		self.assertEqual(missed, [])

		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.status, "Draft")
		self.assertEqual(rep.docstatus, 0)

	def test_retention_failure_keeps_media_retryable(self):
		shift = _create_open_shift(self.profile)
		tmpl = self._create_template("Per Shift")
		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)

		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "retention_fail.jpg",
				"attached_to_doctype": "POS Monitoring Report",
				"attached_to_name": rep.name,
				"is_private": 1,
				"content": TINY_JPEG,
			}
		).insert(ignore_permissions=True)

		rep.append(
			"media",
			{
				"checkpoint_row": rep.checkpoints[0].name,
				"file_url": file_doc.file_url,
				"media_type": "Photo",
				"file_size": 100,
				"uploaded_at": add_days(today(), -95),
				"purged": 0,
			},
		)
		rep.status = "Reviewed"
		rep.docstatus = 1
		rep.flags.ignore_permissions = True
		rep.save()

		# Simulate deletion failure (e.g. delete_doc raising an exception)
		with patch("frappe.delete_doc", side_effect=OSError("Disk read-only")):
			deleted = purge_expired_media(today=today())
			self.assertEqual(deleted, 0)

		# Row must NOT be purged=1 and must remain retryable
		rep.reload()
		self.assertEqual(rep.media[0].purged, 0)
		self.assertTrue(frappe.db.exists("File", file_doc.name))

	# ---- Task 2: label resolution, snapshot, scope, opens_at ----

	def test_label_resolution_checkpoint_over_category_over_settings(self):
		_ensure_label(f"{_RUN_ID} CP Label", "Blue")
		_ensure_label(f"{_RUN_ID} Cat Label", "Orange")
		_ensure_label(f"{_RUN_ID} Set Label", "Purple")
		s = frappe.get_single("POS Monitoring Settings")
		s.set("default_labels", [{"label": f"{_RUN_ID} Set Label"}])
		s.save(ignore_permissions=True)

		cat_name = f"{_RUN_ID} Svc Cat Res {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Category",
				"category_name": cat_name,
				"cashier_can_label": 0,
				"labels": [{"label": f"{_RUN_ID} Cat Label"}],
			}
		).insert(ignore_permissions=True)

		# Checkpoint with own labels wins over category and settings
		cp_own = f"{_RUN_ID} Svc CP Own {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": cp_own,
				"category": cat_name,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
				"labels": [{"label": f"{_RUN_ID} CP Label"}],
			}
		).insert(ignore_permissions=True)
		opts = resolve_label_options(frappe.get_doc("POS Monitoring Checkpoint", cp_own))
		self.assertEqual(opts, [{"label": f"{_RUN_ID} CP Label", "color": "Blue"}])

		# Checkpoint without labels falls back to category
		cp_cat = f"{_RUN_ID} Svc CP Cat {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": cp_cat,
				"category": cat_name,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)
		opts = resolve_label_options(frappe.get_doc("POS Monitoring Checkpoint", cp_cat))
		self.assertEqual(opts, [{"label": f"{_RUN_ID} Cat Label", "color": "Orange"}])

		# Category without labels falls back to settings
		cat_bare = f"{_RUN_ID} Svc Cat Bare {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Category",
				"category_name": cat_bare,
				"cashier_can_label": 1,
			}
		).insert(ignore_permissions=True)
		cp_set = f"{_RUN_ID} Svc CP Set {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": cp_set,
				"category": cat_bare,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)
		opts = resolve_label_options(frappe.get_doc("POS Monitoring Checkpoint", cp_set))
		self.assertEqual(opts, [{"label": f"{_RUN_ID} Set Label", "color": "Purple"}])

	def test_disabled_labels_skipped_in_snapshot(self):
		_ensure_label(f"{_RUN_ID} Skip Off", "Red")
		_ensure_label(f"{_RUN_ID} Skip On", "Green")
		cat_name = f"{_RUN_ID} Svc Cat Skip {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Category",
				"category_name": cat_name,
				"labels": [{"label": f"{_RUN_ID} Skip Off"}, {"label": f"{_RUN_ID} Skip On"}],
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("POS Monitoring Label", f"{_RUN_ID} Skip Off", "disabled", 1)

		cp_name = f"{_RUN_ID} Svc CP Skip {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": cp_name,
				"category": cat_name,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)
		opts = resolve_label_options(frappe.get_doc("POS Monitoring Checkpoint", cp_name))
		self.assertEqual(opts, [{"label": f"{_RUN_ID} Skip On", "color": "Green"}])
		self.assertEqual(label_color(opts, f"{_RUN_ID} Skip Off"), None)
		self.assertEqual(label_color(opts, f"{_RUN_ID} Skip On"), "Green")

	def test_snapshot_unchanged_after_master_edit_or_disable(self):
		tmpl = self._create_template("Per Shift")
		shift = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)
		rep_name = frappe.db.get_value(
			"POS Monitoring Report",
			{"obligation_key": obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)},
		)
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		before = rep.checkpoints[0].label_options

		frappe.db.set_value("POS Monitoring Label", SVC_PASS, "color", "Purple")
		frappe.db.set_value("POS Monitoring Label", SVC_FAIL, "disabled", 1)
		self.addCleanup(frappe.db.set_value, "POS Monitoring Label", SVC_PASS, "color", "Green")
		self.addCleanup(frappe.db.set_value, "POS Monitoring Label", SVC_FAIL, "disabled", 0)

		rep.reload()
		self.assertEqual(rep.checkpoints[0].label_options, before)
		self.assertEqual(label_color(before, SVC_PASS), "Green")

	def test_build_report_empty_labels_raises_and_other_templates_continue(self):
		cat_name = f"{_RUN_ID} Svc Cat Empty {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Category",
				"category_name": cat_name,
			}
		).insert(ignore_permissions=True)
		cp_name = f"{_RUN_ID} Svc CP Empty {uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "POS Monitoring Checkpoint",
				"checkpoint_name": cp_name,
				"category": cat_name,
				"media_type": "Photo",
				"min_count": 1,
				"max_count": 2,
			}
		).insert(ignore_permissions=True)
		# tmpl_ok uses the shared Svc fixture checkpoint (labeled category);
		# tmpl_empty uses the bare checkpoint, whose real chain still falls
		# back to the shared defaults. Stub the resolver to [] for the direct
		# build_report call (unit-level raise). The cron-path isolation half
		# is covered by a bare checkpoint + emptied defaults in a throwaway
		# single: point the Single at a category with no labels of its own
		# ... but defaults are global, so instead stub the resolver during
		# ensure_obligations for the empty template only via side_effect.
		tmpl_empty = self._create_template("Per Shift", checkpoints=[cp_name])
		tmpl_ok = self._create_template("Per Shift", checkpoints=[self.checkpoint])
		shift = _create_open_shift(self.profile)

		with patch(
			"pos_next.services.outlet_monitoring.resolve_label_options",
			return_value=[],
		):
			with self.assertRaises(frappe.ValidationError):
				build_report(tmpl_empty, "Per Shift")
		key_empty = obligation_key(tmpl_empty.name, "Per Shift", opening_shift=shift.name)
		self.assertFalse(frappe.db.exists("POS Monitoring Report", {"obligation_key": key_empty}))

		# Cron-path isolation: fail ONLY tmpl_empty's checkpoint resolution
		# while tmpl_ok keeps building. side_effect routes on the checkpoint.
		real_resolve = resolve_label_options

		def _fail_bare_only(checkpoint_doc):
			if checkpoint_doc.name == cp_name:
				return []
			return real_resolve(checkpoint_doc)

		with patch(
			"pos_next.services.outlet_monitoring.resolve_label_options",
			side_effect=_fail_bare_only,
		):
			created = ensure_obligations(pos_profile=self.profile)
		key_ok = obligation_key(tmpl_ok.name, "Per Shift", opening_shift=shift.name)
		self.assertIn(
			frappe.db.get_value("POS Monitoring Report", {"obligation_key": key_ok}),
			created,
		)
		self.assertFalse(frappe.db.exists("POS Monitoring Report", {"obligation_key": key_empty}))
		self.assertTrue(frappe.db.exists("Error Log", {"method": "Outlet Monitoring obligation"}))

	def test_scope_all_and_selected_three_dimensions(self):
		cp = self.checkpoint
		tmpl = self._create_template(
			"Per Shift",
			checkpoints=[cp],
			companies=[self.company],
			company_scope="Selected",
			profile_scope="Selected",
			shift_type_scope="All",
			pos_profiles=[self.profile],
		)
		self.assertEqual(template_companies(tmpl), [self.company])
		self.assertTrue(profile_in_scope(tmpl, self.profile))
		self.assertFalse(profile_in_scope(tmpl, "Ghost Profile"))
		self.assertTrue(shift_in_scope(tmpl, "Anything"))

		t_all = self._create_template("Per Shift", checkpoints=[cp], companies=[], company_scope="All")
		companies = template_companies(t_all)
		self.assertIn(self.company, companies)

	def test_scope_all_ignores_table_rows(self):
		cp = self.checkpoint
		tmpl = self._create_template(
			"Per Shift",
			checkpoints=[cp],
			companies=["_Test Company 1"],
			company_scope="All",
			pos_profiles=[self.profile],
			profile_scope="All",
		)
		# Rows are ignored under All: the ghost company and the filled
		# profile table do not restrict the real shift's obligation.
		shift = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		self.assertTrue(frappe.db.exists("POS Monitoring Report", {"obligation_key": key}))

	def test_scope_narrowing_keeps_old_report_and_excludes(self):
		# All covers every target; narrowing to Selected keeps the old report
		# and creates no new obligation for the excluded target.
		tmpl = self._create_template("Per Shift", companies=[], company_scope="All")
		shift = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)
		key = obligation_key(tmpl.name, "Per Shift", opening_shift=shift.name)
		rep_name = frappe.db.get_value("POS Monitoring Report", {"obligation_key": key})
		self.assertTrue(rep_name)

		tmpl.reload()
		tmpl.company_scope = "Selected"
		tmpl.set("companies", [{"company": "_Test Company 1"}])
		tmpl.save(ignore_permissions=True)

		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		self.assertEqual(rep.company, self.company)
		created_again = ensure_obligations(pos_profile=self.profile)
		self.assertEqual(created_again, [])
		self.assertTrue(frappe.db.exists("POS Monitoring Report", rep_name))

		# A NEW shift for the excluded target gets no report: this is the
		# real scope check, not dedup on the old shift's obligation key.
		# One profile holds one open shift, so close the old shift first
		# (its Per Shift report is already built and stays untouched).
		frappe.db.set_value("POS Opening Shift", shift.name, "status", "Closed")
		shift2 = _create_open_shift(self.profile)
		ensure_obligations(pos_profile=self.profile)
		key2 = obligation_key(tmpl.name, "Per Shift", opening_shift=shift2.name)
		self.assertFalse(frappe.db.exists("POS Monitoring Report", {"obligation_key": key2}))

		# The old shift's report still exists untouched.
		self.assertTrue(frappe.db.exists("POS Monitoring Report", rep_name))

	def test_opens_at_per_shift_and_per_hari(self):
		t_shift = self._create_template("Per Shift", open_after_minutes=15, due_minutes=60)
		self.assertEqual(
			str(compute_opens_at(t_shift, period_start="2026-11-05 08:00:00")),
			"2026-11-05 08:15:00",
		)
		t_day = self._create_template("Per Hari", due_time="14:00:00", open_time="07:30:00")
		self.assertEqual(
			str(compute_opens_at(t_day, operational_date="2026-11-05")),
			"2026-11-05 07:30:00",
		)
		t_none = self._create_template("Per Shift", due_minutes=60)
		self.assertEqual(compute_opens_at(t_none, period_start="2026-11-05 08:00:00"), None)
		self.assertEqual(compute_opens_at(t_none, operational_date="2026-11-05"), None)

	def test_ensure_obligations_never_creates_manual(self):
		tmpl = self._create_template("Manual", submit_within_hours=4)
		shift = _create_open_shift(self.profile)
		created = ensure_obligations(pos_profile=self.profile)
		stale = [name for name in created if name.startswith("x")]
		self.assertEqual(stale, [])
		self.assertFalse(frappe.db.exists("POS Monitoring Report", {"template": tmpl.name}))
		self.assertFalse(shift.name in (created or []))
