# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
from uuid import uuid4

import frappe
from frappe import ValidationError, _
from frappe.utils import flt, getdate
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
		fields=["name", "item_name", "stock_uom", "has_batch_no", "disabled"],
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
					"expiry_date": b.expiry_date,
				}
			)
	out.sort(key=lambda x: x["expiry_date"] or getdate("9999-12-31"))
	return out


def _parse_items(items):
	if isinstance(items, str):
		items = json.loads(items)
	return [
		{"item_code": d["item_code"], "qty": flt(d.get("qty"))}
		for d in items
		if d.get("item_code")
	]


@frappe.whitelist()
def create_production(recipe, qty, items, pos_profile, batches=None):
	"""Consume materials and produce the recipe's item in one Manufacture Stock Entry."""
	try:
		company, warehouse = _resolve_profile(pos_profile)
		qty = flt(qty)
		if qty <= 0:
			frappe.throw(_("Production quantity must be greater than zero"))

		recipe_doc = frappe.get_doc("POS Production Recipe", recipe)
		if recipe_doc.disabled:
			frappe.throw(_("Recipe {0} is disabled").format(recipe_doc.recipe_name))
		if not any(c.company == company and c.enabled for c in recipe_doc.companies):
			frappe.throw(
				_("Recipe {0} is not available for company {1}").format(recipe_doc.recipe_name, company)
			)

		materials = _parse_items(items)
		if not materials:
			frappe.throw(_("At least one material is required"))
		batches = json.loads(batches) if isinstance(batches, str) else (batches or {})

		flags = _item_flags([m["item_code"] for m in materials] + [recipe_doc.production_item])

		# ---- pre-flight stock validation (trust boundary: client data is untrusted) ----
		merged = {}
		for m in materials:
			if m["qty"] <= 0:
				frappe.throw(_("Quantity for {0} must be greater than zero").format(m["item_code"]))
			if m["item_code"] == recipe_doc.production_item:
				frappe.throw(_("Material {0} is the production item itself").format(m["item_code"]))
			if m["item_code"] in merged:
				merged[m["item_code"]]["qty"] += m["qty"]
			else:
				merged[m["item_code"]] = dict(m)

		for m in merged.values():
			info = flags.get(m["item_code"])
			if not info:
				frappe.throw(_("Item {0} does not exist").format(m["item_code"]))
			if info.disabled:
				frappe.throw(_("Item {0} is disabled").format(m["item_code"]))
			if info.has_batch_no:
				batch_no = batches.get(m["item_code"])
				if not batch_no:
					frappe.throw(_("Batch is required for material {0}").format(m["item_code"]))
				batch_qty = flt(
					frappe.db.get_value("Batch", {"name": batch_no, "item": m["item_code"]}, "batch_qty")
				)
				if batch_qty < m["qty"]:
					frappe.throw(
						_("Material {0} batch {1} has only {2}, need {3}").format(
							m["item_code"], batch_no, batch_qty, m["qty"]
						)
					)
				m["batch_no"] = batch_no
			else:
				available = _stock_qty(m["item_code"], warehouse)
				if available < m["qty"]:
					frappe.throw(
						_("Material {0} is short by {1} in {2}").format(
							m["item_code"], m["qty"] - available, warehouse
						)
					)

		fg_info = flags.get(recipe_doc.production_item)
		if fg_info and fg_info.disabled:
			frappe.throw(_("Item {0} is disabled").format(recipe_doc.production_item))

		# ---- build the Manufacture Stock Entry ----
		se = frappe.new_doc("Stock Entry")
		se.company = company
		se.purpose = "Manufacture"
		se.remarks = f"POS Production: {recipe_doc.recipe_name}"
		se.set_stock_entry_type()

		for m in merged.values():
			row = {
				"item_code": m["item_code"],
				"qty": m["qty"],
				"s_warehouse": warehouse,
				"use_serial_batch_fields": 1,
			}
			if m.get("batch_no"):
				row["batch_no"] = m["batch_no"]
			se.append("items", row)

		fg_row = {
			"item_code": recipe_doc.production_item,
			"qty": qty,
			"t_warehouse": warehouse,
			"is_finished_item": 1,
			"use_serial_batch_fields": 1,
		}
		if fg_info and fg_info.has_batch_no:
			fg_batch = frappe.new_doc("Batch")
			fg_batch.batch_id = f"{recipe_doc.production_item}-{uuid4().hex[:6].upper()}"
			fg_batch.item = recipe_doc.production_item
			fg_batch.insert(ignore_permissions=True)
			fg_row["batch_no"] = fg_batch.name
		se.append("items", fg_row)

		# Cashiers have no Stock Entry doctype permission; the POS Production Log
		# insert below stays permission-enforced and is the real access gate.
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()

		snapshot = [
			{"item_code": m["item_code"], "qty": m["qty"], "batch_no": m.get("batch_no")}
			for m in merged.values()
		]
		log = frappe.new_doc("POS Production Log")
		log.recipe = recipe_doc.name
		log.production_item = recipe_doc.production_item
		log.qty = qty
		log.items_used = json.dumps(snapshot)
		log.stock_entry = se.name
		log.pos_profile = pos_profile
		log.company = company
		log.insert()
		log.submit()

		return {
			"stock_entry": se.name,
			"production_log": log.name,
			"production_item": recipe_doc.production_item,
			"qty": qty,
		}
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Create Production Error")
		frappe.throw(_("Error creating production: {0}").format(str(e)))
