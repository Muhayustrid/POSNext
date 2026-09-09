# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_first_day, getdate


class POSMonthlyTarget(Document):
	"""HQ monthly sales / transaction target per company.

	One row per company per month (month_start must be the 1st). Currency is
	implicit: amounts are in the company's default currency.
	"""

	def validate(self):
		if getdate(self.month_start) != get_first_day(self.month_start):
			frappe.throw(_("Month Start must be the first day of the month"))

		self._validate_company_access()
		# Uniqueness of (company, month) is enforced by the deterministic
		# autoname (company + month_start) primary key.

	def _validate_company_access(self):
		# Writers may only set targets for companies they can read.
		if not frappe.has_permission("Company", "read", doc=self.company):
			frappe.throw(
				_("Not permitted to access Company {0}").format(self.company), frappe.PermissionError
			)
