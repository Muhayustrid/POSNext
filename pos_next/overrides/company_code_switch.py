# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Company validate guard for the per-outlet code switches.

The switches must only be flipped by head office: a `permlevel` was rejected
because standard Company perms let several manager roles write at permlevel 0
(and a Custom DocPerm on Company would drop its standard perms entirely).
A `Company` validate doc_event comparing before/after values is the fence.
"""

import frappe
from frappe import _

_SWITCH_FIELDS = ("pos_require_discount_code", "pos_require_refund_code")
# The install default; a new Company carrying it (or nothing) is not a change.
_SWITCH_DEFAULT = 1


def guard_code_switches(doc, method=None):
	"""Throw when a non-System-Manager changed either switch on this save.

	On a NEW Company `has_value_changed` is True for every field, so a switch
	at (or below) the default must pass — only a non-default value is a
	flip."""
	if "System Manager" in frappe.get_roles():
		return
	if not doc.is_new() and not any(doc.has_value_changed(field) for field in _SWITCH_FIELDS):
		return
	if doc.is_new() and all(
		frappe.utils.cint(doc.get(field)) == _SWITCH_DEFAULT for field in _SWITCH_FIELDS
	):
		return
	frappe.throw(_("Only a System Manager can change the code requirement of an outlet."))
