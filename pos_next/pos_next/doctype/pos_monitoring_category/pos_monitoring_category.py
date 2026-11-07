# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


def _reject_duplicate_labels(doc):
	seen = set()
	for row in doc.get("labels") or []:
		if row.label in seen:
			frappe.throw(_("Duplicate label {0} found").format(row.label), frappe.ValidationError)
		seen.add(row.label)


class POSMonitoringCategory(Document):
	def validate(self):
		_reject_duplicate_labels(self)
