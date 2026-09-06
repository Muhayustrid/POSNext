# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""
Discount code gate for POS invoices.

Every manual discount on a POS invoice — an item-level discount (explicit
percentage/amount, or a rate edited below price_list_rate) or the header
additional discount — requires a `POS Discount Confirmation Code` issued by
head office. Codes are multi-use and stay valid until head office disables
them; usage is audited on submit.

Enforcement points (doc_events on Sales Invoice):
- validate:   hard gate on draft save AND submit — a discounted invoice must
              carry an active code that is valid today and for the invoice's
              company in `discount_confirmation_code`.
- on_submit:  re-check the code under a row lock and stamp the usage audit
              fields (used_count, last_used_*).

The gate applies to POS invoices only (is_pos); back-office invoices are not
restricted. Returns (is_return) never require a code.

Exemption: pre-approved discount channels need no code.
- POS Offer promotions: an item whose discount is offer-attributed (its
  `pos_offer_item_rules` stash carries a verified, enabled Pricing Rule —
  written server-side by pos_next.api.invoices.update_invoice) is not a manual
  discount; the same holds for the header additional discount when a verified
  applied rule with apply_on == "Transaction" fired (invoice-level
  `pos_applied_offer_rules`).
- ERPNext Pricing Rules / Promotional Schemes: erpnext's own engine stamps the
  applied rule names on each item row (`pricing_rules`, JSON list). Claims from
  that marker are verified against enabled Pricing Rules exactly like the
  stash, so a named rule that does not exist (or is disabled) confers no
  exemption. Promotional Schemes generate Pricing Rules, so they are covered
  by the same marker.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from pos_next.overrides.pos_offer_usage import parse_applied_offer_rules

CODE_DOCTYPE = "POS Discount Confirmation Code"
CODE_COMPANY_DOCTYPE = "POS Discount Code Company"
# Scopes that consult the child outlet list; anything else is valid everywhere.
_SCOPED_MODES = ("Selected Outlets", "All Outlets Except")


# ==========================================================================
# Discount detection
# ==========================================================================


def _item_has_discount(item):
	"""True when the item row carries a manual discount (incl. a manual rate
	edit below price_list_rate, which is a discount in disguise)."""
	if flt(item.get("discount_percentage") or 0) > 0 or flt(item.get("discount_amount") or 0) > 0:
		return True

	if cint(item.get("is_rate_manually_edited") or 0):
		price_list_rate = flt(item.get("price_list_rate") or 0)
		rate = flt(item.get("rate") or 0)
		if price_list_rate > 0 and rate < price_list_rate:
			return True

	return False


def _engine_rule_claims(item):
	"""Rule names claimed via erpnext's own marker (`pricing_rules` on the item
	row, JSON list — set by the pricing rule engine and by the POS apply-offers
	flow). Same JSON shape as the offer stash, so the same parser applies."""
	return parse_applied_offer_rules(item.get("pricing_rules"))


def _verified_applied_rules(doc):
	"""Verify the claimed applied Pricing Rule names in one query.

	Returns ``(verified_rules, invoice_claims)``: ``verified_rules`` maps each
	claimed name that exists as an enabled Pricing Rule to its ``apply_on``;
	``invoice_claims`` is the set of names claimed on the invoice-level
	``pos_applied_offer_rules`` stash — the only source that can exempt the
	header discount (R3). Claims from item stashes and erpnext's
	``pricing_rules`` marker are verified too (they can exempt an item),
	but never the header. Unknown or disabled claimed rules confer no
	exemption (defense in depth).
	"""
	invoice_claims = set(parse_applied_offer_rules(doc.get("pos_applied_offer_rules")))
	claimed = set(invoice_claims)
	for item in doc.get("items") or []:
		claimed.update(parse_applied_offer_rules(item.get("pos_offer_item_rules")))
		claimed.update(_engine_rule_claims(item))
	if not claimed:
		return {}, invoice_claims

	rows = frappe.get_all(
		"Pricing Rule",
		filters={"name": ["in", sorted(claimed)], "disable": 0},
		fields=["name", "apply_on"],
	)
	return {row.name: row.apply_on for row in rows}, invoice_claims


def _item_is_offer_attributed(item, verified_rules):
	"""True when the item's discount is rule-driven: its per-row stash or
	erpnext's ``pricing_rules`` marker claims at least one verified, enabled
	Pricing Rule (R2)."""
	claimed = set(parse_applied_offer_rules(item.get("pos_offer_item_rules")))
	claimed.update(_engine_rule_claims(item))
	return any(name in verified_rules for name in claimed)


