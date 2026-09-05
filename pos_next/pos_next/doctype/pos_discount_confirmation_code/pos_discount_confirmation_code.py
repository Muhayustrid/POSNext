# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

CODE_DOCTYPE = "POS Discount Confirmation Code"
# Unambiguous alphabet: no 0/O, 1/I/L — codes are dictated over the phone.
CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 8


class POSDiscountConfirmationCode(Document):
	def validate(self):
		self.code = (self.code or "").strip().upper()
		if not self.code:
			frappe.throw(_("Code is required"))
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


@frappe.whitelist(methods=["POST"])
def generate_codes(count: int = 1, company: str | None = None, notes: str | None = None) -> dict:
	"""Generate multi-use discount access codes.

	Head office only — requires create permission on this doctype (System
	Manager). Codes stay Active until disabled here.
	"""
	frappe.has_permission(CODE_DOCTYPE, "create", throw=True)

	count = cint(count)
	if not 1 <= count <= 500:
		frappe.throw(_("Number of codes must be between 1 and 500"))

	company = (company or "").strip() or None
	notes = (notes or "").strip() or None

	codes = []
	attempts = 0
	while len(codes) < count:
		attempts += 1
		if attempts > count * 5 + 10:
			frappe.throw(_("Could not generate unique codes, please try again"))
		code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
		try:
			frappe.get_doc(
				{
					"doctype": CODE_DOCTYPE,
					"code": code,
					"company": company,
					"notes": notes,
					"status": "Active",
				}
			).insert(ignore_permissions=True)
		except frappe.DuplicateEntryError:
			continue
		codes.append(code)

	return {"codes": codes}
