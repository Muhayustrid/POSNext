# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Install wiring for the POS Next Stock Opname workflow.

Outlets submit draft Stock Reconciliations; HQ approves them through a Frappe
Workflow (`POS Next Stock Opname`). `ensure_stock_opname_workflow` is
idempotent: it creates the workflow (and its Workflow States / Action Masters)
once and never overwrites an existing workflow of that name. Called from
install.after_install / after_migrate and the v2_23_0 patch.

Constants here (state names, roles, the function) are consumed by later
Stock Opname tasks — keep them in sync with .scratch/stock-opname/spec.md.
"""

import frappe

WORKFLOW_NAME = "POS Next Stock Opname"
DOCUMENT_TYPE = "Stock Reconciliation"
WORKFLOW_STATE_FIELD = "workflow_state"

# Exact spec state strings (never translated).
STATE_DRAFT = "Draft"
STATE_PENDING_SM = "Approval Pending by Stock Manager"
STATE_PENDING_AM = "Approval Pending by Accounts Manager"
STATE_REJECTED = "Rejected"
STATE_APPROVED = "Approved"
STATE_CANCELLED = "Cancelled"

OUTLET_ROLES = ("POSNext Cashier", "POSNext Manager")
HQ_ROLES = ("Stock Manager", "Accounts Manager")

# (state, doc_status, edit roles). allow_edit is the first role of the row —
# the Desk client only reads it for button display; the real scoping is the
# Custom DocPerm + API guards (spec P1-P3).
STATES = (
	(STATE_DRAFT, 0, ("POSNext Cashier", "POSNext Manager", "Stock Manager")),
	(STATE_PENDING_SM, 0, ("Stock Manager",)),
	(STATE_PENDING_AM, 0, ("Accounts Manager",)),
	(STATE_REJECTED, 0, ("POSNext Cashier", "POSNext Manager", "Stock Manager")),
	(STATE_APPROVED, 1, ("Accounts Manager",)),
	(STATE_CANCELLED, 2, ("Stock Manager",)),
)

# (from, action, to, allowed roles, allow_self_approval)
TRANSITIONS = (
	(STATE_DRAFT, "Submit", STATE_PENDING_SM, ("POSNext Cashier", "POSNext Manager", "Stock Manager"), 1),
	(STATE_PENDING_SM, "Approve", STATE_PENDING_AM, ("Stock Manager",), 0),
	(STATE_PENDING_SM, "Reject", STATE_REJECTED, ("Stock Manager",), 0),
	(STATE_PENDING_AM, "Approve", STATE_APPROVED, ("Accounts Manager",), 0),
	(STATE_PENDING_AM, "Reject", STATE_REJECTED, ("Accounts Manager",), 0),
	(STATE_REJECTED, "Revise", STATE_DRAFT, ("POSNext Cashier", "POSNext Manager", "Stock Manager"), 1),
	(STATE_APPROVED, "Cancel", STATE_CANCELLED, ("Stock Manager",), 1),
)

ACCOUNTS_MANAGER = "Accounts Manager"


def _ensure_masters():
	"""Create the Workflow States and Action Masters the workflow links to."""
	for state, _doc_status, _roles in STATES:
		if not frappe.db.exists("Workflow State", state):
			frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state}).insert(
				ignore_permissions=True
			)
	for _from, action, _to, _roles, _self in TRANSITIONS:
		if not frappe.db.exists("Workflow Action Master", action):
			frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
				ignore_permissions=True
			)


def _ensure_accounts_manager_perm():
	"""Give Accounts Manager read/write/submit on Stock Reconciliation (spec P2).

	ERPNext grants it nothing by default. Only add a Custom DocPerm row when
	none exists — Frappe ignores every standard DocPerm of a doctype once one
	custom row is present, and install.mirror_standard_perms_for_custom_doctypes
	re-mirrors the standard Stock Manager row on every migrate.
	"""
	if frappe.db.exists("Custom DocPerm", {"parent": DOCUMENT_TYPE, "role": ACCOUNTS_MANAGER}):
		return
	frappe.permissions.add_permission(DOCUMENT_TYPE, ACCOUNTS_MANAGER, 0)
	frappe.permissions.update_permission_property(DOCUMENT_TYPE, ACCOUNTS_MANAGER, 0, "write", 1)
	frappe.permissions.update_permission_property(DOCUMENT_TYPE, ACCOUNTS_MANAGER, 0, "submit", 1)


def _backfill_workflow_state():
	"""Stamp existing Stock Reconciliations so the workflow governs them too (W1)."""
	for docstatus, state in ((0, STATE_DRAFT), (1, STATE_APPROVED), (2, STATE_CANCELLED)):
		frappe.db.set_value(
			DOCUMENT_TYPE,
			{"docstatus": docstatus, WORKFLOW_STATE_FIELD: ("is", "not set")},
			WORKFLOW_STATE_FIELD,
			state,
		)


def ensure_stock_opname_workflow(quiet=False):
	"""Create the `POS Next Stock Opname` workflow once; return True if created.

	Idempotent: an existing workflow of that name is never modified (returns
	False). The Accounts Manager perm is (re)ensured on every call, guarded by
	existence, so a manually-created workflow still gets the approval perm.
	"""
	_ensure_accounts_manager_perm()

	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		return False

	_ensure_masters()

	workflow = frappe.get_doc(
		{
			"doctype": "Workflow",
			"workflow_name": WORKFLOW_NAME,
			"document_type": DOCUMENT_TYPE,
			"workflow_state_field": WORKFLOW_STATE_FIELD,
			"is_active": 1,
			"send_email_alert": 0,
			"states": [
				{"state": state, "doc_status": doc_status, "allow_edit": roles[0]}
				for state, doc_status, roles in STATES
			],
			"transitions": [
				{
					"state": from_state,
					"action": action,
					"next_state": to_state,
					"allowed": role,
					"allow_self_approval": allow_self,
				}
				for from_state, action, to_state, roles, allow_self in TRANSITIONS
				for role in roles
			],
		}
	)
	workflow.insert(ignore_permissions=True)

	# Workflow.on_update already backfills by doc_status; repeat explicitly so
	# the mapping stays pinned to the spec state names.
	_backfill_workflow_state()

	if not quiet:
		from pos_next.install import log_message

		log_message(f"Created Workflow: {WORKFLOW_NAME}", level="info")
	return True
