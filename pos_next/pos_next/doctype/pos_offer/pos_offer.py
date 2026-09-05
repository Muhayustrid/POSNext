# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate

import frappe

SCHEME_DOCTYPE = "Promotional Scheme"


class POSOffer(Document):
	def validate(self):
		validate_offer(self)

	def on_update(self):
		from pos_next.overrides.pos_offer_sync import sync_offer

		sync_offer(self)

	def on_trash(self):
		from pos_next.overrides.pos_offer_sync import handle_offer_trash

		handle_offer_trash(self)


def validate_offer(doc):
	"""Field validation for the rebuilt POS Offer (module-level for unit tests)."""
	if doc.valid_from and doc.valid_to and getdate(doc.valid_from) > getdate(doc.valid_to):
		frappe.throw(_("Valid From must be on or before Valid To."))

	companies = doc.get("companies") or []
	if not companies:
		frappe.throw(_("Add at least one company to this offer."))
	seen = set()
	for row in companies:
		if row.company in seen:
			frappe.throw(_("Company {0} is listed more than once.").format(row.company))
		seen.add(row.company)
		if cint(row.max_usage) < 0:
			frappe.throw(_("Max Usage for company {0} cannot be negative.").format(row.company))

	if cint(doc.enforce_usage_quota):
		if not doc.quota_scope:
			frappe.throw(_("Select a quota scope (Global or Per Company)."))
		if not doc.quota_period:
			frappe.throw(_("Select a quota period (Campaign Total or Daily)."))
		if cint(doc.global_max_usage) < 0:
			frappe.throw(_("Global Max Usage cannot be negative."))

	if doc.offer_type == "Discount Percentage":
		pct = flt(doc.discount_percentage)
		if pct <= 0 or pct > 100:
			frappe.throw(_("Discount Percentage must be between 0 and 100."))
		if flt(doc.max_discount_amount) < 0:
			frappe.throw(_("Max Discount Amount cannot be negative."))
		if doc.apply_on == "Transaction":
			# Per-unit cap has no meaning on a whole-transaction discount.
			doc.max_discount_amount = 0
	elif doc.offer_type == "Discount Amount":
		if flt(doc.discount_amount) <= 0:
			frappe.throw(_("Discount Amount must be greater than 0."))
	elif doc.offer_type == "Free Item":
		if not doc.free_item:
			frappe.throw(_("Free Item is required for a Free Item offer."))
		if flt(doc.free_qty) < 1:
			frappe.throw(_("Free Qty must be at least 1."))

	if flt(doc.min_qty) < 0 or flt(doc.min_amt) < 0:
		frappe.throw(_("Min Qty and Min Amount cannot be negative."))

	if doc.apply_on != "Transaction":
		targets = doc.get("targets") or []
		if not targets:
			frappe.throw(_("Add at least one target row for Apply On {0}.").format(doc.apply_on))
		column = {"Item Code": "item_code", "Item Group": "item_group", "Brand": "brand"}[doc.apply_on]
		for row in targets:
			if not row.get(column):
				frappe.throw(_("Every target row needs an {0}.").format(doc.apply_on))

	# The scheme is created with the offer's title — refuse to shadow a scheme
	# owned by another offer (our own scheme, e.g. on rename, is fine).
	if doc.title and frappe.db.exists(SCHEME_DOCTYPE, doc.title):
		owner = frappe.db.get_value(SCHEME_DOCTYPE, doc.title, "pos_offer")
		if owner != doc.name:
			frappe.throw(
				_("A Promotional Scheme named {0} already exists. Choose another title.").format(doc.title)
			)
