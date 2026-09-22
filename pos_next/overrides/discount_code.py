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
restricted. POS returns (is_return) are gated too — every refund needs a code,
discounts or not (same code pool as discounts).

Exemption: pre-approved discount channels need no code.
- POS Offer promotions: an item whose discount is offer-attributed (its
  `pos_offer_item_rules` stash carries a verified, enabled Pricing Rule —
  written server-side by pos_next.api.invoices.update_invoice) is not a manual
  discount; the same holds for the header additional discount when a verified
  applied rule with apply_on == "Transaction" fired (invoice-level
  `pos_applied_offer_rules`).
- ERPNext Pricing Rules / Promotional Schemes: erpnext's own engine stamps the
  applied rule names on each item row (`pricing_rules`, JSON list).

SEC-10: a claimed rule name — from the stash, the engine marker, or the
client-relayed list — exempts only when the rule is REAL for this document:
enabled, in scope (company, validity window, customer dimension), applicable
to the row it is claimed on, and its configured discount reconciles with the
discount the row/header actually gives. A relayed transaction rule joins the
server stash (update_invoice) only when it passes the same header check.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, now_datetime

from pos_next.api.settings_resolver import get_effective_pos_setting
from pos_next.overrides.pos_offer_usage import parse_applied_offer_rules
from pos_next.overrides.pricing_rule import MIN_MAX_OPTIONS

CODE_DOCTYPE = "POS Discount Confirmation Code"
CODE_COMPANY_DOCTYPE = "POS Discount Code Company"
# Scopes that consult the child outlet list; anything else is valid everywhere.
_SCOPED_MODES = ("Selected Outlets", "All Outlets Except")

# Pricing Rule fields every exemption/reconciliation decision reads (SEC-10):
# one batched query verifies claims instead of trusting the names.
_RULE_FIELDS = [
	"name",
	"apply_on",
	"company",
	"valid_from",
	"valid_upto",
	"applicable_for",
	"customer",
	"customer_group",
	"territory",
	"sales_partner",
	"campaign",
	"price_or_product_discount",
	"rate_or_discount",
	"rate",
	"discount_percentage",
	"discount_amount",
	"min_qty",
	"max_qty",
	"min_amt",
	"max_amt",
	"apply_discount_on_price",
	"min_or_max_discount_qty_limit",
	"pos_offer_max_discount",
]
# applicable_for value → the rule field and the matching doc field.
_SCOPE_FIELD_MAP = {
	"Customer": "customer",
	"Customer Group": "customer_group",
	"Territory": "territory",
	"Sales Partner": "sales_partner",
	"Campaign": "campaign",
}
# apply_on value → (child doctype, child value field, item row field).
_CHILD_APPLY_ON = {
	"Item Code": ("Pricing Rule Item Code", "item_code", "item_code"),
	"Item Group": ("Pricing Rule Item Group", "item_group", "item_group"),
	"Brand": ("Pricing Rule Brand", "brand", "brand"),
}
# Percentage-point slack for discount reconciliation: the row's rate is rounded
# to currency precision, so the reconstructed percentage inherits that noise.
_PCT_TOLERANCE = 0.5
# Money slack where values must match near-exactly (ERPNext stamps the doc with
# the rule's own figure and the honest apply_offers flow relays it unchanged).
_EXACT_TOLERANCE = 0.01


# ==========================================================================
# Discount detection
# ==========================================================================


def _item_has_discount(item):
	"""True when the item row carries a manual discount (incl. a rate edited
	below price_list_rate, which is a discount in disguise).

	SEC-04: detection reads the row's resolved prices, not the client's
	is_rate_manually_edited flag — update_invoice stamps the server's list
	price onto manually edited rows, so the gap itself is the evidence.
	"""
	if flt(item.get("discount_percentage") or 0) > 0 or flt(item.get("discount_amount") or 0) > 0:
		return True

	price_list_rate = flt(item.get("price_list_rate") or 0)
	rate = flt(item.get("rate") or 0)
	return price_list_rate > 0 and rate < price_list_rate


