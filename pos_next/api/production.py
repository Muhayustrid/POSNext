# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Thin POS production endpoints (design D12): profile access gate + input
parsing. All orchestration lives in pos_next.services.production — Work Order
lifecycle, BOM sync, Stock Entry construction and stock reads."""

import json
import math

import frappe
from frappe import ValidationError, _
from frappe.utils import cint, flt

from pos_next.api.packages import _assert_profile_access
from pos_next.services.production import (  # noqa: F401  (_uom_whole_field re-exported for tests)
	_pos_status_label,
	_uom_whole_field,
	close_production as _close_production,
	create_production as _create_production,
	cancel_production as _cancel_production,
	finish_production as _finish_production,
	get_active_productions as _get_active_productions,
	get_finish_context as _get_finish_context,
	get_production_dashboard as _get_production_dashboard,
	get_production_history as _get_production_history,
	get_recipes as _get_recipes,
	start_production as _start_production,
)


@frappe.whitelist()
def get_production_recipes(pos_profile):
	"""Enabled recipes for the profile's company, with stock/batch info per material."""
	_assert_profile_access(pos_profile)
	try:
		return _get_recipes(pos_profile)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Production Recipes Error")
		frappe.throw(_("Error fetching production recipes: {0}").format(str(e)))


def _parse_float(value):
	return None if value in (None, "") else flt(value)


@frappe.whitelist()
def create_production(recipe, qty, pos_profile, items=None, batches=None, good_qty=None, loss_qty=None, notes=None):
	"""One-shot production: Work Order + Manufacture Stock Entry in one request.

	Materials and batches always derive server-side from the trusted recipe and
	available stock, scaled to the requested output quantity. The legacy
	items/batches params are accepted (stale cached POS clients still send them)
	but intentionally ignored — clients cannot override BOM quantities.
	"""
	# IDOR gate (SEC-NEW-03): pos_profile selects the warehouses the Work Order
	# and Manufacture Stock Entry post to, so the caller must be assigned to
	# that profile (or manage POS Profiles) before anything is resolved or
	# posted. Deliberately outside the try: the generic handler below would
	# swallow the PermissionError into a 500-style ValidationError.
	_assert_profile_access(pos_profile)
	try:
		qty = _require_positive(qty, "Production quantity")
		good = _parse_float(good_qty)
		if good is not None:
			good = _require_positive(good, "Good quantity")
		loss = _parse_float(loss_qty)
		if loss is not None and (loss < 0 or not math.isfinite(loss)):
			frappe.throw(_("Loss quantity cannot be negative"))
		return _create_production(
			recipe,
			qty,
			pos_profile,
			good_qty=good,
			loss_qty=loss or 0,
			notes=notes,
		)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Create Production Error")
		frappe.throw(_("Error creating production: {0}").format(str(e)))


@frappe.whitelist()
def start_production(recipe, qty, pos_profile, notes=None):
	"""Two-phase flow, phase 1: submit a Work Order for the recipe (no stock moved)."""
	_assert_profile_access(pos_profile)
	try:
		qty = _require_positive(qty, "Production quantity")
		return _start_production(recipe, qty, pos_profile, notes=notes)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Start Production Error")
		frappe.throw(_("Error starting production: {0}").format(str(e)))


@frappe.whitelist()
def get_finish_context(work_order):
	"""Materials + live batches for the finish form's batch pickers."""
	_assert_profile_access(_profile_of(work_order))
	try:
		return _get_finish_context(work_order)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Finish Context Error")
		frappe.throw(_("Error fetching finish context: {0}").format(str(e)))


@frappe.whitelist()
def finish_production(work_order, good_qty, loss_qty=None, notes=None, batch_picks=None):
	"""Two-phase flow, phase 2: post the Manufacture Stock Entry for a Work Order."""
	_assert_profile_access(_profile_of(work_order))
	try:
		good = _require_positive(good_qty, "Good quantity")
		loss = _parse_float(loss_qty)
		if loss is not None and (loss < 0 or not math.isfinite(loss)):
			frappe.throw(_("Loss quantity cannot be negative"))
		return _finish_production(
			work_order,
			good,
			loss_qty=loss or 0,
			notes=notes,
			batch_picks=_parse_batch_picks(batch_picks),
		)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Finish Production Error")
		frappe.throw(_("Error finishing production: {0}").format(str(e)))


@frappe.whitelist()
def close_production(work_order, writeoff=None, notes=None):
	"""Stop early: close the Work Order; writeoff=1 also issues the leftover
	materials to the stock adjustment account (total failure, D10 case 5)."""
	_assert_profile_access(_profile_of(work_order))
	try:
		return _close_production(work_order, writeoff=cint(writeoff), notes=notes)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Close Production Error")
		frappe.throw(_("Error closing production: {0}").format(str(e)))


