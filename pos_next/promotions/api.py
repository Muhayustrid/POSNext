"""Public Python domain API contracts for Dynamic Promotion (Task 3).

Server-authoritative Python-only contract for consumption by the future Mobile POS facade
or Desk POS.

CRITICAL INVARIANT: ZERO @frappe.whitelist() decorators anywhere in this package.
No direct Mobile POS HTTP surface.
"""

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from pos_next.promotions import eligibility, pricing


def available_promotions(pos_profile, on_date=None, search_term=None):
	"""Return list of eligible promotion summaries for the given POS Profile.

	Args:
		pos_profile: POS Profile name (str) or doc/dict.
		on_date: Date string/date/datetime; defaults to today.
		search_term: Optional text filter on promotion_name or parent_item.

	Returns:
		List[dict]: summary dicts for each eligible promotion:
			- promotion (name)
			- promotion_name
			- parent_item
			- base_price
			- currency
			- max_instances_per_invoice
			- valid_from
			- valid_to
	"""
	company, warehouse = eligibility.resolve_outlet_context(pos_profile)
	comp_currency = frappe.get_cached_value("Company", company, "default_currency")
	date_val = on_date or nowdate()

	filters = {"enabled": 1}
	if comp_currency:
		filters["currency"] = comp_currency

	candidate_names = frappe.get_all(
		"Promotion", filters=filters, pluck="name", order_by="promotion_name asc"
	)

	eligible_list = []
	for name in candidate_names:
		doc = frappe.get_doc("Promotion", name)

		# Search term filter if provided
		if search_term:
			st = str(search_term).lower().strip()
			p_name = str(doc.promotion_name or "").lower()
			p_item = str(doc.parent_item or "").lower()
			if st not in p_name and st not in p_item:
				continue

		is_eligible, _ = eligibility.check(doc, company, warehouse, on_date=date_val, currency=comp_currency)
		if is_eligible:
			eligible_list.append(
				{
					"promotion": doc.name,
					"promotion_name": doc.promotion_name,
					"parent_item": doc.parent_item,
					"base_price": flt(doc.base_price),
					"currency": doc.currency,
					"max_instances_per_invoice": int(getattr(doc, "max_instances_per_invoice", 0) or 0),
					"valid_from": str(doc.valid_from) if doc.valid_from else None,
					"valid_to": str(doc.valid_to) if doc.valid_to else None,
				}
			)

	return eligible_list


def promotion_detail(promotion_name, pos_profile=None, on_date=None):
	"""Return detail descriptor of a Promotion master, including components, choice groups, and options.

	Args:
		promotion_name: Promotion name (str).
		pos_profile: Optional POS Profile to evaluate eligibility.
		on_date: Optional evaluation date.

	Returns:
		dict with full promotion structure, choices, components, and optional eligibility result.

	Raises:
		frappe.ValidationError: if promotion does not exist.
	"""
	if not promotion_name or not frappe.db.exists("Promotion", promotion_name):
		frappe.throw(_("Promotion {0} does not exist").format(promotion_name), frappe.ValidationError)

	doc = frappe.get_doc("Promotion", promotion_name)

	eligibility_info = None
	if pos_profile:
		company, warehouse = eligibility.resolve_outlet_context(pos_profile)
		comp_currency = frappe.get_cached_value("Company", company, "default_currency")
		is_el, reason = eligibility.check(doc, company, warehouse, on_date=on_date, currency=comp_currency)
		eligibility_info = {"is_eligible": is_el, "reason": reason}

	# Build options by choice group
	options_by_group = {}
	for opt in doc.options or []:
		gk = opt.choice_group_key
		item_doc = frappe.db.exists("Item", opt.item_code)
		item_name = frappe.get_cached_value("Item", opt.item_code, "item_name") if item_doc else ""
		options_by_group.setdefault(gk, []).append(
			{
				"name": opt.name,
				"option_row": opt.name,
				"choice_group_key": gk,
				"item_code": opt.item_code,
				"item_name": item_name,
				"price_adjustment": flt(opt.price_adjustment),
				"max_per_option": int(getattr(opt, "max_per_option", 0) or 0),
			}
		)

	choice_groups = []
	for grp in doc.choice_groups or []:
		gk = grp.group_key
		choice_groups.append(
			{
				"group_key": gk,
				"label": grp.label,
				"pick_count": int(grp.pick_count or 0),
				"options": options_by_group.get(gk, []),
			}
		)

	components = []
	for comp in doc.components or []:
		item_doc = frappe.db.exists("Item", comp.item_code)
		item_name = frappe.get_cached_value("Item", comp.item_code, "item_name") if item_doc else ""
		components.append(
			{
				"name": comp.name,
				"item_code": comp.item_code,
				"item_name": item_name,
				"qty": flt(comp.qty),
			}
		)

	return {
		"promotion": doc.name,
		"promotion_name": doc.promotion_name,
		"root_company": doc.root_company,
		"parent_item": doc.parent_item,
		"base_price": flt(doc.base_price),
		"currency": doc.currency,
		"enabled": doc.enabled,
		"valid_from": str(doc.valid_from) if doc.valid_from else None,
		"valid_to": str(doc.valid_to) if doc.valid_to else None,
		"max_instances_per_invoice": int(getattr(doc, "max_instances_per_invoice", 0) or 0),
		"components": components,
		"choice_groups": choice_groups,
		"eligibility": eligibility_info,
	}


def quote_promotion(promotion_name, choices, pos_profile):
	"""Server quote calculation for a given promotion and choices within a POS Profile context.

	Args:
		promotion_name: Promotion name (str) or doc.
		choices: list of choice group selections.
		pos_profile: POS Profile name (str) or doc.

	Returns:
		dict: quote calculation result (pricing.quote output).

	Raises:
		frappe.ValidationError: if pos_profile context cannot be resolved, or if promotion
			is not eligible for the outlet context, or on choice validation errors.
	"""
	company, warehouse = eligibility.resolve_outlet_context(pos_profile)
	comp_currency = frappe.get_cached_value("Company", company, "default_currency")

	if isinstance(promotion_name, str):
		if not frappe.db.exists("Promotion", promotion_name):
			frappe.throw(_("Promotion {0} does not exist").format(promotion_name), frappe.ValidationError)
		promo_doc = frappe.get_doc("Promotion", promotion_name)
	else:
		promo_doc = promotion_name

	is_eligible, reason = eligibility.check(
		promo_doc, company, warehouse, on_date=nowdate(), currency=comp_currency
	)
	if not is_eligible:
		frappe.throw(
			_("Promotion {0} is not eligible: {1}").format(promo_doc.name, reason), frappe.ValidationError
		)

	context = {"company": company, "warehouse": warehouse}
	return pricing.quote(promo_doc, choices, context)
