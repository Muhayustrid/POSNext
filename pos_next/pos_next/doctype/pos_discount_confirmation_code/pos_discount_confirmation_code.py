# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

CODE_ALPHABET = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")


class POSDiscountConfirmationCode(Document):
	def validate(self):
		self.code = (self.code or "").strip().upper()
		if not self.code:
			frappe.throw(_("Code is required"))
		invalid = set(self.code) - CODE_ALPHABET
		if invalid:
			frappe.throw(
				_("Code may only contain letters and digits (without {0})").format(
					", ".join(sorted("0O1IL"))
				)
			)

		if self.is_new() and self.status == "Used":
			frappe.throw(_("A new code cannot start out as Used"))
