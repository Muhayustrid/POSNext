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

from pos_next.api.stock_opname import _initial_state, _workflow, get_editable_open_opname
from pos_next.services.production import _resolve_profile
from pos_next.stock_opname_setup import HQ_ROLES

DOCTYPE = "Stock Reconciliation"


class CustomStockReconciliation(StockReconciliation):
	def apply_fieldlevel_read_permissions(self):
		super().apply_fieldlevel_read_permissions()
		# Frappe as_dict reconstructs deleted numeric fields with zero defaults.
		self.flags.opname_filtered_read = True

	def as_dict(self, *args, **kwargs):
		result = super().as_dict(*args, **kwargs)
		if self.flags.opname_filtered_read and not is_hq_user():
			result.pop("difference_amount", None)
			for row in result.get("items", []):
				for field in (
					"valuation_rate", "amount", "current_qty", "current_valuation_rate",
					"current_amount", "quantity_difference", "amount_difference",
					"custom_qty_before", "custom_qty_difference_per_uom", "custom_valuation_rate_per_uom",
					"current_serial_and_batch_bundle", "current_serial_no",
				):
					row.pop(field, None)
		return result

	def before_print(self, settings=None):
		if not is_hq_user():
			frappe.throw(_("Outlet stock counts cannot be printed."), frappe.PermissionError)
		return super().before_print(settings)

	def validate_higher_perm_levels(self):
		# Inspect outlet input before native reset discards unauthorized values.
		self._validate_outlet_valuation_input()
		return super().validate_higher_perm_levels()

	def before_validate(self):
		self._validate_outlet_valuation_input()

	def _validate_outlet_valuation_input(self):
		if is_hq_user():
			return
		before = self.get_doc_before_save()
		previous = {row.name: row for row in before.items} if before else {}
		for row in self.items:
			old = previous.get(row.name)
			for field in ("valuation_rate", "custom_valuation_rate_per_uom", "allow_zero_valuation_rate"):
				value = row.get(field)
				if value not in (None, "", 0, 0.0) and (not old or value != old.get(field)):
					frappe.throw(_("Outlet stock counts cannot set valuation rates."), frappe.PermissionError)
				if old:
					row.set(field, old.get(field))
				elif field == "valuation_rate":
					# Native valuation lookup treats zero as explicit, not missing.
					row.set(field, None)

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
		if self.set_warehouse:
			should_check = False
			if self.is_new():
				should_check = True
			else:
				wf = _workflow()
				if wf:
					initial = _initial_state(wf)
					before = self.get_doc_before_save()
					if before and before.workflow_state != initial and self.workflow_state == initial:
						should_check = True
			if should_check:
				open_name = get_editable_open_opname(self.set_warehouse, include_returned=True, exclude=self.name)
				if open_name:
					frappe.throw(
						_("Warehouse {0} already has an open stock opname: {1}").format(self.set_warehouse, open_name)
					)
		super().validate()
		# Draft rows have no live current qty yet, so ERPNext's differences read
		# as "counted vs zero"; blank them (HQ Desk shows live values instead).
		if self.docstatus == 0 and getattr(self, "_action", None) != "submit":
			for row in self.items:
				row.custom_qty_before = 0
				row.custom_qty_difference_per_uom = 0
				# HQ's Desk fills current_* on screen; never persist them from a save
				if not row.current_serial_and_batch_bundle:
					row.current_qty = 0
					row.current_valuation_rate = 0
				row.current_amount = 0
				row.quantity_difference = 0
				row.amount_difference = 0
			self.difference_amount = 0

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


def assert_system_stock_access(warehouse=None, company=None, user=None):
	"""Check HQ membership, then native read scope when a warehouse is supplied."""
	user = user or frappe.session.user
	if (
		user == "Guest"
		or not frappe.db.exists("User", {"name": user, "enabled": 1})
		or not is_hq_user(user)
		or not frappe.has_permission(DOCTYPE, "read", user=user)
	):
		frappe.throw(_("Not permitted to read system stock."), frappe.PermissionError)
	if warehouse is None and company is None:
		return
	if not warehouse or not frappe.db.exists("Warehouse", warehouse):
		frappe.throw(_("A permitted warehouse is required."), frappe.PermissionError)
	warehouse_doc = frappe.get_doc("Warehouse", warehouse)
	if not warehouse_doc.company or (company and company != warehouse_doc.company):
		frappe.throw(_("Warehouse and company do not match."), frappe.PermissionError)
	if not warehouse_doc.has_permission("read", user=user) or not frappe.get_doc(
		"Company", warehouse_doc.company
	).has_permission("read", user=user):
		frappe.throw(_("Not permitted to read this warehouse or company."), frappe.PermissionError)


@frappe.whitelist()
def get_stock_balance_for(
	item_code: str, warehouse: str, posting_date, posting_time,
	batch_no: str | None = None, with_valuation_rate: bool = True,
	inventory_dimensions_dict=None, row=None, company=None,
):
	from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import (
		get_stock_balance_for as native_get_stock_balance_for,
	)

	assert_system_stock_access(warehouse or "", company)
	return native_get_stock_balance_for(
		item_code, warehouse, posting_date, posting_time, batch_no,
		with_valuation_rate, inventory_dimensions_dict, row, company,
	)


@frappe.whitelist()
def get_items(warehouse, posting_date, posting_time, company, item_code=None, ignore_empty_stock=False):
	assert_system_stock_access(warehouse or "", company)
	if "warehouse_app" in frappe.get_installed_apps():
		from warehouse_app.warehouse_app.stock_reconciliation import get_items as delegate
	else:
		from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import get_items as delegate
	return delegate(warehouse, posting_date, posting_time, company, item_code, ignore_empty_stock)


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
	return bool(warehouse and warehouse in outlet_warehouses(user))
