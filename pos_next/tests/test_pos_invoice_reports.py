# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Task 9 union tests: in POS Invoice mode, sales reports and the HQ
monitoring payload must count each invoice exactly once across POS Invoice +
legacy (non-consolidated) Sales Invoice rows. Run via
pos_next/_pn_run_tests.py pos_next.tests.test_pos_invoice_reports
"""

import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, today

from pos_next.api.invoices import submit_invoice
from pos_next.invoice_type import SALES_INVOICE
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
	make_closing_shift_from_opening,
)
from pos_next.tests._posi_test_utils import (
	_PROFILE_FILTER,
	POSInvoiceModeMixin,
	_set_invoice_type,
)


class TestReportUnionCountsOnce(POSInvoiceModeMixin, FrappeTestCase):
	def test_report_union_counts_once(self):
		from pos_next.api.hq_monitoring import get_sales_monitoring

		# shared dev site: other sales may exist for this company today, so
		# the HQ assertion compares against a scoped pre-test baseline
		baseline = get_sales_monitoring(
			company=self.profile.company, from_date=today(), to_date=today()
		)
		ccy = frappe.get_cached_value("Company", self.profile.company, "default_currency")
		baseline_net = flt(baseline["range"]["net_tax_incl"]["by_currency"].get(ccy))

		# one submitted POS Invoice on the shift (rate 100)
		result = submit_invoice(invoice=self._payload())
		self.posi_name = result.get("name")
		self._created.append(self.posi_name)
		frappe.db.commit()

		# one plain legacy Sales Invoice, same company/today, unconsolidated
		plain_si = self._make_sales_invoice()

		# a legacy-style row flagged is_consolidated represents POS Invoices
		# that are already in the union — must be excluded, never double count
		consolidated = self._make_sales_invoice(rate=777)
		frappe.db.set_value(
			"Sales Invoice", consolidated.name, "is_consolidated", 1, update_modified=False
		)
		self._close_shift_with(
			[
				{
					"sales_invoice": plain_si.name,
					"customer": plain_si.customer,
					"posting_date": plain_si.posting_date,
					"grand_total": plain_si.grand_total,
				},
				{
					"sales_invoice": consolidated.name,
					"customer": consolidated.customer,
					"posting_date": consolidated.posting_date,
					"grand_total": consolidated.grand_total,
				},
			]
		)
		# closing also consolidated the POSI into yet another Sales Invoice
		# (is_consolidated=1) — excluded from every read below

		from pos_next.pos_next.report.sales_vs_shifts_report.sales_vs_shifts_report import (
			execute,
		)

		columns, data, message, chart, summary = execute(
			filters={
				"company": self.profile.company,
				"from_date": today(),
				"to_date": today(),
				# pinned to this test's closing shift so site noise cannot
				# leak into the exact totals
				"shift": self.closing.name,
			}
		)
		total = sum(flt(row.gross_sales) for row in data)
		# POSI + plain SI counted once each (expected derived from the source
		# docs: the shared site may add a default tax template to the legacy
		# SI); the flagged row and the merge-generated consolidated SI must
		# not appear
		posi_gt = flt(frappe.db.get_value("POS Invoice", self.posi_name, "grand_total"))
		expected = posi_gt + flt(plain_si.grand_total)
		self.assertEqual(total, expected)
		self.assertEqual(sum(row.invoices for row in data), 2)

		# HQ monitoring, company-scoped: same union, same exclusion (delta over
		# the pre-test baseline = the two invoices; consolidated ones add 0)
		payload = get_sales_monitoring(
			company=self.profile.company, from_date=today(), to_date=today()
		)
		after_net = flt(payload["range"]["net_tax_incl"]["by_currency"].get(ccy))
		self.assertEqual(after_net - baseline_net, expected)
		frappe.db.commit()

	def _make_sales_invoice(self, rate=100):
		"""A plain legacy Sales Invoice (is_pos, unconsolidated) on the same
		company/day — the union's second leg."""
		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.profile.company,
				"customer": self.customer,
				"is_pos": 1,
				"pos_profile": self.profile.name,
				"posting_date": today(),
				"items": [{"item_code": self.item, "qty": 1, "rate": rate}],
				"payments": [{"mode_of_payment": self.mode[0], "amount": rate}],
			}
		)
		si.flags.ignore_permissions = True
		si.insert()
		si.submit()
		self._created.append(si.name)
		return si

	def _close_shift_with(self, extra_rows):
		"""Close the shift through production code, appending Sales Invoice
		Reference rows (sales_invoice column) for the legacy invoices."""
		opening = frappe.get_doc("POS Opening Shift", self.shift.name)
		result = make_closing_shift_from_opening(
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
		result["pos_transactions"].extend(extra_rows)
		closing_doc = frappe.get_doc(result)
		closing_doc.flags.ignore_permissions = True
		closing_doc.insert(ignore_permissions=True)
		self.closing = closing_doc
		closing_doc.submit()


class TestSIModeCountsConsolidated(FrappeTestCase):
	"""Fix-wave regression: in pure Sales Invoice mode the report unions apply
	NO is_consolidated exclusion — ERPNext built-in-POS consolidated invoices
	must stay counted in HQ monitoring and all reports. (The exclusion's
	anti-double-count behavior in POS Invoice mode is covered by
	TestReportUnionCountsOnce.)"""

	def setUp(self):
		# deterministic on the shared site: default mode (guard mock-neutralised)
		_set_invoice_type(SALES_INVOICE)

	def test_si_mode_union_counts_consolidated(self):
		from pos_next.invoice_type import sales_invoice_item_union, sales_invoice_union

		profile = frappe.db.get_value(
			"POS Profile", _PROFILE_FILTER, ["name", "company"], as_dict=True
		)
		if not profile:
			self.skipTest("no schedule-safe POS Profile")
		item = frappe.get_all(
			"Item", filters={"disabled": 0, "is_sales_item": 1}, pluck="name", limit=1
		)
		if not item:
			self.skipTest("no sales item")
		customer = frappe.db.get_value("Customer", {"is_internal_customer": 0}, "name")
		if not customer:
			self.skipTest("no non-internal customer")
		mode = frappe.db.get_value(
			"POS Payment Method",
			{"parent": profile.name, "parenttype": "POS Profile"},
			"mode_of_payment",
		)
		if not mode:
			self.skipTest("profile has no payment methods")

		si = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": profile.company,
				"customer": customer,
				"is_pos": 1,
				"pos_profile": profile.name,
				"posting_date": today(),
				"items": [{"item_code": item[0], "qty": 1, "rate": 100}],
				"payments": [{"mode_of_payment": mode, "amount": 100}],
			}
		)
		si.flags.ignore_permissions = True
		si.insert()
		si.submit()
		# legacy-style consolidated flag (ERPNext built-in-POS consolidation)
		frappe.db.set_value("Sales Invoice", si.name, "is_consolidated", 1, update_modified=False)
		try:
			head_union = sales_invoice_union("si.name", "si.name = %(name)s")
			item_union = sales_invoice_item_union("sii.qty", "si.name = %(name)s")
			# pure SI mode: no exclusion anywhere in the generated SQL
			self.assertNotIn("is_consolidated", head_union)
			self.assertNotIn("is_consolidated", item_union)
			# and the flagged row is still counted by both unions
			self.assertEqual(
				frappe.db.sql(f"select count(*) c from {head_union}", {"name": si.name})[0][0], 1
			)
			self.assertEqual(
				flt(frappe.db.sql(f"select sum(sii.qty) q from {item_union}", {"name": si.name})[0][0]),
				1.0,
			)
		finally:
			frappe.db.set_value("Sales Invoice", si.name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("Sales Invoice", si.name, force=1, ignore_permissions=True)
