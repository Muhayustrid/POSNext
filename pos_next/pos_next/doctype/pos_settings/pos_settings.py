# Copyright (c) 2025, Youssef Restom and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

from pos_next.api.settings_resolver import get_effective_pos_settings
from pos_next.invoice_type import get_pos_invoice_doctype


class POSSettings(Document):
	def validate(self):
		"""Validate POS Settings"""
		# Guard against None values and validate discount percentage
		max_discount = flt(self.max_discount_allowed)
		if max_discount < 0 or max_discount > 100:
			frappe.throw("Max Discount Allowed must be between 0 and 100")

		# Guard against None values and validate search limit
		if self.use_limit_search:
			search_limit = cint(self.search_limit)
			if search_limit <= 0:
				frappe.throw("Search Limit must be greater than 0")

		# Validate use_exact_amount cannot be enabled with credit sale or partial payment
		if cint(self.use_exact_amount):
			if cint(self.allow_credit_sale):
				frappe.throw(
					"'Use Exact Amount for Non-Cash' cannot be enabled together with 'Allow Credit Sale'. "
					"Please disable Credit Sale first."
				)
			if cint(self.allow_partial_payment):
				frappe.throw(
					"'Use Exact Amount for Non-Cash' cannot be enabled together with 'Allow Partial Payment'. "
					"Please disable Partial Payment first."
				)



@frappe.whitelist()
def get_pos_settings(pos_profile):
	"""
	Get POS Settings for a specific POS Profile.

	An enabled POS Settings row wins whole; without one (no row or a disabled
	row) every field falls back to the POS Next Global Settings single via
	the resolver. Global settings (invoice_type, target bases,
	allow_negative_stock) stay global and are injected here so this
	per-profile feed stays complete for the POS screen.
	"""
	from frappe import _

	if not pos_profile:
		return None

	# Check if user has access to this POS Profile
	has_access = frappe.db.exists("POS Profile User", {"parent": pos_profile, "user": frappe.session.user})

	if not has_access and not frappe.has_permission("POS Settings", "read"):
		frappe.throw(_("You don't have access to this POS Profile"))

	settings = frappe.db.get_value("POS Settings", {"pos_profile": pos_profile, "enabled": 1}, "*", as_dict=True)

	if not settings:
		settings = get_effective_pos_settings(pos_profile)

	# Global negative-stock switch now lives on the POS Next Global Settings
	# single; the payload key stays so the POS screen is unchanged.
	settings["allow_negative_stock"] = cint(
		frappe.db.get_single_value("POS Next Global Settings", "allow_negative_stock") or 0
	)

	# Legacy global columns: the DB columns survive the doctype migration
	# (inert), so drop them instead of leaking stale per-row copies.
	settings.pop("monthly_target_basis", None)
	settings.pop("overall_target_basis", None)

	# Mirror the bootstrap preload feed so both feeds agree; the UI treats a
	# missing key as disabled.
	settings["queue_enabled"] = bool(settings.get("enable_pos_queue"))

	# Mirror the bootstrap preload feed: doctype new POS invoices are created
	# in ("Sales Invoice"/"POS Invoice"), so the non-bootstrap fallback feed
	# shows the same mode.
	settings["invoice_type"] = get_pos_invoice_doctype()

	return settings


@frappe.whitelist()
def update_pos_settings(pos_profile, settings):
	"""Update POS Settings for a POS Profile"""
	import json

	from frappe import _

	if isinstance(settings, str):
		settings = json.loads(settings)

	# Check if user has access to this POS Profile
	has_access = frappe.db.exists("POS Profile User", {"parent": pos_profile, "user": frappe.session.user})

	if not has_access and not frappe.has_permission("POS Settings", "write"):
		frappe.throw(_("You don't have permission to update this POS Profile"))

	# Check if settings exist
	existing = frappe.db.exists("POS Settings", {"pos_profile": pos_profile})

	if existing:
		doc = frappe.get_doc("POS Settings", existing)
		doc.update(settings)
		doc.save()
	else:
		doc = frappe.new_doc("POS Settings")
		doc.pos_profile = pos_profile
		doc.update(settings)
		# Every read path filters enabled=1; a stale SPA payload must never
		# create a row those paths would ignore.
		doc.enabled = 1
		doc.insert()

	return doc.as_dict()
