# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Task 1: POS Next Stock Opname workflow + outlet permissions.

`pos_next.stock_opname_setup.ensure_stock_opname_workflow` creates the
`POS Next Stock Opname` workflow on Stock Reconciliation and the Accounts
Manager submit perm, idempotently on install/migrate. These tests drive it
against the DB and restore every workflow / is_active flag they touch
(IntegrationTestCase also rolls the class transaction back at the end).

Run via pos_next/_pn_run_tests.py pos_next.tests.test_stock_opname_setup
"""

import json
import uuid
from pathlib import Path

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint

from pos_next.install import mirror_standard_perms_for_custom_doctypes
from pos_next.stock_opname_setup import (
	HQ_ROLES,
	OUTLET_ROLES,
	STATE_APPROVED,
	STATE_CANCELLED,
	STATE_DRAFT,
	STATE_PENDING_AM,
	STATE_PENDING_SM,
	STATE_REJECTED,
	WORKFLOW_NAME,
	ensure_stock_opname_workflow,
)

ADMIN = "Administrator"
DOCUMENT_TYPE = "Stock Reconciliation"
ACCOUNTS_MANAGER = "Accounts Manager"
STOCK_MANAGER = "Stock Manager"

# Spec state table: (state, doc_status).
EXPECTED_STATES = {
	(STATE_DRAFT, 0),
	(STATE_PENDING_SM, 0),
	(STATE_PENDING_AM, 0),
	(STATE_REJECTED, 0),
	(STATE_APPROVED, 1),
	(STATE_CANCELLED, 2),
}

# Spec transition table expanded one row per allowed role:
# (state, action, next_state, allowed, allow_self_approval).
EXPECTED_TRANSITIONS = {
	(STATE_DRAFT, "Submit", STATE_PENDING_SM, "POSNext Cashier", 1),
	(STATE_DRAFT, "Submit", STATE_PENDING_SM, "POSNext Manager", 1),
	(STATE_DRAFT, "Submit", STATE_PENDING_SM, STOCK_MANAGER, 1),
	(STATE_PENDING_SM, "Approve", STATE_PENDING_AM, STOCK_MANAGER, 0),
	(STATE_PENDING_SM, "Reject", STATE_REJECTED, STOCK_MANAGER, 0),
	(STATE_PENDING_AM, "Approve", STATE_APPROVED, ACCOUNTS_MANAGER, 0),
	(STATE_PENDING_AM, "Reject", STATE_REJECTED, ACCOUNTS_MANAGER, 0),
	(STATE_REJECTED, "Revise", STATE_DRAFT, "POSNext Cashier", 1),
	(STATE_REJECTED, "Revise", STATE_DRAFT, "POSNext Manager", 1),
	(STATE_REJECTED, "Revise", STATE_DRAFT, STOCK_MANAGER, 1),
	(STATE_APPROVED, "Cancel", STATE_CANCELLED, STOCK_MANAGER, 1),
}


def _make_user(role):
	"""A user holding exactly `role` (fresh users have no roles)."""
	if not frappe.db.exists("Role", role):
		raise AssertionError(f"{role} role does not exist on this site")
	email = f"stock-opname-{uuid.uuid4().hex[:10]}@example.com"
	was_in_import = frappe.flags.in_import
	frappe.flags.in_import = True
	try:
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "SO Test", "enabled": 1, "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	finally:
		frappe.flags.in_import = was_in_import
	frappe.get_doc(
		{
			"doctype": "Has Role",
			"parent": email,
			"parenttype": "User",
			"parentfield": "roles",
			"role": role,
		}
	).insert(ignore_permissions=True)
	frappe.clear_cache(user=email)
	return email


class TestStockOpnameSetup(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self._wf_baseline = {
			w.name: cint(w.is_active)
			for w in frappe.get_all(
				"Workflow", filters={"document_type": DOCUMENT_TYPE}, fields=["name", "is_active"]
			)
		}
		self.addCleanup(self._restore_workflows)

	def tearDown(self):
		frappe.set_user(ADMIN)
		super().tearDown()

	def _restore_workflows(self):
		frappe.set_user(ADMIN)
		# drop any workflow this test created (e.g. the _T probe)
		for w in frappe.get_all("Workflow", filters={"document_type": DOCUMENT_TYPE}, fields=["name"]):
			if w.name not in self._wf_baseline:
				frappe.delete_doc("Workflow", w.name, force=True, ignore_permissions=True)
		# restore is_active flags the test may have flipped
		for name, active in self._wf_baseline.items():
			if frappe.db.exists("Workflow", name):
				frappe.db.set_value("Workflow", name, "is_active", active)
		# recreate our workflow if it existed before and the test deleted it
		if WORKFLOW_NAME in self._wf_baseline and not frappe.db.exists("Workflow", WORKFLOW_NAME):
			ensure_stock_opname_workflow(quiet=True)

	# ------------------------------------------------------------------ constants

	def test_role_tuples_match_spec(self):
		self.assertEqual(OUTLET_ROLES, ("POSNext Cashier", "POSNext Manager"))
		self.assertEqual(
			HQ_ROLES,
			("Stock Manager", "Accounts Manager", "System Manager", "Administrator"),
		)

	# ------------------------------------------------------------------ workflow

	def test_creates_workflow_matching_spec(self):
		if frappe.db.exists("Workflow", WORKFLOW_NAME):
			frappe.delete_doc("Workflow", WORKFLOW_NAME, force=True, ignore_permissions=True)

		self.assertTrue(ensure_stock_opname_workflow(quiet=True))

		wf = frappe.get_doc("Workflow", WORKFLOW_NAME)
		self.assertEqual(wf.document_type, DOCUMENT_TYPE)
		self.assertEqual(cint(wf.is_active), 1)
		self.assertEqual(wf.workflow_state_field, "workflow_state")
		self.assertEqual(cint(wf.send_email_alert), 0)
		self.assertEqual(
			{(s.state, cint(s.doc_status)) for s in wf.states},
			EXPECTED_STATES,
		)
		self.assertEqual(
			{(t.state, t.action, t.next_state, t.allowed, cint(t.allow_self_approval)) for t in wf.transitions},
			EXPECTED_TRANSITIONS,
		)

	def test_idempotent_and_never_overwrites(self):
		ensure_stock_opname_workflow(quiet=True)
		wf = frappe.get_doc("Workflow", WORKFLOW_NAME)
		row = next(t for t in wf.transitions if t.state == STATE_PENDING_SM and t.action == "Approve")
		row.allowed = "System Manager"
		wf.save(ignore_permissions=True)

		self.assertFalse(ensure_stock_opname_workflow(quiet=True))

		wf.reload()
		row = next(t for t in wf.transitions if t.state == STATE_PENDING_SM and t.action == "Approve")
		self.assertEqual(row.allowed, "System Manager")

	def test_existing_other_workflow_not_displaced(self):
		"""An admin's own Stock Reconciliation workflow is never touched."""
		other = "_T SR WF"
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

		self.assertFalse(ensure_stock_opname_workflow(quiet=True))
		self.assertFalse(frappe.db.exists("Workflow", WORKFLOW_NAME))
		self.assertEqual(cint(frappe.db.get_value("Workflow", other, "is_active")), 1)

	def test_inactive_other_workflow_does_not_block(self):
		"""A dormant Stock Reconciliation workflow is left alone, not a blocker."""
		other = "_T SR WF Off"
		if frappe.db.exists("Workflow", other):
			frappe.delete_doc("Workflow", other, force=True, ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": other,
				"document_type": DOCUMENT_TYPE,
				"workflow_state_field": "workflow_state",
				"is_active": 0,
				"states": [{"state": STATE_DRAFT, "doc_status": 0, "allow_edit": "System Manager"}],
			}
		).insert(ignore_permissions=True)
		if frappe.db.exists("Workflow", WORKFLOW_NAME):
			frappe.delete_doc("Workflow", WORKFLOW_NAME, force=True, ignore_permissions=True)

		self.assertTrue(ensure_stock_opname_workflow(quiet=True))
		self.assertTrue(frappe.db.exists("Workflow", WORKFLOW_NAME))
		self.assertEqual(cint(frappe.db.get_value("Workflow", other, "is_active")), 0)

	# --------------------------------------------------------------- permissions

	def test_accounts_manager_can_submit(self):
		ensure_stock_opname_workflow(quiet=True)
		user = _make_user(ACCOUNTS_MANAGER)
		self.addCleanup(frappe.delete_doc, "User", user, force=1, ignore_permissions=True)

		perms = frappe.permissions.get_doc_permissions(frappe.new_doc(DOCUMENT_TYPE), user=user)
		self.assertTrue(perms.get("submit"), "Accounts Manager must be able to submit (approve)")

	def test_cashier_perms_have_no_submit(self):
		# The shipped fixture is the source of truth (applied by migrate); assert
		# its rows rather than the DB so the check is deterministic pre-migrate.
		fixture = Path(frappe.get_app_path("pos_next")) / "fixtures" / "custom_docperm.json"
		rows = json.loads(fixture.read_text(encoding="utf-8"))
		by_role = {r["role"]: r for r in rows if r.get("parent") == DOCUMENT_TYPE}

		self.assertEqual(set(by_role), set(OUTLET_ROLES), "one Stock Reconciliation row per outlet role")
		for role, row in by_role.items():
			self.assertEqual(cint(row["submit"]), 0, f"{role} must not submit")
			self.assertEqual(cint(row["cancel"]), 0, f"{role} must not cancel")
			self.assertEqual(cint(row["amend"]), 0, f"{role} must not amend")
			self.assertEqual(cint(row["create"]), 1, f"{role} must create")
			self.assertEqual(cint(row["write"]), 1, f"{role} must write")
			self.assertEqual(cint(row["read"]), 1, f"{role} must read")
			self.assertEqual(cint(row["select"]), 1, f"{role} Stock Reconciliation must use select=1")

		# Serial and Batch Bundle rows (serial-empty opname saves a bundle) use
		# the same select=1 as every sibling outlet row.
		by_role_sabb = {r["role"]: r for r in rows if r.get("parent") == "Serial and Batch Bundle"}
		self.assertEqual(set(by_role_sabb), set(OUTLET_ROLES), "one Serial and Batch Bundle row per outlet role")
		for role, row in by_role_sabb.items():
			self.assertEqual(cint(row["select"]), 1, f"{role} Serial and Batch Bundle must use select=1")

	def test_stock_manager_keeps_standard_perms(self):
		mirror_standard_perms_for_custom_doctypes(quiet=True)
		user = _make_user(STOCK_MANAGER)
		self.addCleanup(frappe.delete_doc, "User", user, force=1, ignore_permissions=True)

		perms = frappe.permissions.get_doc_permissions(frappe.new_doc(DOCUMENT_TYPE), user=user)
		self.assertTrue(perms.get("submit"), "Stock Manager must keep submit")
		self.assertTrue(perms.get("cancel"), "Stock Manager must keep cancel")

	# ---------------------------------------------------- Desk (Task 5)

	def _workspace(self):
		path = (
			Path(frappe.get_app_path("pos_next"))
			/ "pos_next"
			/ "workspace"
			/ "posnext"
			/ "posnext.json"
		)
		return json.loads(path.read_text(encoding="utf-8"))

	def test_workspace_has_pending_shortcuts(self):
		"""H2: one shortcut per approval stage, counting that stage's pending docs."""
		ws = self._workspace()
		shortcuts = [
			s
			for s in ws["shortcuts"]
			if s.get("type") == "DocType" and s.get("link_to") == DOCUMENT_TYPE
		]
		by_label = {s["label"]: s for s in shortcuts}
		self.assertEqual(
			set(by_label), {"Opname: Stock Manager", "Opname: Accounts Manager"}
		)
		self.assertEqual(
			json.loads(by_label["Opname: Stock Manager"]["stats_filter"]),
			{"workflow_state": STATE_PENDING_SM},
		)
		self.assertEqual(
			json.loads(by_label["Opname: Accounts Manager"]["stats_filter"]),
			{"workflow_state": STATE_PENDING_AM},
		)
		for label, shortcut in by_label.items():
			self.assertEqual(shortcut["format"], "{} Pending", f"{label} format")

		# every shortcut needs a matching content block to render in the grid
		blocks = json.loads(ws["content"])
		shortcut_names = {
			b["data"]["shortcut_name"] for b in blocks if b.get("type") == "shortcut"
		}
		for label in by_label:
			self.assertIn(label, shortcut_names, f"{label} content block")

	def test_reject_prompt_script_wired(self):
		"""W4: Stock Reconciliation form script prompts for a reject reason."""
		from pos_next import hooks

		rel = hooks.doctype_js["Stock Reconciliation"]
		script = (Path(frappe.get_app_path("pos_next")) / rel).read_text(encoding="utf-8")
		self.assertIn("before_workflow_action", script)
		self.assertIn('"Rejected: "', script)
		# the xcall that stores the comment must release the Desk freeze on failure
		self.assertIn(".catch", script)
		self.assertIn("unfreeze", script)

	def test_system_qty_button_script_wired(self):
		"""HQ Show System Qty button: endpoint + role gate in the form script."""
		from pos_next import hooks

		rel = hooks.doctype_js["Stock Reconciliation"]
		script = (Path(frappe.get_app_path("pos_next")) / rel).read_text(encoding="utf-8")
		self.assertIn("get_system_qty", script)
		self.assertIn("has_role", script)
