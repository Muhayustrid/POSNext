# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Install wiring for the POS Next Reject Request workflow.

POS Reject Requests run on a native Frappe Workflow (`POS Next Reject Request`).
`ensure_reject_request_workflow` is idempotent: it creates the workflow (and its
Workflow States / Action Masters) once and never displaces an admin's own active
workflow on the doctype.
Called from install.after_install / after_migrate and the v2_25_0 patch.
"""

import frappe

from pos_next.services.confirmation_code import random_code

WORKFLOW_NAME = "POS Next Reject Request"
DOCUMENT_TYPE = "POS Reject Request"
WORKFLOW_STATE_FIELD = "workflow_state"

STATE_DRAFT = "Draft"
STATE_PENDING = "Pending Approval"
STATE_AWAITING = "Awaiting Destruction"
STATE_PROOF = "Destruction Submitted"
STATE_COMPLETED = "Completed"
STATE_CANCELLED = "Cancelled"

CREW_ROLES = ("POSNext Cashier", "POSNext Manager")
APPROVER_ROLE = "POSNext Reject Approver"

# (state, doc_status, edit roles). allow_edit is the first role in Frappe's child table.
STATES = (
	(STATE_DRAFT, 0, ("POSNext Cashier", "POSNext Manager")),
	(STATE_PENDING, 0, ("POSNext Reject Approver",)),
	(STATE_AWAITING, 1, ("POSNext Cashier", "POSNext Manager")),
	(STATE_PROOF, 1, ("POSNext Reject Approver",)),
	(STATE_COMPLETED, 1, ("POSNext Reject Approver",)),
	(STATE_CANCELLED, 2, ("POSNext Reject Approver",)),
)

# (from, action, to, allowed roles, allow_self_approval)
TRANSITIONS = (
	(STATE_DRAFT, "Submit", STATE_PENDING, ("POSNext Cashier", "POSNext Manager"), 1),
	(STATE_PENDING, "Withdraw", STATE_DRAFT, ("POSNext Cashier", "POSNext Manager"), 1),
	(STATE_PENDING, "Approve", STATE_AWAITING, ("POSNext Reject Approver",), 0),
	(STATE_PENDING, "Return", STATE_DRAFT, ("POSNext Reject Approver",), 0),
	(STATE_AWAITING, "Send Proof", STATE_PROOF, ("POSNext Cashier", "POSNext Manager"), 1),
	(STATE_AWAITING, "Cancel", STATE_CANCELLED, ("POSNext Reject Approver",), 0),
	(STATE_PROOF, "Complete", STATE_COMPLETED, ("POSNext Reject Approver",), 0),
	(STATE_PROOF, "Return", STATE_AWAITING, ("POSNext Reject Approver",), 0),
	(STATE_PROOF, "Cancel", STATE_CANCELLED, ("POSNext Reject Approver",), 0),
)


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


def _unique_approval_code(existing_codes: set | None = None) -> str:
	"""Generate a unique 8-character confirmation code for approval."""
	for _ in range(50):
		code = random_code()
		if existing_codes and code in existing_codes:
			continue
		if not frappe.db.exists(DOCUMENT_TYPE, {"approval_code": code}):
			return code
	frappe.throw("Could not generate a unique approval code")


def ensure_reject_request_workflow(quiet=False) -> bool:
	"""Create the `POS Next Reject Request` workflow once; return True if created."""
	if frappe.db.exists("Workflow", WORKFLOW_NAME):
		return False
	if frappe.db.exists("Workflow", {"document_type": DOCUMENT_TYPE, "is_active": 1}):
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

	if not quiet:
		from pos_next.install import log_message

		log_message(f"Created Workflow: {WORKFLOW_NAME}", level="info")
	return True


def migrate_legacy_states() -> None:
	"""Migrate pre-workflow POS Reject Request records to workflow_state."""
	rows = frappe.db.sql(
		"""SELECT name, status, decision, docstatus, workflow_state, approval_code, borne_by
		   FROM `tabPOS Reject Request`""",
		as_dict=True,
	)

	used_codes = set(
		frappe.db.sql_list(
			"SELECT approval_code FROM `tabPOS Reject Request` WHERE approval_code IS NOT NULL AND approval_code != ''"
		)
	)

	for row in rows:
		raw_status = row.get("status")
		old_status = (raw_status or "").strip().casefold()
		if not old_status:
			frappe.log_error(title="POS Reject Request migration: empty status", message=f"Row {row.get('name')} has empty status")
			continue

		updates = {}
		target_state = None
		target_borne_by = None
		needs_code = False

		if old_status == "draft":
			target_state = STATE_DRAFT
		elif old_status == "pending":
			target_state = STATE_PENDING
		elif old_status == "approved":
			target_state = STATE_AWAITING
			target_borne_by = "Company"
			needs_code = True
		elif old_status == "rejected":
			target_state = STATE_AWAITING
			target_borne_by = "Crew"
			needs_code = True
		elif old_status == "closed":
			target_state = STATE_COMPLETED
			decision = (row.get("decision") or "").strip().casefold()
			if decision == "rejected":
				target_borne_by = "Crew"
			elif decision == "approved":
				target_borne_by = "Company"
			needs_code = True
		elif old_status == "cancelled":
			target_state = STATE_CANCELLED
		else:
			frappe.log_error(title="POS Reject Request migration: unknown status", message=f"Row {row.get('name')} has unknown status: {raw_status}")
			continue

		if target_state and row.get("workflow_state") != target_state:
			updates["workflow_state"] = target_state

		if target_borne_by and row.get("borne_by") != target_borne_by:
			updates["borne_by"] = target_borne_by

		if needs_code and not row.get("approval_code"):
			code = _unique_approval_code(used_codes)
			used_codes.add(code)
			updates["approval_code"] = code

		if updates:
			frappe.db.set_value(DOCUMENT_TYPE, row["name"], updates, update_modified=False)
