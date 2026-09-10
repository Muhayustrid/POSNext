# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Shared POS-Invoice-mode test helpers.

Duplicated from pos_next/api/test_pos_invoice_submit.py on purpose (tests
must not import across app subpackages) — keep the two in sync.
"""

from unittest import mock

import frappe

from pos_next.invoice_type import POS_INVOICE, SALES_INVOICE

# Schedule-safe profile: inserting a POS Opening Shift can never throw for
# being outside a scheduled window (same filter as pos_next.test_invoice_type).
_PROFILE_FILTER = [
	["disabled", "=", 0],
	["pos_schedule_enforce_closing", "=", 0],
	["pos_schedule_end", "is", "set"],
]


def _set_invoice_type(value):
	"""Flip the site switch. The switch guard (open shifts / pending syncs) is
	mock-neutralised: this shared dev site holds real open shifts."""
	with mock.patch("frappe.db.count", return_value=0):
		doc = frappe.get_doc("POS Next Invoice Settings", "POS Next Invoice Settings")
		doc.invoice_type = value
		doc.save(ignore_permissions=True)
	try:
		del frappe.local._pos_next_invoice_doctype
	except AttributeError:
		pass  # not cached yet (`in frappe.local` is unreliable on v16)
	frappe.db.value_cache.pop("POS Next Invoice Settings", None)


class POSInvoiceModeMixin:
	"""Common setup/teardown for tests that submit POS Invoices through the
	real pipeline against a fresh POS Opening Shift.

	setUp: schedule-safe profile + stock item + customer + payment mode,
	a real Material Receipt, site switched to POS Invoice mode, and an
	opening shift (inserted + submitted).
	tearDown: cancels the closing shift via the app's own on_cancel path
	(when the test created one) so merge logs / consolidated Sales Invoices
	are cleaned by production code, then sweeps invoices, stock and the
	shift, and restores Sales Invoice mode.
	"""

	def setUp(self):
		self._created = []
		self.profile = frappe.db.get_value(
			"POS Profile", _PROFILE_FILTER, ["name", "company", "warehouse"], as_dict=True
		)
		if not self.profile:
			self.skipTest("no schedule-safe POS Profile")
		item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
			pluck="name",
			limit=1,
		)
		if not item:
			self.skipTest("no stock sales item")
		self.item = item[0]
		# non-internal customer: internal ones only transact with their
		# 'Allowed To Transact With' companies (ERPNext inter-company check)
		self.customer = frappe.db.get_value("Customer", {"is_internal_customer": 0}, "name")
		if not self.customer:
			self.skipTest("no non-internal customer")
		self.mode = frappe.get_all(
			"POS Payment Method",
			{"parent": self.profile.name, "parenttype": "POS Profile"},
			pluck="mode_of_payment",
			limit=1,
		)
		if not self.mode:
			self.skipTest("profile has no payment methods")
		self._make_stock()
		_set_invoice_type(POS_INVOICE)
		self.shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": "Administrator",
				"posting_date": frappe.utils.nowdate(),
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode[0], "amount": 0}],
			}
		).insert(ignore_permissions=True)
		# a concurrent writer on this shared site can bump `modified` right
		# after insert; refresh before submit (see pos_next.test_invoice_type)
		self.shift.reload()
		self.shift.submit()

	def _make_stock(self):
		"""Real Material Receipt so both the Bin-based pre-check and the
		stock-ledger validation on submit pass (a Bin db.set_value hack would
		fool the former but not the latter)."""
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"stock_entry_type": "Material Receipt",
				"purpose": "Material Receipt",
				"company": self.profile.company,
				"items": [
					{
						"item_code": self.item,
						"qty": 5,
						"t_warehouse": self.profile.warehouse,
						"allow_zero_valuation_rate": 1,
					}
				],
			}
		)
		se.flags.ignore_permissions = True
		se.insert()
		se.submit()
		self.stock_entry = se

	def _payload(self, **overrides):
		payload = {
			"pos_profile": self.profile.name,
			"posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"doctype": "Sales Invoice",  # client guess must be ignored
			"items": [
				{"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.profile.warehouse}
			],
			"payments": [{"mode_of_payment": self.mode[0], "amount": 100}],
		}
		payload.update(overrides)
		return payload

	def tearDown(self):
		# cancel the closing shift through production code: on_cancel clears
		# merge logs, cancels consolidated Sales Invoices and resets the POS
		# Invoices' consolidated_invoice/status
		if getattr(self, "closing", None) and frappe.db.exists("POS Closing Shift", self.closing.name):
			doc = frappe.get_doc("POS Closing Shift", self.closing.name)
			if doc.docstatus == 1:
				doc.reload()
				doc.flags.ignore_permissions = True
				doc.cancel()
			frappe.delete_doc("POS Closing Shift", doc.name, force=1, ignore_permissions=True)
		# safety net on this shared site: any merge logs still pointing at
		# this shift's POS Invoices
		for posi in frappe.get_all(
			"POS Invoice", {"posa_pos_opening_shift": self.shift.name}, pluck="name"
		):
			for log in frappe.get_all("POS Invoice Merge Log", {"pos_invoice": posi}, pluck="name"):
				if frappe.db.get_value("POS Invoice Merge Log", log, "docstatus") == 1:
					frappe.get_doc("POS Invoice Merge Log", log).cancel()
				frappe.delete_doc("POS Invoice Merge Log", log, force=1, ignore_permissions=True)

		# Sweeps also cover the interim state where an invoice landed in the
		# legacy doctype: posa_pos_opening_shift links it to this test's shift.
		for doctype in ("POS Invoice", "Sales Invoice"):
			for name in frappe.get_all(
				doctype, {"posa_pos_opening_shift": self.shift.name}, pluck="name"
			):
				self._created.append(name)
		for name in dict.fromkeys(self._created):
			for doctype in ("POS Invoice", "Sales Invoice"):
				if frappe.db.exists(doctype, name):
					doc = frappe.get_doc(doctype, name)
					if doc.docstatus == 1:
						doc.cancel()
					frappe.delete_doc(doctype, name, force=1, ignore_permissions=True)
					break
		if getattr(self, "stock_entry", None):
			se = frappe.get_doc("Stock Entry", self.stock_entry.name)
			if se.docstatus == 1:
				se.cancel()
			frappe.delete_doc("Stock Entry", se.name, force=1, ignore_permissions=True)
		if getattr(self, "shift", None):
			# staleness-proof teardown (pos_next.test_invoice_type): cancel()
			# can fail on a `modified` bumped by on_submit's db.set_value
			frappe.db.set_value(
				"POS Opening Shift", self.shift.name, "docstatus", 2, update_modified=False
			)
			frappe.delete_doc("POS Opening Shift", self.shift.name, force=1, ignore_permissions=True)
		for offline_id in getattr(self, "_sync_rows", []):
			frappe.db.delete("Offline Invoice Sync", {"offline_id": offline_id})
		_set_invoice_type(SALES_INVOICE)
		frappe.db.commit()
