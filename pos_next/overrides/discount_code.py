# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""
Discount code gate for POS invoices.

Every manual discount on a POS invoice — an item-level discount (explicit
percentage/amount, or a rate edited below price_list_rate) or the header
additional discount — requires a `POS Discount Confirmation Code` issued by
head office. Codes are multi-use and stay valid until head office disables
them; usage is audited on submit.

Enforcement points (doc_events on Sales Invoice):
- validate:   hard gate on draft save AND submit — a discounted invoice must
              carry an active, company-matching code in
              `discount_confirmation_code`.
- on_submit:  re-check the code under a row lock and stamp the usage audit
              fields (used_count, last_used_*).

The gate applies to POS invoices only (is_pos); back-office invoices are not
restricted. Returns (is_return) never require a code.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

CODE_DOCTYPE = "POS Discount Confirmation Code"


# ==========================================================================
# Discount detection
# ==========================================================================


def _item_has_discount(item):
	"""True when the item row carries a manual discount (incl. a manual rate
	edit below price_list_rate, which is a discount in disguise)."""
	if flt(item.get("discount_percentage") or 0) > 0 or flt(item.get("discount_amount") or 0) > 0:
		return True

	if cint(item.get("is_rate_manually_edited") or 0):
		price_list_rate = flt(item.get("price_list_rate") or 0)
		rate = flt(item.get("rate") or 0)
		if price_list_rate > 0 and rate < price_list_rate:
			return True

	return False


def invoice_has_manual_discount(doc):
	"""True when the invoice discounts anything: the header additional
	discount, or any item row."""
	if flt(doc.get("discount_amount") or 0) > 0 or flt(doc.get("additional_discount_percentage") or 0) > 0:
		return True

	return any(_item_has_discount(item) for item in (doc.get("items") or []))


# ==========================================================================
# Code validation
# ==========================================================================


def validate_code(code_value, company):
	"""Read-only code validation. Returns the code document name; raises a
	precise ValidationError on any problem."""
	code_value = (code_value or "").strip().upper()
	if not code_value:
		frappe.throw(_("A discount code from head office is required for this discount."))

	row = frappe.db.get_value(CODE_DOCTYPE, {"code": code_value}, ["name", "status", "company"], as_dict=True)
	if not row:
		frappe.throw(_("Discount code {0} is not valid.").format(code_value))
	if row.status != "Active":
		frappe.throw(_("Discount code {0} has been disabled by head office.").format(code_value))
	if row.company and company and row.company != company:
		frappe.throw(_("Discount code {0} is not valid for company {1}.").format(code_value, company))

	return row.name


# ==========================================================================
# doc_events (Sales Invoice)
# ==========================================================================


def validate_invoice_discounts(doc, method=None):
	"""Sales Invoice validate hook — the hard gate (draft save and submit)."""
	if not doc.get("is_pos") or doc.get("is_return"):
		return
	if not invoice_has_manual_discount(doc):
		return

	validate_code(doc.get("discount_confirmation_code"), doc.get("company"))


def record_code_usage_on_submit(doc, method=None):
	"""Sales Invoice on_submit hook — re-check the code under a row lock and
	stamp the usage audit fields on it."""
	if not doc.get("is_pos") or doc.get("is_return"):
		return
	code_value = (doc.get("discount_confirmation_code") or "").strip().upper()
	if not code_value or not invoice_has_manual_discount(doc):
		return

	code_name = validate_code(code_value, doc.get("company"))

	# Serialize concurrent submits on the same code so used_count cannot lose
	# increments; the audit update lives in the submit transaction and rolls
	# back with it.
	row = frappe.db.get_value(CODE_DOCTYPE, code_name, ["status", "used_count"], as_dict=True, for_update=True)
	if row.status != "Active":
		frappe.throw(_("Discount code {0} has been disabled by head office.").format(code_value))

	frappe.db.set_value(
		CODE_DOCTYPE,
		code_name,
		{
			"used_count": cint(row.used_count) + 1,
			"last_used_by": frappe.session.user,
			"last_used_in_invoice": doc.get("name"),
			"last_used_on": now_datetime(),
		},
		update_modified=False,
	)
