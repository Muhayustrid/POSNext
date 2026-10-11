# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Daily job for system-generated Production Loss drafts.

Two sweeps, both best effort per document (failures are logged, never abort
the run):

1. Backfill: terminal POS Work Orders (Completed/Closed, modified in the
   last 30 days) that still owe a Production Loss request get their draft
   created — covering anything the finish/close hook missed.
2. Overdue: drafts still unreported past their deadline notify the
   approvers once; a marker Comment on the document dedupes daily runs.
"""

import frappe
from frappe import _
from frappe.utils import add_days, flt, today

from pos_next.services.reject_request import approver_users, ensure_production_loss_request, notify

# Canonical English, never translated: the dedupe marker.
NOTIFIED_MARKER_PREFIX = "Unreported production loss notified"
WO_SWEEP_DAYS = 30


def notify_overdue_drafts():
	"""Backfill missing Production Loss drafts, then notify approvers about
	each overdue draft once. Returns the number of drafts reported."""
	_backfill_terminal_work_orders()
	return _notify_overdue()


def _backfill_terminal_work_orders():
	"""POS Work Orders that reached Completed/Closed with an unproduced
	remainder but no system-generated request: recreate the draft."""
	wo_rows = frappe.get_all(
		"Work Order",
		filters={
			"docstatus": 1,
			"status": ["in", ("Completed", "Closed")],
			"posa_pos_profile": ["is", "set"],
			"modified": [">=", add_days(today(), -WO_SWEEP_DAYS)],
		},
		fields=["name", "qty", "produced_qty"],
	)
	for row in wo_rows:
		if flt(row.qty) <= flt(row.produced_qty):
			continue
		if frappe.db.exists(
			"POS Reject Request",
			{"work_order": row.name, "is_system_generated": 1},
		):
			continue
		try:
			ensure_production_loss_request(frappe.get_doc("Work Order", row.name))
		except Exception:
			frappe.log_error(title="Production Loss draft backfill failed", reference_name=row.name)


def _notify_overdue():
	"""Approver notification per overdue system-generated draft, once ever."""
	drafts = frappe.get_all(
		"POS Reject Request",
		filters={
			"is_system_generated": 1,
			"docstatus": 0,
			"deadline_date": ["<", today()],
		},
		pluck="name",
	)
	reported = 0
	for name in drafts:
		if _already_notified(name):
			continue
		try:
			doc = frappe.get_doc("POS Reject Request", name)
			notify(approver_users(), _("Unreported production loss {0}").format(name), doc)
			# count and mark only when somebody can actually see it
			if not approver_users() and not _notify_emails():
				continue
			doc.add_comment("Comment", text=f"{NOTIFIED_MARKER_PREFIX} on {today()}")
			reported += 1
		except Exception:
			frappe.log_error(title="Production Loss overdue notification failed", reference_name=name)
	return reported


def _notify_emails():
	emails = frappe.db.get_single_value("POS Reject Settings", "notify_emails") or ""
	return [e.strip() for e in emails.splitlines() if e.strip()]


def _already_notified(name):
	return bool(
		frappe.db.exists(
			"Comment",
			{
				"comment_type": "Comment",
				"reference_doctype": "POS Reject Request",
				"reference_name": name,
				"content": ["like", f"{NOTIFIED_MARKER_PREFIX}%"],
			},
		)
	)
