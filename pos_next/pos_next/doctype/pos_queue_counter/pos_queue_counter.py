# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class POSQueueCounter(Document):
	# begin: auto-generated types
	from frappe.types import DF

	if frappe.__version__ >= "15":
		company: DF.Link
		date: DF.Date | None
		current_number: DF.Int
	# end: auto-generated types

	def validate(self):
		duplicate = frappe.db.exists(
			"POS Queue Counter",
			{"company": self.company, "date": self.date, "name": ("!=", self.name)},
		)
		if duplicate:
			frappe.throw(
				_("A queue counter for {0} on {1} already exists").format(
					frappe.bold(self.company), frappe.bold(self.date)
				)
			)
