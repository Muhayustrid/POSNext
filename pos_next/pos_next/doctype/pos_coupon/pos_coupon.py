# Copyright (c) 2021, Youssef Restom and contributors
# For license information, please see license.txt


import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, getdate, strip, today

ONE_USE_COUPON_DOCTYPES = ("Sales Invoice", "POS Invoice")


class POSCoupon(Document):
	def autoname(self):
		self.coupon_name = strip(self.coupon_name)
		self.name = self.coupon_name

		if not self.coupon_code:
			if self.coupon_type == "Promotional":
				self.coupon_code = "".join(i for i in self.coupon_name if not i.isdigit())[0:8].upper()
			elif self.coupon_type == "Gift Card":
				self.coupon_code = frappe.generate_hash()[:10].upper()

	def validate(self):
		# Gift Card validations
		if self.coupon_type == "Gift Card":
			self.maximum_use = 1
			if not self.customer:
				frappe.throw(_("Please select the customer for Gift Card."))

		# Discount validations
		if not self.discount_type:
			frappe.throw(_("Discount Type is required"))

		if self.discount_type == "Percentage":
			if not self.discount_percentage:
				frappe.throw(_("Discount Percentage is required"))
			if flt(self.discount_percentage) <= 0 or flt(self.discount_percentage) > 100:
				frappe.throw(_("Discount Percentage must be between 0 and 100"))
		elif self.discount_type == "Amount":
			if not self.discount_amount:
				frappe.throw(_("Discount Amount is required"))
			if flt(self.discount_amount) <= 0:
				frappe.throw(_("Discount Amount must be greater than 0"))

		# Minimum amount validation
		if self.min_amount and flt(self.min_amount) < 0:
			frappe.throw(_("Minimum Amount cannot be negative"))

		# Maximum discount validation
		if self.max_amount and flt(self.max_amount) <= 0:
			frappe.throw(_("Maximum Discount Amount must be greater than 0"))

		# Date validations
		if self.valid_from and self.valid_upto:
			if getdate(self.valid_from) > getdate(self.valid_upto):
				frappe.throw(_("Valid From date cannot be after Valid Until date"))


def check_coupon_code(coupon_code, customer=None, company=None):
	"""Validate and return coupon details"""
	res = {"coupon": None}

	if not frappe.db.exists("POS Coupon", {"coupon_code": coupon_code.upper()}):
		res["msg"] = _("Sorry, this coupon code does not exist")
		return res

	coupon = frappe.get_doc("POS Coupon", {"coupon_code": coupon_code.upper()})

	# Check if coupon is disabled
	if coupon.disabled:
		res["msg"] = _("Sorry, this coupon has been disabled")
		return res

	# Check validity dates
	if coupon.valid_from:
		if coupon.valid_from > getdate(today()):
			res["msg"] = _("Sorry, this coupon code's validity has not started")
			return res

	if coupon.valid_upto:
		if coupon.valid_upto < getdate(today()):
			res["msg"] = _("Sorry, this coupon code has expired")
			return res

	# Check usage limits
	if coupon.used and coupon.maximum_use and coupon.used >= coupon.maximum_use:
		res["msg"] = _("Sorry, this coupon code has been fully redeemed")
		return res

	# Check company
	if company and coupon.company != company:
		res["msg"] = _("Sorry, this coupon is not valid for this company")
		return res

	# Check customer (for Gift Cards)
	if coupon.coupon_type == "Gift Card" and coupon.customer:
		if not customer or coupon.customer != customer:
			res["msg"] = _("Sorry, this gift card is assigned to a specific customer")
			return res

	# Check one-time use per customer
	if coupon.one_use and customer:
		used_count = _get_customer_coupon_usage_count(customer, coupon.coupon_code)
		if used_count > 0:
			res["msg"] = _("Sorry, you have already used this coupon code")
			return res

	# All validations passed
	res["coupon"] = coupon
	res["valid"] = True

	return res


def _get_customer_coupon_usage_count(customer, coupon_code):
	"""Count submitted coupon usage across POSNext's actual sales doctypes."""
	used_count = 0

	for doctype in ONE_USE_COUPON_DOCTYPES:
		if not frappe.db.table_exists(doctype):
			continue

		meta = frappe.get_meta(doctype)
		if not meta.has_field("pos_coupon_code"):
			continue

		used_count += frappe.db.count(
			doctype,
			filters={
				"customer": customer,
				"pos_coupon_code": coupon_code,
				"docstatus": 1,
			},
		)

	return used_count


