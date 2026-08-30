"""Promotion Component child DocType.

Validation lives in the parent Promotion controller: child DocType hooks do
not run when the parent saves (frappe/model/document.py persists children via
db_insert/db_update), so Promotion.validate() owns every rule.
"""

from frappe.model.document import Document


class PromotionComponent(Document):
	pass
