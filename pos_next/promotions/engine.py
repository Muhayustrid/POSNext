"""Dynamic Promotion engine for Sales Invoice (Task 4).

Owns the single materialization pass, the per-invoice instance cap, the frozen
snapshot, and the rate/warehouse re-assertion that keeps Model C true for the
whole draft lifetime.

Immutability after submit (I3) is deliberately not re-implemented here. All four
promotion Custom Fields carry ``allow_on_submit = 0``, so Frappe's own
``validate_update_after_submit`` rejects any change to a promotion field, row, or
selection on a submitted document. A second engine-side guard would cover the
same condition and pin neither.
"""

import json
import uuid

import frappe
from frappe import _
from frappe.utils import flt, now_datetime

from pos_next.promotions import eligibility, pricing

INSTANCE_FIELD = "pos_promotion_instance"
ROLE_FIELD = "pos_promotion_role"
PENDING_FIELD = "pos_pending_promotions"
SELECTIONS_FIELD = "pos_promotion_selections"

PARENT_ROLE = "Promotion Parent"
COMPONENT_ROLE = "Promotion Component"


def on_before_validate(doc, method=None):
	"""Materialize the pending payload before AccountsController.validate computes totals."""
	if doc.docstatus == 0 and doc.get(PENDING_FIELD):
		_materialize_pending_promotions(doc)
	_reassert_promotion_invariants(doc)
	# The return guard runs at this event and only here. ``before_validate`` is the
	# earliest hook and Frappe runs it for both the save and the submit action
	# (frappe/model/document.py run_before_save_methods), so submission can never be
	# the first time this is checked and a second call at ``before_submit`` would
	# cover the same condition without pinning it. Placing it earlier than the other
	# guards is what keeps the named error visible: hooks on ``validate`` run after
	# Sales Invoice's own validate body, which rejects an incomplete return first
	# with a payment or negative-quantity message and hides the real reason.
	_validate_return_completeness(doc)


def on_validate(doc, method=None):
	"""Re-assert the frozen representation and refuse any row that is not fully backed."""
	if doc.docstatus == 0 and doc.get(PENDING_FIELD):
		_materialize_pending_promotions(doc)

	_reassert_promotion_invariants(doc)
	_validate_promotion_row_integrity(doc)


def on_before_submit(doc, method=None):
	"""Re-assert only: submission must not be the first time an invariant is checked."""
	_reassert_promotion_invariants(doc)
	_validate_promotion_row_integrity(doc)


# --- materialization -------------------------------------------------------


def _materialize_pending_promotions(doc):
	"""Consume the pending payload exactly once into selections plus invoice rows."""
	raw_payload = doc.get(PENDING_FIELD)
	if not raw_payload:
		return

	# I8: one materialization pass per invoice. A second non-empty payload on a
	# draft that already holds selections is refused, never merged or ignored.
	if doc.get(SELECTIONS_FIELD):
		frappe.throw(
			_("Cannot apply new promotion payload to an invoice with existing promotion selections"),
			frappe.ValidationError,
		)

	try:
		payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
	except Exception as e:
		frappe.throw(_("Invalid JSON in pending promotions payload: {0}").format(e), frappe.ValidationError)

	instances = payload.get("instances") or []
	if not instances:
		doc.set(PENDING_FIELD, None)
		return

	company, outlet_warehouse = eligibility.resolve_outlet_context(doc.get("pos_profile"))
	currency = _resolve_transaction_currency(doc, company)
	_apply_currency_defaults(doc, currency)
	accounts = _resolve_row_accounts(doc)

	promotions = _load_payload_promotions(doc, instances, company, outlet_warehouse, currency)
	_enforce_instance_cap(instances, promotions)

	context = {"company": company, "warehouse": outlet_warehouse}
	for inst in instances:
		promo = promotions[inst["promotion"]]
		quote = pricing.quote(promo, inst.get("selections") or [], context)
		instance_id = f"inst_{uuid.uuid4().hex[:12]}"

		doc.append(
			SELECTIONS_FIELD,
			{
				"instance_id": instance_id,
				"promotion": promo.name,
				"total_amount": quote["total_price"],
				"snapshot": json.dumps(_build_snapshot(promo, quote)),
			},
		)

		_append_promotion_row(doc, quote["parent_row"], PARENT_ROLE, instance_id, accounts)
		for component_row in quote["component_rows"]:
			_append_promotion_row(doc, component_row, COMPONENT_ROLE, instance_id, accounts)

	# Consumed exactly once. From here on the persisted selections and rows are
	# the only authority; the raw request payload is never retained.
	doc.set(PENDING_FIELD, None)

	if hasattr(doc, "set_missing_values"):
		doc.set_missing_values()


