# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""
POS Offer sync engine: POS Offer is the source of truth, the Promotional
Scheme + Pricing Rules are generated artifacts.

ERPNext's scheme→rule generator cannot express per-company rules (it stamps
the scheme's single company on every generated rule and coerces rules back to
it on every scheme save). So the scheme is kept as a *pure container*:

- the scheme header is created once via ``insert()`` with NO slabs (the
  generator then creates nothing) and afterwards updated with
  ``frappe.db.set_value`` — never ``scheme.save()``;
- slab / eligibility child rows are inserted/deleted directly as child docs
  (child inserts do not trigger the parent's ``on_update``);
- one Pricing Rule per enabled company is created/updated/deleted directly.

Ownership guards (Pricing Rule + Promotional Scheme ``validate``/``on_trash``
doc_events) block manual edits to managed artifacts unless
``frappe.flags.in_pos_offer_sync`` is held by this engine.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, nowdate

OFFER_DOCTYPE = "POS Offer"
SCHEME_DOCTYPE = "Promotional Scheme"
RULE_DOCTYPE = "Pricing Rule"
SYNC_FLAG = "in_pos_offer_sync"

# apply_on → (scheme/rule child field, physical child doctype, target column)
ELIGIBILITY = {
	"Item Code": ("items", "Pricing Rule Item Code", "item_code"),
	"Item Group": ("item_groups", "Pricing Rule Item Group", "item_group"),
	"Brand": ("brands", "Pricing Rule Brand", "brand"),
}
SLAB_DOCTYPES = {
	"price": "Promotional Scheme Price Discount",
	"product": "Promotional Scheme Product Discount",
}


class _SyncFlag:
	"""Hold the request-wide guard bypass for the duration of a sync."""

	def __enter__(self):
		self._previous = frappe.flags.get(SYNC_FLAG)
		frappe.flags[SYNC_FLAG] = True

	def __exit__(self, *exc):
		frappe.flags[SYNC_FLAG] = self._previous


# ==========================================================================
# Entries (POS Offer controller)
# ==========================================================================


def sync_offer(offer):
	"""Converge scheme + rules to the offer (runs on insert and every save)."""
	with _SyncFlag():
		scheme_name = _upsert_scheme(offer)
		slab_name = _sync_scheme_children(offer, scheme_name)
		_reconcile_rules(offer, scheme_name, slab_name)


def handle_offer_trash(offer, method=None):
	"""Offer deleted: drop its rules; disable + rename the scheme (history)."""
	with _SyncFlag():
		scheme_name = frappe.db.get_value(SCHEME_DOCTYPE, {"pos_offer": offer.name}, "name")
		if not scheme_name:
			return
		for rule_name in frappe.get_all(RULE_DOCTYPE, {"promotional_scheme": scheme_name}, pluck="name"):
			frappe.delete_doc(RULE_DOCTYPE, rule_name, ignore_permissions=True)
		frappe.db.set_value(
			SCHEME_DOCTYPE, scheme_name, {"disable": 1, "pos_offer": None}, update_modified=False
		)
		new_name = f"{scheme_name} (DELETED)"
		if frappe.db.exists(SCHEME_DOCTYPE, new_name):
			new_name = f"{scheme_name} (DELETED {nowdate()})"
		frappe.rename_doc(SCHEME_DOCTYPE, scheme_name, new_name, ignore_permissions=True)


# ==========================================================================
# Ownership guards (doc_events)
# ==========================================================================


def guard_pricing_rule(doc, method=None):
	"""Block direct edits/deletes of rules managed by a POS Offer."""
	if frappe.flags.get(SYNC_FLAG):
		return
	scheme = doc.get("promotional_scheme")
	if not scheme:
		return
	owner = frappe.db.get_value(SCHEME_DOCTYPE, scheme, "pos_offer")
	if owner:
		frappe.throw(
			_("Pricing Rule {0} is managed by POS Offer {1} — edit the offer instead.").format(
				doc.get("title") or doc.name, owner
			)
		)


def guard_promotional_scheme(doc, method=None):
	"""Block direct edits/deletes of schemes managed by a POS Offer."""
	if frappe.flags.get(SYNC_FLAG):
		return
	if doc.get("pos_offer"):
		frappe.throw(
			_("Promotional Scheme {0} is managed by POS Offer {1} — edit the offer instead.").format(
				doc.name, doc.pos_offer
			)
		)


# ==========================================================================
# Scheme
# ==========================================================================


def _upsert_scheme(offer):
	"""Create the container scheme once; afterwards only touch header via db."""
	scheme_name = frappe.db.get_value(SCHEME_DOCTYPE, {"pos_offer": offer.name}, "name")
	values = {
		"apply_on": offer.apply_on,
		"selling": 1,
		"buying": 0,
		"valid_from": offer.valid_from,
		"valid_upto": offer.valid_to,
		"disable": 0 if cint(offer.enabled) else 1,
	}
	if scheme_name:
		frappe.db.set_value(SCHEME_DOCTYPE, scheme_name, values, update_modified=True)
		return scheme_name

	scheme = frappe.new_doc(SCHEME_DOCTYPE)
	scheme.name = offer.title
	scheme.pos_offer = offer.name
	scheme.update(values)
	# insert with no slabs: ERPNext's on_update rule generator creates nothing
	scheme.insert(ignore_permissions=True)
	return scheme.name


def _sync_scheme_children(offer, scheme_name):
	"""Rebuild eligibility rows + the single discount slab. Returns slab row name."""
	for child_dt in SLAB_DOCTYPES.values():
		frappe.db.delete(child_dt, {"parent": scheme_name, "parenttype": SCHEME_DOCTYPE})
	for _field, child_dt, _col in ELIGIBILITY.values():
		frappe.db.delete(child_dt, {"parent": scheme_name, "parenttype": SCHEME_DOCTYPE})

	if offer.apply_on in ELIGIBILITY:
		field, child_dt, column = ELIGIBILITY[offer.apply_on]
		for row in offer.get("targets") or []:
			if row.get(column):
				frappe.get_doc(
					{
						"doctype": child_dt,
						"parent": scheme_name,
						"parenttype": SCHEME_DOCTYPE,
						"parentfield": field,
						column: row.get(column),
					}
				).insert(ignore_permissions=True)

	if offer.offer_type == "Free Item":
		slab = frappe.get_doc(
			{
				"doctype": SLAB_DOCTYPES["product"],
				"parent": scheme_name,
				"parenttype": SCHEME_DOCTYPE,
				"parentfield": "product_discount_slabs",
				"rule_description": offer.title,
				"min_qty": flt(offer.min_qty or 0),
				"min_amount": flt(offer.min_amt or 0),
				"free_item": offer.free_item,
				"free_qty": flt(offer.free_qty or 1),
			}
		).insert(ignore_permissions=True)
		return slab.name

	slab = frappe.get_doc(
		{
			"doctype": SLAB_DOCTYPES["price"],
			"parent": scheme_name,
			"parenttype": SCHEME_DOCTYPE,
			"parentfield": "price_discount_slabs",
			"rule_description": offer.title,
			"min_qty": flt(offer.min_qty or 0),
			"min_amount": flt(offer.min_amt or 0),
			"rate_or_discount": offer.offer_type,
			"discount_percentage": flt(offer.discount_percentage or 0)
			if offer.offer_type == "Discount Percentage"
			else 0,
			"discount_amount": flt(offer.discount_amount or 0)
			if offer.offer_type == "Discount Amount"
			else 0,
		}
	).insert(ignore_permissions=True)
	return slab.name


# ==========================================================================
# Rules
# ==========================================================================


def _reconcile_rules(offer, scheme_name, slab_name):
	desired = {row.company: cint(row.enabled) for row in offer.get("companies") or []}
	existing = frappe.get_all(RULE_DOCTYPE, {"promotional_scheme": scheme_name}, ["name", "company"])
	by_company = {row.company: row.name for row in existing if row.company}

	keep = set()
	for company, company_enabled in desired.items():
		rule_name = by_company.get(company)
		if rule_name:
			keep.add(_update_rule(offer, scheme_name, rule_name, company, company_enabled, slab_name))
		else:
			keep.add(_create_rule(offer, scheme_name, company, company_enabled, slab_name))

	for row in existing:
		if row.company and row.name not in keep:
			# company removed from the offer — keep the rule for history, disabled
			frappe.db.set_value(RULE_DOCTYPE, row.name, {"disable": 1}, update_modified=False)


def _rule_values(offer, scheme_name, company, company_enabled, slab_name):
	is_product = offer.offer_type == "Free Item"
	return {
		"title": offer.title,
		"company": company,
		"apply_on": offer.apply_on,
		"selling": 1,
		"buying": 0,
		"price_or_product_discount": "Product" if is_product else "Price",
		"rate_or_discount": None if is_product else offer.offer_type,
		"discount_percentage": flt(offer.discount_percentage or 0)
		if not is_product and offer.offer_type == "Discount Percentage"
		else 0,
		"discount_amount": flt(offer.discount_amount or 0)
		if not is_product and offer.offer_type == "Discount Amount"
		else 0,
		"free_item": offer.free_item if is_product else None,
		"free_qty": flt(offer.free_qty or 1) if is_product else 0,
		"min_qty": flt(offer.min_qty or 0),
		"min_amt": flt(offer.min_amt or 0),
		"valid_from": offer.valid_from,
		"valid_upto": offer.valid_to,
		"disable": 0 if (cint(offer.enabled) and company_enabled) else 1,
		"promotional_scheme": scheme_name,
		"promotional_scheme_id": slab_name,
		"pos_offer_max_discount": flt(offer.max_discount_amount or 0),
		"coupon_code_based": 0,
	}


def _create_rule(offer, scheme_name, company, company_enabled, slab_name):
	rule = frappe.new_doc(RULE_DOCTYPE)
	rule.update(_rule_values(offer, scheme_name, company, company_enabled, slab_name))
	_append_rule_targets(rule, offer)
	rule.insert(ignore_permissions=True)
	return rule.name


def _update_rule(offer, scheme_name, rule_name, company, company_enabled, slab_name):
	rule = frappe.get_doc(RULE_DOCTYPE, rule_name)
	rule.update(_rule_values(offer, scheme_name, company, company_enabled, slab_name))
	for field in ("items", "item_groups", "brands"):
		rule.set(field, [])
	_append_rule_targets(rule, offer)
	rule.save(ignore_permissions=True)
	return rule.name


def _append_rule_targets(rule, offer):
	if offer.apply_on not in ELIGIBILITY:
		return
	field, _child_dt, column = ELIGIBILITY[offer.apply_on]
	for row in offer.get("targets") or []:
		if row.get(column):
			rule.append(field, {column: row.get(column)})
