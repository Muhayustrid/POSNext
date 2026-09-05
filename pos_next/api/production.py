# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import flt
from erpnext.stock.doctype.batch.batch import get_batch_qty


def _resolve_profile(pos_profile):
	"""Company + warehouse come from the POS Profile server-side, never the client."""
	if not pos_profile:
		frappe.throw(_("POS Profile is required"))
	company, warehouse = frappe.db.get_value("POS Profile", pos_profile, ["company", "warehouse"])
	if not company:
		frappe.throw(_("POS Profile {0} has no company").format(pos_profile))
	if not warehouse:
		warehouse = frappe.db.get_value(
			"Warehouse", {"company": company, "is_group": 0, "disabled": 0}, "name"
		)
		if not warehouse:
			frappe.throw(_("No warehouse found for company {0}").format(company))
	return company, warehouse


def _item_flags(item_codes):
	"""item_code -> {item_name, stock_uom, has_batch_no} for the given codes."""
	if not item_codes:
		return {}
	rows = frappe.get_all(
		"Item",
		filters={"name": ["in", item_codes]},
		fields=["name", "item_name", "stock_uom", "has_batch_no"],
	)
	return {r.name: r for r in rows}


def _stock_qty(item_code, warehouse):
	bin_qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")
	return flt(bin_qty)


@frappe.whitelist()
def get_production_recipes(pos_profile):
	"""Enabled recipes for the profile's company, with stock/batch info per material."""
	try:
		company, warehouse = _resolve_profile(pos_profile)

		recipes = frappe.get_all(
			"POS Production Recipe",
			filters={"disabled": 0},
			fields=["name", "recipe_name", "production_item", "output_qty"],
			order_by="recipe_name",
		)
		if not recipes:
			return {"pos_profile": pos_profile, "company": company, "warehouse": warehouse, "recipes": []}

		# multi-company scope: keep recipes with an enabled company row for this company
		scoped = set(
			frappe.get_all(
				"POS Production Recipe Company",
				filters={"parenttype": "POS Production Recipe", "company": company, "enabled": 1},
				pluck="parent",
			)
		)
		recipes = [r for r in recipes if r.name in scoped]

		item_rows = frappe.get_all(
			"POS Production Recipe Item",
			filters={"parenttype": "POS Production Recipe", "parent": ["in", [r.name for r in recipes]]},
			fields=["parent", "item_code", "qty"],
		)
		by_recipe = {}
		for row in item_rows:
			by_recipe.setdefault(row.parent, []).append(row)

		flags = _item_flags(
			[r.production_item for r in recipes] + [r.item_code for rows in by_recipe.values() for r in rows]
		)

		out = []
		for r in recipes:
			fg = flags.get(r.production_item, frappe._dict())
			items = []
			for row in by_recipe.get(r.name, []):
				info = flags.get(row.item_code, frappe._dict())
				items.append(
					{
						"item_code": row.item_code,
						"item_name": info.item_name or row.item_code,
						"qty": flt(row.qty),
						"stock_uom": info.stock_uom or "",
						"has_batch_no": bool(info.has_batch_no),
						"available_qty": _stock_qty(row.item_code, warehouse),
						"batches": _batch_list(row.item_code, warehouse) if info.has_batch_no else [],
					}
				)
			out.append(
				{
					"name": r.name,
					"recipe_name": r.recipe_name,
					"production_item": r.production_item,
					"production_item_name": fg.item_name or r.production_item,
					"output_qty": flt(r.output_qty),
					"fg_stock": _stock_qty(r.production_item, warehouse),
					"fg_has_batch_no": bool(fg.has_batch_no),
					"items": items,
				}
			)
		return {"pos_profile": pos_profile, "company": company, "warehouse": warehouse, "recipes": out}
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Production Recipes Error")
		frappe.throw(_("Error fetching production recipes: {0}").format(str(e)))


def _batch_list(item_code, warehouse):
	out = []
	for b in get_batch_qty(warehouse=warehouse, item_code=item_code) or []:
		if flt(b.qty) > 0:
			out.append(
				{
					"batch_no": b.batch_no,
					"qty": flt(b.qty),
					"expiry_date": frappe.db.get_value("Batch", b.batch_no, "expiry_date"),
				}
			)
	out.sort(key=lambda x: x["expiry_date"] or "9999-12-31")
	return out
