import frappe
from frappe import _
from frappe.model.document import Document


class POSNextInvoiceSettings(Document):
	def validate(self):
		before = self.get_doc_before_save()
		if before and before.invoice_type != self.invoice_type:
			self.validate_switch()

	def validate_switch(self):
		open_shifts = frappe.db.count("POS Opening Shift", {"docstatus": 1, "status": "Open"})
		if open_shifts:
			frappe.throw(
				_("Invoice Type cannot be changed while {0} POS Opening Shift(s) are open.").format(
					frappe.bold(open_shifts)
				)
			)
		pending = frappe.db.count("Offline Invoice Sync", {"status": "Pending"})
		if pending:
			frappe.throw(
				_("Invoice Type cannot be changed while {0} offline invoice(s) are pending sync.").format(
					frappe.bold(pending)
				)
			)