def _engine_rule_claims(item):
	"""Rule names claimed via erpnext's own marker (`pricing_rules` on the item
	row, JSON list — set by the pricing rule engine and by the POS apply-offers
	flow). Same JSON shape as the offer stash, so the same parser applies."""
	return parse_applied_offer_rules(item.get("pricing_rules"))


# ==========================================================================
# Claim verification (SEC-10)
# ==========================================================================


def _rule_in_doc_scope(rule, doc):
	"""Enabled is not enough: the rule must be in scope for THIS document —
	company (blank = all outlets), the validity window on the posting date,
	and the customer-ish dimension it applies for (a set value must match)."""
	if rule.get("company") and rule.get("company") != doc.get("company"):
		return False
	posting_date = getdate(doc.get("posting_date")) or getdate(frappe.utils.today())
	if rule.get("valid_from") and posting_date < getdate(rule.valid_from):
		return False
	if rule.get("valid_upto") and posting_date > getdate(rule.valid_upto):
		return False
	scope_field = _SCOPE_FIELD_MAP.get(rule.get("applicable_for"))
	if scope_field and rule.get(scope_field) and rule.get(scope_field) != doc.get(scope_field):
		return False
	return True


def _verified_applied_rules(doc):
	"""Verify the claimed applied Pricing Rule names in one query.

	A claimed name exempts only when its rule exists, is enabled, and is in
	scope for this document (see _rule_in_doc_scope). Item applicability and
	discount-value reconciliation are the callers' per-row concern.

	Returns ``(verified_rules, invoice_claims)``: ``verified_rules`` maps each
	in-scope claimed name to its Pricing Rule fields; ``invoice_claims`` is the
	set of names claimed on the invoice-level ``pos_applied_offer_rules``
	stash — the only source that can exempt the header discount (R3). Claims
	from item stashes and erpnext's ``pricing_rules`` marker are verified too
	(they can exempt an item), but never the header. Unknown, disabled or
	out-of-scope claimed rules confer no exemption.
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
		fields=_RULE_FIELDS,
	)
	return {row.get("name"): row for row in rows if _rule_in_doc_scope(row, doc)}, invoice_claims


def _item_scope_map(verified_rules):
	"""Applicable values per child-table-scoped claimed rule — one query per
	child doctype, only when such rules are actually claimed."""
	scoped = {}
	by_apply_on = {}
	for rule in verified_rules.values():
		if rule.get("apply_on") in _CHILD_APPLY_ON:
			by_apply_on.setdefault(rule.get("apply_on"), []).append(rule.get("name"))
	for apply_on, names in by_apply_on.items():
		doctype, value_field, _row_field = _CHILD_APPLY_ON[apply_on]
		for row in frappe.get_all(
			doctype,
			filters={"parent": ["in", names], "parenttype": "Pricing Rule"},
			fields=["parent", value_field],
		):
			scoped.setdefault(row.get("parent"), set()).add(row.get(value_field))
	return scoped


def _rule_applies_to_item(rule, item, scope_map):
	"""Rule relevance for one row: what the rule applies on (item code/group/
	brand — Transaction rules never attribute an item) and the row's qty/amount
	window."""
	apply_on = rule.get("apply_on")
	if apply_on == "Transaction":
		return False
	if apply_on in _CHILD_APPLY_ON:
		row_field = _CHILD_APPLY_ON[apply_on][2]
		if item.get(row_field) not in scope_map.get(rule.get("name"), set()):
			return False

	qty = flt(item.get("qty") or item.get("quantity") or 0)
	amount = flt(item.get("rate") or 0) * qty
	if flt(rule.get("min_qty") or 0) > 0 and qty < flt(rule.get("min_qty")):
		return False
	if flt(rule.get("max_qty") or 0) > 0 and qty > flt(rule.get("max_qty")):
		return False
	if flt(rule.get("min_amt") or 0) > 0 and amount < flt(rule.get("min_amt")):
		return False
	if flt(rule.get("max_amt") or 0) > 0 and amount > flt(rule.get("max_amt")):
		return False
	return True


def _row_discount_pct(item):
	"""The discount the row actually gives, read from the money gap between
	price_list_rate and rate — the customer-facing truth, whatever put it
	there. None when the row carries no resolvable list price."""
	price_list_rate = flt(item.get("price_list_rate") or 0)
	if price_list_rate <= 0:
		return None
	return (price_list_rate - flt(item.get("rate") or 0)) / price_list_rate * 100.0


def _expected_rule_pct(rule, item):
	"""Per-unit-equivalent discount percentage the rule itself would produce on
	this row — the only discount a claimed rule can legitimately excuse.
	None when the rule's discount cannot be expressed against the row."""
	price_list_rate = flt(item.get("price_list_rate") or 0)
	if price_list_rate <= 0:
		return None

	rate_or_discount = rule.get("rate_or_discount")
	if rate_or_discount == "Rate":
		pct = (price_list_rate - flt(rule.get("rate") or 0)) / price_list_rate * 100.0
	elif rate_or_discount == "Discount Percentage":
		pct = flt(rule.get("discount_percentage") or 0)
		cap = flt(rule.get("pos_offer_max_discount") or 0)
		if cap > 0:
			# A capped POS Offer percentage becomes a flat per-unit amount
			# (pos_next.overrides.pricing_rule._cap_percentage_discount).
			pct = min(pct, cap / price_list_rate * 100.0)
	elif rate_or_discount == "Discount Amount":
		pct = flt(rule.get("discount_amount") or 0) / price_list_rate * 100.0
	else:
		return None
	pct = max(pct, 0.0)

	if rule.get("apply_discount_on_price") in MIN_MAX_OPTIONS:
		# Min/Max discounts land blended across the winning line's quantity
		# (pos_next.overrides.pricing_rule._apply_discount).
		qty = flt(item.get("qty") or item.get("quantity") or 0)
		if qty <= 0:
			return None
		limit = flt(rule.get("min_or_max_discount_qty_limit") or 0)
		eligible_qty = qty if limit <= 0 else min(limit, qty)
		pct = pct * eligible_qty / qty
	return pct


