# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""PERF-02 regression: the bulk get_pos_invoices must answer exactly what the
per-invoice frappe.get_doc(...).as_dict() it replaced answered — for every
header column and child row the consumers actually read (backend aggregation
in make_closing_shift_from_opening and the Desk form script).

Run via pos_next/_pn_run_tests.py pos_next.tests.test_pos_invoices_bulk_parity
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt

from pos_next.api.invoices import submit_invoice
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	INVOICE_CHILD_FIELDS,
	INVOICE_HEADER_FIELDS,
	get_pos_invoices,
)
from pos_next.tests._posi_test_utils import POSInvoiceModeMixin


class TestGetPosInvoicesBulkParity(POSInvoiceModeMixin, FrappeTestCase):
	def setUp(self):
		super().setUp()
		# one POS Invoice through the real pipeline (setUp) and one Sales
		# Invoice written directly, so both child-table mappings are exercised
		result = submit_invoice(invoice=self._payload())
		self.posi_name = result.get("name")
		self._created.append(self.posi_name)

		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"customer": self.customer,
				"company": self.profile.company,
				"pos_profile": self.profile.name,
				"is_pos": 1,
				"update_stock": 0,
				"posa_pos_opening_shift": self.shift.name,
				"items": [
					# rate at list price: a lower rate would read as a manual
					# discount and trip the head-office discount-code gate
					{"item_code": self.item, "qty": 2, "rate": 100, "warehouse": self.profile.warehouse}
				],
				"payments": [{"mode_of_payment": self.mode[0], "amount": 200}],
			}
		)
		si.flags.ignore_permissions = True
		si.insert()
		si.submit()
		self._created.append(si.name)
		frappe.db.commit()

	def test_bulk_output_matches_get_doc_as_dict(self):
		collected = get_pos_invoices(self.shift.name)
		by_name = {row["name"]: row for row in collected}
		self.assertIn(self.posi_name, by_name)

		for name in (self.posi_name, self._created[-1]):
			row = by_name[name]
			old = frappe.get_doc(row["doctype"], name).as_dict()

			for field in INVOICE_HEADER_FIELDS:
				self.assertEqual(row.get(field), old.get(field), f"{name}.{field}")
			self.assertEqual(row["doctype"], old["doctype"])

			for parentfield, (_child_dt, fields) in INVOICE_CHILD_FIELDS.items():
				new_rows = row[parentfield]
				old_rows = old.get(parentfield) or []
				self.assertEqual(len(new_rows), len(old_rows), f"{name}.{parentfield}")
				for new_row, old_row in zip(new_rows, old_rows):
					for field in fields:
						self.assertEqual(new_row[field], old_row.get(field), f"{name}.{field}")

	def test_get_invoices_bulk_items_match_per_invoice_shape(self):
		"""PERF-02b: the bulk item fetch must answer the same rows and row
		shape the per-invoice query produced (both row doctypes)."""
		from pos_next.api.invoices import get_invoices

		rows = get_invoices(self.profile.name, include_items=True)
		by_name = {row["name"]: row for row in rows}
		for name in (self.posi_name, self._created[-1]):
			row = by_name[name]
			items = frappe.db.sql(
				f"select item_code, item_name, qty, rate, amount from `tab{row['doctype']} Item`"
				" where parent = %(name)s order by idx",
				{"name": name},
				as_dict=True,
			)
			self.assertEqual(len(row["items"]), len(items), name)
			for new_row, old_row in zip(row["items"], items):
				self.assertEqual(dict(new_row), dict(old_row), name)
			self.assertTrue(all(set(p) == {"mode_of_payment", "amount"} for p in row["payments"]))

	def test_closing_aggregation_unchanged(self):
		"""make_closing_shift_from_opening totals must match the invoice docs."""
		import json

		opening = frappe.get_doc("POS Opening Shift", self.shift.name)
		result = get_pos_invoices(self.shift.name)
		expected = sum(flt(inv["grand_total"]) for inv in result)

		from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
			make_closing_shift_from_opening,
		)

		closing = make_closing_shift_from_opening(
			json.dumps(
				{
					"name": self.shift.name,
					"period_start_date": str(opening.period_start_date),
					"pos_profile": self.profile.name,
					"user": "Administrator",
					"company": self.profile.company,
				}
			)
		)
		self.assertEqual(flt(closing["grand_total"]), flt(expected))
		self.assertEqual(len(closing["pos_transactions"]), len(result))
