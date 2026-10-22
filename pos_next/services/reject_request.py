# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""POS Reject Request lifecycle: native workflow (submit, HO approval, destruction
proof, HO completion with Material Issue write-off) and notifications.

Design: docs/superpowers/specs/2026-10-22-reject-request-workflow-design.md
"""

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import make_notification_logs
from frappe.utils import add_days, cint, flt, get_first_day, get_last_day, get_url_to_form, getdate, now, today

from pos_next.reject_request_setup import (
	APPROVER_ROLE,
	CREW_ROLES,
	STATE_AWAITING,
	STATE_CANCELLED,
	STATE_COMPLETED,
	STATE_DRAFT,
	STATE_PENDING,
	STATE_PROOF,
	_unique_approval_code,
)
from pos_next.services.production import _pick_batch, _resolve_profile, _stock_qty

ISSUE_REMARK_PREFIX = "POS Reject write-off: "
MAX_EVIDENCE = 5
AUTO_REASON_PREFIX = "Auto: production loss on "

ALL_STATES = {
	STATE_DRAFT,
	STATE_PENDING,
	STATE_AWAITING,
	STATE_PROOF,
	STATE_COMPLETED,
	STATE_CANCELLED,
}

ALLOWED_TRANSITIONS = {
	(STATE_DRAFT, STATE_DRAFT),
	(STATE_DRAFT, STATE_PENDING),
	(STATE_PENDING, STATE_PENDING),
	(STATE_PENDING, STATE_DRAFT),
	(STATE_PENDING, STATE_AWAITING),
	(STATE_AWAITING, STATE_AWAITING),
	(STATE_AWAITING, STATE_PROOF),
	(STATE_AWAITING, STATE_CANCELLED),
	(STATE_PROOF, STATE_PROOF),
	(STATE_PROOF, STATE_COMPLETED),
	(STATE_PROOF, STATE_AWAITING),
	(STATE_PROOF, STATE_CANCELLED),
	(STATE_COMPLETED, STATE_COMPLETED),
	(STATE_CANCELLED, STATE_CANCELLED),
}

APPROVER_TRANSITIONS = {
	(STATE_PENDING, STATE_AWAITING),
	(STATE_AWAITING, STATE_CANCELLED),
	(STATE_PROOF, STATE_COMPLETED),
	(STATE_PROOF, STATE_AWAITING),
	(STATE_PROOF, STATE_CANCELLED),
}

CREW_TRANSITIONS = {
	(STATE_DRAFT, STATE_PENDING),
	(STATE_AWAITING, STATE_PROOF),
}


def is_system_purpose(purpose):
	"""True when the purpose is a system purpose (currently Production Loss).
	Non-existent purposes are never system."""
	return bool(frappe.db.get_value("POS Reject Purpose", purpose, "is_system"))


def validate_request(doc):
	from pos_next.api.packages import _assert_profile_access

	if frappe.session.user != "Administrator" and not doc.flags.ignore_permissions:
		if APPROVER_ROLE not in frappe.get_roles(frappe.session.user):
			_assert_profile_access(doc.pos_profile)

	before = doc.get_doc_before_save()
	if before:
		_validate_state_transition(doc, before)
		_guard_fields(doc, before)

	doc.company, warehouse = _resolve_profile(doc.pos_profile)
	if doc.docstatus == 0:
		_price_items(doc, warehouse)

	if not doc.letter_head and doc.company:
		doc.letter_head = frappe.db.get_value("Company", doc.company, "default_letter_head")

	incident = getdate(doc.incident_date)
	if incident > getdate(today()):
		frappe.throw(_("Incident date cannot be in the future"))

	settings = frappe.get_doc("POS Reject Settings")
	days = settings.deadline_days
	if is_system_purpose(doc.purpose) and (settings.production_loss_deadline_days or 0) > 0:
		days = settings.production_loss_deadline_days
	doc.deadline_date = add_days(incident, days or 0)

	if is_system_purpose(doc.purpose) and not (
		doc.is_system_generated
		or frappe.db.get_value("POS Reject Request", doc.name, "is_system_generated")
	):
		frappe.throw(_("Production Loss requests can only be created by the system from a Work Order"))

	if not doc.workflow_state:
		doc.workflow_state = STATE_DRAFT

	if doc.is_new():
		if doc.workflow_state not in ALL_STATES:
			frappe.throw(_("Invalid workflow state: {0}").format(doc.workflow_state))
		if frappe.session.user != "Administrator" and not doc.flags.ignore_permissions:
			if doc.workflow_state != STATE_DRAFT:
				frappe.throw(_("New reject request must start in Draft state"))
		if doc.video_link:
			frappe.throw(_("Video link can only be set in Awaiting Destruction"))

	if doc.video_link and not doc.video_link.lower().startswith(("http://", "https://")):
		frappe.throw(_("Video link must start with http:// or https://"))

	system = bool(doc.is_system_generated)
	if doc.is_new():
		if system or doc.work_order:
			if not doc.flags.ignore_permissions:
				frappe.throw(_("Only the system can create a system-generated Production Loss request"))
		doc.approval_code = None
		doc.decided_by = None
		doc.decided_on = None
		doc.issue_entry = None
		doc.closed_on = None
		doc.borne_by = None
		doc.set("liabilities", [])
	else:
		stored_flag = frappe.db.get_value("POS Reject Request", doc.name, "is_system_generated")
		if stored_flag:
			if not system:
				frappe.throw(_("This request is system-generated and stays that way"))
			system = True
	if system:
		doc.is_system_generated = 1
		_validate_system_generated(doc)

	for item in doc.items:
		if item.production_date and doc.incident_date:
			if getdate(item.production_date) > getdate(doc.incident_date):
				frappe.throw(
					_("Item {0}: Production date {1} cannot be after incident date {2}").format(
						item.item_code, item.production_date, doc.incident_date
					),
					frappe.ValidationError,
				)

	if before and before.workflow_state == STATE_DRAFT and doc.workflow_state == STATE_PENDING:
		if not is_system_purpose(doc.purpose):
			_check_stock(doc, warehouse)

		evidence = frappe.db.count(
			"File",
			{
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
				"attached_to_field": "evidence",
			},
		)
		if not 1 <= evidence <= MAX_EVIDENCE:
			frappe.throw(_("Attach 1 to {0} evidence photos").format(MAX_EVIDENCE))

		if doc.purpose and not is_system_purpose(doc.purpose):
			if not frappe.db.get_value("POS Reject Purpose", doc.purpose, "enabled"):
				frappe.throw(_("Purpose {0} is disabled").format(doc.purpose))

		if doc.is_system_generated and (doc.reason or "").startswith(AUTO_REASON_PREFIX):
			frappe.throw(_("Describe what happened to the lost quantity before submitting"))

		doc.is_late = 1 if getdate(today()) > getdate(doc.deadline_date) else 0
		limit = flt(settings.monthly_limit_amount)
		doc.over_limit = 1 if limit > 0 and flt(doc.total_amount) + _month_total(doc, incident) > limit else 0

		if doc.is_late and frappe.db.get_single_value("POS Reject Settings", "late_mode") == "Strict":
			doc.borne_by = "Crew"

		if doc.return_reason:
			doc.return_reason = None

	if before and before.workflow_state == STATE_PENDING and doc.workflow_state == STATE_AWAITING:
		if not doc.borne_by:
			frappe.throw(_("Borne By must be set before approving"))

	if before and before.workflow_state == STATE_AWAITING and doc.workflow_state == STATE_PROOF:
		has_video = bool(doc.video_link and doc.video_link.strip())
		destruction_count = frappe.db.count(
			"File",
			{
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
				"attached_to_field": "destruction",
			},
		)
		if not has_video and destruction_count == 0:
			frappe.throw(_("Destruction proof (video link or at least one photo) is required"))

		if doc.return_reason:
			doc.return_reason = None

	if before and before.workflow_state == STATE_PROOF and doc.workflow_state == STATE_COMPLETED:
		if not is_system_purpose(doc.purpose):
			if doc.transfer_entry:
				rows = frappe.db.get_all(
					"Stock Entry Detail",
					filters={"parent": doc.transfer_entry, "parenttype": "Stock Entry"},
					fields=["t_warehouse"],
				)
				source = next((r.t_warehouse for r in rows if r.t_warehouse), None)
			else:
				source = _resolve_profile(doc.pos_profile)[1]
			_check_stock(doc, source)


def _validate_state_transition(doc, before):
	if not before:
		return
	old_state = before.workflow_state or STATE_DRAFT
	new_state = doc.workflow_state or STATE_DRAFT

	if old_state not in ALL_STATES:
		frappe.throw(_("Invalid workflow state: {0}").format(old_state))
	if new_state not in ALL_STATES:
		frappe.throw(_("Invalid workflow state: {0}").format(new_state))
	if (old_state, new_state) not in ALLOWED_TRANSITIONS:
		frappe.throw(_("Invalid workflow transition from {0} to {1}").format(old_state, new_state))

	if old_state == new_state:
		return

	if frappe.session.user == "Administrator" or frappe.flags.in_migrate or frappe.flags.in_patch:
		return

	user_roles = set(frappe.get_roles(frappe.session.user))

	if (old_state, new_state) in APPROVER_TRANSITIONS:
		if APPROVER_ROLE not in user_roles:
			frappe.throw(_("Only {0} can perform this action").format(APPROVER_ROLE), frappe.PermissionError)
		if frappe.session.user == doc.owner:
			frappe.throw(_("You cannot approve, return or cancel your own reject request"), frappe.PermissionError)

	elif (old_state, new_state) in CREW_TRANSITIONS:
		if not (user_roles & set(CREW_ROLES)):
			frappe.throw(_("Only cashier or manager can perform this action"), frappe.PermissionError)

	elif (old_state, new_state) == (STATE_PENDING, STATE_DRAFT):
		is_approver_return = (APPROVER_ROLE in user_roles and frappe.session.user != doc.owner)
		is_crew_withdraw = bool(user_roles & set(CREW_ROLES))
		if not (is_approver_return or is_crew_withdraw):
			frappe.throw(_("Only cashier, manager or approver can return/withdraw this request"), frappe.PermissionError)


def _guard_fields(doc, before):
	if not before:
		return

	if frappe.session.user == "Administrator" or frappe.flags.in_migrate or frappe.flags.in_patch:
		return

	# server-only fields cannot be changed via save
	for field in ("approval_code", "decided_by", "issue_entry"):
		if getattr(doc, field, None) != getattr(before, field, None):
			frappe.throw(_("{0} cannot be modified directly").format(field))

	for field in ("decided_on", "closed_on"):
		if str(getattr(doc, field, None) or "") != str(getattr(before, field, None) or ""):
			frappe.throw(_("{0} cannot be modified directly").format(field))

	if getattr(doc, "work_order", None) != getattr(before, "work_order", None):
		frappe.throw(_("work_order cannot be modified directly"))

	if getattr(doc, "company", None) != getattr(before, "company", None):
		frappe.throw(_("company cannot be modified directly"))

	if getattr(doc, "letter_head", None) != getattr(before, "letter_head", None):
		frappe.throw(_("letter_head cannot be modified directly"))

	if (doc.return_reason or "") != (before.return_reason or ""):
		is_clearing = (
			(before.workflow_state == STATE_DRAFT and doc.workflow_state == STATE_PENDING and doc.return_reason is None)
			or (before.workflow_state == STATE_AWAITING and doc.workflow_state == STATE_PROOF and doc.return_reason is None)
		)
		if not is_clearing:
			frappe.throw(_("return_reason cannot be modified directly"))

	if not (before.workflow_state == STATE_DRAFT and doc.workflow_state == STATE_PENDING):
		if cint(doc.is_late) != cint(before.is_late):
			frappe.throw(_("is_late cannot be modified directly"))
		if cint(doc.over_limit) != cint(before.over_limit):
			frappe.throw(_("over_limit cannot be modified directly"))

	if flt(doc.total_amount) != flt(before.total_amount):
		doc_items = [(r.item_code, flt(r.qty), getattr(r, "uom", None)) for r in (doc.items or [])]
		bef_items = [(r.item_code, flt(r.qty), getattr(r, "uom", None)) for r in (before.items or [])]
		if doc_items == bef_items:
			frappe.throw(_("total_amount cannot be modified directly"))

	if getdate(doc.deadline_date) != getdate(before.deadline_date):
		if getdate(doc.incident_date) == getdate(before.incident_date):
			frappe.throw(_("deadline_date cannot be modified directly"))

	if getattr(doc, "borne_by", None) != getattr(before, "borne_by", None):
		# Allowed only when moving Draft -> Pending in Strict late mode
		if not (
			before.workflow_state == STATE_DRAFT
			and doc.workflow_state == STATE_PENDING
			and doc.borne_by == "Crew"
			and doc.is_late
			and frappe.db.get_single_value("POS Reject Settings", "late_mode") == "Strict"
		):
			frappe.throw(_("borne_by cannot be modified directly"))

	doc_liab = [(r.user, flt(r.amount)) for r in (doc.get("liabilities") or [])]
	bef_liab = [(r.user, flt(r.amount)) for r in (before.get("liabilities") or [])]
	if doc_liab != bef_liab:
		frappe.throw(_("liabilities cannot be modified directly"))

	if (doc.video_link or "") != (before.video_link or ""):
		if before.workflow_state != STATE_AWAITING:
			frappe.throw(_("Video link can only be changed in Awaiting Destruction"))

	# in any state but Draft, items, purpose, pos_profile, incident_date, reason must be unchanged
	if before.workflow_state and before.workflow_state != STATE_DRAFT:
		if doc.purpose != before.purpose:
			frappe.throw(_("Purpose cannot be changed once submitted"))
		if doc.pos_profile != before.pos_profile:
			frappe.throw(_("POS Profile cannot be changed once submitted"))
		if getdate(doc.incident_date) != getdate(before.incident_date):
			frappe.throw(_("Incident date cannot be changed once submitted"))
		if (doc.reason or "") != (before.reason or ""):
			frappe.throw(_("Reason cannot be changed once submitted"))

		doc_items = [
			(r.item_code, flt(r.qty), str(r.production_date or ""), r.reject_reason or "")
			for r in (doc.items or [])
		]
		bef_items = [
			(r.item_code, flt(r.qty), str(r.production_date or ""), r.reject_reason or "")
			for r in (before.items or [])
		]
		if doc_items != bef_items:
			frappe.throw(_("Items cannot be changed once submitted"))


def _price_items(doc, warehouse):
	"""uom/valuation_rate/amount are server-owned: client values are overwritten."""
	total = 0.0
	for row in doc.items:
		qty = flt(row.qty)
		if qty <= 0 or not math.isfinite(qty):
			frappe.throw(_("Row {0}: quantity must be greater than zero").format(row.idx))
		stock_uom, item_rate = frappe.db.get_value("Item", row.item_code, ["stock_uom", "valuation_rate"])
		if row.uom and row.uom != stock_uom:
			conversion_factor = frappe.db.get_value(
				"UOM Conversion Detail",
				{"parent": row.item_code, "uom": row.uom},
				"conversion_factor",
			)
			if not conversion_factor:
				frappe.throw(_("UOM {0} not configured for item {1}").format(row.uom, row.item_code))
			qty = flt(qty * flt(conversion_factor))
			row.qty = qty
		bin_rate = frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": warehouse}, "valuation_rate")
		row.uom = stock_uom
		row.valuation_rate = flt(bin_rate) or flt(item_rate)
		row.amount = flt(qty * row.valuation_rate)
		total += row.amount
	doc.total_amount = total


def _validate_system_generated(doc):
	"""Anti-manipulation guard for drafts born from a Work Order: the loss is
	what the WO says it is — the crew can add a reason and evidence, nothing
	else. purpose/work_order/pos_profile may never be repurposed."""
	wo = frappe.db.get_value(
		"Work Order", doc.work_order, ["qty", "produced_qty", "production_item"], as_dict=True
	)
	if not wo:
		frappe.throw(_("Work Order {0} does not exist").format(doc.work_order))

	if not doc.is_new():
		stored = frappe.db.get_value(
			"POS Reject Request", doc.name, ["purpose", "work_order", "pos_profile"], as_dict=True
		)
		if stored and (
			stored.purpose != doc.purpose
			or stored.work_order != doc.work_order
			or stored.pos_profile != doc.pos_profile
		):
			frappe.throw(_("A system-generated Production Loss request cannot be repurposed"))

	if len(doc.items) != 1 or doc.items[0].item_code != wo.production_item:
		frappe.throw(_("A system-generated request holds exactly the production item of its Work Order"))
	loss = flt(wo.qty) - flt(wo.produced_qty)
	precision = doc.items[0].precision("qty") or 3
	if flt(doc.items[0].qty, precision) != flt(loss, precision):
		frappe.throw(
			_("The lost quantity is fixed at {0} by the Work Order and cannot be changed").format(loss)
		)


def ensure_production_loss_request(wo):
	"""Draft a Production Loss request for the unproduced remainder of a
	terminal Work Order (status Completed/Closed). Returns the draft name,
	or None when nothing was lost. Idempotent per Work Order."""
	loss = flt(wo.qty) - flt(wo.produced_qty)
	if loss <= 0:
		return None

	existing = frappe.db.get_value(
		"POS Reject Request",
		{"work_order": wo.name, "is_system_generated": 1},
		"name",
	)
	if existing:
		return existing

	doc = frappe.new_doc("POS Reject Request")
	doc.purpose = "Production Loss"
	doc.pos_profile = wo.posa_pos_profile
	doc.incident_date = getdate(wo.actual_end_date) if wo.actual_end_date else getdate(wo.modified)
	doc.reason = f"{AUTO_REASON_PREFIX}{wo.name}"
	doc.work_order = wo.name
	doc.is_system_generated = 1
	doc.append("items", {"item_code": wo.production_item, "qty": loss})
	doc.flags.ignore_permissions = True
	doc.insert()
	doc.db_set("owner", wo.posa_operator or frappe.session.user)
	return doc.name


def _month_total(doc, incident):
	return flt(
		frappe.db.sql(
			"""select sum(total_amount) from `tabPOS Reject Request`
			where pos_profile = %s and workflow_state not in ('Draft', 'Cancelled') and name != %s
				and incident_date between %s and %s""",
			(doc.pos_profile, doc.name or "", get_first_day(incident), get_last_day(incident)),
		)[0][0]
	)


def _check_stock(doc, warehouse):
	needed = defaultdict(float)
	for row in doc.items:
		needed[row.item_code] += flt(row.qty)
	for item_code, qty in needed.items():
		available = _stock_qty(item_code, warehouse)
		if available < qty:
			frappe.throw(
				_("Item {0} has only {1} in {2}, cannot reject {3}").format(item_code, available, warehouse, qty)
			)


def _issue_stock(doc):
	"""Issue goods out via a Material Issue.

	Lock with `for_update` on `issue_entry` to prevent duplicate issues.
	For legacy docs with `transfer_entry`, issue from that transfer's target warehouse.
	For new docs, issue from POS Profile warehouse.
	Idempotent: returns existing Stock Entry if already issued or linked.
	"""
	current_issue = frappe.db.get_value("POS Reject Request", doc.name, "issue_entry", for_update=True)
	if current_issue:
		return frappe.get_doc("Stock Entry", current_issue)

	existing_se = frappe.db.get_value(
		"Stock Entry",
		{"remarks": ["like", f"{ISSUE_REMARK_PREFIX}{doc.name}%"], "docstatus": 1},
		"name",
	)
	if existing_se:
		doc.db_set("issue_entry", existing_se)
		doc.set("issue_entry", existing_se)
		return frappe.get_doc("Stock Entry", existing_se)

	if doc.transfer_entry:
		rows = frappe.db.get_all(
			"Stock Entry Detail",
			filters={"parent": doc.transfer_entry, "parenttype": "Stock Entry"},
			fields=["t_warehouse"],
		)
		source = next((r.t_warehouse for r in rows if r.t_warehouse), None)
		if not source:
			frappe.throw(_("The original transfer entry has no target warehouse"))
	else:
		source = _resolve_profile(doc.pos_profile)[1]

	_check_stock(doc, source)

	account = frappe.db.get_value("Company", doc.company, "stock_adjustment_account")
	if not account:
		frappe.throw(_("Company {0} has no Stock Adjustment Account for the write-off").format(doc.company))

	se = _make_stock_entry(doc, "Material Issue", source, None, ISSUE_REMARK_PREFIX + doc.name, account)
	doc.db_set("issue_entry", se.name)
	doc.set("issue_entry", se.name)
	return se


def on_state_change(doc):
	before = getattr(doc, "_doc_before_save", None)
	old_state = before.workflow_state if before else None
	new_state = doc.workflow_state

	if not old_state or old_state == new_state:
		return

	handled_key = f"_handled_state_change_{doc.name}_{old_state}_{new_state}"
	if getattr(frappe.flags, handled_key, False):
		return
	setattr(frappe.flags, handled_key, True)

	# If Completed, ensure stock is issued (only when transitioning from Destruction Submitted)
	if new_state == STATE_COMPLETED:
		if old_state == STATE_PROOF:
			if not is_system_purpose(doc.purpose):
				_issue_stock(doc)
			doc.db_set("closed_on", now())
			doc.set("closed_on", now())
			notify([doc.owner], _("Reject request {0} completed").format(doc.name), doc)
		return

	if new_state == STATE_PENDING:
		notify(approver_users(), _("New reject request {0}").format(doc.name), doc)

	elif new_state == STATE_AWAITING:
		if old_state == STATE_PENDING:
			if doc.borne_by == "Crew":
				existing_liab = frappe.db.count(
					"POS Reject Liability",
					{"parent": doc.name, "parenttype": doc.doctype, "parentfield": "liabilities"},
				)
				if not existing_liab:
					_write_liabilities(doc, [{"user": doc.owner, "amount": flt(doc.total_amount)}])

			if not doc.approval_code:
				code = _unique_approval_code()
				doc.db_set("approval_code", code)
				doc.set("approval_code", code)
			doc.db_set("decided_by", frappe.session.user)
			doc.set("decided_by", frappe.session.user)
			cur_now = now()
			doc.db_set("decided_on", cur_now)
			doc.set("decided_on", cur_now)
			notify([doc.owner], _("Reject request {0} approved").format(doc.name), doc)
		elif old_state == STATE_PROOF:
			notify([doc.owner], _("Destruction proof returned for {0}").format(doc.name), doc)

	elif new_state == STATE_DRAFT:
		notify([doc.owner], _("Reject request {0} returned to Draft").format(doc.name), doc)

	elif new_state == STATE_PROOF:
		notify(approver_users(), _("Destruction proof submitted for {0}").format(doc.name), doc)

	elif new_state == STATE_CANCELLED:
		on_cancel_request(doc)


def set_liability(doc, borne_by: str, liabilities: list[dict] | None = None) -> dict:
	if frappe.session.user != "Administrator" and APPROVER_ROLE not in frappe.get_roles(frappe.session.user):
		frappe.throw(_("Only {0} can set liability").format(APPROVER_ROLE), frappe.PermissionError)

	if frappe.session.user != "Administrator" and frappe.session.user == doc.owner:
		frappe.throw(_("You cannot set liability on your own request"), frappe.PermissionError)

	if doc.workflow_state != STATE_PENDING:
		frappe.throw(_("Liability can only be set in Pending Approval"))

	borne_by = (borne_by or "").strip()
	if borne_by not in ("Company", "Crew"):
		frappe.throw(_("Borne By must be Company or Crew"))

	if (
		doc.is_late
		and frappe.db.get_single_value("POS Reject Settings", "late_mode") == "Strict"
		and borne_by == "Company"
	):
		frappe.throw(_("Late request in Strict mode must be borne by Crew"))

	rows = _resolve_liabilities(doc, borne_by, liabilities)
	doc.db_set("borne_by", borne_by)
	doc.set("borne_by", borne_by)
	_write_liabilities(doc, rows)
	return {"borne_by": borne_by, "liabilities": rows}


def _resolve_liabilities(doc, borne_by, liabilities):
	if borne_by == "Company":
		if liabilities:
			frappe.throw(_("Company-borne requests cannot have crew liabilities"))
		return []
	if liabilities is None:
		return [{"user": doc.owner, "amount": flt(doc.total_amount)}]
	rows = []
	seen = set()
	for row in liabilities:
		user = (row.get("user") or "").strip()
		amount = flt(row.get("amount"))
		if not user or not frappe.db.get_value("User", user, "enabled"):
			frappe.throw(_("Liability user {0} must be an enabled user").format(user or "?"))
		if amount <= 0:
			frappe.throw(_("Liability amount must be greater than zero"))
		if user in seen:
			frappe.throw(_("Liability user {0} appears more than once").format(user))
		seen.add(user)
		rows.append({"user": user, "amount": amount})
	if not rows:
		return [{"user": doc.owner, "amount": flt(doc.total_amount)}]
	precision = doc.precision("total_amount") or 2
	if abs(sum(r["amount"] for r in rows) - flt(doc.total_amount)) > 0.5 * 10 ** -precision:
		frappe.throw(
			_("Liability amounts must add up to {0}").format(doc.get_formatted("total_amount"))
		)
	return rows


def _write_liabilities(doc, rows):
	frappe.db.delete("POS Reject Liability", {"parent": doc.name, "parentfield": "liabilities"})
	for idx, row in enumerate(rows, start=1):
		frappe.get_doc(
			{
				"doctype": "POS Reject Liability",
				"parent": doc.name,
				"parenttype": doc.doctype,
				"parentfield": "liabilities",
				"idx": idx,
				**row,
			}
		).db_insert()


def record_return_reason(doc, reason: str) -> None:
	if frappe.session.user != "Administrator" and APPROVER_ROLE not in frappe.get_roles(frappe.session.user):
		frappe.throw(_("Only {0} can record return reason").format(APPROVER_ROLE), frappe.PermissionError)

	if doc.workflow_state not in (STATE_PENDING, STATE_PROOF):
		frappe.throw(_("Return reason can only be recorded in Pending Approval or Destruction Submitted"))

	reason = (reason or "").strip()
	if not reason:
		frappe.throw(_("Return reason is required"))

	doc.db_set("return_reason", reason)
	doc.set("return_reason", reason)
	doc.add_comment("Comment", text=_("Returned: {0}").format(reason))


def on_cancel_request(doc):
	if doc.transfer_entry:
		se = frappe.get_doc("Stock Entry", doc.transfer_entry)
		if se.docstatus == 1:
			se.flags.ignore_permissions = True
			se.cancel()


def approver_users():
	users = frappe.get_all("Has Role", filters={"role": APPROVER_ROLE, "parenttype": "User"}, pluck="parent")
	if not users:
		return []
	return frappe.get_all(
		"User", filters={"name": ["in", users], "enabled": 1, "user_type": "System User"}, pluck="name"
	)


def notify(users, subject, doc):
	"""Notification Log for users; HO approver notices also go by email to the
	addresses in POS Reject Settings (best effort: never blocks the request).
	Logs are written inline: the enqueued variant is a background job."""
	if users:
		try:
			make_notification_logs(
				frappe._dict(
					type="Alert",
					subject=subject,
					document_type=doc.doctype,
					document_name=doc.name,
					from_user=frappe.session.user,
				),
				users,
			)
		except Exception:
			frappe.log_error(title="POS Reject Request notification failed")
	if set(users) != set(approver_users()):
		return
	emails = frappe.db.get_single_value("POS Reject Settings", "notify_emails") or ""
	recipients = [e.strip() for e in emails.splitlines() if e.strip()]
	if not recipients:
		return
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=f'<a href="{get_url_to_form(doc.doctype, doc.name)}">{doc.name}</a>',
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title="POS Reject Request email failed")


def _make_stock_entry(doc, purpose, source, target, remarks, expense_account=None):
	se = frappe.new_doc("Stock Entry")
	se.company = doc.company
	se.purpose = purpose
	se.set_stock_entry_type()
	se.remarks = remarks
	for row in doc.items:
		line = {
			"item_code": row.item_code,
			"qty": row.qty,
			"s_warehouse": source,
			"t_warehouse": target,
			"use_serial_batch_fields": 1,
		}
		if expense_account:
			line["expense_account"] = expense_account
		if frappe.db.get_value("Item", row.item_code, "has_batch_no"):
			line["batch_no"] = _pick_batch(row.item_code, source, flt(row.qty))
		se.append("items", line)
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	return se
