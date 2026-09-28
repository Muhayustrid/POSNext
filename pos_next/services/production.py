# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""POS production orchestrator on top of native ERPNext Work Orders.

Design: docs/superpowers/plans/2026-09-27-pos-production-work-order.md (D1-D12).
The service NEVER recomputes what ERPNext already computes:

- Consumption/backflush math comes from ``make_stock_entry(work_order, ...)`` —
  the draft is only decorated (raw batch FIFO, FG good qty, remarks) before
  insert+submit. Process loss is derived natively from
  ``fg_completed_qty`` minus the FG row qty (stock_entry.py validate).
- Aggregates (produced_qty, process_loss_qty, status) are maintained by the
  Work Order itself; POS labels are computed, never stored (D4/D6).
- Failed runs close via ``close_work_order`` semantics plus an optional
  Material Issue to the company's stock adjustment account (D3/D9); the issue
  entry cannot carry ``work_order`` (ERPNext strips it), so it links via remark.
"""

import json
import math
from zoneinfo import ZoneInfo

import frappe
from erpnext.manufacturing.doctype.work_order.work_order import make_stock_entry
from erpnext.stock.doctype.batch.batch import get_batch_qty
from frappe import _
from frappe.utils import cint, flt, get_datetime, get_system_timezone, getdate

from pos_next.api.settings_resolver import get_effective_pos_setting

# POS-facing labels computed from native Work Order fields (design D4).
ACTIVE_STATUSES = ("Not Started", "In Process")
# Cancelled WOs are docstatus 2, so the history query needs an or_filters for
# them — a plain status filter would silently drop every cancelled production.
HISTORY_TERMINAL_STATUSES = ("Completed", "Closed")
LABEL_BY_STATUS = {
	"Not Started": "Not Started",
	"In Process": "In Progress",
	"Completed": "Completed",
	"Cancelled": "Cancelled",
}


# ---------------------------------------------------------------------------
# shared helpers (stock/batch reads + FIFO pick)
# ---------------------------------------------------------------------------


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


def _pick_batch(item_code, warehouse, qty):
	"""FIFO: batches sorted by expiry; first batch that covers qty wins."""
	batches = _batch_list(item_code, warehouse)
	for b in batches:
		if b["qty"] >= qty:
			return b["batch_no"]
	total = sum(b["qty"] for b in batches)
	frappe.throw(_("Material {0} is short by {1} in {2}").format(item_code, qty - total, warehouse))


def _checked_batch_pick(item_code, warehouse, qty, batch_no):
	"""Cashier-picked batch: must exist for this item with enough live qty in
	the source warehouse — the same ledger view the FEFO pick reads. Throws
	before anything is submitted, so a bad pick can never half-post."""
	for b in _batch_list(item_code, warehouse):
		if b["batch_no"] == batch_no:
			if b["qty"] < qty:
				frappe.throw(_("Batch {0} has only {1} left in {2}").format(batch_no, b["qty"], warehouse))
			return batch_no
	frappe.throw(_("Batch {0} is not available for {1} in {2}").format(batch_no, item_code, warehouse))


def _uom_whole_field():
	"""ERPNext v15 names the UOM flag must_be_whole; v16 must_be_whole_number."""
	return (
		"must_be_whole_number"
		if frappe.get_meta("UOM").has_field("must_be_whole_number")
		else "must_be_whole"
	)


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


def _production_warehouses(pos_profile, profile_warehouse):
	"""Source/FG warehouse for production: POS Settings row first (D2b), else the
	profile's own warehouse. Tier-2 global is deliberately not consulted —
	warehouses are outlet-specific, an empty value coalesces to the fallback."""
	source = get_effective_pos_setting(pos_profile, "production_source_warehouse") or profile_warehouse
	fg = get_effective_pos_setting(pos_profile, "production_fg_warehouse") or profile_warehouse
	return source, fg


def _validate_qty(value, label):
	qty = flt(value)
	if qty <= 0 or not math.isfinite(qty):
		frappe.throw(_("{0} must be greater than zero").format(_(label)))
	return qty


def _started_at_epoch(value):
	"""started_at as epoch seconds — the client computes elapsed time from an
	absolute instant. A naive site-tz string parsed as device-local drifts by
	the timezone gap between site and device (e.g. Asia/Kolkata vs WIB = 1h30m)."""
	if not value:
		return None
	dt = get_datetime(value)
	if dt.tzinfo is None:
		dt = dt.replace(tzinfo=ZoneInfo(get_system_timezone()))
	return int(dt.timestamp())


def _pos_status_label(status, produced_qty, qty):
	"""POS label from native fields only (design D4) — never stored.

	Canonical English: this is API payload — the client translates it via __()
	and compares the literal, so server-side _() here would make the value
	locale-dependent and break statusClass matching.
	"""
	if status in LABEL_BY_STATUS:
		return LABEL_BY_STATUS[status]
	if status == "Closed":
		if 0 < flt(produced_qty) < flt(qty):
			return "Partially Completed"
		return "Failed"
	return status


# ---------------------------------------------------------------------------
# read: recipe cards for the POS dialog
# ---------------------------------------------------------------------------


def get_recipes(pos_profile):
	"""Enabled recipes for the profile's company, with stock/batch info per material."""
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


