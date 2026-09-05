# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""
Discount code API for the POS frontend.

These endpoints are UX-only (early feedback, live code validation) — the
authoritative gate lives in the Sales Invoice doc_events
(pos_next.overrides.discount_code), so a tampered client cannot bypass it.
"""

import json

import frappe
from frappe import _
from frappe.utils import flt

from pos_next.overrides.discount_code import invoice_has_manual_discount, validate_code


@frappe.whitelist(methods=["POST"])
def get_status(company: str):
	"""Discount gate state for a company — drives POS UI hints.

	The gate is always on; the endpoint exists so the frontend has one stable
	place to ask (and a future per-company toggle can land without client
	changes).
	"""
	return {"enabled": True}


@frappe.whitelist(methods=["POST"])
def validate_confirmation_code(code: str, company: str, items=None, additional_discount: float = 0):
	"""Live-validate a discount code against the cart about to be saved.

	`items` is a JSON list of cart item dicts (item_code, discount_percentage,
	discount_amount, rate, price_list_rate, is_rate_manually_edited) so the
	check mirrors what the server will enforce on save/submit. Returns
	{valid, requires_code, message?} without throwing, for inline feedback.
	"""
	if isinstance(items, str):
		try:
			items = json.loads(items) if items else []
		except (ValueError, TypeError):
			items = []
	if items is None:
		items = []
	if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
		# Malformed payload (entries that are not dicts used to 500 on .get()
		# in the discount detection) — answer defensively instead of raising.
		return {
			"valid": False,
			"requires_code": True,
			"message": _("Invalid items payload; cannot validate the discount code."),
		}

	payload = {"items": items, "discount_amount": flt(additional_discount or 0)}
	if not invoice_has_manual_discount(payload):
		return {"valid": True, "requires_code": False}

	try:
		validate_code(code, company)
	except frappe.ValidationError as e:
		return {"valid": False, "requires_code": True, "message": str(e)}

	return {"valid": True, "requires_code": True}
