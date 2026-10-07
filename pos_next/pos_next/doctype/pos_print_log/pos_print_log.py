# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.query_builder import Interval
from frappe.query_builder.functions import Now
from frappe.utils import cint


class POSPrintLog(Document):
	@staticmethod
	def clear_old_logs(days=90):
		days = cint(days) if days is not None else 90
		table = frappe.qb.DocType("POS Print Log")
		frappe.db.delete(table, filters=(table.creation < (Now() - Interval(days=days))))