# ---------------------------------------------------------------------------
# D1 — Recipe -> BOM one-way sync (BOM is a derived, regenerable artifact)
# ---------------------------------------------------------------------------


def sync_recipe_boms(recipe, method=None):
	"""doc_events hook (POS Production Recipe on_update): keep one active BOM per
	enabled company row, storing its name in the row's ``bom_no``.

	A BOM is regenerated only when its fingerprint (item, quantity, materials)
	no longer matches the recipe, so unrelated saves don't churn BOMs. Row state
	is read fresh from the DB — the in-memory rows the hook receives can predate
	another writer — and updates go through db_set: no re-save, no recursion.
	BOMs carry no warehouse (D1): warehouses live on the Work Order.
	"""
	for row in recipe.companies:
		current = frappe.db.get_value(
			"POS Production Recipe Company", row.name, ["company", "enabled", "bom_no"], as_dict=True
		)
		if not current:
			continue
		if current.enabled and _bom_matches(current.bom_no, recipe, current.company):
			continue
		if current.bom_no:
			_deactivate_bom(current.bom_no)
		if not current.enabled:
			frappe.db.set_value("POS Production Recipe Company", row.name, "bom_no", None)
			continue
		bom = _create_bom(recipe, current.company)
		frappe.db.set_value("POS Production Recipe Company", row.name, "bom_no", bom.name)


def _bom_matches(bom_no, recipe, company):
	if not bom_no or not frappe.db.exists("BOM", bom_no):
		return False
	bom = frappe.db.get_value(
		"BOM", bom_no, ["item", "company", "quantity", "with_operations", "docstatus", "is_active"], as_dict=True
	)
	if (
		bom.docstatus != 1
		or not bom.is_active
		or bom.company != company
		or bom.item != recipe.production_item
		or bom.with_operations
	):
		return False
	if flt(bom.quantity, 6) != flt(recipe.output_qty, 6):
		return False
	bom_items = sorted(
		(r.item_code, flt(r.qty, 6))
		for r in frappe.get_all(
			"BOM Item", filters={"parent": bom_no, "parenttype": "BOM"}, fields=["item_code", "qty"]
		)
	)
	ours = sorted((r.item_code, flt(r.qty, 6)) for r in recipe.items)
	return bom_items == ours


def _create_bom(recipe, company):
	bom = frappe.new_doc("BOM")
	bom.item = recipe.production_item
	bom.company = company
	bom.quantity = flt(recipe.output_qty)
	bom.with_operations = 0
	bom.is_active = 1
	# WO carries bom_no explicitly, so this BOM never needs to be the default.
	bom.is_default = 0
	for row in recipe.items:
		bom.append("items", {"item_code": row.item_code, "qty": flt(row.qty)})
	bom.flags.ignore_permissions = True
	bom.insert()
	bom.submit()
	return bom