def _resolve_transaction_currency(doc, company):
	"""Currency of the outlet context, not of the half-built document.

	At ``before_validate`` the document's own ``currency`` still holds the field
	default; ERPNext only rewrites it from the POS Profile later in its own
	``set_missing_values``. Reading it here would compare the promotion against a
	currency the transaction never uses, so the POS Profile — and then the outlet
	Company — is the authority.
	"""
	if doc.get("pos_profile"):
		profile_currency = frappe.db.get_value("POS Profile", doc.pos_profile, "currency")
		if profile_currency:
			return profile_currency

	return frappe.get_cached_value("Company", company, "default_currency")


def _load_payload_promotions(doc, instances, company, outlet_warehouse, currency):
	"""Resolve and eligibility-check every Promotion named by the payload."""
	promotions = {}
	for inst in instances:
		promo_name = inst.get("promotion")
		if not promo_name:
			frappe.throw(_("Promotion is required for every promotion instance"), frappe.ValidationError)
		if promo_name in promotions:
			continue
		# Defense-in-depth, deliberately without its own killing test: the
		# frappe.get_doc below already raises DoesNotExistError — a
		# ValidationError subclass — with the identical "Promotion {0} not
		# found" message (frappe/model/document.py load_from_db), so no honest
		# test can distinguish this line from the framework's own rejection.
		if not frappe.db.exists("Promotion", promo_name):
			frappe.throw(_("Promotion {0} not found").format(promo_name), frappe.ValidationError)

		promo = frappe.get_doc("Promotion", promo_name)
		is_eligible, reason = eligibility.check(
			promo,
			company,
			outlet_warehouse,
			on_date=doc.get("posting_date"),
			currency=currency,
		)
		if not is_eligible:
			frappe.throw(
				_("Promotion {0} is not eligible for this transaction: {1}").format(promo_name, reason),
				frappe.ValidationError,
			)
		promotions[promo_name] = promo

	return promotions


def _enforce_instance_cap(instances, promotions):
	"""I16 / D19: reject an over-cap payload before any row or selection exists.

	Instances are counted, never rows, so identical choices still count
	separately and duplicated invoice rows cannot influence the count.
	``max_instances_per_invoice = 0`` means unlimited.
	"""
	counts = {}
	for inst in instances:
		promo_name = inst["promotion"]
		counts[promo_name] = counts.get(promo_name, 0) + 1

	for promo_name, requested in counts.items():
		cap = int(flt(promotions[promo_name].max_instances_per_invoice))
		if cap > 0 and requested > cap:
			frappe.throw(
				_("Promotion {0} allows at most {1} instance(s) per invoice, but {2} were requested").format(
					promo_name, cap, requested
				),
				frappe.ValidationError,
			)


