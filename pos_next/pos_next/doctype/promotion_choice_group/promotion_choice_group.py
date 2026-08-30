"""Promotion Choice Group child DocType.

group_key identity (D3) is assigned by the parent Promotion controller: child
DocType hooks do not run when the parent saves (frappe/model/document.py
persists children via db_insert/db_update), so key generation and every
validation live in Promotion.validate().
"""

from frappe.model.document import Document


class PromotionChoiceGroup(Document):
	pass