def _deactivate_bom(bom_no):
	if not frappe.db.exists("BOM", bom_no):
		return
	bom = frappe.get_doc("BOM", bom_no)
	if bom.docstatus != 1 or not bom.is_active:
		return
	bom.is_active = 0  # is_active ships with allow_on_submit=1 (bom.json)
	bom.flags.ignore_permissions = True
	bom.save()


def get_recipe_bom(recipe_doc, company):
	"""Active BOM name for the recipe's enabled company row.

	Legacy recipes saved before this field existed are healed here once
	(same sync logic as the save hook); without a heal they could never be
	produced until someone re-saved every recipe by hand.
	"""

	def _stored_bom():
		rows = frappe.get_all(
			"POS Production Recipe Company",
			filters={
				"parent": recipe_doc.name,
				"parenttype": "POS Production Recipe",
				"company": company,
				"enabled": 1,
			},
			fields=["bom_no"],
		)
		return rows[0].bom_no if rows else None

	bom_no = _stored_bom()
	if not _bom_matches(bom_no, recipe_doc, company):
		sync_recipe_boms(recipe_doc)
		bom_no = _stored_bom()
		if not _bom_matches(bom_no, recipe_doc, company):
			frappe.throw(
				_("Recipe {0} has no active BOM for company {1}").format(recipe_doc.recipe_name, company)
			)
	return bom_no


# ---------------------------------------------------------------------------
# recipe + material pre-flight
# ---------------------------------------------------------------------------


def _get_enabled_recipe(recipe, company):
	recipe_doc = frappe.get_doc("POS Production Recipe", recipe)
	if recipe_doc.disabled:
		frappe.throw(_("Recipe {0} is disabled").format(recipe_doc.recipe_name))
	if not any(c.company == company and c.enabled for c in recipe_doc.companies):
		frappe.throw(_("Recipe {0} is not available for company {1}").format(recipe_doc.recipe_name, company))
	if not recipe_doc.items:
		frappe.throw(_("Recipe {0} has no materials").format(recipe_doc.recipe_name))
	if flt(recipe_doc.output_qty) <= 0:
		frappe.throw(_("Recipe {0} has no output quantity").format(recipe_doc.recipe_name))
	return recipe_doc


def _merged_materials(recipe_doc, qty):
	"""Trusted recipe rows scaled to the requested output (server-side only)."""
	factor = qty / flt(recipe_doc.output_qty)
	merged = {}
	for row in recipe_doc.items:
		scaled = flt(flt(row.qty) * factor, 9)
		if scaled <= 0:
			frappe.throw(_("Quantity for {0} must be greater than zero").format(row.item_code))
		if row.item_code == recipe_doc.production_item:
			frappe.throw(_("Material {0} is the production item itself").format(row.item_code))
		if row.item_code in merged:
			merged[row.item_code]["qty"] += scaled
		else:
			merged[row.item_code] = {"item_code": row.item_code, "qty": scaled}
	return merged


def _validate_materials(merged, warehouse, production_item):
	"""Pre-flight trust boundary: recipe rows are trusted, stock is not. Batched
	items FIFO-pick now (advisory at Start, binding at Finish via the same code)."""
	flags = _item_flags([m["item_code"] for m in merged.values()] + [production_item])
	fg_info = flags.get(production_item)
	if fg_info and fg_info.disabled:
		frappe.throw(_("Item {0} is disabled").format(production_item))
	for m in merged.values():
		info = flags.get(m["item_code"])
		if not info:
			frappe.throw(_("Item {0} does not exist").format(m["item_code"]))
		if info.disabled:
			frappe.throw(_("Item {0} is disabled").format(m["item_code"]))
		if info.has_batch_no:
			m["batch_no"] = _pick_batch(m["item_code"], warehouse, m["qty"])
		elif _stock_qty(m["item_code"], warehouse) < m["qty"]:
			frappe.throw(
				_("Material {0} is short by {1} in {2}").format(
					m["item_code"], m["qty"] - _stock_qty(m["item_code"], warehouse), warehouse
				)
			)
	return flags, fg_info


