# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Company validate guard for the Code Gate Group mirror link.

The Members table on the group is the single source of membership; the
Company's `pos_code_gate_group` link is a read-only mirror. A link without a
matching member row would falsely claim membership: the next group save would
not sync it (and would unlink it outright), and it never received the group's
switches. Reject that state at Company save — membership is edited on the
Code Gate Group.
"""

import frappe
from frappe import _

GATE_TITLE = _("Code Gate Group")


def validate_company_group_membership(doc, method=None):
	"""doc_events validate hook on Company: reject a group link with no member row."""
	group = doc.get("pos_code_gate_group")
	if not group:
		return
	if not frappe.db.exists(
		"POS Code Gate Group Member",
		{"parent": group, "parenttype": "POS Code Gate Group", "company": doc.name},
	):
		frappe.throw(
			_("Company {0} is not in the Members table of Code Gate Group {1}. Manage membership from the Code Gate Group.").format(
				doc.name, group
			),
			title=GATE_TITLE,
		)
