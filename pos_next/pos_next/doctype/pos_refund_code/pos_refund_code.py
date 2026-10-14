import frappe
from frappe.model.document import Document

from pos_next.services.confirmation_code import generate_codes_for, prepare_code

CODE_DOCTYPE = "POS Refund Code"


class POSRefundCode(Document):
	def validate(self):
		prepare_code(self)


@frappe.whitelist(methods=["POST"])
def generate_codes(count: int = 1, company: str | None = None, notes: str | None = None) -> dict:
	"""Generate multi-use refund access codes (see the shared helper)."""
	return generate_codes_for(CODE_DOCTYPE, count, company, notes)