# ---------------------------------------------------------------------------
# lifecycle: Start / Finish / Close / Cancel (D3)
# ---------------------------------------------------------------------------


def start_production(recipe, qty, pos_profile, notes=None):
	"""Insert + submit a native Work Order (skip_transfer=1) for the recipe."""
	company, profile_warehouse = _resolve_profile(pos_profile)
	qty = _validate_qty(qty, "Production quantity")

	recipe_doc = _get_enabled_recipe(recipe, company)
	if frappe.db.get_value("UOM", frappe.db.get_value("Item", recipe_doc.production_item, "stock_uom"), _uom_whole_field()) and qty != int(qty):
		frappe.throw(
			_("Quantity must be a whole number for UOM {0}").format(
				frappe.db.get_value("Item", recipe_doc.production_item, "stock_uom")
			)
		)

	merged = _merged_materials(recipe_doc, qty)
	source_warehouse, fg_warehouse = _production_warehouses(pos_profile, profile_warehouse)
	# Pre-flight against the source warehouse: fail before any document exists.
	_validate_materials(merged, source_warehouse, recipe_doc.production_item)
	bom_no = get_recipe_bom(recipe_doc, company)

	wo = frappe.new_doc("Work Order")
	wo.company = company
	wo.production_item = recipe_doc.production_item
	wo.bom_no = bom_no
	wo.qty = qty
	wo.skip_transfer = 1
	wo.source_warehouse = source_warehouse
	wo.fg_warehouse = fg_warehouse
	# POS home data (D5): outlet, operator and recipe ride on native fields.
	wo.posa_pos_profile = pos_profile
	wo.posa_operator = frappe.session.user
	wo.posa_recipe = recipe_doc.name
	wo.flags.ignore_permissions = True
	wo.insert()
	wo.submit()
	if notes:
		wo.add_comment("Comment", text=notes)

	return _wo_payload(frappe.get_doc("Work Order", wo.name))


def finish_production(work_order, good_qty, loss_qty=0, notes=None, batch_picks=None):
	"""Submit a Manufacture Stock Entry born from native make_stock_entry.

	good becomes the FG row qty, loss stays implicit: fg_completed_qty = good +
	loss and the row difference is converted to process_loss_qty by ERPNext
	itself (stock_entry.py validate_fg_completed_qty). Overproduction is left
	to ERPNext's own setting-based guard. batch_picks (item_code -> batch_no)
	lets the cashier override the FEFO pick per batched raw material.
	"""
	wo = _get_pos_work_order(work_order)
	if wo.status in ("Closed", "Cancelled"):
		frappe.throw(_("Work Order {0} is {1} and cannot be finished").format(wo.name, _(wo.status)))

	good = _validate_qty(good_qty, "Good quantity")
	loss = flt(loss_qty)
	if loss < 0 or not math.isfinite(loss):
		frappe.throw(_("Loss quantity cannot be negative"))
	gross = good + loss

	draft = make_stock_entry(wo.name, "Manufacture", qty=gross)
	se = frappe.get_doc(draft)
	se.remarks = _production_remark(wo, notes)

	for row in se.items:
		if row.is_finished_item:
			# good out, loss implicit — native derives process_loss_qty from the
			# difference. The auto-batch bundle was built for the gross qty, so
			# trim its entries to the good qty to keep bundle and row consistent.
			row.qty = good
			_trim_fg_bundle(row, good, gross)
			# D1: the WO keeps its BOM snapshot, but a recipe change deactivates
			# that BOM and native validate_bom then rejects the SE row. The draft
			# was already priced from the WO's required_items, so the stale
			# reference can go without changing any math.
			if row.bom_no and not frappe.db.get_value("BOM", row.bom_no, "is_active"):
				row.bom_no = None
		elif row.s_warehouse and frappe.db.get_value("Item", row.item_code, "has_batch_no"):
			# Draft backflush rows are batch-empty (probe item 8): resolve the
			# batch via the legacy use_serial_batch_fields path (1 batch per
			# item) — the cashier's explicit pick when given, else FEFO.
			picked = (batch_picks or {}).get(row.item_code)
			if picked:
				_checked_batch_pick(row.item_code, row.s_warehouse, flt(row.transfer_qty), picked)
				row.batch_no = picked
			else:
				row.batch_no = _pick_batch(row.item_code, row.s_warehouse, flt(row.transfer_qty))
			row.use_serial_batch_fields = 1

	se.flags.ignore_permissions = True
	se.insert()
	se.submit()

	snapshot = [
		{"item_code": d.item_code, "qty": flt(d.transfer_qty), "batch_no": d.batch_no}
		for d in se.items
		if d.s_warehouse and not d.is_finished_item
	]
	log = _write_production_log(wo, se, good, snapshot)

	result = _wo_payload(frappe.get_doc("Work Order", wo.name))
	result.update(
		{
			"stock_entry": se.name,
			"production_log": log.name,
			"good_qty": good,
			"loss_qty": flt(se.process_loss_qty),
			"items_used": snapshot,
		}
	)
	return result


