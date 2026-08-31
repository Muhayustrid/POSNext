"""Pricing domain for Dynamic Promotion (Task 3).

Pure, no writes. Computes quote from master + choices.
No whitelisted HTTP surface.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt


def quote(promotion, choices, context):
	"""Validate choices and compute total price + row descriptors.

	Args:
		promotion: Promotion doc or doc name.
		choices: list of dicts. Supported shapes (flexible):
			- [{"choice_group_key": "grp1", "options": [{"option_id": "opt1", "qty": 2}]}]
			- [{"group_key": "grp1", "picks": [{"option_row": "opt1", "qty": 2}]}]
			- [{"group": "grp1", "options": [{"option": "opt1", "qty": 2}]}]
		context: dict or object with ``warehouse`` attribute/key.

	Returns:
		dict with promotion, parent_item, base_price, total_price, currency,
		max_instances_per_invoice, parent_row, component_rows, choices_summary.

	Raises:
		frappe.ValidationError: on any choice validation failure or negative total.
	"""
	if isinstance(promotion, str):
		if not frappe.db.exists("Promotion", promotion):
			frappe.throw(_("Promotion {0} does not exist").format(promotion), frappe.ValidationError)
		promotion = frappe.get_doc("Promotion", promotion)

	# Resolve warehouse from context
	if isinstance(context, dict):
		warehouse = context.get("warehouse")
	else:
		warehouse = getattr(context, "warehouse", None)
		if warehouse is None and hasattr(context, "get"):
			try:
				warehouse = context.get("warehouse")
			except Exception:
				warehouse = None

	if not warehouse:
		frappe.throw(_("Pricing context warehouse is required"), frappe.ValidationError)

	# Normalize choices
	normalized = _normalize_choices(choices or [])

	# Build lookup maps
	groups_by_key = {}
	for g in promotion.choice_groups or []:
		groups_by_key[str(g.group_key)] = g

	options_by_name = {}
	for opt in promotion.options or []:
		options_by_name[str(opt.name)] = opt

	# Validate: every promotion choice group must be accounted for
	# (MVP: all groups are required with pick_count >= 1)
	if groups_by_key:
		provided_keys = set()
		for grp in normalized:
			gk = grp["group_key"]
			if not gk:
				frappe.throw(_("Choice group key is required in choices"), frappe.ValidationError)
			if gk not in groups_by_key:
				frappe.throw(
					_("Choice group {0} does not belong to Promotion {1}").format(gk, promotion.name),
					frappe.ValidationError,
				)
			if gk in provided_keys:
				frappe.throw(_("Duplicate choice group {0} in choices").format(gk), frappe.ValidationError)
			provided_keys.add(gk)

		# Ensure every promotion group is present
		for gk in groups_by_key:
			if gk not in provided_keys:
				frappe.throw(_("Missing choices for group {0}").format(gk), frappe.ValidationError)

	# Validate each group's picks
	choices_summary = []
	total_price = flt(promotion.base_price)
	component_rows = []

	# Fixed components first
	for comp in promotion.components or []:
		component_rows.append(
			{
				"item_code": comp.item_code,
				"qty": flt(comp.qty),
				"rate": 0.0,
				"amount": 0.0,
				"is_free_item": 1,
				"warehouse": warehouse,
				"role": "Promotion Component",
			}
		)

	for grp_entry in normalized:
		gk = grp_entry["group_key"]
		group_doc = groups_by_key[gk]
		pick_count = int(group_doc.pick_count or 0)
		# Repeats gate (design D3 enhancement): default off means the group is
		# "pick-N-distinct" — no option may be chosen more than once. On means
		# "pick-N-any", capped by each option's max_per_option.
		allow_repeats = cint(group_doc.allow_repeats or 0)

		picks = grp_entry.get("picks") or []
		if not picks:
			frappe.throw(
				_("Choice group {0} requires {1} picks").format(gk, pick_count), frappe.ValidationError
			)

		# Sum qty check
		total_picked = 0
		for pick in picks:
			qty = pick.get("qty")
			if qty is None:
				frappe.throw(_("Option quantity is required in group {0}").format(gk), frappe.ValidationError)
			# Must be positive integer
			try:
				qty_val = int(qty) if isinstance(qty, (int, float)) else int(str(qty).strip())
				# Check float that is not integer
				if flt(qty) != int(flt(qty)):
					frappe.throw(
						_("Option qty must be a positive integer in group {0}").format(gk),
						frappe.ValidationError,
					)
			except (ValueError, TypeError, AttributeError):
				frappe.throw(
					_("Option qty must be a positive integer in group {0}").format(gk),
					frappe.ValidationError,
				)
			if qty_val <= 0:
				frappe.throw(
					_("Option qty must be greater than zero in group {0}").format(gk),
					frappe.ValidationError,
				)
			total_picked += qty_val

		if total_picked != pick_count:
			if total_picked < pick_count:
				frappe.throw(
					_("Choice group {0} requires {1} picks, got {2} (under-pick)").format(
						gk, pick_count, total_picked
					),
					frappe.ValidationError,
				)
			else:
				frappe.throw(
					_("Choice group {0} requires {1} picks, got {2} (over-pick)").format(
						gk, pick_count, total_picked
					),
					frappe.ValidationError,
				)

		# Repeats semantics (design D3): when allow_repeats is off the group is
		# pick-N-distinct. Aggregate by option so a crafted client cannot slip a
		# repeated pick past as two single-unit rows.
		if not allow_repeats:
			per_option_qty: dict[str, int] = {}
			for pick in picks:
				pick_name = str(pick.get("option_row") or pick.get("option_id") or pick.get("option") or "").strip()
				if pick_name:
					per_option_qty[pick_name] = per_option_qty.get(pick_name, 0) + int(pick.get("qty"))
			for opt_name, total_qty in per_option_qty.items():
				if total_qty > 1:
					frappe.throw(
						_(
							"Choice group {0} does not allow repeats: option {1}"
							" was picked {2} times but each option may be picked at most once"
						).format(gk, opt_name, total_qty),
						frappe.ValidationError,
					)

		# Validate each option
		for pick in picks:
			opt_name = pick.get("option_row") or pick.get("option_id") or pick.get("option") or ""
			opt_name = str(opt_name).strip()
			if not opt_name:
				frappe.throw(_("Option row is required in group {0}").format(gk), frappe.ValidationError)
			opt_doc = options_by_name.get(opt_name)
			if not opt_doc:
				frappe.throw(
					_("Option {0} not found in Promotion {1}").format(opt_name, promotion.name),
					frappe.ValidationError,
				)
			if str(opt_doc.choice_group_key) != str(gk):
				frappe.throw(
					_("Option {0} does not belong to group {1}").format(opt_name, gk),
					frappe.ValidationError,
				)
			qty_val = int(pick.get("qty"))

			# max_per_option check
			max_per = int(getattr(opt_doc, "max_per_option", 0) or 0)
			if max_per > 0 and qty_val > max_per:
				frappe.throw(
					_("Option {0} qty {1} exceeds max_per_option {2}").format(opt_name, qty_val, max_per),
					frappe.ValidationError,
				)

			# Accumulate price
			adj = flt(getattr(opt_doc, "price_adjustment", 0))
			total_price += adj * flt(qty_val)

			# Component row for this option
			component_rows.append(
				{
					"item_code": opt_doc.item_code,
					"qty": flt(qty_val),
					"rate": 0.0,
					"amount": 0.0,
					"is_free_item": 1,
					"warehouse": warehouse,
					"role": "Promotion Component",
				}
			)

		choices_summary.append(
			{
				"group_key": gk,
				"label": getattr(group_doc, "label", ""),
				"pick_count": pick_count,
				"picks": picks,
			}
		)

	if total_price < 0:
		frappe.throw(_("Total price must not be negative"), frappe.ValidationError)

	parent_row = {
		"item_code": promotion.parent_item,
		"qty": 1,
		"rate": flt(total_price),
		"amount": flt(total_price),
		"is_free_item": 0,
		"warehouse": warehouse,
		"role": "Promotion Parent",
	}

	return {
		"promotion": promotion.name,
		"parent_item": promotion.parent_item,
		"base_price": flt(promotion.base_price),
		"total_price": flt(total_price),
		"currency": promotion.currency,
		"max_instances_per_invoice": int(getattr(promotion, "max_instances_per_invoice", 0) or 0),
		"parent_row": parent_row,
		"component_rows": component_rows,
		"choices_summary": choices_summary,
	}


def _normalize_choices(choices):
	"""Normalize flexible choice structures to canonical form.

	Canonical: [{"group_key": str, "picks": [{"option_row": str, "qty": int}]}]
	"""
	if not choices:
		return []

	normalized = []
	for entry in choices:
		if not isinstance(entry, dict):
			frappe.throw(_("Each choice entry must be a dict"), frappe.ValidationError)

		gk = entry.get("choice_group_key") or entry.get("group_key") or entry.get("group") or ""
		gk = str(gk).strip() if gk else ""

		# Options list: supports "options", "picks", "selections"
		raw_picks = entry.get("options")
		if raw_picks is None:
			raw_picks = entry.get("picks")
		if raw_picks is None:
			raw_picks = entry.get("selections")
		if raw_picks is None:
			raw_picks = []

		if not isinstance(raw_picks, list):
			frappe.throw(_("Options must be a list in group {0}").format(gk), frappe.ValidationError)

		picks = []
		for p in raw_picks:
			if not isinstance(p, dict):
				frappe.throw(
					_("Each option entry must be a dict in group {0}").format(gk), frappe.ValidationError
				)
			opt_id = p.get("option_id") or p.get("option_row") or p.get("option") or ""
			opt_id = str(opt_id).strip() if opt_id else ""
			qty = p.get("qty")
			picks.append({"option_row": opt_id, "qty": qty, "option_id": opt_id, "option": opt_id})

		normalized.append({"group_key": gk, "picks": picks, "options": picks})

	return normalized
