# Copyright (c) 2025, Youssef Restom and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt

from pos_next.api.settings_resolver import get_effective_pos_settings
from pos_next.invoice_type import get_pos_invoice_doctype
from pos_next.utils.authz import is_management_user

# The POS "Sales Management" tab: money rules a cashier must not loosen for
# themselves. Only management may change them through update_pos_settings.
SALES_MANAGEMENT_FIELDS = (
	"allow_credit_sale",
	"allow_partial_payment",
	"allow_return",
	"allow_user_to_edit_additional_discount",
	"allow_user_to_edit_item_discount",
	"allow_user_to_edit_rate",
	"allow_write_off_change",
	"auto_apply_offers",
	"disable_rounded_total",
	"max_discount_allowed",
	"print_mode",
	"tax_inclusive",
	"use_percentage_discount",
)

# The POS "Printing" tab: the only fields a cashier may save through
# update_pos_settings (cashiers hold read-only POS Settings, so the save runs
# with ignore_permissions once the profile-membership gate passed).
CASHIER_PRINT_FIELDS = (
	"enable_pos_queue",
	"imin_copy_delay_ms",
	"imin_crew_delay_ms",
	"imin_crew_font_scale",
	"imin_crew_slip_enabled",
	"imin_custom_dots",
	"imin_cut_paper",
	"imin_eod_copy_delay_ms",
	"imin_eod_feed_dots",
	"imin_eod_font_scale",
	"imin_eod_line_spacing",
	"imin_eod_print_copies",
	"imin_eod_print_mode",
	"imin_eod_side_margin",
	"imin_eod_tail_dots",
	"imin_eod_top_margin",
	"imin_feed_dots",
	"imin_font_scale",
	"imin_line_spacing",
	"imin_paper_width",
	"imin_print_copies",
	"imin_print_mode",
	"imin_print_speed",
	"imin_queue_gap",
	"imin_side_margin",
	"imin_tail_dots",
	"imin_top_margin",
	"print_driver",
	"print_fallback_enabled",
)


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

	# Global package-allocation switch (same single), read through the same
	# schema-guarded helper packages.py uses so both feeds agree. The POS
	# mirror defaults to OFF until this key arrives.
	from pos_next.api.packages import _package_allocation_enabled

	settings["enable_pos_package_allocation"] = cint(_package_allocation_enabled())
	settings["enable_automatic_stock_sync"] = cint(
		frappe.db.get_single_value("POS Next Global Settings", "enable_automatic_stock_sync")
	)
	settings["block_out_of_stock_items"] = cint(
		frappe.db.get_single_value("POS Next Global Settings", "block_out_of_stock_items")
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

	# a cashier of this profile may only tune printing; the SPA still posts
	# the whole form, so everything else is dropped server-side
	cashier = has_access and not is_management_user()
	if cashier:
		settings = {k: v for k, v in settings.items() if k in CASHIER_PRINT_FIELDS}
	elif not is_management_user():
		# the SPA still posts the whole form; drop what a cashier may not touch
		for field in SALES_MANAGEMENT_FIELDS:
			settings.pop(field, None)

	# Check if settings exist
	existing = frappe.db.exists("POS Settings", {"pos_profile": pos_profile})

	if existing:
		doc = frappe.get_doc("POS Settings", existing)
		doc.update(settings)
		doc.save(ignore_permissions=cashier)
	else:
		doc = frappe.new_doc("POS Settings")
		doc.pos_profile = pos_profile
		if cashier:
			# an enabled row wins whole — seed it with the effective values so
			# a cashier's print-only save keeps every other rule as it was
			doc.update(get_effective_pos_settings(pos_profile))
		doc.update(settings)
		# Every read path filters enabled=1; a stale SPA payload must never
		# create a row those paths would ignore.
		doc.enabled = 1
		doc.insert(ignore_permissions=cashier)

	return doc.as_dict()
