# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class OfflineInvoiceSync(Document):
	"""
	Tracks offline invoice synchronization to prevent duplicate submissions.

	Each record maps an offline_id (generated client-side) to a Sales Invoice,
	allowing the system to detect and prevent duplicate sync attempts.
	"""

	def before_insert(self):
		"""Set synced_at timestamp before insert."""
		if not self.synced_at:
			self.synced_at = frappe.utils.now_datetime()

	@staticmethod
	def is_synced(offline_id):
		"""
		Check if an offline_id has already been synced.

		Args:
		    offline_id: The offline ID to check

		Returns:
		    dict with 'synced' (bool), 'sales_invoice'/'pos_invoice' (str or None),
		    and 'status' (str or None)
		"""
		if not offline_id:
			return {"synced": False, "sales_invoice": None, "pos_invoice": None, "status": None}

		existing = frappe.db.get_value(
			"Offline Invoice Sync",
			{"offline_id": offline_id},
			["name", "sales_invoice", "pos_invoice", "status"],
			as_dict=True,
		)

		if existing:
			# Only consider it synced if status is "Synced" with an invoice in
			# either column (POS Invoice mode writes pos_invoice, not sales_invoice)
			has_invoice = existing.sales_invoice or existing.pos_invoice
			is_synced = existing.status == "Synced" and has_invoice
			return {
				"synced": bool(is_synced),
				"sales_invoice": existing.sales_invoice if is_synced else None,
				"pos_invoice": existing.pos_invoice if is_synced else None,
				"status": existing.status,
			}

		return {"synced": False, "sales_invoice": None, "pos_invoice": None, "status": None}
