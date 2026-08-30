"""Promotion Selection Fact reporting projection DocType (design section 11)."""

from frappe.model.document import Document


class PromotionSelectionFact(Document):
	"""Pass-through controller.

	Rows are written only by ``pos_next.promotions.facts`` doc-event
	handlers and ``facts.rebuild()``; no role holds create/write permission
	(design section 18), so there is deliberately no validated Desk lifecycle
	here to extend. The table is derived, rebuildable state and never
	transaction authority (invariant I14).
	"""
