# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from pos_next.services import reject_request


class POSRejectRequest(Document):
	def validate(self):
		reject_request.validate_request(self)

	def on_submit(self):
		reject_request.on_submit_request(self)

	def before_cancel(self):
		reject_request.before_cancel_request(self)

	def on_cancel(self):
		reject_request.on_cancel_request(self)
