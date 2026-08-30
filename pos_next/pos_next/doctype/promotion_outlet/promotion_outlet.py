"""Promotion Outlet child DocType.

Validation lives in the parent Promotion controller: child DocType hooks do
not run when the parent saves (frappe/model/document.py persists children via
db_insert/db_update), so Promotion.validate() owns every rule — including the
(company, warehouse) identity, warehouse ownership, root-company fence, and
currency uniformity of outlet rows.
"""

from frappe.model.document import Document


class PromotionOutlet(Document):
	pass
