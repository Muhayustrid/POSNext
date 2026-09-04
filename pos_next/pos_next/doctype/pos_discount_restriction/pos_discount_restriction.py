# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

CONFIRMATION_CODE_DOCTYPE = "POS Discount Confirmation Code"

# Confusion-safe alphabet: no 0/O, 1/I/L — codes are often read out over the phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class POSDiscountRestriction(Document):
	def validate(self):
		self.validate_dates()
		self.validate_quota_config()

	def validate_dates(self):
		if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
			frappe.throw(_("Valid From must be on or before Valid To"))

	def validate_quota_config(self):
		if not cint(self.enforce_usage_quota):
			return
		if self.quota_mode not in ("Global", "Per Company"):
			frappe.throw(_("Quota Mode is required when usage quota is enforced"))
		if self.quota_mode == "Global" and cint(self.global_max_usage) < 0:
			frappe.throw(_("Max Usage (All Companies) cannot be negative"))

	@frappe.whitelist()
	def generate_codes(self, count: int = 1, company: str | None = None):
		"""Generate one-time confirmation codes for this rule (head office action)."""
		if not frappe.has_permission(self.doctype, "write", doc=self):
			frappe.throw(_("Not permitted to generate confirmation codes"), frappe.PermissionError)

		if not cint(self.require_confirmation_code):
			frappe.throw(_("Enable 'Require Confirmation Code' before generating codes"))

		count = cint(count)
		if count < 1 or count > 500:
			frappe.throw(_("Count must be between 1 and 500"))

		company = (company or "").strip() or None
		if company and not frappe.db.exists("Company", company):
			frappe.throw(_("Company {0} does not exist").format(company))

		codes = []
		for _ in range(count):
			code = self._new_code_value()
			frappe.get_doc(
				{
					"doctype": CONFIRMATION_CODE_DOCTYPE,
					"restriction": self.name,
					"code": code,
					"company": company,
					"status": "Available",
				}
			).insert(ignore_permissions=True)
			codes.append(code)

		return {"codes": codes}

	def _new_code_value(self):
		while True:
			code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
			if not frappe.db.exists(CONFIRMATION_CODE_DOCTYPE, {"code": code}):
				return code