def coupon_base_amount(coupon, doc):
	"""Server-side pre-discount basis for the coupon, from a SAVED doc.

	The stored totals are AFTER the header discount: ERPNext distributes
	discount_amount across the rows and recalculates
	(apply_discount_amount, erpnext/controllers/taxes_and_totals.py), so the
	pre-discount basis the coupon promises against is the stored total plus
	the stored discount_amount — exact for both "Grand Total" and "Net Total"
	up to currency rounding. A Grand Total cash/non-trade discount never
	reduces the totals (apply_discount_amount returns early), so its basis is
	the stored grand_total alone.
	"""
	discount = flt(doc.get("discount_amount") or 0)
	if (coupon.apply_on or "Grand Total") == "Net Total":
		return flt(doc.get("net_total") or 0) + discount
	if doc.get("is_cash_or_non_trade_discount"):
		return flt(doc.get("grand_total") or 0)
	return flt(doc.get("grand_total") or 0) + discount


def expected_coupon_discount(coupon, doc):
	"""The header discount `coupon` promises against invoice `doc`.

	The single calculator for both the checkout recompute
	(overrides/discount_code.enforce_stamped_coupon_discount) and the
	discount-code exemption (stamped_coupon_explains_header), so a coupon can
	never excuse more than it grants.

	Pure: no DB access, no throws. An unknown discount_type grants nothing.
	"""
	base = coupon_base_amount(coupon, doc)
	if coupon.discount_type == "Percentage":
		expected = base * flt(coupon.discount_percentage or 0) / 100
	elif coupon.discount_type == "Amount":
		expected = flt(coupon.discount_amount or 0)
	else:
		return 0.0

	# Apply maximum discount limit, then never exceed the basis itself
	# (mirrors the old apply_coupon_discount).
	max_amount = flt(coupon.max_amount or 0)
	if max_amount > 0:
		expected = min(expected, max_amount)
	if expected > base:
		expected = base
	return max(expected, 0.0)


def coupon_min_amount_unmet(coupon, doc):
	"""True when the coupon's minimum spend is not met on the coupon's own
	basis (server totals of the saved doc — the same basis the CouponDialog
	UI check reads)."""
	min_amount = flt(coupon.min_amount or 0)
	if min_amount <= 0:
		return False
	return coupon_base_amount(coupon, doc) < min_amount


def increment_coupon_usage(coupon_code, customer=None):
	"""Increment the usage counter for a coupon under a row lock (SEC-15, PATTERN C).

	Runs inside the caller's invoice submit transaction: the FOR UPDATE lock
	serializes concurrent claims, and the max-use re-check under the lock
	refuses to push `used` past `maximum_use`. The one-use-per-customer rule
	is re-checked here too (COR-BE-07): the draft-save validation
	(check_coupon_code) only sees committed invoices, so two drafts saved
	before either was submitted both pass it, and only this submit-time
	re-check can refuse the second one. No manual commit — the increment
	commits (or rolls back) together with the invoice.
	"""
	coupon = frappe.db.get_value(
		"POS Coupon",
		{"coupon_code": coupon_code.upper()},
		["name", "used", "maximum_use", "one_use", "coupon_code"],
		as_dict=True,
		for_update=True,
	)
	if not coupon:
		return

	used = cint(coupon.used)
	if cint(coupon.maximum_use) and used >= cint(coupon.maximum_use):
		frappe.throw(_("Sorry, this coupon code has been fully redeemed"))

	# COR-BE-07: re-check under the lock. The submitting invoice is still a
	# draft here, so the count only covers earlier submits; a thrown violation
	# aborts the whole submit and never consumes quota.
	if cint(coupon.one_use) and customer:
		if _get_customer_coupon_usage_count(customer, coupon.coupon_code) > 0:
			frappe.throw(_("Sorry, you have already used this coupon code"))

	frappe.db.set_value("POS Coupon", coupon.name, "used", used + 1, update_modified=False)


def decrement_coupon_usage(coupon_code):
	"""Decrement the usage counter for a coupon (for cancelled invoices)"""
	coupon = frappe.db.get_value(
		"POS Coupon",
		{"coupon_code": coupon_code.upper()},
		["name", "used"],
		as_dict=True,
		for_update=True,
	)
	if not coupon:
		return

	used = cint(coupon.used)
	if used > 0:
		frappe.db.set_value("POS Coupon", coupon.name, "used", used - 1, update_modified=False)
