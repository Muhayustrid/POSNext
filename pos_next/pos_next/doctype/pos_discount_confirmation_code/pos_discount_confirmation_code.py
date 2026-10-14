# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import frappe

from pos_next.services.confirmation_code import (
	CODE_ALPHABET,
	CODE_LENGTH,
	generate_codes_for,
	random_code,
)

CODE_DOCTYPE = "POS Discount Confirmation Code"


# Backwards-compatible aliases — the shared helper owns the values now.
_random_code = random_code


class POSDiscountConfirmationCode(frappe.model.document.Document):
	def validate(self):
		from pos_next.services.confirmation_code import prepare_code

		prepare_code(self)


@frappe.whitelist(methods=["POST"])
def generate_codes(count: int = 1, company: str | None = None, notes: str | None = None) -> dict:
	"""Generate multi-use discount access codes (see the shared helper)."""
	return generate_codes_for(CODE_DOCTYPE, count, company, notes)
