# Copyright (c) 2026, Youssef Restom and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint

from pos_next.api.settings_resolver import _NON_DATA_TYPES
from pos_next.invoice_type import validate_invoice_type_change
from pos_next.target_basis import validate_target_bases


class POSNextGlobalSettings(Document):
	def load_from_db(self):
		"""Hydrate never-persisted fields with their meta defaults.

		A single fetched with get_doc carries a zeroed skeleton for fields
		that were never persisted, so any doc.save() (patch, tests, the
		 settings form) would write 0 over them and silently defeat the
		resolver's "unset -> meta default" tier. Hydration makes a save
		persist the default instead, which the resolver treats the same as
		never-persisted.
		"""
		super().load_from_db()
		persisted = {
			row[0]
			for row in frappe.db.sql(
				"select field from `tabSingles` where doctype = %s", self.doctype, as_list=True
			)
		}
		for df in self.meta.get("fields"):
			if (
				df.fieldname not in persisted
				and df.fieldtype not in _NON_DATA_TYPES
				and df.fieldname not in ("name",)
				and df.default
				and not self.get(df.fieldname)
			):
				self.set(df.fieldname, df.default)

	def validate(self):
		"""Invoice-type switches stay gated; target bases must be valid."""
		validate_invoice_type_change(self)
		validate_target_bases(self)

	def on_update(self):
		"""Bridge allow_negative_stock to the core Stock Settings toggle.

		The single is the only writer now, so a plain compare-and-set replaces
		the old per-profile "count other enabled rows" logic.
		"""
		current = cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock") or 0)
		wanted = cint(self.allow_negative_stock)
		if wanted == current:
			return

		frappe.db.set_single_value("Stock Settings", "allow_negative_stock", wanted, update_modified=False)
		if wanted:
			frappe.msgprint(
				_("Stock Settings 'Allow Negative Stock' has been automatically enabled."),
				indicator="green",
				alert=True,
			)
		else:
			frappe.msgprint(
				_("Stock Settings 'Allow Negative Stock' has been automatically disabled."),
				indicator="orange",
				alert=True,
			)