def _append_promotion_row(doc, descriptor, role, instance_id, accounts):
	"""Append one invoice row from a pricing descriptor, frozen to its role."""
	item = frappe.get_cached_doc("Item", descriptor["item_code"])
	qty = flt(descriptor["qty"])
	rate = flt(descriptor["rate"])
	amount = qty * rate

	row = {
		"item_code": descriptor["item_code"],
		"item_name": item.item_name,
		"qty": qty,
		"uom": item.stock_uom,
		"stock_uom": item.stock_uom,
		"conversion_factor": 1.0,
		"stock_qty": qty,
		"rate": rate,
		"price_list_rate": 0.0,
		"amount": amount,
		"base_rate": rate,
		"base_amount": amount,
		"discount_amount": 0.0,
		"discount_percentage": 0.0,
		"warehouse": descriptor["warehouse"],
		INSTANCE_FIELD: instance_id,
		ROLE_FIELD: role,
	}
	row.update(accounts)
	doc.append("items", row)


def _build_snapshot(promo, quote):
	"""Freeze the master state this instance was sold under (design section 10)."""
	options_by_name = {opt.name: opt for opt in promo.options or []}
	chosen_options = []
	for group in quote["choices_summary"]:
		for pick in group["picks"]:
			option = options_by_name[pick["option_row"]]
			chosen_options.append(
				{
					"group_key": group["group_key"],
					"group_label": group["label"],
					"option_row": option.name,
					"item_code": option.item_code,
					"item_name": frappe.get_cached_value("Item", option.item_code, "item_name"),
					"qty": flt(pick["qty"]),
					"price_adjustment": flt(option.price_adjustment),
				}
			)

	return {
		"promotion": promo.name,
		"promotion_name": promo.promotion_name,
		"root_company": promo.root_company,
		"currency": promo.currency,
		"base_price": flt(promo.base_price),
		"max_instances_per_invoice": promo.max_instances_per_invoice,
		"parent_item": promo.parent_item,
		"choice_groups": [
			{"group_key": g.group_key, "label": g.label, "pick_count": g.pick_count}
			for g in promo.choice_groups or []
		],
		"fixed_components": [{"item_code": c.item_code, "qty": flt(c.qty)} for c in promo.components or []],
		"chosen_options": chosen_options,
		"total_amount": quote["total_price"],
		"timestamp": str(now_datetime()),
	}


def _apply_currency_defaults(doc, currency):
	"""Fill the currency fields AccountsController needs before totals are computed."""
	doc.currency = currency
	if not doc.get("conversion_rate"):
		doc.conversion_rate = 1.0
	if not doc.get("plc_conversion_rate"):
		doc.plc_conversion_rate = 1.0
	if not doc.get("price_list_currency"):
		doc.price_list_currency = currency


def _resolve_row_accounts(doc):
	"""Read the POS Profile accounting defaults applied to every generated row."""
	if not doc.get("pos_profile"):
		return {}

	profile = frappe.db.get_value(
		"POS Profile",
		doc.pos_profile,
		["income_account", "cost_center", "expense_account", "selling_price_list"],
		as_dict=True,
	)
	if not profile:
		return {}

	if not doc.get("selling_price_list") and profile.selling_price_list:
		doc.selling_price_list = profile.selling_price_list

	return {
		"income_account": profile.income_account,
		"cost_center": profile.cost_center,
		"expense_account": profile.expense_account,
	}


# --- enforcement -----------------------------------------------------------


def _reassert_promotion_invariants(doc):
	"""I1 / I2 / I13: restore rate, discount, and warehouse from the frozen selection."""
	promotion_rows = [row for row in doc.get("items") or [] if row.get(INSTANCE_FIELD) or row.get(ROLE_FIELD)]
	if not promotion_rows:
		return

	selections = _resolve_selections(doc)
	outlet_warehouse = _resolve_outlet_warehouse(doc)

	for row in promotion_rows:
		role = row.get(ROLE_FIELD)
		selection = selections.get(row.get(INSTANCE_FIELD))

		if role == PARENT_ROLE and selection:
			row.rate = flt(selection.total_amount)
		elif role == COMPONENT_ROLE:
			row.rate = 0.0
		else:
			continue

		row.price_list_rate = 0.0
		row.amount = flt(row.qty) * flt(row.rate)
		row.base_rate = row.rate
		row.base_amount = row.amount
		row.discount_amount = 0.0
		row.discount_percentage = 0.0
		if outlet_warehouse:
			row.warehouse = outlet_warehouse

	if hasattr(doc, "calculate_taxes_and_totals"):
		doc.calculate_taxes_and_totals()


