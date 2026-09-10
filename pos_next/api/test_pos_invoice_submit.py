# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Full-cycle tests: server-resolved doctype through the submit pipeline.

In POS Invoice mode the client's `doctype` guess must be ignored —
update_invoice/submit_invoice resolve the target doctype from POS Next
Invoice Settings, credit sale is blocked, and the returns listing surfaces
POS Invoices. Run via
pos_next/_pn_run_tests.py pos_next.api.test_pos_invoice_submit
"""

from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import (
	_sync_existing_invoice,
	check_offline_invoice_synced,
	get_returnable_invoices,
	submit_invoice,
)
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


class TestSubmitInvoicePOSIMode(FrappeTestCase):
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

	def tearDown(self):
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

	def test_submit_creates_pos_invoice(self):
		result = submit_invoice(invoice=self._payload())
		name = result.get("name")
		self._created.append(name)
		self.assertTrue(name)
		doc = frappe.get_doc("POS Invoice", name)
		self.assertEqual(doc.doctype, "POS Invoice")
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.posa_pos_opening_shift, self.shift.name)
		# the profile's tax template is inclusive, so payment == grand_total
		self.assertEqual(doc.paid_amount, doc.grand_total)

	def test_credit_sale_blocked_in_posi_mode(self):
		payload = self._payload(pos_next_credit_sale=1, payments=[])
		with self.assertRaises(frappe.ValidationError) as ctx:
			submit_invoice(invoice=payload)
		# must be OUR clear message, not ERPNext's payment-row error
		self.assertIn("POS Invoice mode", str(ctx.exception))

	def test_returnable_lists_pos_invoice(self):
		result = submit_invoice(invoice=self._payload())
		name = result.get("name")
		self._created.append(name)
		self.assertTrue(name)
		# actual whitelist signature: get_returnable_invoices(limit, pos_profile)
		invoices = get_returnable_invoices(limit=50, pos_profile=self.profile.name)
		names = [row.get("name") for row in invoices]
		self.assertIn(name, names)

	def test_offline_dedup_cross_doctype(self):
		# replaying the same offline payload twice must yield ONE POS Invoice,
		# with the sync record's pos_invoice column set (not sales_invoice)
		offline_id = "pos_offline_test_xmode1"
		self._sync_rows = [offline_id]
		frappe.db.delete("Offline Invoice Sync", {"offline_id": offline_id})
		payload = self._payload(offline_id=offline_id)
		first = submit_invoice(invoice=payload)
		frappe.db.commit()
		second = submit_invoice(invoice=payload)  # replay must return the same invoice
		self.assertEqual(first.get("name"), second.get("name"))
		sync = frappe.db.get_value(
			"Offline Invoice Sync",
			{"offline_id": offline_id},
			["pos_invoice", "sales_invoice"],
			as_dict=1,
		)
		self.assertEqual(sync.pos_invoice, first.get("name"))
		self.assertFalse(sync.sales_invoice)
		# pre-sync check returns whichever column holds the invoice
		result = check_offline_invoice_synced(offline_id)
		self.assertTrue(result.get("synced"))
		self.assertEqual(result.get("sales_invoice"), first.get("name"))

	def test_sync_existing_invoice_tries_both_columns(self):
		result = submit_invoice(invoice=self._payload())
		name = result.get("name")
		self._created.append(name)
		# a Sales-Invoice-column pointer at a POS Invoice name must NOT resolve
		# (the name lives in the POS Invoice table); the pos_invoice column must
		self.assertIsNone(_sync_existing_invoice({"sales_invoice": name, "pos_invoice": None}))
		self.assertEqual(
			_sync_existing_invoice({"sales_invoice": None, "pos_invoice": name}),
			(POS_INVOICE, name),
		)

	def test_effective_stock_blocks_oversell(self):
		from pos_next.api.invoices import validate_cart_items
		from pos_next.api.items import get_item_stock

		def _bin_qty():
			return frappe.utils.flt(
				frappe.db.get_value(
					"Bin", {"item_code": self.item, "warehouse": self.profile.warehouse}, "actual_qty"
				)
				or 0
			)

		# submit one invoice consuming ALL stocked qty (5 from _make_stock, plus
		# any residual on this shared site so effective stock lands at exactly 0)
		qty = int(_bin_qty())
		payload = {
			"pos_profile": self.profile.name, "posa_pos_opening_shift": self.shift.name,
			"customer": self.customer,
			"items": [{"item_code": self.item, "qty": qty, "rate": 100,
			           "warehouse": self.profile.warehouse}],
			"payments": [{"mode_of_payment": self.mode[0], "amount": 100 * qty}],
		}
		result = submit_invoice(invoice=payload)
		self._created.append(result.get("name"))
		frappe.db.commit()

		# Bin still shows the full qty (no SLE until consolidation)…
		bin_after = _bin_qty()
		self.assertEqual(bin_after, qty)
		# …but the items API reports the effective (deducted) qty …
		stock = get_item_stock(self.item, self.profile.warehouse)
		self.assertEqual(stock["stock_qty"], 0)
		# …and cart validation rejects another unit (real signature returns the
		# error list instead of raising).
		errors = validate_cart_items(
			[{"item_code": self.item, "qty": 1, "warehouse": self.profile.warehouse}],
			pos_profile=self.profile.name,
		)
		self.assertTrue(errors)

	def test_history_lists_pos_invoice(self):
		from pos_next.api.invoices import get_invoice, get_invoices

		result = submit_invoice(invoice=self._payload())
		name = result.get("name")
		self._created.append(name)
		self.assertTrue(name)
		# same args the history dialog sends (InvoiceHistoryDialog makeParams)
		rows = get_invoices(pos_profile=self.profile.name)
		names = [r.get("name") for r in rows if isinstance(r, dict)]
		self.assertIn(name, names)
		row = next(r for r in rows if r.get("name") == name)
		self.assertEqual(row.get("doctype"), "POS Invoice")

		# cross-doctype fallback: fetch by name must also work for a legacy SI
		legacy = frappe.get_all("Sales Invoice", {"docstatus": 1}, pluck="name", limit=1)
		if legacy:
			doc = get_invoice(legacy[0])
			self.assertEqual(doc.get("doctype"), "Sales Invoice")

	def test_draft_invoices_default_resolves_doctype(self):
		from pos_next.api.invoices import get_draft_invoices

		# no doctype passed (the HTTP shape) -> resolves to the mode doctype;
		# neither doctype has a standard pos_opening_shift column, so the shift
		# filter is dropped and the result tracks all drafts of that doctype
		drafts = get_draft_invoices(self.shift.name)
		self.assertEqual(
			{d.name for d in drafts},
			set(frappe.get_all("POS Invoice", filters={"docstatus": 0}, pluck="name")),
		)
		# explicit doctype callers keep their behavior
		drafts = get_draft_invoices(self.shift.name, doctype="Sales Invoice")
		self.assertEqual(
			{d.name for d in drafts},
			set(frappe.get_all("Sales Invoice", filters={"docstatus": 0}, pluck="name")),
		)


class TestSubmitInvoiceSalesOrderPayload(FrappeTestCase):
	"""Fix-wave regression: submit_invoice must read the client's doctype
	BEFORE _strip_server_managed_fields removes it — a "Sales Order" payload
	(the InvoiceCart selectDocType flow) must resolve to the Sales Order draft
	lookup, not to the site's invoice doctype."""

	def test_sales_order_payload_looks_up_sales_order_draft(self):
		seen = []

		class _ShortCircuit(Exception):
			pass

		def _exists(doctype, name=None):
			# first doctype consumption in submit_invoice is the draft lookup
			seen.append((doctype, name))
			raise _ShortCircuit()

		payload = {
			"doctype": "Sales Order",
			"name": "SO-PN-TEST-NONEXISTENT",
			"customer": "_Test Customer",
			"items": [],
		}
		with mock.patch("frappe.db.exists", side_effect=_exists), mock.patch(
			"frappe.log_error"
		):
			with self.assertRaises(_ShortCircuit):
				submit_invoice(invoice=payload)

		self.assertEqual(seen, [("Sales Order", "SO-PN-TEST-NONEXISTENT")])