def get_finish_context(work_order):
	"""Materials + live batches for the finish form's batch pickers.

	Raw rows are merged by item_code — the same key the Manufacture draft merges
	on (get_bom_raw_materials) — so table rows, Stock Entry rows and batch_picks
	keys stay 1:1 even in the theoretical duplicate-row case.
	"""
	wo = _get_pos_work_order(work_order)
	if wo.status in ("Closed", "Cancelled"):
		frappe.throw(_("Work Order {0} is {1} and cannot be finished").format(wo.name, _(wo.status)))

	merged = {}
	for row in wo.required_items:
		m = merged.setdefault(
			row.item_code,
			{
				"item_code": row.item_code,
				"required_qty": 0.0,
				"source_warehouse": row.get("source_warehouse") or wo.source_warehouse,
			},
		)
		m["required_qty"] += flt(row.required_qty)

	flags = _item_flags(list(merged))
	materials = []
	for m in merged.values():
		info = flags.get(m["item_code"])
		materials.append(
			{
				"item_code": m["item_code"],
				"item_name": (info.item_name if info else None) or m["item_code"],
				"required_qty": flt(m["required_qty"]),
				"stock_uom": (info.stock_uom if info else "") or "",
				"has_batch_no": bool(info.has_batch_no) if info else False,
				"batches": _batch_list(m["item_code"], m["source_warehouse"])
				if info and info.has_batch_no
				else [],
			}
		)
	return {
		"work_order": wo.name,
		"wo_qty": flt(wo.qty),
		"fg_has_batch_no": bool(frappe.db.get_value("Item", wo.production_item, "has_batch_no")),
		"materials": materials,
	}


def close_production(work_order, writeoff=False, notes=None):
	"""Stop early (D10 case 2/3/5): close the WO, optionally issuing the still-
	unconsumed materials to the stock adjustment account as abnormal loss.

	The Material Issue cannot reference the WO (ERPNext strips work_order for
	Material Issue, stock_entry.py), so it links via the remark.
	"""
	wo = _get_pos_work_order(work_order)
	if wo.status in ("Closed", "Cancelled"):
		# re-closing is a no-op natively, but a second write-off would double-
		# issue the remaining material — fail loudly instead.
		frappe.throw(_("Work Order {0} is {1} and cannot be closed").format(wo.name, _(wo.status)))
	# close_work_order minus its Desk permission line (work_order.py:2830) —
	# the POS access gate is the endpoint's _assert_profile_access, and the
	# document writes below run permission-exempt like every other write here.
	wo.update_status("Closed")
	wo.on_close_or_cancel()
	wo.notify_update()

	result = _wo_payload(frappe.get_doc("Work Order", wo.name))
	result["material_issue"] = None
	if writeoff:
		issue = _make_material_issue(wo, notes)
		result["material_issue"] = issue.name if issue else None
	return result


