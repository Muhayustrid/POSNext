# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""
Discount Restriction API for the POS frontend.

These endpoints are UX-only (early feedback, live code validation) — the
authoritative gate lives in the Sales Invoice doc_events
(pos_next.overrides.discount_restriction), so a tampered client cannot
bypass any rule.
"""

import json

import frappe
from frappe.utils import cint, flt

from pos_next.overrides.discount_restriction import (
	RESTRICTION_DOCTYPE,
	get_applicable_restriction,
	get_quota_info,
	_validate_code_value,
	invoice_requires_code,
)


@frappe.whitelist(methods=["POST"])
def get_status(company: str):
	"""Active restriction state for a company — drives POS UI hints."""
	rule = get_applicable_restriction(company)
	if not rule:
		return {"applicable": False}

	quota = get_quota_info(rule, company)
	quota_exhausted = bool(quota and quota["remaining"] == 0)

	return {
		"applicable": True,
		"rule": {"name": rule.name, "title": rule.title},
		"enforce_quota": cint(rule.enforce_usage_quota),
		"quota": quota,
		"quota_exhausted": quota_exhausted,
		"requires_code": cint(rule.require_confirmation_code),
		"code_items": [row.item for row in (rule.get("code_items") or [])],
	}


@frappe.whitelist(methods=["POST"])
def validate_confirmation_code(code: str, company: str, items=None, additional_discount: float = 0):
	"""Live-validate a confirmation code against the active rule.

	`items` is a JSON list of cart item dicts (item_code, discount_percentage,
	discount_amount, rate, price_list_rate, is_rate_manually_edited) so the
	check mirrors what the server will enforce on submit. Returns
	{valid, message} without throwing, for inline form feedback.
	"""
	rule = get_applicable_restriction(company)
	if not rule:
		return {"valid": True, "applicable": False, "requires_code": False}

	if isinstance(items, str):
		try:
			items = json.loads(items) if items else []
		except (ValueError, TypeError):
			items = []
	items = items or []

	payload = {"items": items, "discount_amount": flt(additional_discount or 0)}
	if not invoice_requires_code(rule, payload):
		return {
			"valid": True,
			"applicable": True,
			"requires_code": False,
			"rule": {"name": rule.name, "title": rule.title},
		}

	try:
		_validate_code_value(rule, code, company)
	except frappe.ValidationError as e:
		return {"valid": False, "applicable": True, "requires_code": True, "message": str(e)}

	return {
		"valid": True,
		"applicable": True,
		"requires_code": True,
		"rule": {"name": rule.name, "title": rule.title},
	}
