# Copyright (c) 2024, POS Next and contributors
# For license information, please see license.txt

"""
Real-time event handlers for POS Next.
Emits Socket.IO events when stock-affecting transactions occur.
"""

import frappe
from frappe import _

from pos_next.api.items import get_stock_quantities


def publish_to_profile(event, message, pos_profile):
	"""Publish to the pos_profile room, never site-wide.

	PERF-06/SEC-NEW-04: invoice/customer events carry transaction and PII
	details; publishing with user=None landed in the site room ("all") that
	every System User's socket joins, leaking them to every logged-in session.
	Rooms are joined only by sockets verified as POS Profile Users of that
	profile (see pos_next/realtime/handlers.js). No profile means no safe
	room, so the event is dropped instead of broadcast.
	"""
	if not pos_profile:
		return
	frappe.publish_realtime(
		event=event,
		message=message,
		room=f"pos_profile:{pos_profile}",
		after_commit=True,  # Only emit after successful DB commit
	)


def emit_stock_update_event(doc, method=None):
	"""
	Emit real-time stock update event when Sales Invoice is submitted.

	This event notifies all connected POS terminals about stock changes,
	allowing them to update their cached item quantities in real-time.

	Args:
		doc: Sales Invoice document
		method: Hook method name (on_submit, on_cancel, etc.)
	"""
	if not doc.update_stock:
		return

	# Skip if not a POS invoice (check if field exists first)
	if hasattr(doc, "is_pos") and not doc.is_pos:
		return

	# PERF-06: the event is only meaningful to terminals of this invoice's
	# profile. Resolve the room before doing any work so non-POS invoices
	# keep their early return and profile-less invoices skip the stock queries.
	pos_profile = doc.get("pos_profile")
	if not pos_profile:
		return

	try:
		# Collect unique item codes per warehouse to avoid redundant queries
		item_codes_by_warehouse = {}
		for item in doc.items:
			item_code = getattr(item, "item_code", None)
			warehouse = getattr(item, "warehouse", None)

			# Skip rows that don't affect stock
			if not item_code or not warehouse:
				continue

			if hasattr(item, "is_stock_item") and item.is_stock_item is not None:
				if not int(item.is_stock_item):
					continue
			elif hasattr(item, "stock_qty") and not frappe.utils.flt(item.stock_qty):
				continue

			item_codes_by_warehouse.setdefault(warehouse, set()).add(item_code)

		if not item_codes_by_warehouse:
			return

		# Build stock updates with current quantities
		stock_updates = []
		warehouses = set()
		for warehouse, codes in item_codes_by_warehouse.items():
			warehouses.add(warehouse)
			# Use shared stock utility to keep logic consistent with API responses
			warehouse_updates = get_stock_quantities(list(codes), warehouse)

			# Ensure events always have numeric qty fields, even if API returns None
			for update in warehouse_updates:
				actual_qty = frappe.utils.flt(update.get("actual_qty"))
				update["actual_qty"] = actual_qty
				update["stock_qty"] = actual_qty if update.get("stock_qty") is None else update["stock_qty"]
				update["warehouse"] = update.get("warehouse") or warehouse

			stock_updates.extend(warehouse_updates)

		if not stock_updates:
			return

		# Prepare event data
		event_data = {
			"invoice_name": doc.name,
			"warehouses": list(warehouses),
			"stock_updates": stock_updates,
			"timestamp": frappe.utils.now(),
			"event_type": "cancel" if method == "on_cancel" else "submit",
		}

		# Emit to the terminals of the invoice's profile only
		# Event name: pos_stock_update
		publish_to_profile(event="pos_stock_update", message=event_data, pos_profile=pos_profile)

	except Exception as e:
		# Log error but don't fail the transaction
		frappe.log_error(
			title=_("Real-time Stock Update Event Error"),
			message=f"Failed to emit stock update event for {doc.name}: {e!s}",
		)


