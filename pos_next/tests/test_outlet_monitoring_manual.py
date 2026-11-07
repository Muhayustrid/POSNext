# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Task 4 tests: Manual cadence (§4.8) — scope, reference validation, candidates."""

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, getdate, now_datetime, today

from pos_next.api import outlet_monitoring as api
from pos_next.services.outlet_monitoring import ensure_obligations, mark_missed
from pos_next.services.outlet_monitoring_manual import (
	manual_obligation_key,
	manual_template_matches,
	reference_candidates,
	shipment_key,
)
from pos_next.tests.outlet_monitoring_test_utils import (
	delete_owned_fixtures,
	insert_owned_user,
	owned_fixtures,
	restore_settings_state,
	snapshot_settings_state,
)

_RUN_ID = uuid.uuid4().hex[:6]
# Run-unique fixtures: this suite never mutates a Label, Category or
# Checkpoint it did not create in this process.
MNL_PASS = f"{_RUN_ID} MNL Pass"
MNL_FAIL = f"{_RUN_ID} MNL Fail"
MNL_CATEGORY = f"{_RUN_ID} MNL Category"
MNL_CP = f"{_RUN_ID} MNL CP Photo"


def _create_user(role, prefix="mnl-cashier"):
	email = f"{_RUN_ID}-{prefix}-{uuid.uuid4().hex[:8]}@example.com"
	was_in_import = frappe.flags.in_import
	frappe.flags.in_import = True
	try:
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": prefix,
				"enabled": 1,
				"send_welcome_email": 0,
			}
		)
		user.flags.no_welcome_mail = True
		insert_owned_user(user, _RUN_ID, role)
	finally:
		frappe.flags.in_import = was_in_import

	frappe.clear_cache(user=email)
	return email


