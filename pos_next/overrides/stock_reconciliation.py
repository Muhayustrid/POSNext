# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Stock Opname: blind-count override + outlet scoping for Stock Reconciliation.

Outlets count physical stock without seeing system quantities. ERPNext's
`remove_items_with_no_change` would delete counted rows that equal system
stock (leaking the system qty and blocking all-correct counts), so drafts keep
every row; filtering still runs on submit. One open opname per warehouse, and
the workflow state is persisted before ERPNext's background submit (>100 rows)
reloads the doc from DB.

Outlet-only users (no HQ role) are scoped to the warehouse of a POS Profile
they are a `POS Profile User` of, via `permission_query_conditions` (list view)
and `has_permission` (controller denies; it cannot grant).
"""

import frappe
from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
	EmptyStockReconciliationItemsError,
	StockReconciliation,
)
from frappe import _
from frappe.utils import flt

from pos_next.services.production import _resolve_profile
from pos_next.stock_opname_setup import HQ_ROLES

DOCTYPE = "Stock Reconciliation"


class CustomStockReconciliation(StockReconciliation):
	def remove_items_with_no_change(self):
		# Drafts keep every counted row (blind count); filter only on submit.
		if self.docstatus == 0 and getattr(self, "_action", None) != "submit":
			# super() resets this; ERPNext validate_data later does `difference_amount +=`
			self.difference_amount = 0.0
			return
		try:
			super().remove_items_with_no_change()
		except EmptyStockReconciliationItemsError:
			frappe.throw(
				_(
					"Every counted quantity matches the system stock; nothing to adjust. Reject or delete this opname."
				),
				EmptyStockReconciliationItemsError,
			)

	def validate(self):
		if (
			self.is_new()
			and self.set_warehouse
			and frappe.db.exists(DOCTYPE, {"docstatus": 0, "set_warehouse": self.set_warehouse})
		):
			open_name = frappe.db.get_value(
				DOCTYPE, {"docstatus": 0, "set_warehouse": self.set_warehouse}, "name"
			)
			frappe.throw(
				_("Warehouse {0} already has an open stock opname: {1}").format(self.set_warehouse, open_name)
			)
		# Blind count: the warehouse_app hook fills system-derived custom_qty_before
		# and custom_qty_difference_per_uom in before_validate; on a draft they would
		# leak the system qty to the outlet. Clear them — the hook recomputes both on
		# the submit that posts stock (HQ still reads system qty via get_system_qty).
		if self.docstatus == 0 and getattr(self, "_action", None) != "submit":
			for row in self.items:
				row.custom_qty_before = 0
				row.custom_qty_difference_per_uom = 0
		super().validate()
		self._fill_per_uom_valuation_rate()
		# Draft rows have no live current qty yet, so ERPNext's differences read
		# as "counted vs zero"; blank them (HQ Desk shows live values instead).
		if self.docstatus == 0 and getattr(self, "_action", None) != "submit":
			for row in self.items:
				# HQ's Desk fills current_* on screen; never persist them from a save
				if not row.current_serial_and_batch_bundle:
					row.current_qty = 0
					row.current_valuation_rate = 0
				row.current_amount = 0
				row.quantity_difference = 0
				row.amount_difference = 0
			self.difference_amount = 0

	def _fill_per_uom_valuation_rate(self):
		"""Mirror ERPNext's default valuation_rate into warehouse_app's
		`custom_valuation_rate_per_uom` (outlet counts never send a rate), so the
		Desk row shows both. An explicit rate, or an allowed zero, is kept."""
		if not frappe.get_meta("Stock Reconciliation Item").has_field("custom_valuation_rate_per_uom"):
			return
		for row in self.items:
			rate = row.get("custom_valuation_rate_per_uom")
			if rate not in (None, "") and (flt(rate) or row.get("allow_zero_valuation_rate")):
				continue
			if flt(row.valuation_rate):
				factor = flt(row.get("custom_conversion_factor")) or 1
				row.custom_valuation_rate_per_uom = flt(
					flt(row.valuation_rate) * factor, row.precision("custom_valuation_rate_per_uom")
				)

	def submit(self):
		# The background worker reloads from DB, losing the in-memory
		# workflow state apply_workflow just set — persist it first.
		if len(self.items) > 100 and frappe.db.exists("Workflow", {"document_type": DOCTYPE, "is_active": 1}):
			self.db_set("workflow_state", self.workflow_state, update_modified=False)
		super().submit()


def outlet_warehouses(user=None):
	"""Warehouses of POS Profiles `user` is a `POS Profile User` of.

	Resolved through `_resolve_profile` (same as creation in Task 3): a
	profile with an empty warehouse falls back to the company default.
	Unresolvable profiles are skipped.
	"""
	user = user or frappe.session.user
	profiles = frappe.get_all(
		"POS Profile User",
		filters={"user": user, "parenttype": "POS Profile"},
		pluck="parent",
	)
	warehouses = []
	for profile in profiles:
		try:
			_warehouse = _resolve_profile(profile)[1]
		except Exception:
			continue
		if _warehouse and _warehouse not in warehouses:
			warehouses.append(_warehouse)
	return warehouses


def is_hq_user(user=None):
	"""True when `user` holds any HQ role (four-eyes for everyone, spec W1)."""
	user = user or frappe.session.user
	return bool(set(frappe.get_roles(user)) & set(HQ_ROLES))


def get_permission_query_conditions(user):
	if is_hq_user(user):
		return ""
	warehouses = outlet_warehouses(user)
	if not warehouses:
		return "1=0"
	escaped = ", ".join(frappe.db.escape(w) for w in warehouses)
	return f"`tabStock Reconciliation`.set_warehouse in ({escaped})"


def has_permission(doc, ptype=None, user=None):
	"""Controller: deny outlet users outside their warehouses; HQ defers.

	Note: the framework treats a falsy controller result (including None)
	as deny, so HQ must return True — controllers can only deny, never grant.
	"""
	if is_hq_user(user):
		return True
	warehouse = doc.get("set_warehouse")
	if not warehouse:
		return True
	return bool(warehouse in outlet_warehouses(user)) or False
