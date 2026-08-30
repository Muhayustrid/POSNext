"""Eligibility domain for Dynamic Promotion (Task 3).

Server-authoritative, pure Python contracts. No whitelisted HTTP surface.
"""

import frappe
from frappe import _
from frappe.utils import getdate, nowdate


def resolve_outlet_context(pos_profile):
	"""Resolve (company, warehouse) from a POS Profile.

	Args:
		pos_profile: POS Profile name (str) or doc/dict-like object.

	Returns:
		Tuple[str, str]: (company, warehouse)

	Raises:
		frappe.ValidationError: if pos_profile is missing/empty, does not exist,
			or company/warehouse is not set.
	"""
	if not pos_profile:
		frappe.throw(_("POS Profile is required"), frappe.ValidationError)

	# String -> fetch document
	if isinstance(pos_profile, str):
		name = pos_profile.strip()
		if not name:
			frappe.throw(_("POS Profile is required"), frappe.ValidationError)
		if not frappe.db.exists("POS Profile", name):
			frappe.throw(_("POS Profile {0} does not exist").format(name), frappe.ValidationError)
		doc = frappe.get_doc("POS Profile", name)
		company = doc.get("company")
		warehouse = doc.get("warehouse")
	else:
		# Document or dict-like
		company = (
			pos_profile.get("company")
			if hasattr(pos_profile, "get")
			else getattr(pos_profile, "company", None)
		)
		warehouse = (
			pos_profile.get("warehouse")
			if hasattr(pos_profile, "get")
			else getattr(pos_profile, "warehouse", None)
		)

	if not company:
		frappe.throw(_("POS Profile company is not set"), frappe.ValidationError)
	if not warehouse:
		frappe.throw(_("POS Profile warehouse is not set"), frappe.ValidationError)

	return (str(company), str(warehouse))


def check(promotion, company, warehouse, on_date=None, currency=None):
	"""Fail-closed eligibility check.

	Args:
		promotion: Promotion doc or doc name (str).
		company: Outlet company to check.
		warehouse: Outlet warehouse to check.
		on_date: Date string/date/datetime; defaults to nowdate() if None/empty.
		currency: Transaction currency; if provided, must match promotion.currency.

	Returns:
		Tuple[bool, str]: (is_eligible, reason). Reason is empty string when eligible.

	Order:
		1. Master enabled
		2. valid_from window
		3. valid_to window
		4. currency match (if currency passed)
		5. outlet authority: exact (company, warehouse) with enabled=1
	"""
	# Load promotion if string
	if isinstance(promotion, str):
		if not frappe.db.exists("Promotion", promotion):
			return (False, "Promotion {0} does not exist".format(promotion))
		promotion = frappe.get_doc("Promotion", promotion)

	# Resolve on_date
	if not on_date:
		on_date_val = getdate(nowdate())
	else:
		try:
			on_date_val = getdate(on_date)
		except Exception:
			frappe.throw(_("Invalid on_date value: {0}").format(on_date), frappe.ValidationError)

	# 1. Master enabled
	if not getattr(promotion, "enabled", 0):
		return (False, "Promotion is disabled")

	# 2. valid_from
	valid_from = getattr(promotion, "valid_from", None)
	if valid_from:
		try:
			vf = getdate(valid_from)
			if on_date_val < vf:
				return (False, "Promotion is not yet valid")
		except Exception:
			# If getdate fails, fallback to string compare as in brief
			if str(on_date_val) < str(valid_from):
				return (False, "Promotion is not yet valid")

	# 3. valid_to
	valid_to = getattr(promotion, "valid_to", None)
	if valid_to:
		try:
			vt = getdate(valid_to)
			if on_date_val > vt:
				return (False, "Promotion has expired")
		except Exception:
			if str(on_date_val) > str(valid_to):
				return (False, "Promotion has expired")

	# 4. Currency
	if currency:
		promo_currency = getattr(promotion, "currency", None)
		if promo_currency != currency:
			return (False, f"Currency mismatch: expected {promo_currency}, got {currency}")

	# 5. Outlet authority
	outlets = getattr(promotion, "outlets", None) or []
	found = False
	for row in outlets:
		row_company = getattr(row, "company", None)
		row_warehouse = getattr(row, "warehouse", None)
		# Support dict-like row
		if row_company is None and isinstance(row, dict):
			row_company = row.get("company")
		if row_warehouse is None and isinstance(row, dict):
			row_warehouse = row.get("warehouse")
		if str(row_company) == str(company) and str(row_warehouse) == str(warehouse):
			found = True
			enabled = getattr(row, "enabled", 1)
			if isinstance(row, dict):
				enabled = row.get("enabled", 1)
			if not enabled:
				return (False, "Outlet is disabled for this promotion")
			break

	if not found:
		return (False, "Outlet not configured for this promotion")

	return (True, "")
