# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Task 1: POS Next Reject Request workflow + schema + legacy patch.

Tests for pos_next.reject_request_setup:
- ensure_reject_request_workflow creates the workflow with spec states and transitions
- idempotent and never displaces admin workflow
- migrate_legacy_states correctly maps old status/decision to workflow_state/borne_by/approval_code

Run via:
pos_next/_pn_run_tests.py pos_next.tests.test_reject_request_setup
"""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint, today

from pos_next.api.test_production import _make_test_item, _seed_stock
from pos_next.reject_request_setup import (
	APPROVER_ROLE,
	CREW_ROLES,
	DOCUMENT_TYPE,
	STATE_AWAITING,
	STATE_CANCELLED,
	STATE_COMPLETED,
	STATE_DRAFT,
	STATE_PENDING,
	STATE_PROOF,
	STATES,
	TRANSITIONS,
	WORKFLOW_NAME,
	ensure_reject_request_workflow,
	migrate_legacy_states,
)

ADMIN = "Administrator"


class TestRejectRequestSetup(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._wf_baseline = {
			w.name: cint(w.is_active)
			for w in frappe.get_all(
				"Workflow", filters={"document_type": DOCUMENT_TYPE}, fields=["name", "is_active"]
			)
		}
		self.addCleanup(self._restore_workflows)

		from pos_next.install import ensure_reject_purposes

		ensure_reject_purposes()
		self.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		self.company, self.warehouse = frappe.db.get_value(
			"POS Profile", self.pos_profile, ["company", "warehouse"]
		)
		if not self.warehouse:
			self.warehouse = frappe.db.get_value(
				"Warehouse", {"company": self.company, "is_group": 0, "disabled": 0}, "name"
			)
		self.item = _make_test_item()
		_seed_stock(self.item, self.warehouse, 20)

	def tearDown(self):
		frappe.set_user(ADMIN)
		super().tearDown()

	def _restore_workflows(self):
		frappe.set_user(ADMIN)
		for w in frappe.get_all("Workflow", filters={"document_type": DOCUMENT_TYPE}, fields=["name"]):
			if w.name not in self._wf_baseline:
				frappe.delete_doc("Workflow", w.name, force=True, ignore_permissions=True)
		for name, active in self._wf_baseline.items():
			if frappe.db.exists("Workflow", name):
				frappe.db.set_value("Workflow", name, "is_active", active)
		if WORKFLOW_NAME in self._wf_baseline and not frappe.db.exists("Workflow", WORKFLOW_NAME):
			ensure_reject_request_workflow(quiet=True)

	def _make_reject_doc(self, qty=1, **extra):
		doc = frappe.get_doc(
			{
				"doctype": DOCUMENT_TYPE,
				"purpose": "Unsold",
				"pos_profile": self.pos_profile,
				"incident_date": today(),
				"reason": "setup test",
				"items": [{"item_code": self.item, "qty": qty}],
				**extra,
			}
		).insert(ignore_permissions=True)
		return doc

	# ------------------------------------------------------------------ constants

	def test_constants_match_spec(self):
		self.assertEqual(WORKFLOW_NAME, "POS Next Reject Request")
		self.assertEqual(DOCUMENT_TYPE, "POS Reject Request")
		self.assertEqual(CREW_ROLES, ("POSNext Cashier", "POSNext Manager"))
		self.assertEqual(APPROVER_ROLE, "POSNext Reject Approver")
		self.assertEqual(STATE_DRAFT, "Draft")
		self.assertEqual(STATE_PENDING, "Pending Approval")
		self.assertEqual(STATE_AWAITING, "Awaiting Destruction")
		self.assertEqual(STATE_PROOF, "Destruction Submitted")
		self.assertEqual(STATE_COMPLETED, "Completed")
		self.assertEqual(STATE_CANCELLED, "Cancelled")

	# ------------------------------------------------------------------ schema & perms

	def test_doctype_schema_and_permissions(self):
		meta = frappe.get_meta(DOCUMENT_TYPE)
		self.assertTrue(meta.has_field("approval_code"))
		f_app = meta.get_field("approval_code")
		self.assertEqual(cint(f_app.unique), 1)
		self.assertEqual(cint(f_app.read_only), 1)
		self.assertEqual(cint(f_app.no_copy), 1)
		self.assertEqual(cint(f_app.allow_on_submit), 1)

		self.assertTrue(meta.has_field("borne_by"))
		f_borne = meta.get_field("borne_by")
		self.assertEqual(cint(f_borne.allow_on_submit), 1)
		self.assertIn("Company", f_borne.options)
		self.assertIn("Crew", f_borne.options)

		self.assertTrue(meta.has_field("return_reason"))
		f_ret = meta.get_field("return_reason")
		self.assertEqual(cint(f_ret.allow_on_submit), 1)
		self.assertEqual(cint(f_ret.read_only), 1)
		self.assertEqual(cint(f_ret.no_copy), 1)

		self.assertTrue(meta.has_field("letter_head"))
		f_lh = meta.get_field("letter_head")
		self.assertEqual(cint(f_lh.hidden), 1)
		self.assertEqual(cint(f_lh.print_hide), 1)

		for f in ("video_link", "liabilities", "decided_by", "decided_on", "issue_entry", "closed_on"):
			self.assertEqual(cint(meta.get_field(f).allow_on_submit), 1, f"{f} must have allow_on_submit: 1")

		# Old status, decision, decision_notes removed from meta
		for f in ("status", "decision", "decision_notes"):
			self.assertFalse(meta.has_field(f), f"{f} must be removed from meta")

		# Item doctype fields
		item_meta = frappe.get_meta("POS Reject Request Item")
		self.assertTrue(item_meta.has_field("production_date"))
		self.assertEqual(item_meta.get_field("production_date").fieldtype, "Date")
		self.assertTrue(item_meta.has_field("reject_reason"))
		self.assertEqual(item_meta.get_field("reject_reason").fieldtype, "Small Text")

		# Permissions
		perms = {p.role: p for p in meta.permissions}
		self.assertIn(APPROVER_ROLE, perms)
		app_perm = perms[APPROVER_ROLE]
		self.assertEqual(cint(app_perm.write), 1)
		self.assertEqual(cint(app_perm.submit), 1)
		self.assertEqual(cint(app_perm.cancel), 1)

		for role in CREW_ROLES:
			self.assertIn(role, perms)
			self.assertEqual(cint(perms[role].write), 1)
			self.assertEqual(cint(perms[role].submit), 1)
			self.assertEqual(cint(perms[role].cancel), 0)

	# ------------------------------------------------------------------ workflow

	def test_creates_workflow_with_spec_states(self):
		if frappe.db.exists("Workflow", WORKFLOW_NAME):
			frappe.delete_doc("Workflow", WORKFLOW_NAME, force=True, ignore_permissions=True)

		self.assertTrue(ensure_reject_request_workflow(quiet=True))

		wf = frappe.get_doc("Workflow", WORKFLOW_NAME)
		self.assertEqual(wf.document_type, DOCUMENT_TYPE)
		self.assertEqual(cint(wf.is_active), 1)
		self.assertEqual(wf.workflow_state_field, "workflow_state")
		self.assertEqual(cint(wf.send_email_alert), 0)

		expected_states = {(s[0], s[1]) for s in STATES}
		actual_states = {(s.state, cint(s.doc_status)) for s in wf.states}
		self.assertEqual(actual_states, expected_states)

		expected_transitions = {
			(from_state, action, to_state, role, allow_self)
			for from_state, action, to_state, roles, allow_self in TRANSITIONS
			for role in roles
		}
		actual_transitions = {
			(t.state, t.action, t.next_state, t.allowed, cint(t.allow_self_approval))
			for t in wf.transitions
		}
		self.assertEqual(actual_transitions, expected_transitions)

	def test_idempotent_and_never_displaces_admin_workflow(self):
		ensure_reject_request_workflow(quiet=True)
		self.assertFalse(ensure_reject_request_workflow(quiet=True))

		# Another active workflow must not be displaced
		other = "_T Other Reject WF"
		if frappe.db.exists("Workflow", other):
			frappe.delete_doc("Workflow", other, force=True, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": other,
				"document_type": DOCUMENT_TYPE,
				"workflow_state_field": "workflow_state",
				"is_active": 1,
				"states": [{"state": STATE_DRAFT, "doc_status": 0, "allow_edit": "System Manager"}],
			}
		).insert(ignore_permissions=True)

		if frappe.db.exists("Workflow", WORKFLOW_NAME):
			frappe.delete_doc("Workflow", WORKFLOW_NAME, force=True, ignore_permissions=True)

		self.assertFalse(ensure_reject_request_workflow(quiet=True))
		self.assertFalse(frappe.db.exists("Workflow", WORKFLOW_NAME))
		self.assertEqual(cint(frappe.db.get_value("Workflow", other, "is_active")), 1)

	# ------------------------------------------------------------------ legacy migration

	def test_migrate_legacy_states(self):
		created_docs = []

		# Seed legacy rows (Draft/0, Pending/1, Approved/1, Rejected/1, Closed/1, Cancelled/2)
		test_cases = [
			("Draft", 0, None),
			("Pending", 1, None),
			("Approved", 1, "Approved"),
			("Rejected", 1, "Rejected"),
			("Closed", 1, "Approved"),
			("Cancelled", 2, None),
		]

		for status, docstatus, decision in test_cases:
			doc = self._make_reject_doc()
			created_docs.append(doc.name)
			frappe.db.sql(
				"""UPDATE `tabPOS Reject Request`
				   SET status=%s, docstatus=%s, decision=%s, workflow_state=NULL,
				       borne_by=NULL, approval_code=NULL
				   WHERE name=%s""",
				(status, docstatus, decision, doc.name),
			)

		def _cleanup():
			for name in created_docs:
				if frappe.db.exists(DOCUMENT_TYPE, name):
					frappe.db.set_value(DOCUMENT_TYPE, name, "docstatus", 0)
					frappe.delete_doc(DOCUMENT_TYPE, name, force=True, ignore_permissions=True)

		self.addCleanup(_cleanup)

		migrate_legacy_states()

		# Verify mapped states
		d_draft = frappe.get_doc(DOCUMENT_TYPE, created_docs[0])
		self.assertEqual(d_draft.workflow_state, STATE_DRAFT)
		self.assertFalse(d_draft.approval_code)
		self.assertFalse(d_draft.borne_by)

		d_pending = frappe.get_doc(DOCUMENT_TYPE, created_docs[1])
		self.assertEqual(d_pending.workflow_state, STATE_PENDING)
		self.assertFalse(d_pending.approval_code)
		self.assertFalse(d_pending.borne_by)

		d_approved = frappe.get_doc(DOCUMENT_TYPE, created_docs[2])
		self.assertEqual(d_approved.workflow_state, STATE_AWAITING)
		self.assertEqual(d_approved.borne_by, "Company")
		self.assertTrue(d_approved.approval_code)
		self.assertEqual(len(d_approved.approval_code), 8)

		d_rejected = frappe.get_doc(DOCUMENT_TYPE, created_docs[3])
		self.assertEqual(d_rejected.workflow_state, STATE_AWAITING)
		self.assertEqual(d_rejected.borne_by, "Crew")
		self.assertTrue(d_rejected.approval_code)
		self.assertEqual(len(d_rejected.approval_code), 8)

		d_closed = frappe.get_doc(DOCUMENT_TYPE, created_docs[4])
		self.assertEqual(d_closed.workflow_state, STATE_COMPLETED)
		self.assertTrue(d_closed.approval_code)
		self.assertEqual(len(d_closed.approval_code), 8)

		d_cancelled = frappe.get_doc(DOCUMENT_TYPE, created_docs[5])
		self.assertEqual(d_cancelled.workflow_state, STATE_CANCELLED)

		# Rerun changes nothing
		app_code = d_approved.approval_code
		rej_code = d_rejected.approval_code
		closed_code = d_closed.approval_code

		migrate_legacy_states()

		d_approved.reload()
		d_rejected.reload()
		d_closed.reload()

		self.assertEqual(d_approved.approval_code, app_code)
		self.assertEqual(d_approved.workflow_state, STATE_AWAITING)
		self.assertEqual(d_approved.borne_by, "Company")

		self.assertEqual(d_rejected.approval_code, rej_code)
		self.assertEqual(d_rejected.workflow_state, STATE_AWAITING)
		self.assertEqual(d_rejected.borne_by, "Crew")

		self.assertEqual(d_closed.approval_code, closed_code)
		self.assertEqual(d_closed.workflow_state, STATE_COMPLETED)

	def test_workflow_allow_edit_per_state(self):
		ensure_reject_request_workflow(quiet=True)
		wf = frappe.get_doc("Workflow", WORKFLOW_NAME)
		expected_allow_edit = {s[0]: s[2][0] for s in STATES}
		actual_allow_edit = {s.state: s.allow_edit for s in wf.states}
		self.assertEqual(actual_allow_edit, expected_allow_edit)

	def test_migrate_legacy_states_logs_error_on_empty_and_unknown_status(self):
		initial_errors = frappe.db.count("Error Log")
		doc_empty = self._make_reject_doc()
		doc_unknown = self._make_reject_doc()

		frappe.db.sql(
			"UPDATE `tabPOS Reject Request` SET status='', workflow_state=NULL WHERE name=%s",
			(doc_empty.name,),
		)
		frappe.db.sql(
			"UPDATE `tabPOS Reject Request` SET status='mystery_status', workflow_state=NULL WHERE name=%s",
			(doc_unknown.name,),
		)

		self.addCleanup(lambda: frappe.delete_doc(DOCUMENT_TYPE, doc_empty.name, force=True, ignore_permissions=True))
		self.addCleanup(lambda: frappe.delete_doc(DOCUMENT_TYPE, doc_unknown.name, force=True, ignore_permissions=True))

		migrate_legacy_states()

		doc_empty.reload()
		doc_unknown.reload()
		self.assertIsNone(doc_empty.workflow_state)
		self.assertIsNone(doc_unknown.workflow_state)

		new_errors = frappe.get_all(
			"Error Log",
			filters={"method": ["like", "POS Reject Request migration:%"]},
			fields=["method", "error"],
			order_by="creation desc",
			limit=2,
		)
		titles = {e.method for e in new_errors}
		self.assertIn("POS Reject Request migration: empty status", titles)
		self.assertIn("POS Reject Request migration: unknown status", titles)

	def test_legacy_pending_docstatus_1_migrates_and_approves(self):
		from frappe.model.workflow import apply_workflow
		from pos_next.services import reject_request

		doc = self._make_reject_doc()
		frappe.db.sql(
			"UPDATE `tabPOS Reject Request` SET status='pending', docstatus=1, workflow_state=NULL WHERE name=%s",
			(doc.name,),
		)
		def _cleanup():
			if frappe.db.exists(DOCUMENT_TYPE, doc.name):
				frappe.db.set_value(DOCUMENT_TYPE, doc.name, "docstatus", 0)
				frappe.delete_doc(DOCUMENT_TYPE, doc.name, force=True, ignore_permissions=True)
		self.addCleanup(_cleanup)

		migrate_legacy_states()
		doc.reload()
		self.assertEqual(doc.workflow_state, STATE_PENDING)
		self.assertEqual(doc.docstatus, 1)

		# Approver approves it
		approver = "approver-setup@example.com"
		if not frappe.db.exists("User", approver):
			u = frappe.new_doc("User")
			u.email = approver
			u.first_name = "Approver"
			u.append("roles", {"role": APPROVER_ROLE})
			u.insert(ignore_permissions=True)

		from pos_next.api.test_reject_request import _as_user
		with _as_user(approver):
			reject_request.set_liability(doc, "Company", None)
			doc = apply_workflow(doc, "Approve")

		self.assertEqual(doc.workflow_state, STATE_AWAITING)
		self.assertEqual(doc.docstatus, 1)
		self.assertTrue(doc.approval_code)