def _item_offer_matches(item, verified_rules, scope_map):
	"""True when the item's discount is fully explained by its claimed rules:
	at least one claimed, verified, applicable rule whose configured discount
	reconciles with the row's actual discount (stacked rules may sum)."""
	claimed = set(parse_applied_offer_rules(item.get("pos_offer_item_rules")))
	claimed.update(_engine_rule_claims(item))
	applicable = [
		verified_rules[name]
		for name in claimed
		if name in verified_rules and _rule_applies_to_item(verified_rules[name], item, scope_map)
	]
	if not applicable:
		return False

	actual_pct = _row_discount_pct(item)
	if actual_pct is None:
		return False
	expected_pcts = [pct for pct in (_expected_rule_pct(rule, item) for rule in applicable) if pct is not None]
	if not expected_pcts:
		return False
	if any(abs(actual_pct - pct) <= _PCT_TOLERANCE for pct in expected_pcts):
		return True
	return abs(actual_pct - sum(expected_pcts)) <= _PCT_TOLERANCE


def _header_rule_exempts(rule, doc):
	"""True when a verified Transaction rule actually produces this doc's
	header discount: Price-type (Product-type rules give free items, never a
	header discount), within the doc-level qty/amount window, and the header's
	additional_discount_percentage / discount_amount equal the rule's own
	configured figure."""
	if rule.get("price_or_product_discount") != "Price":
		return False

	total_qty = flt(doc.get("total_qty") or 0)
	total = flt(doc.get("total") or 0)
	if flt(rule.get("min_qty") or 0) > 0 and total_qty < flt(rule.get("min_qty")):
		return False
	if flt(rule.get("max_qty") or 0) > 0 and total_qty > flt(rule.get("max_qty")):
		return False
	if flt(rule.get("min_amt") or 0) > 0 and total < flt(rule.get("min_amt")):
		return False
	if flt(rule.get("max_amt") or 0) > 0 and total > flt(rule.get("max_amt")):
		return False

	pct = flt(doc.get("additional_discount_percentage") or 0)
	if rule.get("rate_or_discount") == "Discount Percentage":
		return pct > 0 and abs(pct - flt(rule.get("discount_percentage") or 0)) <= _EXACT_TOLERANCE
	if rule.get("rate_or_discount") == "Discount Amount":
		amount = flt(doc.get("discount_amount") or 0)
		return amount > 0 and abs(amount - flt(rule.get("discount_amount") or 0)) <= _EXACT_TOLERANCE
	return False