def _validate_promotion_row_integrity(doc):
	"""I15: a promotion parent may never stand alone or unbacked.

	Row-driven on purpose. Judging the selection table instead would reject a
	return that legitimately carries a subset of rows; return completeness is
	the return guard's rule, not this one.
	"""
	rows = doc.get("items") or []
	if not rows:
		return

	selections = _resolve_selections(doc)
	parent_items = _promotion_parent_items(rows)

	parent_rows_by_instance = {}
	component_instances = set()

	for row in rows:
		instance_id = row.get(INSTANCE_FIELD)
		role = row.get(ROLE_FIELD)

		if row.item_code in parent_items and role != PARENT_ROLE:
			frappe.throw(
				_("Row {0}: Item {1} is a Promotion parent item and cannot be sold on its own").format(
					row.idx, row.item_code
				),
				frappe.ValidationError,
			)

		if not instance_id and not role:
			continue

		if not instance_id or not role:
			frappe.throw(
				_("Row {0}: a promotion row must carry both a promotion instance and a role").format(row.idx),
				frappe.ValidationError,
			)

		if instance_id not in selections:
			frappe.throw(
				_("Row {0}: promotion instance {1} has no backing promotion selection").format(
					row.idx, instance_id
				),
				frappe.ValidationError,
			)

		if role == PARENT_ROLE:
			if instance_id in parent_rows_by_instance:
				frappe.throw(
					_("Row {0}: promotion instance {1} already has a parent row on row {2}").format(
						row.idx, instance_id, parent_rows_by_instance[instance_id]
					),
					frappe.ValidationError,
				)
			parent_rows_by_instance[instance_id] = row.idx
		elif role == COMPONENT_ROLE:
			component_instances.add(instance_id)
		else:
			frappe.throw(
				_("Row {0}: unknown promotion role {1}").format(row.idx, role), frappe.ValidationError
			)

	for instance_id, idx in parent_rows_by_instance.items():
		if instance_id not in component_instances:
			frappe.throw(
				_("Row {0}: promotion instance {1} carries no component rows").format(idx, instance_id),
				frappe.ValidationError,
			)


# --- return guard ----------------------------------------------------------


