# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""POS Package API.

A POS Package ("Paket") sells a fixed set of items plus customer-chosen options
under one price. On the invoice it materialises as:

- one **parent** row (the non-stock package item) carrying the whole price, and
- one **component** row per included/chosen item at rate 0.

Stock therefore moves on the components while revenue sits on the parent line.

With ``enable_pos_package_allocation`` on POS Next Global Settings the price is
instead split across the component rows — proportionally to each component's
current POS price-list value — and the parent row drops to zero. The snapshot
then carries an ``allocation`` marker so re-validation can tell the modes
apart; snapshots without the marker are always repriced the legacy way.

Pricing is ``base_price + sum(option.price_adjustment * qty)``.

The quote is computed here on the server and mirrored byte-for-byte by
``POS/src/utils/packageQuote.js`` (allocation off) so the POS can price
packages while offline; the offline mirror still quotes the legacy shape, so
an allocation-mode invoice is always repriced by the server.
``validate_invoice_packages`` re-quotes every package on the Sales Invoice, so a
tampered or stale client payload can never set its own price.
"""

import json
import re

import frappe
from frappe import _
from frappe.utils import cint, cstr, flt, getdate, nowdate

from pos_next.api.items import _fetch_uom_prices_map

PARENT_ROLE = "Package"
COMPONENT_ROLE = "Package Item"
ALLOCATION_MODE = "proportional"

INSTANCE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,140}$")


def _parse_json(value, default):
	if value is None or value == "":
		return default
	if isinstance(value, str):
		try:
			return json.loads(value)
		except (ValueError, TypeError):
			frappe.throw(_("Malformed package payload."))
	return value


def _assert_profile_access(pos_profile):
	"""Reject callers who aren't assigned to this POS Profile.

	Both whitelisted endpoints take `pos_profile` straight from the caller, so
	without this any logged-in user could read another outlet's packages and
	pricing. Mirrors pos_next.api.pos_profile.get_pos_profile_data.
	"""
	if not pos_profile:
		frappe.throw(_("POS Profile is required"))

	if frappe.db.exists("POS Profile User", {"parent": pos_profile, "user": frappe.session.user}):
		return

	if frappe.has_permission("POS Profile", "write"):
		return

	frappe.throw(_("You don't have access to this POS Profile"), frappe.PermissionError)


def _package_is_valid_on(package, on_date):
	if cint(package.get("is_lifetime")):
		return True
	if package.get("valid_from") and getdate(on_date) < getdate(package["valid_from"]):
		return False
	if package.get("valid_upto") and getdate(on_date) > getdate(package["valid_upto"]):
		return False
	return True


def _stock_flags_map(item_codes):
	"""PERF-05/15 pattern: one bulk is_stock_item lookup instead of per row.
	Missing items resolve through .get() (None), so callers keep their old
	default semantics."""
	return (
		{
			code: cint(flag)
			for code, flag in frappe.get_all(
				"Item",
				filters={"name": ["in", list(item_codes)]},
				fields=["name", "is_stock_item"],
				as_list=True,
			)
		}
		if item_codes
		else {}
	)


def _serialize_package(doc):
	"""Full package definition — enough for the POS to render and price offline."""
	component_codes = {row.item_code for row in doc.items or []}
	component_codes |= {row.item_code for row in doc.options or []}
	stock_flags = _stock_flags_map(component_codes)

	return {
		"name": doc.name,
		"package_name": doc.package_name,
		"parent_item": doc.parent_item,
		"base_price": flt(doc.base_price),
		"currency": doc.currency,
		"company": doc.company,
		"description": doc.description,
		"valid_from": str(doc.valid_from) if doc.valid_from else None,
		"valid_upto": str(doc.valid_upto) if doc.valid_upto else None,
		"is_lifetime": cint(getattr(doc, "is_lifetime", 0)),
		"items": [
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"uom": row.uom,
				"is_stock_item": stock_flags.get(row.item_code, 1),
			}
			for row in doc.items or []
		],
		"groups": [
			{
				"group_key": row.group_key,
				"label": row.label,
				"description": row.description,
				"min_qty": cint(row.min_qty),
				"max_qty": cint(row.max_qty),
			}
			for row in doc.groups or []
		],
		"options": [
			{
				"option_id": row.name,
				"group_key": row.group_key,
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty_per_unit": flt(row.qty_per_unit) or 1.0,
				"uom": row.uom,
				"price_adjustment": flt(row.price_adjustment),
				"max_qty": cint(row.max_qty),
				"is_stock_item": stock_flags.get(row.item_code, 1),
			}
			for row in doc.options or []
		],
	}


def _eligible_package_names(pos_profile, on_date=None):
	"""Names of enabled, in-date packages available on this POS Profile.

	A package with no outlet rows is available to every profile of its package
	company; otherwise it is scoped by outlet Company + Warehouse — an outlet
	applies to every POS Profile sharing that pair.
	"""
	profile = frappe.db.get_value("POS Profile", pos_profile, ["name", "company", "warehouse"], as_dict=True)
	if not profile:
		frappe.throw(_("POS Profile {0} not found.").format(frappe.bold(pos_profile)))

	on_date = on_date or nowdate()

	packages = frappe.get_all(
		"POS Package",
		filters={"disabled": 0},
		fields=["name", "company", "valid_from", "valid_upto", "is_lifetime"],
	)
	if not packages:
		return []

	names = [p.name for p in packages]
	packages_by_name = {p.name: p for p in packages}

	outlet_rows = frappe.get_all(
		"POS Package Outlet",
		filters={"parent": ["in", names], "parenttype": "POS Package"},
		fields=["parent", "company", "warehouse", "enabled"],
	)
	restricted = {row.parent for row in outlet_rows}
	allowed = {
		row.parent
		for row in outlet_rows
		if row.enabled
		and row.company == profile.company
		and (row.warehouse or "") == (profile.warehouse or "")
	}

	def _is_available(p):
		if _package_is_valid_on(p, on_date) is False:
			return False
		if p.name not in restricted:
			return p.company == profile.company
		return p.name in allowed

	return [p.name for p in packages if _is_available(packages_by_name[p.name])]


@frappe.whitelist()
def get_packages(pos_profile, on_date=None):
	"""Return every package available on this profile, fully expanded.

	The POS caches this payload in IndexedDB so package selection and pricing keep
	working offline.
	"""
	_assert_profile_access(pos_profile)

	names = _eligible_package_names(pos_profile, on_date)
	return [_serialize_package(frappe.get_cached_doc("POS Package", name)) for name in names]


def _index_choices(choices):
	"""Normalise the client payload into ``{group_key: {option_id: qty}}``."""
	indexed = {}
	for entry in choices or []:
		group_key = (entry or {}).get("group_key")
		if not group_key:
			frappe.throw(_("Each selection must reference a group."))

		bucket = indexed.setdefault(group_key, {})
		for option in entry.get("options") or []:
			option_id = (option or {}).get("option_id")
			qty = cint((option or {}).get("qty"))
			if not option_id:
				frappe.throw(_("Each selection must reference an option."))
			if qty < 0:
				frappe.throw(_("Selected quantity cannot be negative."))
			if qty:
				bucket[option_id] = bucket.get(option_id, 0) + qty
	return indexed


def _rate_precision(child_doctype="Sales Invoice Item"):
	"""Currency precision for invoice rates (System Settings; IDR = 0).

	``child_doctype`` selects the child table whose meta carries the precision —
	the two invoice item doctypes can be configured apart, tiny as the odds are.
	"""
	return cint(frappe.get_precision(child_doctype, "rate"))


def _return_row_link_field(doc):
	"""Child-row back-link field for a return document.

	ERPNext maps returns through a doctype-specific pointer: Sales Invoice Item
	uses ``sales_invoice_item``, POS Invoice Item uses ``pos_invoice_item``
	(controllers/sales_and_purchase_return.py:make_return_doc). Restoring
	package metadata must read the same column the mapper wrote.
	"""
	return "pos_invoice_item" if doc.get("doctype") == "POS Invoice" else "sales_invoice_item"


def _return_child_doctype(doc):
	"""Child table of the return document itself ("{doctype} Item")."""
	return f"{doc.get('doctype')} Item" if doc.get("doctype") else "Sales Invoice Item"


def _original_child_doctype(return_against, default):
	"""Child table of the ORIGINAL invoice a return points at.

	The site's invoice type is switchable, so the original can live in the
	other table than the return being validated; the row links and the package
	metadata are stored per original doctype.
	"""
	if return_against:
		if frappe.db.exists("Sales Invoice", return_against):
			return "Sales Invoice Item"
		if frappe.db.exists("POS Invoice", return_against):
			return "POS Invoice Item"
	return default


def _package_allocation_enabled():
	"""Global package-allocation toggle (POS Next Global Settings single).

	cache=False so a toggle flipped mid-process (tests, scripts) is seen on the
	next read. A site whose schema predates the field (code deployed, migrate
	not yet run) keeps the legacy default instead of raising "field does not
	exist".
	"""
	if not frappe.get_meta("POS Next Global Settings").has_field("enable_pos_package_allocation"):
		return False
	return bool(
		cint(
			frappe.db.get_single_value(
				"POS Next Global Settings", "enable_pos_package_allocation", cache=False
			)
		)
	)


def _allocation_mode(snapshot):
	"""Mode recorded in a quote snapshot; None for legacy snapshots."""
	allocation = snapshot.get("allocation") if isinstance(snapshot, dict) else None
	return allocation.get("mode") if isinstance(allocation, dict) else None


def allocate_package_rates(package_price, children, package_qty=1, precision=0):
	"""Split a package price across its component lines, proportionally.

	``children`` is a list of ``{qty_per_package, price_list_rate}``: each
	component's quantity in one package and its current POS price-list rate.
	A component's weight is ``price_list_rate * qty_per_package``, so the
	package price is distributed by value; when *no* component carries a price
	the split falls back to quantity weights, then to an even split, logging a
	warning instead of failing. Returns one per-unit rate per child, in input
	order, with the rounding remainder carried by the last child, so that

	    sum(rate_i * qty_per_package_i * package_qty) == package_price * package_qty

	Pure (no database access): price-list lookups stay with the caller.
	"""
	children = list(children or [])
	if not children:
		return []

	package_price = flt(package_price)
	package_qty = flt(package_qty) or 1.0
	quantities = [flt(child.get("qty_per_package")) for child in children]

	weights = [
		flt(child.get("price_list_rate")) * qty for child, qty in zip(children, quantities, strict=False)
	]
	if not any(weights):
		weights = list(quantities)
		if any(weights):
			frappe.logger("pos_next").warning(
				"POS package allocation: no component has a price-list rate; using quantity weights."
			)
	if not any(weights):
		weights = [1.0] * len(children)
		frappe.logger("pos_next").warning(
			"POS package allocation: no component has a price-list rate or quantity; splitting evenly."
		)

	total_weight = sum(weights)
	total_money = package_price * package_qty
	rates = []
	allocated = 0.0
	last_index = len(children) - 1
	for index, (weight, qty) in enumerate(zip(weights, quantities, strict=False)):
		line_qty = qty * package_qty
		if not line_qty:
			rate = 0.0
		elif index == last_index:
			rate = (total_money - allocated) / line_qty
		else:
			rate = weight / total_weight * total_money / line_qty if total_weight else 0.0
		rate = flt(rate, precision)
		rates.append(rate)
		allocated += rate * line_qty

	return rates


def _component_price_list_rates(component_lines, pos_profile):
	"""POS price-list rate for each component line, used as allocation weight.

	One bulk Item Price lookup on the profile's selling price list — the same
	source POS carts price from. A component the price list does not carry gets
	rate 0, routing the whole quote to the weight fallback.
	"""
	price_list = frappe.db.get_value("POS Profile", pos_profile, "selling_price_list")
	if not price_list:
		return [0.0] * len(component_lines)

	prices = _fetch_uom_prices_map(sorted({line["item_code"] for line in component_lines}), price_list)
	rates = []
	for line in component_lines:
		uom_prices = prices.get(line["item_code"]) or {}
		rate = None
		if line.get("uom"):
			rate = uom_prices.get(line["uom"])
		if rate is None:
			rate = uom_prices.get("")
		if rate is None and len(uom_prices) == 1:
			# Single-UOM item priced under its named UOM while the package row
			# carries none: the only price is unambiguous.
			rate = next(iter(uom_prices.values()))
		rates.append(flt(rate))
	return rates


def _allocated_component_rates(component_lines, precision):
	"""Per-item queues of per-unit rates from the server's allocated quote lines.

	Kept as one rate per line, in line order: a component listed twice carries a
	different rate per line (each was rounded independently), and collapsing
	them into one merged rate could not satisfy the exact-sum invariant.
	"""
	rates = {}
	for line in component_lines:
		rates.setdefault(line["item_code"], []).append(flt(line["rate"], precision))
	return rates


def quote(package_name, choices, pos_profile, warehouse=None, allocate=None):
	"""Validate a selection and return the priced package (non-whitelisted core).

	Returns ``{package, package_name, total, currency, lines, snapshot}`` where
	``lines[0]`` is the parent row and the rest are components at rate 0. When
	``allocate`` is on (default: the global toggle), the components carry the
	price split proportionally and the parent row is zero, with the mode marked
	in the snapshot.
	"""
	if allocate is None:
		allocate = _package_allocation_enabled()

	if package_name not in _eligible_package_names(pos_profile):
		frappe.throw(_("Package {0} is not available on this POS Profile.").format(frappe.bold(package_name)))

	doc = frappe.get_cached_doc("POS Package", package_name)
	indexed = _index_choices(choices)

	# PERF-15: one bulk lookup for every component row (was db.get_value per row).
	component_codes = {row.item_code for row in doc.items or []}
	component_codes |= {row.item_code for row in doc.options or []}
	stock_flags = _stock_flags_map(component_codes)

	options_by_id = {row.name: row for row in doc.options or []}
	group_keys = {group.group_key for group in doc.groups or []}

	for group_key in indexed:
		if group_key not in group_keys:
			frappe.throw(_("Unknown choice group {0}.").format(frappe.bold(group_key)))

	total = flt(doc.base_price)
	component_lines = []
	snapshot_selections = []

	for group in doc.groups or []:
		picks = indexed.get(group.group_key, {})
		picked_qty = sum(picks.values())
		min_qty = cint(group.min_qty)
		max_qty = cint(group.max_qty)

		if picked_qty < min_qty:
			frappe.throw(_("Choose at least {0} item(s) from {1}.").format(min_qty, frappe.bold(group.label)))
		if picked_qty > max_qty:
			frappe.throw(_("Choose at most {0} item(s) from {1}.").format(max_qty, frappe.bold(group.label)))

		for option_id, qty in picks.items():
			option = options_by_id.get(option_id)
			if not option or option.group_key != group.group_key:
				frappe.throw(
					_("Option {0} does not belong to {1}.").format(option_id, frappe.bold(group.label))
				)

			option_max = cint(option.max_qty)
			if option_max and qty > option_max:
				frappe.throw(
					_("You can pick at most {0} x {1}.").format(
						option_max, frappe.bold(option.item_name or option.item_code)
					)
				)

			total += flt(option.price_adjustment) * qty

			component_lines.append(
				{
					"item_code": option.item_code,
					"item_name": option.item_name,
					"qty": (flt(option.qty_per_unit) or 1.0) * qty,
					"uom": option.uom,
					"rate": 0.0,
					"role": COMPONENT_ROLE,
					"is_stock_item": cint(stock_flags.get(option.item_code)),
				}
			)
			snapshot_selections.append(
				{
					"group_key": group.group_key,
					"group_label": group.label,
					"option_id": option_id,
					"item_code": option.item_code,
					"item_name": option.item_name,
					"qty": qty,
					"price_adjustment": flt(option.price_adjustment),
				}
			)

	for row in doc.items or []:
		component_lines.append(
			{
				"item_code": row.item_code,
				"item_name": row.item_name,
				"qty": flt(row.qty),
				"uom": row.uom,
				"rate": 0.0,
				"role": COMPONENT_ROLE,
				"is_stock_item": cint(stock_flags.get(row.item_code)),
			}
		)

	if total < 0:
		frappe.throw(_("Package price cannot be negative."))

	precision = _rate_precision()
	total = flt(total, precision)

	# With no component rows there is nowhere to move the money; the parent
	# keeps it and the snapshot stays legacy-shaped (no allocation marker).
	allocation_applied = bool(allocate and component_lines)
	parent_rate = total
	if allocation_applied:
		list_rates = _component_price_list_rates(component_lines, pos_profile)
		rates = allocate_package_rates(
			total,
			[
				{"qty_per_package": flt(line["qty"]), "price_list_rate": plr}
				for line, plr in zip(component_lines, list_rates, strict=False)
			],
			precision=precision,
		)
		for line, rate in zip(component_lines, rates, strict=False):
			line["rate"] = rate
		parent_rate = 0.0

	parent_line = {
		"item_code": doc.parent_item,
		"item_name": doc.package_name,
		"qty": 1,
		"rate": parent_rate,
		"role": PARENT_ROLE,
	}

	snapshot = {
		"package": doc.name,
		"package_name": doc.package_name,
		"base_price": flt(doc.base_price),
		"total": total,
		"selections": snapshot_selections,
		"included_items": [
			{"item_code": row.item_code, "item_name": row.item_name, "qty": flt(row.qty)}
			for row in doc.items or []
		],
	}
	if allocation_applied:
		snapshot["allocation"] = {"mode": ALLOCATION_MODE, "precision": precision}

	return {
		"package": doc.name,
		"package_name": doc.package_name,
		"parent_item": doc.parent_item,
		"currency": doc.currency,
		"total": total,
		"lines": [parent_line, *component_lines],
		"snapshot": snapshot,
	}


@frappe.whitelist()
def quote_package(package, choices, pos_profile, warehouse=None):
	"""Server-authoritative price for a package selection."""
	_assert_profile_access(pos_profile)

	return quote(package, _parse_json(choices, []), pos_profile, warehouse)


def _group_invoice_rows_by_instance(doc):
	instances = {}
	for row in doc.get("items") or []:
		instance = row.get("pos_package_instance")
		if not instance:
			continue
		instances.setdefault(instance, []).append(row)
	return instances


def _recalculate_totals(doc):
	"""Recompute invoice totals after re-pricing package rows.

	Frappe runs the controller's own ``validate`` (which calculates taxes and
	totals) BEFORE app ``doc_events`` hooks, so correcting a rate here leaves
	grand_total holding the client's figure — a tampered payload would be
	repriced yet still charged the old amount. Recalculating closes that gap.
	"""
	# `frappe._dict` returns None for unknown keys, so hasattr() is not enough.
	recalculate = getattr(doc, "calculate_taxes_and_totals", None)
	if callable(recalculate):
		recalculate()


def _restore_return_package_metadata(doc):
	"""Rebuild package fields that the return payload never carries.

	``ReturnInvoiceDialog.vue`` builds its items from a fixed field whitelist, so
	``pos_package_instance`` / ``pos_package_role`` never reach the server. Without
	restoring them a credit note looks package-free and skips every guard below —
	letting the priced parent be refunded while its components are dropped.

	Membership is re-derived from the original invoice through the doctype's
	row link (``sales_invoice_item`` for Sales Invoice, ``pos_invoice_item``
	for POS Invoice — the same link ERPNext itself uses for return tracking),
	so the client never gets to declare which rows belong to a package.
	"""
	rows = doc.get("items") or []
	link_field = _return_row_link_field(doc)

	# Cross-mode returns (POS Invoice sale, Sales Invoice return — the mode is
	# switchable) arrive with the field name of the ORIGINAL doctype, which is
	# not necessarily the one the return document carries. Prefer the target
	# doctype's link; fall back to the other only when no row carries it.
	other_field = "sales_invoice_item" if link_field == "pos_invoice_item" else "pos_invoice_item"
	if not any(row.get(link_field) for row in rows) and any(row.get(other_field) for row in rows):
		link_field = other_field

	# Source rows live in the ORIGINAL invoice's child table, which can differ
	# from the return's when the site's invoice type changed in between.
	child_doctype = _original_child_doctype(doc.get("return_against"), _return_child_doctype(doc))

	link_names = [
		row.get(link_field) for row in rows if not row.get("pos_package_instance") and row.get(link_field)
	]

	if link_names:
		sources = frappe.get_all(
			child_doctype,
			filters={"name": ["in", link_names], "parent": doc.get("return_against")},
			fields=[
				"name",
				"pos_package",
				"pos_package_instance",
				"pos_package_role",
				"pos_package_snapshot",
			],
		)
		by_name = {source["name"]: source for source in sources}

		for row in rows:
			source = by_name.get(row.get(link_field))
			if not source or not source.get("pos_package_instance"):
				continue

			row.pos_package = source["pos_package"]
			row.pos_package_instance = source["pos_package_instance"]
			row.pos_package_role = source["pos_package_role"]
			row.pos_package_snapshot = source["pos_package_snapshot"]

	for row in rows:
		if row.get("pos_package_instance") and not row.get(link_field):
			frappe.throw(
				_(
					"Package return rows must reference the original invoice row. Create the return from the POS Return screen."
				)
			)


def _validate_return_packages(doc):
	"""Force a package credit note to mirror the invoice it returns.

	A return cannot be re-quoted (its rows are copies), so it is checked against
	the original instead. Without this, two things are possible: raising the
	parent row's qty to refund more than was sold, and deleting the component
	rows to get the money back without returning any goods.
	"""
	if not doc.get("return_against"):
		if _group_invoice_rows_by_instance(doc):
			frappe.throw(_("A package return must reference the original invoice."))
		return

	_restore_return_package_metadata(doc)

	instances = _group_invoice_rows_by_instance(doc)
	if not instances:
		return

	child_doctype = _return_child_doctype(doc)
	precision = frappe.get_precision(child_doctype, "qty") or 3
	# Original rows live in the ORIGINAL invoice's child table (cross-mode
	# returns: sale made under the other invoice type).
	source_child_doctype = _original_child_doctype(doc.get("return_against"), child_doctype)

	# PERF-15: one fetch for every package instance (was one get_all per instance).
	original_rows_by_instance = {instance: [] for instance in instances}
	for row in frappe.get_all(
		source_child_doctype,
		filters={"parent": doc.return_against, "pos_package_instance": ["in", list(instances)]},
		fields=["item_code", "qty", "rate", "pos_package_role", "pos_package_instance"],
	):
		original_rows_by_instance[row["pos_package_instance"]].append(row)

	for instance, rows in instances.items():
		original_rows = original_rows_by_instance[instance]
		if not original_rows:
			frappe.throw(
				_("Package {0} does not exist on invoice {1}.").format(
					frappe.bold(instance), frappe.bold(doc.return_against)
				)
			)

		parents = [r for r in rows if r.get("pos_package_role") == PARENT_ROLE]
		original_parents = [r for r in original_rows if r.get("pos_package_role") == PARENT_ROLE]
		if len(parents) != 1 or len(original_parents) != 1:
			frappe.throw(_("Package {0} must have exactly one package line.").format(frappe.bold(instance)))

		parent = parents[0]
		original_parent = original_parents[0]

		original_parent_qty = flt(original_parent["qty"])
		if not original_parent_qty:
			frappe.throw(_("Package {0} has no quantity on the original invoice.").format(instance))

		# Returns are negative; compare magnitudes.
		fraction = abs(flt(parent.qty)) / abs(original_parent_qty)
		if fraction <= 0 or fraction > 1:
			frappe.throw(
				_("You cannot return more of {0} than was sold.").format(frappe.bold(parent.item_code))
			)

		parent.rate = flt(original_parent["rate"])
		parent.price_list_rate = flt(original_parent["rate"])
		parent.discount_amount = 0
		parent.discount_percentage = 0

		original_money = {}
		original_qty = {}
		expected = {}
		for row in original_rows:
			if row.get("pos_package_role") != COMPONENT_ROLE:
				continue
			original_money[row["item_code"]] = original_money.get(row["item_code"], 0.0) + flt(
				row["rate"]
			) * flt(row["qty"])
			original_qty[row["item_code"]] = original_qty.get(row["item_code"], 0.0) + flt(row["qty"])
			expected[row["item_code"]] = expected.get(row["item_code"], 0) + flt(row["qty"])

		rate_precision = _rate_precision(child_doctype)
		submitted = {}
		for row in rows:
			if row.get("pos_package_role") != COMPONENT_ROLE:
				continue
			# Mirror the original component rate: allocation-mode invoices carry
			# revenue on the components, so zeroing here would refund nothing.
			# Legacy component rates are already 0, keeping this a no-op there.
			row.rate = (
				flt(original_money[row.item_code] / original_qty[row.item_code], rate_precision)
				if original_qty.get(row.item_code)
				else 0
			)
			row.price_list_rate = row.rate
			row.discount_amount = 0
			row.discount_percentage = 0
			submitted[row.item_code] = submitted.get(row.item_code, 0) + abs(flt(row.qty))

		# Every component must come back in the same proportion as the parent,
		# so a partial return stays consistent and none can be dropped.
		for item_code, original_qty in expected.items():
			wanted = flt(abs(original_qty) * fraction, precision)
			got = flt(submitted.get(item_code, 0), precision)
			if wanted != got:
				frappe.throw(
					_("Package {0}: return {1} x {2} to match the package being returned (got {3}).").format(
						frappe.bold(parent.item_code), wanted, frappe.bold(item_code), got
					)
				)

		for item_code in submitted:
			if item_code not in expected:
				frappe.throw(
					_("Package {0} does not contain {1}.").format(
						frappe.bold(parent.item_code), frappe.bold(item_code)
					)
				)

	_recalculate_totals(doc)


def validate_invoice_packages(doc, method=None):
	"""Re-price every package on the invoice from its stored selection.

	Hooked on Sales Invoice ``validate``. The client sends the chosen options; the
	rates come from here, never from the payload — so an edited offline queue or a
	crafted request cannot change what a package costs.
	"""
	if doc.get("is_consolidated"):
		return
	if doc.get("is_return"):
		_validate_return_packages(doc)
		return

	instances = _group_invoice_rows_by_instance(doc)
	if not instances:
		return

	if not doc.get("pos_profile"):
		frappe.throw(_("Packages can only be sold from a POS Profile."))

	for instance, rows in instances.items():
		if not INSTANCE_PATTERN.match(instance):
			frappe.throw(_("Invalid package reference {0}.").format(frappe.bold(instance)))

		parents = [r for r in rows if r.get("pos_package_role") == PARENT_ROLE]
		if len(parents) != 1:
			frappe.throw(
				_("Package {0} must have exactly one package line, found {1}.").format(
					frappe.bold(instance), len(parents)
				)
			)

		parent = parents[0]
		package_name = parent.get("pos_package")
		if not package_name:
			frappe.throw(
				_("Package line {0} is missing its package reference.").format(frappe.bold(instance))
			)

		snapshot = _parse_json(parent.get("pos_package_snapshot"), {})
		selections = snapshot.get("selections") or []
		choices = {}
		for selection in selections:
			choices.setdefault(selection.get("group_key"), []).append(
				{"option_id": selection.get("option_id"), "qty": cint(selection.get("qty"))}
			)

		# Allocation applies only when the global toggle is on AND the stored
		# snapshot records that the quote was made that way; snapshots without
		# the marker keep the legacy single-price-on-parent repricing.
		allocate = _allocation_mode(snapshot) == ALLOCATION_MODE and _package_allocation_enabled()

		result = quote(
			package_name,
			[{"group_key": key, "options": options} for key, options in choices.items()],
			doc.pos_profile,
			allocate=allocate,
		)

		# Trust what the quote actually applied (a forged marker on a package
		# with no component rows must not zero the price).
		allocation_applied = _allocation_mode(result["snapshot"]) == ALLOCATION_MODE

		parent.discount_amount = 0
		parent.discount_percentage = 0
		parent.qty = 1
		parent.pos_package_snapshot = json.dumps(result["snapshot"])

		if allocation_applied:
			# The header stays a zero line; revenue rides the components.
			parent.rate = 0
			parent.price_list_rate = 0
			# "On Item Quantity" taxes charge every row's qty, so the zero-rate
			# package header would be taxed as one extra unit — a silent
			# overcharge in allocation mode. Fail closed rather than book it;
			# switch the tax to a value-based charge type or turn allocation off.
			per_qty_taxes = [
				tax for tax in doc.get("taxes") or [] if tax.get("charge_type") == "On Item Quantity"
			]
			if per_qty_taxes:
				frappe.throw(
					_(
						"Package {0} cannot be priced with allocation while tax {1} is charged On Item Quantity — the package header would be taxed as an extra unit. Use a value-based tax or disable package allocation."
					).format(
						frappe.bold(result["package_name"]),
						frappe.bold(
							", ".join(
								cstr(tax.get("account_head") or tax.get("description") or tax.get("idx"))
								for tax in per_qty_taxes
							)
						),
					)
				)
			rates_by_item = _allocated_component_rates(
				result["lines"][1:], _rate_precision(_return_child_doctype(doc))
			)
		else:
			# Authoritative price on the parent, zero on every component.
			parent.rate = result["total"]
			parent.price_list_rate = result["total"]
			rates_by_item = {}

		expected = {}
		for line in result["lines"][1:]:
			expected[line["item_code"]] = expected.get(line["item_code"], 0) + flt(line["qty"])

		submitted = {}
		rate_cursor = {}
		allocated_money = 0.0
		for row in rows:
			if row.get("pos_package_role") != COMPONENT_ROLE:
				continue
			item_rates = rates_by_item.get(row.item_code) or []
			position = rate_cursor.get(row.item_code, 0)
			rate = item_rates[position] if position < len(item_rates) else 0.0
			rate_cursor[row.item_code] = position + 1
			row.rate = rate
			row.price_list_rate = rate
			row.discount_amount = 0
			row.discount_percentage = 0
			submitted[row.item_code] = submitted.get(row.item_code, 0) + flt(row.qty)
			allocated_money += rate * flt(row.qty)

		if expected != submitted:
			frappe.throw(
				_("Package {0} contents do not match its definition. Please re-add the package.").format(
					frappe.bold(result["package_name"])
				)
			)

		if allocation_applied:
			row_precision = _rate_precision(_return_child_doctype(doc))
			expected_money = flt(result["total"], row_precision) * flt(parent.qty)
			if flt(allocated_money, row_precision) != expected_money:
				frappe.throw(
					_(
						"Package {0}: component rates total {1} but the package price is {2}; the price cannot be split exactly at this site's currency precision."
					).format(
						frappe.bold(result["package_name"]),
						frappe.bold(flt(allocated_money, row_precision)),
						frappe.bold(expected_money),
					)
				)

	_recalculate_totals(doc)