def verify_transaction_rule_names(rule_names, doc):
	"""Server-side filter for client-relayed Transaction rule names (SEC-10).

	update_invoice merges the apply_offers relay into the invoice-level offer
	stash; only names this function returns may join it: an enabled, in-scope
	(company, validity, customer dimension), Price-type ``apply_on ==
	"Transaction"`` Pricing Rule whose configured discount equals the doc's
	header discount. Item-level and free-item rules never need the relay —
	they ride item rows and are stashed from item.pricing_rules."""
	names = {name for name in (rule_names or []) if name}
	if not names:
		return []
	rows = frappe.get_all(
		"Pricing Rule",
		filters={"name": ["in", sorted(names)], "disable": 0, "apply_on": "Transaction"},
		fields=_RULE_FIELDS,
	)
	return sorted(
		row.get("name")
		for row in rows
		if _rule_in_doc_scope(row, doc) and _header_rule_exempts(row, doc)
	)


def invoice_has_manual_discount(doc):
	"""True when the invoice discounts anything manually: the header additional
	discount, or any item row whose discount is not fully explained by a
	verified, applicable, reconciling Pricing Rule claim.

	Offer-driven discounts (POS Offer promotions, computed server-side) are
	exempt: the header only when the invoice-level stash carries a verified
	Transaction rule that reconciles with the header discount (R3), an item
	when its own claims reconcile with the row discount (R2)."""
	verified_rules, invoice_claims = _verified_applied_rules(doc)

	if flt(doc.get("discount_amount") or 0) > 0 or flt(doc.get("additional_discount_percentage") or 0) > 0:
		has_transaction_rule = any(
			name in verified_rules and _header_rule_exempts(verified_rules[name], doc)
			for name in invoice_claims
		)
		if not has_transaction_rule:
			return True

	scope_map = _item_scope_map(verified_rules)
	return any(
		_item_has_discount(item) and not _item_offer_matches(item, verified_rules, scope_map)
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


def refund_code_required(pos_profile):
	"""Refund gate toggle from POS Settings (per profile, else global single).

	An unset value fails closed — the gate stays on until head office
	explicitly turns it off.
	"""
	if not pos_profile:
		return True
	value = get_effective_pos_setting(pos_profile, "require_refund_code")
	return True if value is None else bool(cint(value))


def validate_invoice_discounts(doc, method=None):
	"""Sales Invoice validate hook — the hard gate (draft save and submit)."""
	if doc.get("is_consolidated"):
		return
	if not doc.get("is_pos"):
		return
	if doc.get("is_return"):
		# Refund gate — every return needs a code when the profile requires one.
		if refund_code_required(doc.get("pos_profile")):
			validate_code(doc.get("discount_confirmation_code"), doc.get("company"))
		return
	if not invoice_has_manual_discount(doc):
		return

	validate_code(doc.get("discount_confirmation_code"), doc.get("company"))


def record_code_usage_on_submit(doc, method=None):
	"""Sales Invoice on_submit hook — re-check the code under a row lock and
	stamp the usage audit fields on it."""
	if doc.get("is_consolidated"):
		return
	if not doc.get("is_pos"):
		return
	code_value = (doc.get("discount_confirmation_code") or "").strip().upper()
	if not code_value:
		return
	# Returns audit whenever they carry a code and the profile's refund gate is
	# on; sales only when the code was actually needed for a discount.
	if doc.get("is_return"):
		if not refund_code_required(doc.get("pos_profile")):
			return
	elif not invoice_has_manual_discount(doc):
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