def emit_invoice_created_event(doc, method=None):
	"""
	Emit real-time event when invoice is created.

	This can be used to notify other terminals about new sales,
	update dashboards, or trigger other real-time UI updates.

	Args:
		doc: Sales Invoice document
		method: Hook method name
	"""
	if doc.get("is_consolidated"):
		return
	if not doc.is_pos:
		return

	try:
		event_data = {
			"invoice_name": doc.name,
			"grand_total": doc.grand_total,
			"customer": doc.customer,
			"pos_profile": doc.pos_profile,
			"timestamp": frappe.utils.now(),
		}

		publish_to_profile(event="pos_invoice_created", message=event_data, pos_profile=doc.pos_profile)

	except Exception as e:
		frappe.log_error(
			title=_("Real-time Invoice Created Event Error"),
			message=f"Failed to emit invoice created event for {doc.name}: {e!s}",
		)


def emit_pos_profile_updated_event(doc, method=None):
	"""
	Emit real-time event when POS Profile is updated.

	This event notifies all connected POS terminals about configuration changes,
	particularly item group filters, allowing them to clear their cache and reload
	items automatically without manual intervention.

	Args:
		doc: POS Profile document
		method: Hook method name (on_update, validate, etc.)
	"""
	try:
		# Check if item_groups have changed by comparing with the original doc
		if doc.has_value_changed("item_groups"):
			# Extract current item groups
			current_item_groups = [{"item_group": ig.item_group} for ig in doc.get("item_groups", [])]

			# Prepare event data
			event_data = {
				"pos_profile": doc.name,
				"item_groups": current_item_groups,
				"timestamp": frappe.utils.now(),
				"change_type": "item_groups_updated",
			}

			# Emit to the terminals of the updated profile; the payload is about
			# this profile only, so its room is the exact audience.
			# Event name: pos_profile_updated
			publish_to_profile(event="pos_profile_updated", message=event_data, pos_profile=doc.name)

			frappe.logger().info(f"Emitted pos_profile_updated event for {doc.name} - item groups changed")

	except Exception as e:
		# Log error but don't fail the transaction
		frappe.log_error(
			title=_("Real-time POS Profile Update Event Error"),
			message=f"Failed to emit POS profile update event for {doc.name}: {e!s}",
		)


def emit_customer_event(doc, method=None):
	"""
	Emit real-time customer update event.

	This event notifies all connected POS terminals about customer changes,
	allowing them to update their local cache immediately.

	Args:
		doc: Customer document
		method: Hook method name (after_insert, on_update, on_trash)
	"""
	try:
		action = "update"
		if method == "after_insert":
			action = "create"
		elif method == "on_trash":
			action = "delete"

		event_data = {
			"name": doc.name,
			"customer_name": doc.customer_name,
			"mobile_no": doc.mobile_no or "",
			"email_id": doc.email_id or "",
			"disabled": doc.disabled,
			"action": action,
			"timestamp": frappe.utils.now(),
		}

		# PERF-06: Customer carries no POS Profile, so fan the event out to the
		# room of every enabled profile — every terminal still syncs its cache,
		# while sessions that belong to no profile stop receiving customer PII.
		for pos_profile in frappe.get_all("POS Profile", filters={"disabled": 0}, pluck="name"):
			publish_to_profile(event="pos_customer_changed", message=event_data, pos_profile=pos_profile)

	except Exception as e:
		frappe.log_error(
			title=_("Real-time Customer Update Event Error"),
			message=f"Failed to emit customer update event for {doc.name}: {e!s}",
		)


@frappe.whitelist()
def has_pos_profile_access(pos_profile):
	"""Join gate for the pos_profile:<name> room (pos_next/realtime/handlers.js).

	Membership mirrors who the events are about: a cashier assigned to the
	profile. Login alone must not be enough — these rooms carry payment and
	customer details.
	"""
	if not pos_profile or not isinstance(pos_profile, str):
		return False
	return bool(
		frappe.db.exists(
			"POS Profile User", {"parent": pos_profile, "user": frappe.session.user}
		)
	)
