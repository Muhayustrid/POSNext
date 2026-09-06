# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class POSProductionRecipe(Document):
	def validate(self):
		self.validate_items_not_finished_item()

	def validate_items_not_finished_item(self):
		for row in self.items:
			if row.item_code == self.production_item:
				frappe.throw(
					_("Material {0} is the production item itself").format(row.item_code)
				)