def cancel_production(work_order):
	"""D10 case 1/6/7: cancel linked entries LIFO (newest first), then the WO.
	Cancelling a Manufacture SE makes the WO aggregates and status step back
	natively — no manual state repair."""
	wo = _get_pos_work_order(work_order)
	# Logs reference the Stock Entries, so they unwind first (LIFO overall).
	logs = frappe.get_all(
		"POS Production Log",
		filters={"work_order": wo.name, "docstatus": 1},
		fields=["name", "creation"],
	)
	cancelled_logs = []
	for entry in sorted(logs, key=lambda r: r.creation or "", reverse=True):
		log = frappe.get_doc("POS Production Log", entry.name)
		log.flags.ignore_permissions = True
		log.cancel()
		cancelled_logs.append(entry.name)

	linked = frappe.get_all(
		"Stock Entry",
		filters={"work_order": wo.name, "docstatus": 1},
		fields=["name", "creation"],
	)
	# Write-off issues lost their work_order link to ERPNext; the remark owns
	# it. Match the exact canonical prefix + separator — a free-text note from
	# another WO's close may legitimately contain this WO's name as a token.
	marker = f"{WRITEOFF_REMARK_PREFIX}{wo.name}"
	writeoffs = frappe.get_all(
		"Stock Entry",
		filters={"docstatus": 1, "purpose": "Material Issue", "remarks": ["like", f"{marker}%"]},
		fields=["name", "creation", "remarks"],
	)
	writeoffs = [
		w for w in writeoffs if (w.remarks or "") == marker or (w.remarks or "").startswith(marker + " ")
	]
	cancelled = []
	for entry in sorted(linked + writeoffs, key=lambda r: r.creation or "", reverse=True):
		se = frappe.get_doc("Stock Entry", entry.name)
		se.flags.ignore_permissions = True
		se.cancel()
		cancelled.append(entry.name)

	# SE cancels touched the WO (aggregates, modified timestamp): reload before
	# cancelling or the stale doc trips TimestampMismatchError.
	wo = frappe.get_doc("Work Order", work_order)
	wo.flags.ignore_permissions = True
	wo.cancel()
	return {
		"work_order": wo.name,
		"status": wo.status,
		"cancelled_stock_entries": cancelled,
		"cancelled_production_logs": cancelled_logs,
	}


def get_active_productions(pos_profile):
	"""Submitted WOs of this outlet still in progress (D8 two-phase list)."""
	company, _warehouse = _resolve_profile(pos_profile)
	rows = frappe.get_all(
		"Work Order",
		filters={"docstatus": 1, "status": ["in", ACTIVE_STATUSES], "posa_pos_profile": pos_profile},
		fields=[
			"name",
			"production_item",
			"qty",
			"produced_qty",
			"process_loss_qty",
			"status",
			"posa_recipe",
			"posa_operator",
			"planned_start_date",
			"actual_start_date",
			"modified",
		],
		order_by="modified desc",
	)
	item_names = {r.production_item for r in rows}
	items = {
		r.name: r.item_name
		for r in frappe.get_all("Item", filters={"name": ["in", list(item_names)]}, fields=["name", "item_name"])
	}
	recipes = {
		r.name: r.recipe_name
		for r in frappe.get_all(
			"POS Production Recipe",
			filters={"name": ["in", [r.posa_recipe for r in rows if r.posa_recipe]]},
			fields=["name", "recipe_name"],
		)
	}
	return {
		"pos_profile": pos_profile,
		"company": company,
		"productions": [
			{
				"work_order": r.name,
				"production_item": r.production_item,
				"production_item_name": items.get(r.production_item, r.production_item),
				"recipe": r.posa_recipe,
				"recipe_name": recipes.get(r.posa_recipe),
				"qty": flt(r.qty),
				"produced_qty": flt(r.produced_qty),
				"process_loss_qty": flt(r.process_loss_qty),
				"status": r.status,
				"pos_status": _pos_status_label(r.status, r.produced_qty, r.qty),
				"operator": r.posa_operator,
				"started_at": _started_at_epoch(r.actual_start_date or r.planned_start_date),
				"updated_at": r.modified,
			}
			for r in rows
		],
	}