def _setup_profile(company="_Test Company"):
	profile_name = f"{_RUN_ID} _Test MNL Prof {uuid.uuid4().hex[:6]}"
	warehouse = frappe.db.get_value("Warehouse", {"company": company, "is_group": 0}, "name")
	if not warehouse:
		warehouse = (
			frappe.get_doc(
				{
					"doctype": "Warehouse",
					"warehouse_name": f"WH-{uuid.uuid4().hex[:6]}",
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


# Same fixture-delete pattern as api/test_outlet_monitoring.py: innermost
# children first, labels via DB-only deletes (Label.on_trash always rejects).
_FIXTURE_DOCTYPES = (
	"POS Monitoring Report",
	"POS Monitoring Template",
	"POS Monitoring Checkpoint",
	"POS Monitoring Category",
	"POS Monitoring Label",
	"POS Opening Shift",
	"POS Profile",
	"Delivery Note",
	"Purchase Receipt",
	"User",
)
_FIXTURE_CHILDREN = {
	"POS Monitoring Report": ("POS Monitoring Report Checkpoint", "POS Monitoring Report Media"),
	"POS Monitoring Template": (
		"POS Monitoring Template Checkpoint",
		"POS Monitoring Template Company",
		"POS Monitoring Template POS Profile",
		"POS Monitoring Template Shift Type",
	),
	"POS Monitoring Checkpoint": ("POS Monitoring Label Item",),
	"POS Monitoring Category": ("POS Monitoring Label Item",),
	"POS Opening Shift": ("POS Opening Shift Detail",),
	"POS Profile": (
		"POS Profile User",
		"POS Payment Method",
		"POS Item Group",
		"POS Customer Group",
	),
	"Delivery Note": ("Delivery Note Item",),
	"Purchase Receipt": ("Purchase Receipt Item",),
	"User": ("Has Role",),
}


def _snapshot_fixtures():
	return {dt: set(frappe.get_all(dt, pluck="name")) for dt in _FIXTURE_DOCTYPES}


# This suite's own name namespaces; a snapshot diff alone is not ownership.
_FIXTURE_PREFIXES = {
	"POS Monitoring Category": (f"{_RUN_ID} MNL Category",),
	"POS Monitoring Checkpoint": (f"{_RUN_ID} MNL CP",),
	"POS Monitoring Label": (f"{_RUN_ID} MNL Pass", f"{_RUN_ID} MNL Fail"),
	"POS Monitoring Template": (f"{_RUN_ID} Tmpl-MNL-",),
	"POS Profile": (f"{_RUN_ID} _Test MNL Prof ",),
	"Delivery Note": (f"{_RUN_ID} T-MNL-DN-",),
	"Purchase Receipt": (f"{_RUN_ID} T-MNL-PR-",),
	"User": (f"{_RUN_ID}-mnl-cashier-",),
}
_FIXTURE_LINKS = {
	# Shift names come from the site naming series, so they are claimed through
	# the POS Profile this suite created rather than by name.
	"POS Opening Shift": (("pos_profile", "POS Profile"),),
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


class TestOutletMonitoringManual(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls._suite_initial = _snapshot_fixtures()
		cls._suite_state = snapshot_settings_state()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		# Full-suite safety net (setup-suite pattern): catches everything the
		# per-test snapshot teardown may have missed under fix-stack contention.
		_delete_new_fixtures(getattr(cls, "_suite_initial", {}))
		_restore_settings_exact(cls._suite_state)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._original_state = snapshot_settings_state()
		self._test_initial = _snapshot_fixtures()
		self.addCleanup(self._restore_settings)
		self.company = "_Test Company"
		# Other company is a sibling test company: real Company doc so
		# template Companies child rows pass link validation on save.
		self.other_company = "_Test Company 2"
		self.profile = _setup_profile(self.company)
		self.cashier = _create_user("POSNext Cashier", "mnl-cashier")

		pdoc = frappe.get_doc("POS Profile", self.profile)
		pdoc.append("applicable_for_users", {"user": self.cashier})
		pdoc.save(ignore_permissions=True)

		for lbl, color in ((MNL_PASS, "Green"), (MNL_FAIL, "Red")):
			if not frappe.db.exists("POS Monitoring Label", lbl):
				frappe.get_doc(
					{
						"doctype": "POS Monitoring Label",
						"label_name": lbl,
						"color": color,
					}
				).insert(ignore_permissions=True)
		if not frappe.db.exists("POS Monitoring Category", MNL_CATEGORY):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Category",
					"category_name": MNL_CATEGORY,
					"cashier_can_label": 1,
					"labels": [{"label": MNL_PASS}, {"label": MNL_FAIL}],
				}
			).insert(ignore_permissions=True)
		if not frappe.db.exists("POS Monitoring Checkpoint", MNL_CP):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Checkpoint",
					"checkpoint_name": MNL_CP,
					"category": MNL_CATEGORY,
					"media_type": "Photo",
					"min_count": 1,
					"max_count": 2,
				}
			).insert(ignore_permissions=True)

		s = frappe.get_single("POS Monitoring Settings")
		s.enabled = 1
		s.allow_late_submission = 1
		s.allow_correction = 1
		s.shift_missed_grace_hours = 12
		existing = {r.label for r in s.default_labels}
		for lbl in (MNL_PASS, MNL_FAIL):
			if lbl not in existing:
				s.append("default_labels", {"label": lbl})
		s.save(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		_delete_new_fixtures(getattr(self, "_test_initial", {}))
		self._restore_settings()
		frappe.db.commit()
		super().tearDown()

	def _restore_settings(self):
		_restore_settings_exact(self._original_state)

	def _manual_template(self, checkpoints=None, submit_within_hours=24):
		t = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Tmpl-MNL-{uuid.uuid4().hex[:6]}",
				"cadence": "Manual",
				"enabled": 1,
				"submit_within_hours": submit_within_hours,
				"checkpoints": [{"checkpoint": cp, "required": 0} for cp in (checkpoints or [MNL_CP])],
				"company_scope": "Selected",
				"companies": [{"company": self.company}],
			}
		)
		t.insert(ignore_permissions=True)
		return t

	def _open_shift(self):
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile,
				"company": self.company,
				"user": self.cashier,
				"posting_date": today(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": "Cash", "opening_amount": 100000}],
			}
		)
		shift.insert(ignore_permissions=True)
		shift.submit()
		return shift

	def _make_dn(
		self,
		company,
		represents,
		posting_date=None,
		docstatus=1,
		is_return=0,
		internal_customer=True,
		items=2,
		prefix=f"{_RUN_ID} T-MNL-DN",
	):
		"""Synthetic DN header + items via db_insert (no stock ledger)."""
		dn_name = f"{prefix}-{uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "Delivery Note",
				"name": dn_name,
				"docstatus": docstatus,
				"is_return": is_return,
				"is_internal_customer": 1 if internal_customer else 0,
				"represents_company": represents,
				"company": company,
				"posting_date": posting_date or getdate(today()),
			}
		).db_insert()
		item_names = []
		for i in range(items):
			iname = f"{dn_name}-I{i + 1}"
			frappe.get_doc(
				{
					"doctype": "Delivery Note Item",
					"name": iname,
					"parent": dn_name,
					"parenttype": "Delivery Note",
					"parentfield": "items",
					"idx": i + 1,
					"docstatus": docstatus,
				}
			).db_insert()
			item_names.append(iname)
		return dn_name, item_names

	def _make_pr(
		self,
		company,
		posting_date=None,
		docstatus=1,
		is_return=0,
		internal_supplier=False,
		dn_ref=None,
		dn_items=None,
		items=1,
		prefix=f"{_RUN_ID} T-MNL-PR",
	):
		"""Synthetic PR header + items via db_insert (no stock ledger)."""
		pr_name = f"{prefix}-{uuid.uuid4().hex[:6]}"
		frappe.get_doc(
			{
				"doctype": "Purchase Receipt",
				"name": pr_name,
				"docstatus": docstatus,
				"is_return": is_return,
				"is_internal_supplier": 1 if internal_supplier else 0,
				"company": company,
				"inter_company_reference": dn_ref,
				"posting_date": posting_date or getdate(today()),
			}
		).db_insert()
		for i in range(items):
			row = {
				"doctype": "Purchase Receipt Item",
				"name": f"{pr_name}-I{i + 1}",
				"parent": pr_name,
				"parenttype": "Purchase Receipt",
				"parentfield": "items",
				"idx": i + 1,
				"docstatus": docstatus,
			}
			if dn_items is not None:
				row["delivery_note_item"] = dn_items[i % len(dn_items)]
			frappe.get_doc(row).db_insert()
		return pr_name

	def _review_as_admin(self, rep_name, label=MNL_PASS):
		frappe.set_user("Administrator")
		rep = frappe.get_doc("POS Monitoring Report", rep_name)
		rep.checkpoints[0].label = label
		rep.status = "Reviewed"
		rep.correction_requested = 1
		rep.save()

	def test_start_manual_with_supplier_pr_and_internal_dn(self):
		tmpl = self._manual_template()
		pr = self._make_pr(self.company)
		dn, dn_items = self._make_dn(self.other_company, self.company)
		ipr = self._make_pr(self.company, internal_supplier=True, dn_ref=dn, dn_items=dn_items, items=2)

		templates = api.get_manual_templates(self.profile)
		self.assertTrue(any(t["name"] == tmpl.name for t in templates))
		row = next(t for t in templates if t["name"] == tmpl.name)
		self.assertEqual(set(row), {"name", "template_name"})

		frappe.set_user(self.cashier)
		# Supplier PR: key is the PR itself
		res = api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", pr)
		self.assertEqual(res["status"], "Draft")
		self.assertEqual(res["cadence"], "Manual")
		self.assertEqual(res["reference_doctype"], "Purchase Receipt")
		self.assertEqual(res["reference_name"], pr)
		doc = frappe.get_doc("POS Monitoring Report", res["name"])
		self.assertEqual(doc.obligation_key, manual_obligation_key(tmpl.name, "Purchase Receipt", pr))

		# Internal PR: normalized to its DN (use a second template to avoid dup key)
		tmpl2 = self._manual_template()
		res2 = api.start_manual_report(self.profile, tmpl2.name, "Purchase Receipt", ipr)
		doc2 = frappe.get_doc("POS Monitoring Report", res2["name"])
		self.assertEqual(res2["reference_doctype"], "Purchase Receipt")
		self.assertEqual(res2["reference_name"], ipr)
		self.assertEqual(doc2.obligation_key, manual_obligation_key(tmpl2.name, "Delivery Note", dn))

		# Direct DN
		tmpl3 = self._manual_template()
		res3 = api.start_manual_report(self.profile, tmpl3.name, "Delivery Note", dn)
		doc3 = frappe.get_doc("POS Monitoring Report", res3["name"])
		self.assertEqual(doc3.obligation_key, manual_obligation_key(tmpl3.name, "Delivery Note", dn))

	def test_reference_rejections(self):
		tmpl = self._manual_template()
		old = getdate(add_to_date(getdate(today()), days=-8))
		future = getdate(add_to_date(getdate(today()), days=1))
		pr_other = self._make_pr(self.other_company)
		pr_draft = self._make_pr(self.company, docstatus=0)
		pr_cancelled = self._make_pr(self.company, docstatus=2)
		pr_return = self._make_pr(self.company, is_return=1)
		dn_external, _ = self._make_dn(self.other_company, self.other_company, internal_customer=True)
		dn_future, _ = self._make_dn(self.other_company, self.company, posting_date=future)
		dn_old, _ = self._make_dn(self.other_company, self.company, posting_date=old)

		frappe.set_user(self.cashier)
		for doctype, name in (
			("Purchase Receipt", pr_other),
			("Purchase Receipt", pr_draft),
			("Purchase Receipt", pr_cancelled),
			("Purchase Receipt", pr_return),
			("Delivery Note", dn_external),
			("Delivery Note", dn_future),
			("Delivery Note", dn_old),
		):
			with self.assertRaises(frappe.ValidationError, msg=f"{doctype} {name}"):
				api.start_manual_report(self.profile, tmpl.name, doctype, name)

	def test_internal_pr_keys_to_its_dn_no_double_report(self):
		tmpl = self._manual_template()
		dn, dn_items = self._make_dn(self.other_company, self.company)
		ipr = self._make_pr(self.company, internal_supplier=True, dn_ref=dn, dn_items=dn_items)

		frappe.set_user(self.cashier)
		api.start_manual_report(self.profile, tmpl.name, "Delivery Note", dn)
		# Same shipment via its internal PR: duplicate
		with self.assertRaises((frappe.ValidationError, frappe.DuplicateEntryError)):
			api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", ipr)

		# Reverse direction with fresh docs
		tmpl2 = self._manual_template()
		dn2, dn2_items = self._make_dn(self.other_company, self.company)
		ipr2 = self._make_pr(self.company, internal_supplier=True, dn_ref=dn2, dn_items=dn2_items)
		api.start_manual_report(self.profile, tmpl2.name, "Purchase Receipt", ipr2)
		with self.assertRaises((frappe.ValidationError, frappe.DuplicateEntryError)):
			api.start_manual_report(self.profile, tmpl2.name, "Delivery Note", dn2)

	def test_internal_pr_without_reference_or_mixed_items_rejected(self):
		tmpl = self._manual_template()
		dn1, dn1_items = self._make_dn(self.other_company, self.company)
		_dn2, dn2_items = self._make_dn(self.other_company, self.company)
		no_ref = self._make_pr(self.company, internal_supplier=True)
		mixed = self._make_pr(self.company, internal_supplier=True, dn_ref=dn1, dn_items=None, items=1)
		frappe.db.set_value("Purchase Receipt Item", f"{mixed}-I1", "delivery_note_item", dn2_items[0])
		cross = self._make_pr(self.company, internal_supplier=True, dn_ref=dn1, dn_items=dn1_items, items=1)
		frappe.get_doc(
			{
				"doctype": "Purchase Receipt Item",
				"name": f"{cross}-I2",
				"parent": cross,
				"parenttype": "Purchase Receipt",
				"parentfield": "items",
				"idx": 2,
				"docstatus": 1,
				"delivery_note_item": dn2_items[0],
			}
		).db_insert()

		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", no_ref)
		with self.assertRaises(frappe.ValidationError):
			api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", mixed)
		with self.assertRaises(frappe.ValidationError):
			api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", cross)

	def test_new_pr_from_old_dn_still_candidate(self):
		# Age applies to the chosen doc only: old DN is rejected directly,
		# but a fresh internal PR pointing at it is accepted and listed.
		tmpl = self._manual_template()
		old = getdate(add_to_date(getdate(today()), days=-8))
		dn, dn_items = self._make_dn(self.other_company, self.company, posting_date=old)
		ipr = self._make_pr(self.company, internal_supplier=True, dn_ref=dn, dn_items=dn_items)

		# Candidate BEFORE it is reported: the fresh PR is listed, the old DN is not.
		frappe.set_user(self.cashier)
		cands = api.get_reference_candidates(self.profile, tmpl.name)
		self.assertIn(
			("Purchase Receipt", ipr),
			{(c["doctype"], c["name"]) for c in cands},
			"a fresh internal PR from an old DN must be a candidate",
		)
		self.assertNotIn(("Delivery Note", dn), {(c["doctype"], c["name"]) for c in cands})

		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.start_manual_report(self.profile, tmpl.name, "Delivery Note", dn)
		res = api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", ipr)
		doc = frappe.get_doc("POS Monitoring Report", res["name"])
		self.assertEqual(doc.obligation_key, manual_obligation_key(tmpl.name, "Delivery Note", dn))

		cands = api.get_reference_candidates(self.profile, tmpl.name)
		pr_rows = [c for c in cands if c["doctype"] == "Purchase Receipt" and c["name"] == ipr]
		self.assertEqual(len(pr_rows), 0)  # reported already
		dn_rows = [c for c in cands if c["doctype"] == "Delivery Note" and c["name"] == dn]
		self.assertEqual(len(dn_rows), 0)  # old DN never a candidate

	def _cap_fixture(self, doctype):
		"""25 eligible docs of one type: the first 20 already reported.

		Returns (template, reported_names, [the 5 unreported names]). The 20
		reported rows are dated today and the remaining 5 yesterday, so the
		reported rows sort FIRST in the posting_date desc scan — the 21st raw
		row is the first still-eligible document, which a cap applied before
		the filters would never reach.
		"""
		tmpl = self._manual_template()
		today_ = getdate(today())
		if doctype == "Purchase Receipt":
			make = lambda date: self._make_pr(self.company, posting_date=date)  # noqa: E731
		else:
			make = lambda date: self._make_dn(self.other_company, self.company, posting_date=date)[0]  # noqa: E731
		reported = [make(today_) for _ in range(20)]
		eligible = [make(getdate(add_to_date(today_, days=-1))) for _ in range(5)]
		frappe.set_user(self.cashier)
		for name in reported:
			api.start_manual_report(self.profile, tmpl.name, doctype, name)
		return tmpl, reported, eligible

	def test_candidates_cap_counts_only_eligible_docs_pr(self):
		# The cap applies AFTER the rule/reported filters: with 25 PRs and the
		# first 20 reported, the 21st (still eligible) one must come back.
		tmpl, reported, eligible = self._cap_fixture("Purchase Receipt")
		cands = api.get_reference_candidates(self.profile, tmpl.name)
		pr_rows = [c["name"] for c in cands if c["doctype"] == "Purchase Receipt"]
		self.assertIn(eligible[0], pr_rows, "the 21st eligible PR must survive the cap")
		self.assertEqual(set(eligible) - set(pr_rows), set(), "all 5 eligible PRs fit the cap")
		self.assertLessEqual(len(pr_rows), 20)
		self.assertTrue(set(pr_rows).isdisjoint(reported))

	def test_candidates_cap_counts_only_eligible_docs_dn(self):
		# Same shape for Delivery Notes, whose eligibility rules are different
		# (internal customer, represents_company, other company).
		tmpl, reported, eligible = self._cap_fixture("Delivery Note")
		cands = api.get_reference_candidates(self.profile, tmpl.name)
		dn_rows = [c["name"] for c in cands if c["doctype"] == "Delivery Note"]
		self.assertIn(eligible[0], dn_rows, "the 21st eligible DN must survive the cap")
		self.assertEqual(set(eligible) - set(dn_rows), set(), "all 5 eligible DNs fit the cap")
		self.assertLessEqual(len(dn_rows), 20)
		self.assertTrue(set(dn_rows).isdisjoint(reported))

	def test_candidates_search_matches_name_or_sender(self):
		tmpl = self._manual_template()
		pr = self._make_pr(self.company)
		dn, _ = self._make_dn(self.other_company, self.company)
		frappe.db.set_value("Purchase Receipt", pr, "supplier_name", "Toko Sumber Rejeki")
		frappe.set_user("Administrator")
		other_pr = self._make_pr(self.company)
		frappe.db.set_value("Purchase Receipt", other_pr, "supplier_name", "Pemasok Lain")

		frappe.set_user(self.cashier)
		# Search by document number (exact) on both types
		by_name = api.get_reference_candidates(self.profile, tmpl.name, pr)
		self.assertEqual([(c["doctype"], c["name"]) for c in by_name], [("Purchase Receipt", pr)])
		by_dn = api.get_reference_candidates(self.profile, tmpl.name, dn)
		self.assertEqual([(c["doctype"], c["name"]) for c in by_dn], [("Delivery Note", dn)])

		# Search by sender: PR supplier_name, DN company
		by_supplier = api.get_reference_candidates(self.profile, tmpl.name, "Sumber Rejeki")
		self.assertEqual([(c["doctype"], c["name"]) for c in by_supplier], [("Purchase Receipt", pr)])
		by_company = api.get_reference_candidates(self.profile, tmpl.name, self.other_company)
		dn_rows = [(c["doctype"], c["name"]) for c in by_company if c["doctype"] == "Delivery Note"]
		self.assertIn(("Delivery Note", dn), dn_rows)

	def test_partial_prs_of_one_dn_share_key(self):
		tmpl = self._manual_template()
		dn, dn_items = self._make_dn(self.other_company, self.company, items=3)
		pr1 = self._make_pr(self.company, internal_supplier=True, dn_ref=dn, dn_items=dn_items[:2], items=2)
		pr2 = self._make_pr(self.company, internal_supplier=True, dn_ref=dn, dn_items=dn_items[2:], items=1)

		frappe.set_user(self.cashier)
		api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", pr1)
		with self.assertRaises((frappe.ValidationError, frappe.DuplicateEntryError)):
			api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", pr2)

	def test_same_doc_other_manual_template_allowed(self):
		tmpl1 = self._manual_template()
		tmpl2 = self._manual_template()
		pr = self._make_pr(self.company)

		frappe.set_user(self.cashier)
		r1 = api.start_manual_report(self.profile, tmpl1.name, "Purchase Receipt", pr)
		r2 = api.start_manual_report(self.profile, tmpl2.name, "Purchase Receipt", pr)
		self.assertNotEqual(r1["name"], r2["name"])

	def test_concurrent_double_start_one_report(self):
		import threading

		from werkzeug.test import EnvironBuilder
		from werkzeug.wrappers import Request

		tmpl = self._manual_template()
		dn, _ = self._make_dn(self.other_company, self.company)
		frappe.db.commit()
		cashier = self.cashier
		profile = self.profile
		tmpl_name = tmpl.name

		site = frappe.local.site
		sites_path = getattr(frappe.local, "sites_path", None) or "/home/frappe/frappe-bench/sites"
		barrier = threading.Barrier(2)
		results = []
		errors = []

		def worker():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			builder = EnvironBuilder(path="/api/method/pos_next.api.outlet_monitoring.start_manual_report")
			frappe.local.request = Request(builder.get_environ())
			frappe.local.form_dict = frappe._dict(cmd="start_manual_report")
			frappe.local.session = frappe._dict(user=cashier, data=frappe._dict())
			try:
				barrier.wait(timeout=10)
				res = api.start_manual_report(profile, tmpl_name, "Delivery Note", dn)
				frappe.db.commit()
				results.append(res["name"])
			except Exception as e:
				frappe.db.rollback()
				errors.append(e)
			finally:
				frappe.db.close()

		t1 = threading.Thread(target=worker)
		t2 = threading.Thread(target=worker)
		t1.start()
		t2.start()
		t1.join(timeout=30)
		t2.join(timeout=30)

		frappe.db.rollback()
		frappe.set_user("Administrator")

		self.assertEqual(len(results), 1, f"Expected exactly 1 success, got {len(results)}. Errors: {errors}")
		self.assertEqual(len(errors), 1, f"Expected exactly 1 error, got {len(errors)}")
		self.assertIsInstance(errors[0], (frappe.ValidationError, frappe.DuplicateEntryError))
		key = manual_obligation_key(tmpl_name, "Delivery Note", dn)
		count = frappe.db.count("POS Monitoring Report", {"obligation_key": key, "revision": 1})
		self.assertEqual(count, 1)

	def test_scope_shift_selected_without_open_shift_fails_closed(self):
		frappe.set_user("Administrator")
		tmpl = self._manual_template()
		tmpl.reload()
		tmpl.shift_type_scope = "Selected"
		tmpl.append("shift_types", {"shift_type": "Morning Slot"})
		tmpl.save(ignore_permissions=True)
		dn, _ = self._make_dn(self.other_company, self.company)
		self.assertFalse(manual_template_matches(tmpl, self.profile))

		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.start_manual_report(self.profile, tmpl.name, "Delivery Note", dn)

	def test_template_non_manual_disabled_or_out_of_scope_rejected(self):
		frappe.set_user("Administrator")
		manual = self._manual_template()
		manual.reload()
		manual.enabled = 0
		manual.save(ignore_permissions=True)
		other = self._manual_template()
		other.reload()
		other.company_scope = "Selected"
		other.companies = []
		other.append("companies", {"company": self.other_company})
		other.save(ignore_permissions=True)
		per_shift = frappe.get_doc(
			{
				"doctype": "POS Monitoring Template",
				"template_name": f"{_RUN_ID} Tmpl-MNL-PS-{uuid.uuid4().hex[:6]}",
				"cadence": "Per Shift",
				"enabled": 1,
				"due_minutes_after_open": 30,
				"checkpoints": [{"checkpoint": MNL_CP, "required": 0}],
				"company_scope": "Selected",
				"companies": [{"company": self.company}],
			}
		)
		per_shift.insert(ignore_permissions=True)
		dn, _ = self._make_dn(self.other_company, self.company)

		frappe.set_user(self.cashier)
		for t in (manual, other, per_shift):
			with self.assertRaises(frappe.ValidationError, msg=t.name):
				api.start_manual_report(self.profile, t.name, "Delivery Note", dn)
			with self.assertRaises(frappe.ValidationError, msg=t.name):
				api.get_reference_candidates(self.profile, t.name)

	def test_candidates_no_money_fields_exclude_used_and_auth(self):
		tmpl = self._manual_template()
		pr = self._make_pr(self.company)
		dn, _ = self._make_dn(self.other_company, self.company)

		frappe.set_user(self.cashier)
		cands = api.get_reference_candidates(self.profile, tmpl.name)
		names = {(c["doctype"], c["name"]) for c in cands}
		self.assertIn(("Purchase Receipt", pr), names)
		self.assertIn(("Delivery Note", dn), names)
		for c in cands:
			self.assertLessEqual(set(c), {"doctype", "name", "posting_date", "sender", "item_count"})
			self.assertIn("item_count", c)

		# Reported docs disappear from candidates
		api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", pr)
		cands = api.get_reference_candidates(self.profile, tmpl.name)
		names = {(c["doctype"], c["name"]) for c in cands}
		self.assertNotIn(("Purchase Receipt", pr), names)
		self.assertIn(("Delivery Note", dn), names)

		# Guest is rejected
		frappe.set_user("Guest")
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			api.get_reference_candidates(self.profile, tmpl.name)

		# Cashier of another unassigned profile is rejected
		profile2 = _setup_profile(self.company)
		frappe.set_user(self.cashier)
		with self.assertRaises((frappe.ValidationError, frappe.PermissionError)):
			api.get_reference_candidates(profile2, tmpl.name)

		# Out-of-scope template is rejected
		frappe.set_user("Administrator")
		other = self._manual_template()
		other.reload()
		other.company_scope = "Selected"
		other.companies = []
		other.append("companies", {"company": self.other_company})
		other.save(ignore_permissions=True)
		frappe.set_user(self.cashier)
		with self.assertRaises(frappe.ValidationError):
			api.get_reference_candidates(self.profile, other.name)

	def test_manual_draft_in_my_tasks_once_and_in_shift_tasks(self):
		shift = self._open_shift()
		tmpl = self._manual_template()

		frappe.set_user(self.cashier)
		templates = api.get_manual_templates(self.profile)
		self.assertTrue(any(t["name"] == tmpl.name for t in templates))
		res = api.start_manual_report(
			self.profile, tmpl.name, "Purchase Receipt", self._make_pr(self.company)
		)

		tasks = api.get_my_tasks(self.profile)
		matching = [t for t in tasks["tasks"] if t["name"] == res["name"]]
		self.assertEqual(len(matching), 1)
		self.assertEqual(matching[0]["cadence"], "Manual")
		self.assertEqual(matching[0]["reference_doctype"], "Purchase Receipt")

		shift_tasks = api.get_shift_tasks(shift.name)
		self.assertTrue(any(i["name"] == res["name"] for i in shift_tasks["items"]))

		# ensure_obligations never creates Manual reports on its own
		frappe.set_user("Administrator")
		created = ensure_obligations(pos_profile=self.profile)
		self.assertNotIn(res["name"], created)
		count = frappe.db.count(
			"POS Monitoring Report",
			{"cadence": "Manual", "pos_profile": self.profile, "docstatus": 0, "status": "Draft"},
		)
		self.assertEqual(count, 1)

	def test_manual_missed_after_due_plus_grace(self):
		tmpl = self._manual_template()
		pr = self._make_pr(self.company)

		frappe.set_user(self.cashier)
		res = api.start_manual_report(self.profile, tmpl.name, "Purchase Receipt", pr)
		fresh = api.start_manual_report(
			self.profile, tmpl.name, "Delivery Note", self._make_dn(self.other_company, self.company)[0]
		)

		frappe.set_user("Administrator")
		past = add_to_date(now_datetime(), hours=-(12 + 1))
		frappe.db.set_value("POS Monitoring Report", res["name"], "due_at", past)
		missed = mark_missed(names=[res["name"], fresh["name"]])
		self.assertIn(res["name"], missed)
		self.assertNotIn(fresh["name"], missed)
		doc = frappe.get_doc("POS Monitoring Report", res["name"])
		self.assertEqual(doc.status, "Missed")
		self.assertEqual(doc.docstatus, 1)

	def test_manual_revision_ignores_reference_age(self):
		tmpl = self._manual_template()
		dn, _ = self._make_dn(self.other_company, self.company)

		frappe.set_user(self.cashier)
		res = api.start_manual_report(self.profile, tmpl.name, "Delivery Note", dn)
		self._review_as_admin(res["name"])

		# Age the reference past the 7-day window after reporting
		frappe.set_user("Administrator")
		old = getdate(add_to_date(getdate(today()), days=-30))
		frappe.db.set_value("Delivery Note", dn, "posting_date", old)

		frappe.set_user(self.cashier)
		rev = api.create_revision(res["name"])
		self.assertEqual(rev["revision"], 2)
		self.assertEqual(rev["status"], "Draft")
		self.assertEqual(rev["reference_doctype"], "Delivery Note")
		self.assertEqual(rev["reference_name"], dn)
		rev_doc = frappe.get_doc("POS Monitoring Report", rev["name"])
		old_doc = frappe.get_doc("POS Monitoring Report", res["name"])
		self.assertEqual(rev_doc.obligation_key, old_doc.obligation_key)

	def test_shipment_key_and_obligation_key_helpers(self):
		dn, dn_items = self._make_dn(self.other_company, self.company)
		ipr = self._make_pr(self.company, internal_supplier=True, dn_ref=dn, dn_items=dn_items)
		spr = self._make_pr(self.company)

		self.assertEqual(shipment_key("Delivery Note", dn, self.company), ("Delivery Note", dn))
		self.assertEqual(shipment_key("Purchase Receipt", ipr, self.company), ("Delivery Note", dn))
		self.assertEqual(shipment_key("Purchase Receipt", spr, self.company), ("Purchase Receipt", spr))
		with self.assertRaises(frappe.ValidationError):
			shipment_key("Sales Invoice", "X", self.company)
		with self.assertRaises(frappe.ValidationError):
			shipment_key("Delivery Note", "NO-SUCH-DN", self.company)
		self.assertEqual(
			manual_obligation_key("T", "Delivery Note", "D1"),
			"T|manual|Delivery Note|D1",
		)