def _validate_return_completeness(doc):
	"""I6 / D11: a promotion instance is returned whole or not at all.

	Model C assigns all revenue to the parent and none to the components, so a
	partial instance return has no defensible refund amount. Completeness is
	measured per instance against the source invoice's own rows, which is why a
	return without ``return_against`` can never carry a promotion row: there is
	nothing to prove completeness against. The sale-side instance cap (I16) is
	deliberately not re-checked here — a valid return must not start failing
	because the master limit changed after the sale.
	"""
	if not doc.get("is_return"):
		return

	# Selected on either field: a row carrying only the role would otherwise slip
	# past this guard and be rejected later by _validate_promotion_row_integrity,
	# which runs on ``validate`` and therefore behind ERPNext's own return errors.
	# The role branch and the missing-instance throw below form one causal
	# guard: the branch's only observable effect is delivering a role-only row
	# to that throw, so every test that kills one kills the other. They share
	# their killer by construction, not by omission (Task 7 mutation review).
	promotion_rows = [row for row in doc.get("items") or [] if row.get(INSTANCE_FIELD) or row.get(ROLE_FIELD)]
	if not promotion_rows:
		return

	if not doc.get("return_against"):
		frappe.throw(
			_("A promotion instance cannot be returned without a source invoice"),
			frappe.ValidationError,
		)

	source_rows = _source_promotion_rows(doc)

	returned_qty = {}
	for row in promotion_rows:
		if not row.get(INSTANCE_FIELD):
			frappe.throw(
				_("Row {0}: a promotion row on a return must carry its promotion instance").format(row.idx),
				frappe.ValidationError,
			)
		# Sign is checked here rather than absorbed with abs(): a positive promotion
		# row would otherwise satisfy the returned quantity of a negative one.
		if flt(row.qty) > 0:
			frappe.throw(
				_("Row {0}: a promotion row on a return must carry a negative quantity").format(row.idx),
				frappe.ValidationError,
			)
		# The sign check above already guarantees a non-positive quantity, so this is
		# a plain sign flip rather than a normalization. Using abs() here instead
		# would be an equivalent expression, not a second guard.
		key = (row.get(INSTANCE_FIELD), row.item_code)
		returned_qty[key] = returned_qty.get(key, 0.0) - flt(row.qty)

	returned_instances = {row.get(INSTANCE_FIELD) for row in promotion_rows}
	for instance_id in sorted(returned_instances):
		expected = source_rows.get(instance_id)
		if not expected:
			frappe.throw(
				_("Promotion instance {0} is not present on {1}").format(instance_id, doc.return_against),
				frappe.ValidationError,
			)

		for item_code, expected_qty in expected.items():
			if flt(returned_qty.get((instance_id, item_code))) != flt(expected_qty):
				frappe.throw(
					_(
						"Promotion instance {0} must be returned in full: item {1} expects "
						"quantity {2} but the return carries {3}"
					).format(
						instance_id,
						item_code,
						flt(expected_qty),
						flt(returned_qty.get((instance_id, item_code))),
					),
					frappe.ValidationError,
				)

		extra_items = {item_code for inst, item_code in returned_qty if inst == instance_id} - set(expected)
		if extra_items:
			frappe.throw(
				_("Promotion instance {0} must be returned in full: item {1} was never sold on {2}").format(
					instance_id, sorted(extra_items)[0], doc.return_against
				),
				frappe.ValidationError,
			)


def _source_promotion_rows(doc):
	"""Sold quantity per instance and item on the returned-against invoice.

	Read straight from the child table rather than the source document so that a
	non-promotion column, a controller default, or a later amendment of the
	source cannot change what completeness is measured against.

	The instance filter narrows the read; it is not a guard. Unfiltered rows would
	only add an entry keyed on the empty instance, which no returned instance can
	match because the caller throws on a promotion row that carries none.
	"""
	rows = frappe.get_all(
		f"{doc.doctype} Item",
		filters={"parent": doc.return_against, INSTANCE_FIELD: ["is", "set"]},
		fields=[INSTANCE_FIELD, "item_code", "qty"],
	)

	sold = {}
	for row in rows:
		instance_id = row.get(INSTANCE_FIELD)
		per_item = sold.setdefault(instance_id, {})
		per_item[row.item_code] = per_item.get(row.item_code, 0.0) + flt(row.qty)

	return sold


def _promotion_parent_items(rows):
	"""Item codes on this document that any Promotion claims as its parent item."""
	item_codes = {row.item_code for row in rows if row.item_code}
	if not item_codes:
		return set()

	return set(
		frappe.get_all("Promotion", filters={"parent_item": ["in", list(item_codes)]}, pluck="parent_item")
	)


def _resolve_selections(doc):
	"""Map instance id to selection, falling back to the returned-against invoice."""
	selections = doc.get(SELECTIONS_FIELD) or []
	if not selections and doc.get("is_return") and doc.get("return_against"):
		original = frappe.get_doc(doc.doctype, doc.return_against)
		selections = original.get(SELECTIONS_FIELD) or []

	return {row.instance_id: row for row in selections}


def _resolve_outlet_warehouse(doc):
	"""D14: the outlet warehouse comes from the authoritative POS context only."""
	if doc.get("pos_profile"):
		return eligibility.resolve_outlet_context(doc.pos_profile)[1]

	return None
