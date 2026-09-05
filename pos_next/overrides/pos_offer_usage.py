# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""
POS Offer usage quota enforcement for POS invoices.

Capture: ``update_invoice`` stashes every applied Pricing Rule name on
``Sales Invoice.pos_applied_offer_rules`` (JSON list) before clearing
``item.pricing_rules`` — the same transport as the one-time-per-customer
stash. That stash is the server-side truth; the client cannot fake it.

Enforcement (doc_events on Sales Invoice, same posture as Discount
Restriction):
- validate: hard gate on draft save AND submit for every applied offer with
  a quota (Global counts all ledger rows of the offer; Per Company only this
  invoice's company; Daily adds a posting-date filter — reset is implicit).
- on_submit: lock the offer row (serializes concurrent submits racing for the
  last slot), re-check the quota, then insert one idempotent ledger row per
  applied offer (composite name {pos_offer}:{sales_invoice}).
- on_cancel: delete the invoice's ledger rows (quota released).
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, cstr, now_datetime, today

OFFER_DOCTYPE = "POS Offer"
SCHEME_DOCTYPE = "Promotional Scheme"
RULE_DOCTYPE = "Pricing Rule"
USAGE_DOCTYPE = "POS Offer Usage"


# ==========================================================================
# Quota math
# ==========================================================================


def get_quota_info(offer, company, posting_date=None, company_rows=None):
	"""Return {scope, period, limit, used, remaining} or None when not enforced.

	``remaining`` is None for an unlimited quota (limit 0). ``offer`` may be a
	document or a mapping (``.get`` access); ``company_rows`` lets batched
	callers pass the POS Offer Company rows instead of offer.get("companies").
	"""
	if not cint(offer.get("enforce_usage_quota")):
		return None

	date = posting_date or today()
	scope = offer.get("quota_scope") or "Global"
	period = offer.get("quota_period") or "Campaign Total"

	filters = {"pos_offer": offer.get("name")}
	if scope == "Per Company":
		filters["company"] = company
		rows = company_rows if company_rows is not None else offer.get("companies") or []
		limit = 0
		for row in rows:
			if row.company == company:
				limit = cint(row.max_usage)
				break
	else:
		limit = cint(offer.get("global_max_usage"))

	if period == "Daily":
		filters["posting_date"] = date

	used = frappe.db.count(USAGE_DOCTYPE, filters)
	remaining = None if limit <= 0 else max(limit - used, 0)
	return {"scope": scope, "period": period, "limit": limit, "used": used, "remaining": remaining}


def check_quota(offer, company, posting_date=None):
	"""Raise ValidationError when the offer quota is used up."""
	info = get_quota_info(offer, company, posting_date=posting_date)
	if not info or info["remaining"] is None:
		return info

	if info["remaining"] == 0:
		title = offer.get("title") or offer.get("name")
		period_txt = _("today") if info["period"] == "Daily" else _("for this campaign")
		if info["scope"] == "Global":
			frappe.throw(
				_("Offer quota for {0} is used up {1} ({2} of {3} transactions across all companies).").format(
					title, period_txt, info["used"], info["limit"]
				)
			)
		frappe.throw(
			_("Offer quota for {0} is used up for company {1} {2} ({3} of {4} transactions).").format(
				title, company, period_txt, info["used"], info["limit"]
			)
		)
	return info


# ==========================================================================
# Rule → offer mapping
# ==========================================================================


def parse_applied_offer_rules(raw):
	"""Parse the JSON stash of applied Pricing Rule names (garbage-safe).

	Returns the names sorted so downstream quota checks and ledger rows are
	order-independent.
	"""
	if not raw:
		return []
	try:
		parsed = json.loads(raw)
	except (ValueError, TypeError):
		return []
	if not isinstance(parsed, list):
		return []
	return sorted(cstr(name) for name in parsed if cstr(name))


def resolve_offers_from_rules(rule_names):
	"""Map applied Pricing Rule names → the POS Offers that own them.

	Walks rule → promotional_scheme → scheme.pos_offer; standalone rules and
	schemes not owned by an offer drop out. Returns offer mappings with the
	company child rows attached (batched, no per-offed get_doc).
	"""
	rule_names = [name for name in rule_names or [] if name]
	if not rule_names:
		return []

	rules = frappe.get_all(RULE_DOCTYPE, {"name": ["in", rule_names]}, ["name", "promotional_scheme"])
	scheme_names = {rule.promotional_scheme for rule in rules if rule.promotional_scheme}
	if not scheme_names:
		return []
	scheme_rows = frappe.get_all(
		SCHEME_DOCTYPE, {"name": ["in", scheme_names]}, ["name", "pos_offer"]
	)
	offer_names = sorted({row.pos_offer for row in scheme_rows if row.pos_offer})
	if not offer_names:
		return []

	fields = [
		"name",
		"title",
		"enabled",
		"enforce_usage_quota",
		"quota_scope",
		"quota_period",
		"global_max_usage",
	]
	offers = frappe.get_all(OFFER_DOCTYPE, {"name": ["in", offer_names]}, fields)

	company_rows = {}
	for row in frappe.get_all(
		"POS Offer Company",
		{"parent": ["in", offer_names], "parenttype": OFFER_DOCTYPE},
		["parent", "company", "enabled", "max_usage"],
	):
		company_rows.setdefault(row.parent, []).append(row)

	for offer in offers:
		offer.companies = company_rows.get(offer.name, [])
	return offers


# ==========================================================================
# doc_events (Sales Invoice)
# ==========================================================================


def validate_invoice_offers(doc, method=None):
	"""Sales Invoice validate hook — hard quota gate (draft save and submit)."""
	if not doc.get("is_pos") or doc.get("is_return"):
		return
	rule_names = parse_applied_offer_rules(doc.get("pos_applied_offer_rules"))
	if not rule_names:
		return

	company = doc.get("company")
	for offer in resolve_offers_from_rules(rule_names):
		if cint(offer.get("enforce_usage_quota")):
			check_quota(offer, company, posting_date=doc.get("posting_date"))


def record_offer_usage_on_submit(doc, method=None):
	"""Sales Invoice on_submit hook — consume quota (idempotent ledger rows)."""
	if not doc.get("is_pos") or doc.get("is_return"):
		return
	rule_names = parse_applied_offer_rules(doc.get("pos_applied_offer_rules"))
	if not rule_names:
		return
	offers = resolve_offers_from_rules(rule_names)
	if not offers:
		return

	company = doc.get("company")
	for offer in offers:
		# Serialize concurrent submits racing for the last quota slot.
		frappe.db.get_value(OFFER_DOCTYPE, offer.name, "name", for_update=True)
		if cint(offer.get("enforce_usage_quota")):
			check_quota(offer, company, posting_date=doc.get("posting_date"))
		try:
			frappe.get_doc(
				{
					"doctype": USAGE_DOCTYPE,
					"pos_offer": offer.name,
					"company": company,
					"sales_invoice": doc.get("name"),
					"posting_date": doc.get("posting_date") or today(),
					"used_by": frappe.session.user,
					"used_on": now_datetime(),
				}
			).insert(ignore_permissions=True, ignore_if_duplicate=True)
		except frappe.DuplicateEntryError:
			pass


def release_offer_usage_on_cancel(doc, method=None):
	"""Sales Invoice on_cancel hook — release the consumed quota."""
	frappe.db.delete(USAGE_DOCTYPE, {"sales_invoice": doc.get("name")})