@frappe.whitelist()
def cancel_production(work_order):
	"""Cancel the Work Order's entries LIFO, then the Work Order itself."""
	_assert_profile_access(_profile_of(work_order))
	try:
		return _cancel_production(work_order)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Cancel Production Error")
		frappe.throw(_("Error cancelling production: {0}").format(str(e)))


@frappe.whitelist()
def get_active_productions(pos_profile):
	"""Work Orders of this outlet still Not Started / In Process (two-phase list)."""
	_assert_profile_access(pos_profile)
	try:
		return _get_active_productions(pos_profile)
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Active Productions Error")
		frappe.throw(_("Error fetching active productions: {0}").format(str(e)))


@frappe.whitelist()
def get_production_history(pos_profile, limit=30, offset=0):
	"""Finished Work Orders of this outlet (Completed / Closed / Cancelled)."""
	_assert_profile_access(pos_profile)
	try:
		return _get_production_history(pos_profile, limit=cint(limit), offset=cint(offset))
	except ValidationError:
		raise
	except Exception as e:
		frappe.log_error(frappe.get_traceback(), "Get Production History Error")
		frappe.throw(_("Error fetching production history: {0}").format(str(e)))


def _period(from_date, to_date):
	from pos_next.api.shifts import _validate_period

	return _validate_period(from_date, to_date)

@frappe.whitelist()
def get_production_dashboard(pos_profile, from_date, to_date):
	"""Output / loss / runs of this outlet over a creation-date window."""
	_assert_profile_access(pos_profile)
	from_date, to_date = _period(from_date, to_date)
	return _get_production_dashboard(pos_profile, from_date, to_date)

EXPORT_HEADERS = ("Date", "Work Order", "Item", "Recipe", "Planned", "Produced", "Loss", "Status", "Operator")

@frappe.whitelist()
def export_production_history(pos_profile, from_date, to_date):
	"""Download this outlet's Work Orders in the window as .xlsx (GET)."""
	_assert_profile_access(pos_profile)
	from frappe.utils.xlsxutils import build_xlsx_response

	from_date, to_date = _period(from_date, to_date)
	rows = frappe.db.sql(
		"""SELECT wo.creation, wo.name, COALESCE(i.item_name, wo.production_item) AS item_name,
			r.recipe_name, wo.qty, wo.produced_qty, wo.process_loss_qty, wo.status,
			wo.docstatus, wo.posa_operator
		FROM `tabWork Order` wo
		LEFT JOIN `tabItem` i ON i.name = wo.production_item
		LEFT JOIN `tabPOS Production Recipe` r ON r.name = wo.posa_recipe
		WHERE wo.posa_pos_profile = %s AND wo.docstatus > 0 AND DATE(wo.creation) BETWEEN %s AND %s
		ORDER BY wo.creation DESC""",
		(pos_profile, from_date, to_date),
		as_dict=True,
	)
	data = [list(EXPORT_HEADERS)] + [
		[
			str(r.creation)[:16],
			r.name,
			r.item_name,
			r.recipe_name or "",
			flt(r.qty),
			flt(r.produced_qty),
			flt(r.process_loss_qty),
			"Cancelled" if r.docstatus == 2 else _pos_status_label(r.status, r.produced_qty, r.qty),
			r.posa_operator or "",
		]
		for r in rows
	]
	return build_xlsx_response(data, f"production-{from_date}-{to_date}")

def _profile_of(work_order):
	"""The profile a Work Order belongs to decides who may act on it (SEC-NEW-03
	still applies: finish/close/cancel select the warehouses and ledger moves)."""
	profile = frappe.db.get_value("Work Order", work_order, "posa_pos_profile") if work_order else None
	if not profile:
		frappe.throw(_("Work Order {0} was not created from POS").format(work_order))
	return profile


def _parse_batch_picks(raw):
	"""item_code -> batch_no, arriving as a JSON string from the form (tests may
	pass a dict). Empty values are dropped; unknown item codes are ignored
	downstream (the service only reads keys of its own raw rows)."""
	if raw in (None, "", "null"):
		return None
	try:
		picks = json.loads(raw) if isinstance(raw, str) else raw
	except ValueError:
		frappe.throw(_("Invalid batch selection"))
	if not isinstance(picks, dict):
		frappe.throw(_("Invalid batch selection"))
	clean = {str(k): str(v).strip() for k, v in picks.items() if str(v).strip()}
	return clean or None


def _require_positive(value, label):
	qty = flt(value)
	if qty <= 0 or not math.isfinite(qty):
		frappe.throw(_("{0} must be greater than zero").format(_(label)))
	return qty
