# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Install wiring for the POS Next Stock Opname workflow.

Outlets submit draft Stock Reconciliations; HQ approves them through a Frappe
Workflow (`POS Next Stock Opname`). `ensure_stock_opname_workflow` is
idempotent: it creates the workflow (and its Workflow States / Action Masters)
once and never displaces an admin's own active Stock Reconciliation workflow.
Called from install.after_install / after_migrate and the v2_23_0 patch.

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
HQ_ROLES = ("Stock Manager", "Accounts Manager", "System Manager", "Administrator")
# Field permissions are per role, so Desk Stock Users get system qty/valuation
# through this managed role, held only while the user has no outlet role.
SYSTEM_STOCK_READER = "POSNext System Stock Reader"


def _wants_system_stock_reader(roles):
	return "Stock User" in roles and not set(roles) & set(OUTLET_ROLES)


def sync_user_system_stock_reader(doc, method=None):
	"""User validate hook: add/remove the reader role in the roles being saved."""
	if doc.name in ("Administrator", "Guest") or not frappe.db.exists("Role", SYSTEM_STOCK_READER):
		return
	roles = [row.role for row in doc.get("roles") or []]
	if _wants_system_stock_reader(roles):
		if SYSTEM_STOCK_READER not in roles:
			doc.append("roles", {"role": SYSTEM_STOCK_READER})
	elif SYSTEM_STOCK_READER in roles:
		doc.set("roles", [row for row in doc.roles if row.role != SYSTEM_STOCK_READER])


def _sync_system_stock_reader(user):
	if user in ("Administrator", "Guest") or not frappe.db.exists("Role", SYSTEM_STOCK_READER):
		return
	roles = frappe.get_all("Has Role", filters={"parenttype": "User", "parent": user}, pluck="role")
	wanted, held = _wants_system_stock_reader(roles), SYSTEM_STOCK_READER in roles
	if wanted and not held:
		frappe.get_doc({
			"doctype": "Has Role", "parenttype": "User", "parentfield": "roles",
			"parent": user, "role": SYSTEM_STOCK_READER,
		}).insert(ignore_permissions=True)
	elif held and not wanted:
		frappe.db.delete("Has Role", {"parenttype": "User", "parent": user, "role": SYSTEM_STOCK_READER})
	else:
		return
	frappe.clear_cache(user=user)


def sync_has_role_system_stock_reader(doc, method=None):
	"""Has Role insert/trash hook: direct role edits bypass the User hook."""
	if doc.parenttype == "User" and doc.parent:
		_sync_system_stock_reader(doc.parent)


def _sync_all_system_stock_readers():
	"""Heal role assignments made without any hook (imports, raw SQL)."""
	for user in set(frappe.get_all(
		"Has Role", filters={"parenttype": "User", "role": ["in", ("Stock User", SYSTEM_STOCK_READER)]},
		pluck="parent",
	)):
		_sync_system_stock_reader(user)

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


def ensure_stock_opname_data_permissions(quiet=False):
	"""Hide SR system fields and stock valuation through native field permissions."""
	from frappe.custom.doctype.property_setter.property_setter import make_property_setter

	fields = {
		"Bin": ("valuation_rate", "stock_value"),
		"Serial and Batch Bundle": ("avg_rate", "total_amount"),
		"Serial and Batch Entry": ("incoming_rate", "outgoing_rate", "stock_value_difference", "stock_queue"),
		DOCUMENT_TYPE: ("difference_amount",),
		"Stock Reconciliation Item": (
			"valuation_rate", "amount", "current_qty", "current_valuation_rate",
			"current_amount", "quantity_difference", "amount_difference",
			"custom_qty_before", "custom_qty_difference_per_uom", "custom_valuation_rate_per_uom",
			"current_serial_and_batch_bundle", "current_serial_no",
		),
	}
	for doctype, names in fields.items():
		meta = frappe.get_meta(doctype)
		for name in names:
			if meta.has_field(name):
				make_property_setter(doctype, name, "permlevel", 1, "Int")
		frappe.clear_cache(doctype=doctype)
	for doctype in ("Bin", "Serial and Batch Bundle"):
		for row in frappe.get_meta(doctype).permissions:
			if row.permlevel or not row.read or row.role in OUTLET_ROLES:
				continue
			filters = {"parent": doctype, "role": row.role, "permlevel": 1, "if_owner": row.if_owner}
			if frappe.db.exists("Custom DocPerm", filters):
				frappe.db.set_value("Custom DocPerm", filters, "read", 1)
			else:
				frappe.get_doc({
					"doctype": "Custom DocPerm", "parenttype": "DocType", "parentfield": "permissions",
					**filters, "read": 1, "write": row.write,
				}).insert(ignore_permissions=True)
		frappe.clear_cache(doctype=doctype)
	for role in HQ_ROLES:
		if role == "Administrator":
			continue
		frappe.permissions.add_permission(DOCUMENT_TYPE, role, 1)
		for action in ("read", "write"):
			frappe.permissions.update_permission_property(DOCUMENT_TYPE, role, 1, action, 1)
	for role in OUTLET_ROLES:
		for action in ("print", "export", "report"):
			frappe.permissions.update_permission_property(DOCUMENT_TYPE, role, 0, action, 0)
	if not frappe.db.exists("Role", SYSTEM_STOCK_READER):
		frappe.get_doc({"doctype": "Role", "role_name": SYSTEM_STOCK_READER, "desk_access": 1}).insert(
			ignore_permissions=True
		)
	for permlevel, actions in ((0, ("read", "write", "create")), (1, ("read",))):
		if not frappe.db.exists("Custom DocPerm", {
			"parent": DOCUMENT_TYPE, "role": SYSTEM_STOCK_READER, "permlevel": permlevel,
		}):
			frappe.permissions.add_permission(DOCUMENT_TYPE, SYSTEM_STOCK_READER, permlevel)
		for action in actions:
			frappe.permissions.update_permission_property(DOCUMENT_TYPE, SYSTEM_STOCK_READER, permlevel, action, 1)
	frappe.clear_cache(doctype=DOCUMENT_TYPE)
	_sync_all_system_stock_readers()


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

	Idempotent, and never displaces a workflow the admin is using: an existing
	workflow of that name (never recreate a duplicate) or any active workflow on
	Stock Reconciliation (the admin's own) makes this a no-op that returns False.
	An unrelated inactive workflow is left alone and does not block creation. The
	Accounts Manager perm is (re)ensured on every call, guarded by existence, so a
	manually-created workflow still gets the approval perm.
	"""
	_ensure_accounts_manager_perm()

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

	# Workflow.on_update already backfills by doc_status; repeat explicitly so
	# the mapping stays pinned to the spec state names.
	_backfill_workflow_state()

	if not quiet:
		from pos_next.install import log_message

		log_message(f"Created Workflow: {WORKFLOW_NAME}", level="info")
	return True
