# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, getdate

CODE_DOCTYPE = "POS Discount Confirmation Code"
# Unambiguous alphabet: no 0/O, 1/I/L — codes are dictated over the phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8
# Bounded retries when a freshly generated code collides with an existing row.
CODE_GENERATION_ATTEMPTS = 10


def _random_code() -> str:
	return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


class POSDiscountConfirmationCode(Document):
	def validate(self):
		self.code = (self.code or "").strip().upper()
		if not self.code:
			self.code = self._generate_unique_code()
		invalid = set(self.code) - set(CODE_ALPHABET)
		if invalid:
			frappe.throw(
				_("Code may only contain letters and digits (without {0})").format(
					", ".join(sorted("0O1IL"))
				)
			)

		# A code that already recorded usages is an audit anchor — only its
		# status may change from here on, never its value.
		if not self.is_new() and cint(self.used_count) > 0:
			previous = frappe.db.get_value(self.doctype, self.name, "code") or ""
			if self.code != previous:
				frappe.throw(_("A code that has already been used cannot be changed"))

		if self.valid_from and self.valid_upto and getdate(self.valid_upto) < getdate(self.valid_from):
			frappe.throw(_("Valid Upto cannot be before Valid From"))

		if (self.company_scope or "All Outlets") != "All Outlets" and not self.companies:
			frappe.throw(_("Select at least one outlet for the chosen scope"))

	def _generate_unique_code(self) -> str:
		"""Random code that does not collide with an existing row (the insert
		would raise DuplicateEntryError; probe uniqueness here instead so the
		retry can happen before the save)."""
		for _ in range(CODE_GENERATION_ATTEMPTS):
			code = _random_code()
			if not frappe.db.exists(CODE_DOCTYPE, {"code": code}):
				return code
		frappe.throw(_("Could not generate a unique code, please try again"))


@frappe.whitelist(methods=["POST"])
def generate_codes(count: int = 1, company: str | None = None, notes: str | None = None) -> dict:
	"""Generate multi-use discount access codes.

	Head office only — requires create permission on this doctype (System
	Manager). Codes stay Active until disabled here. When `company` is given,
	the code is scoped to that outlet only (Selected Outlets).
	"""
	frappe.has_permission(CODE_DOCTYPE, "create", throw=True)

	count = cint(count)
	if not 1 <= count <= 500:
		frappe.throw(_("Number of codes must be between 1 and 500"))

	company = (company or "").strip() or None
	notes = (notes or "").strip() or None

	if company and not frappe.db.exists("Company", company):
		frappe.throw(_("Company {0} does not exist").format(company))

	payload = {"doctype": CODE_DOCTYPE, "notes": notes, "status": "Active"}
	if company:
		payload["company_scope"] = "Selected Outlets"
		payload["companies"] = [{"company": company}]

	codes = []
	attempts = 0
	while len(codes) < count:
		attempts += 1
		if attempts > count * 5 + 10:
			frappe.throw(_("Could not generate unique codes, please try again"))
		code = _random_code()
		try:
			frappe.get_doc({**payload, "code": code}).insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			continue
		codes.append(code)

	return {"codes": codes}
