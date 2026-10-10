# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""POS Reject Request lifecycle: submit (Unsold), validation, deadline, limit,
stock transfer to the reject warehouse, Head Office decisions (segregation of
duties), closing with destruction proof and notifications.

Design: docs/superpowers/specs/2026-10-11-pos-reject-request-design.md
"""

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import make_notification_logs
from frappe.utils import add_days, flt, get_first_day, get_last_day, get_url_to_form, getdate, now, today

from pos_next.api.settings_resolver import get_effective_pos_setting
from pos_next.services.production import _pick_batch, _resolve_profile, _stock_qty

# Canonical English, never translated: cancel matches on it.
TRANSFER_REMARK_PREFIX = "POS Reject transfer: "
ISSUE_REMARK_PREFIX = "POS Reject write-off: "
APPROVER_ROLE = "POSNext Reject Approver"
MAX_EVIDENCE = 5
DECISIONS = ("Write Off", "Charge Crew", "Rejected")
AUTO_REASON_PREFIX = "Auto: production loss on "


def validate_request(doc):
	from pos_next.api.packages import _assert_profile_access

	if frappe.session.user != "Administrator" and not doc.flags.ignore_permissions:
		_assert_profile_access(doc.pos_profile)

	doc.company, warehouse = _resolve_profile(doc.pos_profile)
	_price_items(doc, warehouse)

	incident = getdate(doc.incident_date)
	if incident > getdate(today()):
		frappe.throw(_("Incident date cannot be in the future"))

	settings = frappe.get_doc("POS Reject Settings")
	days = settings.deadline_days
	if doc.purpose == "Production Loss" and (settings.production_loss_deadline_days or 0) > 0:
		days = settings.production_loss_deadline_days
	doc.deadline_date = add_days(incident, days or 0)

	if doc.video_link and not doc.video_link.lower().startswith(("http://", "https://")):
		frappe.throw(_("Video link must start with http:// or https://"))

	if doc.is_system_generated:
		_validate_system_generated(doc)

	if doc._action != "submit":
		return

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

	if doc.is_system_generated and (doc.reason or "").startswith(AUTO_REASON_PREFIX):
		frappe.throw(_("Describe what happened to the lost quantity before submitting"))

	doc.is_late = 1 if getdate(today()) > getdate(doc.deadline_date) else 0
	limit = flt(settings.monthly_limit_amount)
	doc.over_limit = 1 if limit > 0 and flt(doc.total_amount) + _month_total(doc, incident) > limit else 0


def _price_items(doc, warehouse):
	"""uom/valuation_rate/amount are server-owned: client values are overwritten."""
	total = 0.0
	for row in doc.items:
		qty = flt(row.qty)
		if qty <= 0 or not math.isfinite(qty):
			frappe.throw(_("Row {0}: quantity must be greater than zero").format(row.idx))
		stock_uom, item_rate = frappe.db.get_value("Item", row.item_code, ["stock_uom", "valuation_rate"])
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
	if flt(doc.items[0].qty) != loss:
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
		"POS Reject Request", {"work_order": wo.name, "docstatus": ["!=", 2]}, "name"
	)
	if existing:
		return existing

	doc = frappe.new_doc("POS Reject Request")
	doc.purpose = "Production Loss"
	doc.pos_profile = wo.posa_pos_profile
	doc.incident_date = today()
	doc.reason = f"{AUTO_REASON_PREFIX}{wo.name}"
	doc.work_order = wo.name
	doc.is_system_generated = 1
	doc.append("items", {"item_code": wo.production_item, "qty": loss})
	doc.flags.ignore_permissions = True
	doc.insert()
	# insert stamps the session user as owner; the operator who ran the WO
	# owns the draft so they are the one who must account for the loss.
	doc.db_set("owner", wo.posa_operator or frappe.session.user)
	return doc.name


def _month_total(doc, incident):	return flt(
		frappe.db.sql(
			"""select sum(total_amount) from `tabPOS Reject Request`
			where pos_profile = %s and docstatus = 1 and name != %s
				and incident_date between %s and %s""",
			(doc.pos_profile, doc.name or "", get_first_day(incident), get_last_day(incident)),
		)[0][0]
	)


def on_submit_request(doc):
	doc.db_set("status", "Pending")
	if doc.purpose == "Unsold":
		_transfer_to_reject_warehouse(doc)
	if doc.is_late and frappe.db.get_single_value("POS Reject Settings", "late_mode") == "Strict":
		return apply_decision(
			doc, "Charge Crew", _("Auto: past deadline (Strict mode)"), system=True
		)
	notify(approver_users(), _("New reject request {0}").format(doc.name), doc)
	doc.add_comment("Comment", text=_("Submitted, waiting for a decision"))


def before_cancel_request(doc):
	if doc.status != "Pending":
		frappe.throw(_("Only a Pending reject request can be cancelled"))


def on_cancel_request(doc):
	if doc.transfer_entry:
		se = frappe.get_doc("Stock Entry", doc.transfer_entry)
		if se.docstatus == 1:
			se.flags.ignore_permissions = True
			se.cancel()
	doc.db_set("status", "Cancelled")


def _transfer_to_reject_warehouse(doc):
	source = _resolve_profile(doc.pos_profile)[1]
	target = get_effective_pos_setting(doc.pos_profile, "reject_warehouse")
	if not target:
		frappe.throw(_("Set Reject Warehouse in POS Settings for {0}").format(doc.pos_profile))

	needed = defaultdict(float)
	for row in doc.items:
		needed[row.item_code] += flt(row.qty)
	for item_code, qty in needed.items():
		available = _stock_qty(item_code, source)
		if available < qty:
			frappe.throw(
				_("Item {0} has only {1} in {2}, cannot reject {3}").format(item_code, available, source, qty)
			)

	se = _make_stock_entry(doc, "Material Transfer", source, target, TRANSFER_REMARK_PREFIX + doc.name)
	doc.db_set("transfer_entry", se.name)


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


def apply_decision(doc, decision, notes=None, system=False):
	"""Head Office verdict on a Pending request (segregation of duties).

	The row is locked for update first: a double click loses the race and the
	second call throws instead of creating a second Stock Entry.
	"""
	decided_by = "Administrator" if system else frappe.session.user
	if not system:
		if APPROVER_ROLE not in frappe.get_roles(decided_by):
			frappe.throw(
				_("Only {0} can decide a reject request").format(APPROVER_ROLE), frappe.PermissionError
			)
		if decided_by == doc.owner:
			frappe.throw(_("You cannot decide your own request"), frappe.PermissionError)

	frappe.db.get_value("POS Reject Request", doc.name, "status", for_update=True)
	if frappe.db.get_value("POS Reject Request", doc.name, "status") != "Pending":
		frappe.throw(_("Only a Pending reject request can be decided"))

	decision = (decision or "").strip()
	if decision not in DECISIONS:
		frappe.throw(_("Decision must be one of {0}").format(", ".join(DECISIONS)))
	if decision == "Rejected" and doc.purpose != "Unsold":
		frappe.throw(_("Rejected only applies to an Unsold request"))
	notes = (notes or "").strip()
	if decision in ("Charge Crew", "Rejected") and not notes:
		frappe.throw(_("Notes are required for {0}").format(decision))

	if decision == "Rejected":
		_return_to_outlet(doc)
		status = "Rejected"
	else:
		status = "Approved"

	doc.db_set("decision", decision)
	doc.db_set("decision_notes", notes or None)
	doc.db_set("decided_by", decided_by)
	doc.db_set("decided_on", now())
	doc.db_set("status", status)
	doc.add_comment("Comment", text=_("Decision: {0}").format(decision))
	notify([doc.owner], _("Reject request {0}: {1}").format(doc.name, decision), doc)
	return _payload(doc)


def close_request(doc):
	"""Close an Approved request once destruction proof is attached.

	Unsold stock leaves the reject warehouse via a Material Issue to the
	company's stock adjustment account; Production Loss never touched stock.
	"""
	frappe.db.get_value("POS Reject Request", doc.name, "status", for_update=True)
	status, issue_entry = frappe.db.get_value("POS Reject Request", doc.name, ["status", "issue_entry"])
	if status != "Approved":
		frappe.throw(_("Only an Approved reject request can be closed"))
	if not frappe.db.count(
		"File",
		{
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": "destruction",
		},
	):
		frappe.throw(_("Attach at least one destruction photo"))

	if doc.purpose == "Unsold" and not issue_entry:
		doc.db_set("issue_entry", _write_off_reject_stock(doc).name)
	doc.db_set("status", "Closed")
	doc.db_set("closed_on", now())
	doc.add_comment("Comment", text=_("Closed with destruction proof"))
	return _payload(doc)


def _return_to_outlet(doc):
	"""Rejected: the outlet takes the goods back (mirrors the outbound transfer)."""
	source = get_effective_pos_setting(doc.pos_profile, "reject_warehouse")
	target = _resolve_profile(doc.pos_profile)[1]
	return _make_stock_entry(
		doc, "Material Transfer", source, target, TRANSFER_REMARK_PREFIX + doc.name + " return"
	)


def _write_off_reject_stock(doc):
	"""Material Issue from the reject warehouse to the stock adjustment account."""
	source = get_effective_pos_setting(doc.pos_profile, "reject_warehouse")
	account = frappe.db.get_value("Company", doc.company, "stock_adjustment_account")
	if not account:
		frappe.throw(_("Company {0} has no Stock Adjustment Account for the write-off").format(doc.company))
	return _make_stock_entry(doc, "Material Issue", source, None, ISSUE_REMARK_PREFIX + doc.name, account)


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


def _payload(doc):
	return {
		"name": doc.name,
		"status": frappe.db.get_value("POS Reject Request", doc.name, "status"),
		"decision": frappe.db.get_value("POS Reject Request", doc.name, "decision"),
		"issue_entry": frappe.db.get_value("POS Reject Request", doc.name, "issue_entry"),
	}
