# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Daily job: prod the approvers (and the crew's owner via the same log) about
system-generated Production Loss drafts still unreported past their deadline.

One notification per draft ever: the "already notified" marker is a Comment
with a fixed prefix on the document itself.
"""

import frappe
from frappe import _
from frappe.utils import today

from pos_next.services.reject_request import approver_users, notify

# Canonical English, never translated: the dedupe marker.
NOTIFIED_MARKER_PREFIX = "Unreported production loss notified"


def notify_overdue_drafts():
	"""Notify approvers about each overdue system-generated draft once.

	Returns the number of drafts reported in this run.
	"""
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
		doc = frappe.get_doc("POS Reject Request", name)
		notify(approver_users(), _("Unreported production loss {0}").format(name), doc)
		doc.add_comment("Comment", text=f"{NOTIFIED_MARKER_PREFIX} on {today()}")
		reported += 1
	return reported


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
