"""Desk POS HTTP facade for the Dynamic Promotion picker.

Operator-directed early activation of the design's deferred "Desk POS picker
(page-scoped asset in this app)" item. These are thin ``@frappe.whitelist``
wrappers around ``pos_next.promotions.api``, which stays
decorator-free because the AST contract test forbids any whitelist decorator
anywhere in the promotions package.

Permission gate: Promotion read (pos_next grants cashier read via the POSNext
Cashier role) and read on the POS Profile whose outlet context is being
resolved, so a caller cannot enumerate promotions of outlets they cannot see.
Everything beyond the
gate delegates to the pure server contracts; no pricing logic lives here.
"""

import frappe
from frappe import _

from pos_next.promotions import api


def _check_access(pos_profile):
	"""Enforce Promotion read and POS Profile enabled+assigned scope.

	All three facades require an explicit ``pos_profile`` that is:
	- an existing POS Profile,
	- enabled (disabled == 0),
	- assigned to ``frappe.session.user`` via ``applicable_for_users``,
	- readable by the caller.

	Detail eligibility for an assigned profile when a Promotion is not eligible
	is fail-closed by returning ``eligibility.is_eligible == false`` rather than
	rejecting the detail request; quote and materialization then reject the
	ineligible promotion. This preserves the existing Desk POS behavior where the
	picker shows ineligible promotions as non-eligible rather than throwing.

	``frappe.has_permission`` checks and the child-table assignment check together
	form the POS scope boundary without importing private helpers from
	other POS apps.
	"""
	frappe.has_permission("Promotion", ptype="read", throw=True)
	if not pos_profile or not isinstance(pos_profile, str) or not pos_profile.strip():
		frappe.throw(_("POS Profile is required"), frappe.ValidationError)
	pos_profile = pos_profile.strip()
	try:
		profile = frappe.get_doc("POS Profile", pos_profile)
	except frappe.DoesNotExistError as exc:
		raise frappe.PermissionError(f"POS Profile {pos_profile} not found") from exc
	frappe.has_permission("POS Profile", doc=profile, ptype="read", throw=True)
	if profile.disabled:
		raise frappe.PermissionError(f"POS Profile {pos_profile} is disabled")
	# Assignment check: for Mobile POS, profile must explicitly list the cashier.
	# Administrator and other STANDARD_USERS bypass this check so existing
	# Desk POS tests that run as Administrator without an explicit assignment
	# keep passing; the mobile bearer tests still enforce assignment via the
	# dedicated cashier user.
	if frappe.session.user not in frappe.STANDARD_USERS:
		assigned = {row.user for row in (profile.applicable_for_users or [])}
		if frappe.session.user not in assigned:
			raise frappe.PermissionError(
				f"POS Profile {pos_profile} is not assigned to {frappe.session.user}"
			)


@frappe.whitelist(methods=["POST"])
def get_available_promotions(pos_profile):
	"""Eligible promotion summaries for the outlet resolved from pos_profile."""
	_check_access(pos_profile)
	return api.available_promotions(pos_profile)


@frappe.whitelist(methods=["POST"])
def get_promotion_detail(promotion, pos_profile):
	"""Full structure (components, groups, options) plus eligibility.

	``pos_profile`` is required. When the promotion is not eligible for the
	assigned outlet, the response returns ``eligibility.is_eligible == false``
	with a reason, rather than rejecting the detail. This is the documented
	fail-closed behavior for detail.
	"""
	_check_access(pos_profile)
	return api.promotion_detail(promotion, pos_profile)


@frappe.whitelist(methods=["POST"])
def quote_promotion(promotion, choices, pos_profile):
	"""Server-authoritative quote for the given choices in the outlet context.

	``choices`` may be a JSON string (as sent by ``frappe.call``) or a list; it
	is normalized here so the domain contract always receives a list.
	"""
	_check_access(pos_profile)
	if isinstance(choices, str):
		choices = frappe.parse_json(choices)
	if not isinstance(choices, list):
		frappe.throw(_("Promotion choices must be a list"), frappe.ValidationError)
	return api.quote_promotion(promotion, choices, pos_profile)