def invoice_has_manual_discount(doc):
	"""True when the invoice discounts anything manually: the header additional
	discount, or any item row that is not offer-attributed.

	Offer-driven discounts (POS Offer promotions, computed server-side) are
	exempt: the header only when the invoice-level stash carries a verified
	applied rule with ``apply_on == "Transaction"`` (R3 — the stash is the
	item-derived rules plus the client-relayed transaction rule names, merged
	by update_invoice), an item when its own stash carries a verified rule
	(R2).
	"""
	verified_rules, invoice_claims = _verified_applied_rules(doc)

	if flt(doc.get("discount_amount") or 0) > 0 or flt(doc.get("additional_discount_percentage") or 0) > 0:
		has_transaction_rule = any(
			verified_rules.get(name) == "Transaction" for name in invoice_claims
		)
		if not has_transaction_rule:
			return True

	return any(
		_item_has_discount(item) and not _item_is_offer_attributed(item, verified_rules)
		for item in (doc.get("items") or [])
	)


# ==========================================================================
# Code validation
# ==========================================================================


def _code_usability_error(row, company, companies, today=None):
	"""None when the code row is usable for `company` right now; otherwise the
	reason as a user-facing message.

	`row` carries the parent fields (status, validity window, company_scope);
	`companies` is the code's scoped outlet list (None when the scope does not
	need it). `today` is injectable for callers with their own clock.
	"""
	code = row.get("code") or row.get("name")
	if row.get("status") != "Active":
		return _("Discount code {0} has been disabled by head office.").format(code)

	today = getdate(today or frappe.utils.today())
	if row.get("valid_from") and today < getdate(row.valid_from):
		return _("Discount code {0} is not valid yet (starts on {1}).").format(code, row.valid_from)
	if row.get("valid_upto") and today > getdate(row.valid_upto):
		return _("Discount code {0} has expired (was valid until {1}).").format(code, row.valid_upto)

	scope = row.get("company_scope") or ""
	if not company or scope not in _SCOPED_MODES:
		return None
	outlets = set(companies or [])
	if scope == "Selected Outlets":
		allowed = company in outlets
	else:
		allowed = company not in outlets
	if not allowed:
		return _("Discount code {0} is not valid for company {1}.").format(code, company)
	return None


def _scoped_outlets(code_name):
	"""Outlet names listed on the code's companies child table."""
	return frappe.get_all(
		CODE_COMPANY_DOCTYPE,
		filters={"parenttype": CODE_DOCTYPE, "parent": code_name},
		pluck="company",
	)


def validate_code(code_value, company):
	"""Read-only code validation. Returns the code document name; raises a
	precise ValidationError on any problem."""
	code_value = (code_value or "").strip().upper()
	if not code_value:
		frappe.throw(_("A discount code from head office is required for this discount."))

	row = frappe.db.get_value(
		CODE_DOCTYPE,
		{"code": code_value},
		["name", "code", "status", "valid_from", "valid_upto", "company_scope"],
		as_dict=True,
	)
	if not row:
		frappe.throw(_("Discount code {0} is not valid.").format(code_value))

	# Child rows are only queried when the scope actually consults them.
	companies = None
	if company and (row.company_scope or "") in _SCOPED_MODES:
		companies = _scoped_outlets(row.name)

	error = _code_usability_error(row, company, companies)
	if error:
		frappe.throw(error)

	return row.name


# ==========================================================================
# doc_events (Sales Invoice)
# ==========================================================================


def validate_invoice_discounts(doc, method=None):
	"""Sales Invoice validate hook — the hard gate (draft save and submit)."""
	if not doc.get("is_pos") or doc.get("is_return"):
		return
	if not invoice_has_manual_discount(doc):
		return

	validate_code(doc.get("discount_confirmation_code"), doc.get("company"))


def record_code_usage_on_submit(doc, method=None):
	"""Sales Invoice on_submit hook — re-check the code under a row lock and
	stamp the usage audit fields on it."""
	if not doc.get("is_pos") or doc.get("is_return"):
		return
	code_value = (doc.get("discount_confirmation_code") or "").strip().upper()
	if not code_value or not invoice_has_manual_discount(doc):
		return

	code_name = validate_code(code_value, doc.get("company"))

	# Serialize concurrent submits on the same code so used_count cannot lose
	# increments; the audit update lives in the submit transaction and rolls
	# back with it.
	row = frappe.db.get_value(
		CODE_DOCTYPE,
		code_name,
		["name", "code", "status", "valid_from", "valid_upto", "company_scope", "used_count"],
		as_dict=True,
		for_update=True,
	)
	# Re-verify full usability under the lock — status, validity window and
	# outlet scope may all have changed between the draft-time check and the
	# submit (mirror of the old status/company re-check).
	companies = None
	if doc.get("company") and (row.company_scope or "") in _SCOPED_MODES:
		companies = _scoped_outlets(row.name)
	error = _code_usability_error(row, doc.get("company"), companies)
	if error:
		frappe.throw(error)

	frappe.db.set_value(
		CODE_DOCTYPE,
		code_name,
		{
			"used_count": cint(row.used_count) + 1,
			"last_used_by": frappe.session.user,
			"last_used_in_invoice": doc.get("name"),
			"last_used_on": now_datetime(),
		},
		update_modified=False,
	)