def get_production_history(pos_profile, limit=30, offset=0):
	"""Finished Work Orders of this outlet, newest first (D5: the WO is the
	history — no separate read model). Terminal statuses only, so "Stopped"
	Work Orders closed from the Desk never appear in either list: by design."""
	company, _warehouse = _resolve_profile(pos_profile)
	limit = max(1, min(cint(limit), 100))
	offset = max(0, cint(offset))
	rows = frappe.get_all(
		"Work Order",
		filters={"posa_pos_profile": pos_profile},
		or_filters=[
			["docstatus", "=", 2],
			["status", "in", HISTORY_TERMINAL_STATUSES],
		],
		fields=[
			"name",
			"production_item",
			"qty",
			"produced_qty",
			"process_loss_qty",
			"status",
			"posa_recipe",
			"posa_operator",
			"planned_start_date",
			"actual_start_date",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=limit + 1,
		limit_start=offset,
	)
	has_more = len(rows) > limit
	rows = rows[:limit]
	item_names = {r.production_item for r in rows}
	items = {
		r.name: r.item_name
		for r in frappe.get_all("Item", filters={"name": ["in", list(item_names)]}, fields=["name", "item_name"])
	}
	recipes = {
		r.name: r.recipe_name
		for r in frappe.get_all(
			"POS Production Recipe",
			filters={"name": ["in", [r.posa_recipe for r in rows if r.posa_recipe]]},
			fields=["name", "recipe_name"],
		)
	}
	return {
		"pos_profile": pos_profile,
		"company": company,
		"productions": [
			{
				"work_order": r.name,
				"production_item": r.production_item,
				"production_item_name": items.get(r.production_item, r.production_item),
				"recipe": r.posa_recipe,
				"recipe_name": recipes.get(r.posa_recipe),
				"qty": flt(r.qty),
				"produced_qty": flt(r.produced_qty),
				"process_loss_qty": flt(r.process_loss_qty),
				"status": r.status,
				"pos_status": _pos_status_label(r.status, r.produced_qty, r.qty),
				"operator": r.posa_operator,
				"started_at": _started_at_epoch(r.actual_start_date or r.planned_start_date),
				"created_at": r.creation,
			}
			for r in rows
		],
		"has_more": has_more,
	}


def create_production(recipe, qty, pos_profile, good_qty=None, loss_qty=0, notes=None):
	"""One-shot Start+Finish in a single request = a single DB transaction (D3)."""
	started = start_production(recipe, qty, pos_profile, notes=notes)
	good = started["qty"] if good_qty is None else good_qty
	result = finish_production(started["work_order"], good, loss_qty=loss_qty, notes=notes)
	result.update(
		{
			"recipe": recipe,
			"production_item": started["production_item"],
			"qty": started["qty"],
		}
	)
	return result


# ---------------------------------------------------------------------------
# internals
# ---------------------------------------------------------------------------


def _get_pos_work_order(work_order):
	wo = frappe.get_doc("Work Order", work_order)
	if wo.docstatus != 1:
		frappe.throw(_("Work Order {0} is not submitted").format(wo.name))
	if not wo.posa_pos_profile:
		frappe.throw(_("Work Order {0} was not created from POS").format(wo.name))
	return wo


def _production_remark(wo, notes=None):
	recipe_name = frappe.db.get_value("POS Production Recipe", wo.posa_recipe, "recipe_name")
	remark = _("POS Production: {0}").format(recipe_name or wo.name)
	if notes:
		remark = f"{remark} — {notes}"
	return remark


WRITEOFF_REMARK_PREFIX = "POS Production write-off: "


def _writeoff_remark(wo, notes=None):
	"""Canonical English, never translated: the Material Issue lost its
	work_order link to ERPNext, so this remark owns the link and
	cancel_production matches on it."""
	remark = f"{WRITEOFF_REMARK_PREFIX}{wo.name}"
	if notes:
		remark = f"{remark} — {notes}"
	return remark


def _trim_fg_bundle(row, good, gross):
	"""Scale the FG auto-batch bundle from the gross qty down to the good qty.

	make_stock_entry built the bundle for fg_completed_qty = good + loss; after
	the row moves to ``good`` the bundle must carry the same total, so the
	excess (the process loss) is trimmed off the bundle entries.
	"""
	excess = flt(gross) - flt(good)
	if excess <= 0 or not row.serial_and_batch_bundle:
		return
	bundle = frappe.get_doc("Serial and Batch Bundle", row.serial_and_batch_bundle)
	if bundle.docstatus != 0:
		frappe.throw(_("Serial and Batch Bundle {0} is not editable").format(bundle.name))
	for entry in reversed(bundle.entries):
		if excess <= 0:
			break
		take = min(excess, flt(entry.qty, 9))
		entry.qty = flt(flt(entry.qty, 9) - take, 9)
		excess = flt(excess - take, 9)
		if entry.qty <= 0:
			bundle.remove(entry)
	if excess > 0:
		frappe.throw(_("Batch bundle {0} is short by {1}").format(bundle.name, excess))
	bundle.flags.ignore_permissions = True
	bundle.save()


def _write_production_log(wo, se, good, snapshot):
	"""POS Production Log stays the human-facing receipt for now (D7 phase 1)."""
	log = frappe.new_doc("POS Production Log")
	log.recipe = wo.posa_recipe
	log.production_item = wo.production_item
	log.qty = good
	log.items_used = json.dumps(snapshot)
	log.stock_entry = se.name
	log.work_order = wo.name
	log.pos_profile = wo.posa_pos_profile
	log.company = wo.company
	log.flags.ignore_permissions = True
	log.insert()
	log.submit()
	return log


def _make_material_issue(wo, notes=None):
	"""Issue the WO's not-yet-consumed materials to the adjustment account (D9).

	Remaining = required_items snapshot minus what submitted Manufacture entries
	already consumed. Cashiers have no Stock Entry perms; the write is exempt —
	access was already gated by the endpoint.
	"""
	consumed = dict(
		frappe.db.sql(
			"""
			select sed.item_code, sum(sed.transfer_qty)
			from `tabStock Entry Detail` sed
			join `tabStock Entry` se on se.name = sed.parent
			where se.work_order = %s and se.docstatus = 1 and se.purpose = 'Manufacture'
				and ifnull(sed.s_warehouse, '') != ''
			group by sed.item_code
			""",
			wo.name,
		)
	)
	remaining = [
		{"item_code": r.item_code, "qty": flt(r.required_qty) - flt(consumed.get(r.item_code, 0))}
		for r in wo.required_items
		if flt(r.required_qty) - flt(consumed.get(r.item_code, 0)) > 0
	]
	if not remaining:
		return None

	wastage_account = frappe.db.get_value("Company", wo.company, "stock_adjustment_account")
	if not wastage_account:
		frappe.throw(
			_("Company {0} has no Stock Adjustment Account for the write-off").format(wo.company)
		)

	se = frappe.new_doc("Stock Entry")
	se.company = wo.company
	se.purpose = "Material Issue"
	se.set_stock_entry_type()
	se.remarks = _writeoff_remark(wo, notes)
	for m in remaining:
		row = {
			"item_code": m["item_code"],
			"qty": m["qty"],
			"s_warehouse": wo.source_warehouse,
			"expense_account": wastage_account,
			"use_serial_batch_fields": 1,
		}
		if frappe.db.get_value("Item", m["item_code"], "has_batch_no"):
			row["batch_no"] = _pick_batch(m["item_code"], wo.source_warehouse, m["qty"])
		se.append("items", row)
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	return se


def _wo_payload(wo):
	return {
		"work_order": wo.name,
		"production_item": wo.production_item,
		"recipe": wo.posa_recipe,
		"qty": flt(wo.qty),
		"produced_qty": flt(wo.produced_qty),
		"process_loss_qty": flt(wo.process_loss_qty),
		"status": wo.status,
		"pos_status": _pos_status_label(wo.status, wo.produced_qty, wo.qty),
		"source_warehouse": wo.source_warehouse,
		"fg_warehouse": wo.fg_warehouse,
		"operator": wo.posa_operator,
		"pos_profile": wo.posa_pos_profile,
	}
