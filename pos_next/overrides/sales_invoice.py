# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

"""
Sales Invoice Override
Handles wallet payments that require party information for Receivable accounts.

"""

import frappe
from erpnext.accounts.doctype.pos_invoice.pos_invoice import POSInvoice
from erpnext.accounts.doctype.sales_invoice.sales_invoice import SalesInvoice
from erpnext.accounts.utils import get_account_currency
from frappe import _
from frappe.utils import cint, flt

from pos_next.invoice_type import is_pos_next_owned


def _find_paid_bundle_row_for_free(si_doc, free_row):
	"""Pick the paid SI item row whose bundle qty should absorb this free bundle's packed items."""
	candidates = []
	for row in si_doc.get("items"):
		if row.name == free_row.name or cint(row.is_free_item):
			continue
		if row.item_code != free_row.item_code:
			continue
		if (row.warehouse or "") != (free_row.warehouse or ""):
			continue
		candidates.append(row)
	if not candidates:
		return None
	free_idx = free_row.idx or 0
	before = [r for r in candidates if (r.idx or 0) < free_idx]
	if before:
		return max(before, key=lambda r: r.idx or 0)
	return candidates[0]


def _find_matching_packed_item_for_merge(si_doc, paid_row, component_item_code, warehouse):
	"""Match a packed item on the paid bundle line; prefer same warehouse."""
	w = warehouse or ""
	matches = []
	for pi in si_doc.get("packed_items"):
		if pi.parent_detail_docname != paid_row.name:
			continue
		if pi.parent_item != paid_row.item_code:
			continue
		if pi.item_code != component_item_code:
			continue
		matches.append(pi)
	if not matches:
		return None
	for pi in matches:
		if (pi.warehouse or "") == w:
			return pi
	return matches[0]


def _get_post_change_gl_entries_setting():
	"""
	Get post_change_gl_entries setting compatible with ERPNext v15 and v16.

	- ERPNext v15: Field is in 'Accounts Settings'
	- ERPNext v16: Field moved to ERPNext's 'POS Settings' (singleton)

	Since pos_next has its own 'POS Settings' doctype (non-singleton) that overrides
	ERPNext's, we read directly from the Singles table for v16 compatibility.

	Returns:
		int: 1 if post_change_gl_entries is enabled, 0 otherwise (default: 0)
	"""
	# Check if field exists in Accounts Settings schema (v15)
	meta = frappe.get_meta("Accounts Settings")
	if meta.has_field("post_change_gl_entries"):
		value = frappe.db.get_single_value("Accounts Settings", "post_change_gl_entries")
		return cint(value) if value is not None else 0

	# For v16, read directly from Singles table using Query Builder to avoid ORM issues
	# ERPNext's POS Settings is a singleton, data stored in Singles table
	Singles = frappe.qb.DocType("Singles")
	result = (
		frappe.qb.from_(Singles)
		.select(Singles.value)
		.where(Singles.doctype == "POS Settings")
		.where(Singles.field == "post_change_gl_entries")
		.limit(1)
		.run()
	)
	return cint(result[0][0]) if result else 0


class CustomSalesInvoice(SalesInvoice):
	"""
	Custom Sales Invoice class that handles wallet payments correctly.

	When a wallet payment is made using a Receivable account, ERPNext requires
	party information in the GL entry. This override adds party_type and party
	for wallet payment methods marked with is_wallet_payment.
	"""

	def make_pos_gl_entries(self, gl_entries):
		"""
		Override to add party information for wallet payment accounts.

		The standard ERPNext implementation doesn't set party_type/party for
		payment mode accounts, which causes validation errors for Receivable
		accounts (like wallet accounts).
		"""
		if cint(self.is_pos):
			skip_change_gl_entries = not _get_post_change_gl_entries_setting()

			for payment_mode in self.payments:
				if skip_change_gl_entries and payment_mode.account == self.account_for_change_amount:
					payment_mode.base_amount -= flt(self.change_amount)

				if payment_mode.amount:
					# POS, make payment entries
					# Credit entry to debit_to (customer receivable)
					gl_entries.append(
						self.get_gl_dict(
							{
								"account": self.debit_to,
								"party_type": "Customer",
								"party": self.customer,
								"against": payment_mode.account,
								"credit": payment_mode.base_amount,
								"credit_in_account_currency": payment_mode.base_amount
								if self.party_account_currency == self.company_currency
								else payment_mode.amount,
								"against_voucher": self.return_against
								if cint(self.is_return) and self.return_against
								else self.name,
								"against_voucher_type": self.doctype,
								"cost_center": self.cost_center,
							},
							self.party_account_currency,
							item=self,
						)
					)

					# Debit entry to payment mode account
					payment_mode_account_currency = get_account_currency(payment_mode.account)

					# Get party info for wallet payments
					party_type, party = self.get_party_and_party_type_for_pos_gl_entry(
						payment_mode.mode_of_payment, payment_mode.account
					)

					gl_entries.append(
						self.get_gl_dict(
							{
								"account": payment_mode.account,
								"party_type": party_type,
								"party": party,
								"against": self.customer,
								"debit": payment_mode.base_amount,
								"debit_in_account_currency": payment_mode.base_amount
								if payment_mode_account_currency == self.company_currency
								else payment_mode.amount,
								"cost_center": self.cost_center,
							},
							payment_mode_account_currency,
							item=self,
						)
					)

			if not skip_change_gl_entries:
				if hasattr(self, "get_gle_for_change_amount"):
					# ERPNext v16+: Method renamed and returns a list of GL entries
					# that needs to be extended to the main gl_entries list
					gl_entries.extend(self.get_gle_for_change_amount())
				else:
					# ERPNext v15: Method takes gl_entries as parameter
					# and appends change amount entries directly to it
					self.make_gle_for_change_amount(gl_entries)

	def validate_pos_paid_amount(self):
		"""
		Allow POS sales to submit without a payment row in two cases:

		1. Pure customer-credit redemption — POSNext redeems existing customer
		   credit after submit through Journal Entries / Payment Entry allocation,
		   so there is no real Mode of Payment row to send.
		2. "Pay on Account" credit sales — the cashier intentionally puts the full
		   amount on the customer's account, leaving the invoice outstanding.

		Both are only honoured when submit_invoice has explicitly marked the
		document via the corresponding flag (set after verifying the POS Settings
		permit the operation), so a tampered client can't bypass the check.
		"""
		if getattr(self.flags, "pos_next_redeemed_customer_credit", 0) or getattr(
			self.flags, "pos_next_credit_sale", 0
		):
			if len(self.payments) == 0 and cint(self.is_pos) and flt(self.grand_total) > 0:
				return

		super().validate_pos_paid_amount()

	def get_party_and_party_type_for_pos_gl_entry(self, mode_of_payment, account):
		"""
		Get party type and party for wallet payment GL entries.

		For wallet payments (Mode of Payment with is_wallet_payment=1),
		returns Customer as party_type and the invoice customer as party.
		For regular payments, returns empty strings.
		"""
		is_wallet_mode_of_payment = frappe.db.get_value(
			"Mode of Payment", mode_of_payment, "is_wallet_payment"
		)

		party_type, party = "", ""
		if is_wallet_mode_of_payment:
			party_type, party = "Customer", self.customer

		return party_type, party

	def update_packing_list(self):
		super().update_packing_list()
		self._combine_packed_qty_for_free_product_bundles()
		self._set_use_serial_batch_fields_on_packed_items()

	def _set_use_serial_batch_fields_on_packed_items(self):
		"""
		Force packed_items for batch/serial-tracked Items to use legacy fields path.

		ERPNext's auto-SBB creation during SLE.on_submit fails to link the bundle
		because SBB.voucher_detail_no gets remapped to the parent SI Item row name
		(set_serial_and_batch_values) while validation expects either a matching SLE
		or a Packed Item with that name. Routing through use_serial_batch_fields=1
		bypasses the broken auto-creation for the row.
		"""
		if not self.get("packed_items"):
			return
		for pi in self.get("packed_items"):
			if pi.get("serial_and_batch_bundle"):
				continue
			tracking = frappe.get_cached_value(
				"Item",
				pi.item_code,
				["has_batch_no", "has_serial_no"],
				as_dict=True,
			)
			if not tracking:
				continue
			if tracking.has_batch_no or tracking.has_serial_no:
				pi.use_serial_batch_fields = 1

	def _combine_packed_qty_for_free_product_bundles(self):
		"""
		Merge packed_items from free bundle lines into the matching paid bundle line.

		ERPNext builds packed rows per Sales Invoice Item row. For BOGO / pricing-rule
		free rows, the same product bundle often appears twice (paid + is_free_item).
		That duplicates component rows. Stock and picking should follow total bundle
		qty on one set of packed lines tied to the paid row.
		"""
		if self.is_return or not self.get("packed_items"):
			return

		free_bundle_rows = [
			row
			for row in self.get("items")
			if row.item_code and cint(row.is_free_item) and self.has_product_bundle(row.item_code)
		]
		if not free_bundle_rows:
			return

		for free_row in free_bundle_rows:
			paid_row = _find_paid_bundle_row_for_free(self, free_row)
			if not paid_row:
				continue

			to_remove = []
			for pi in list(self.get("packed_items")):
				if pi.parent_detail_docname != free_row.name or pi.parent_item != free_row.item_code:
					continue
				tgt = _find_matching_packed_item_for_merge(self, paid_row, pi.item_code, pi.warehouse)
				if tgt:
					prec = tgt.precision("qty")
					tgt.qty = flt(flt(tgt.qty) + flt(pi.qty), prec)
					to_remove.append(pi)

			for pi in to_remove:
				self.remove(pi)


class CustomPOSInvoice(CustomSalesInvoice, POSInvoice):
	"""POS Invoice lifecycle (ERPNext) + POS Next customizations.

	MRO: CustomPOSInvoice -> CustomSalesInvoice -> POSInvoice -> SalesInvoice.
	ERPNext's POS Invoice validate/on_submit win over SalesInvoice's; POS Next's
	update_packing_list / use_serial_batch_fields handling is inherited.

	Accounting parity: stock ledger and GL entries. Stocked-up ERPNext POS
	Invoices post nothing until a POS Closing Entry consolidates them into a
	Sales Invoice, which leaves Stock Balance and the ledger blind for the
	whole day. POS Next wants a POS Invoice to move stock and books like a
	Sales Invoice does, so its own invoices post those two steps here and are
	never consolidated. Built-in ERPNext POS Invoices (no
	posa_pos_opening_shift) keep the deferred behaviour.

	The accounting steps are added ON TOP of POSInvoice.on_submit/on_cancel
	rather than by calling SalesInvoice's: the Sales Invoice lifecycle is not
	doctype-safe here (it drives TCS through the `tax_withholding_entries`
	child table, which POS Invoice does not have), and ERPNext itself never
	runs it for a POS Invoice.
	"""

	# The GL builders walk a few fields that only exist on Sales Invoice.
	# These defaults mirror the Sales Invoice field defaults exactly, so a
	# POS Invoice books the same entries it would as a Sales Invoice; they are
	# in-memory only — no column is added, and nothing reads them back.
	use_company_roundoff_cost_center = 0
	update_outstanding_for_self = 1
	is_consolidated = 0

	def _post_parity_accounting(self):
		"""Stock ledger + GL, the two steps a POS Invoice defers to closing."""
		if self.update_stock == 1:
			self.update_stock_ledger()
		self.make_gl_entries()
		if self.update_stock == 1:
			self.repost_future_sle_and_gle()

	def on_submit(self):
		if not is_pos_next_owned(self):
			return super().on_submit()

		# POS Invoice's own tail (loyalty, coupon counts, serial/batch bundles,
		# unallocated-mode cleanup, status), then the deferred accounting.
		POSInvoice.on_submit(self)
		self._post_parity_accounting()

	def on_cancel(self):
		if not is_pos_next_owned(self):
			return super().on_cancel()

		# Reverse accounting while the serial/batch bundles are still linked:
		# POSInvoice.on_cancel delinks them, which would leave the reversal
		# without the bundle it has to reverse.
		if self.update_stock == 1:
			self.update_stock_ledger()
		self.make_gl_entries_on_cancel()
		if self.update_stock == 1:
			self.repost_future_sle_and_gle()
		POSInvoice.on_cancel(self)

		# POSInvoice.on_cancel overwrites ignore_linked_doctypes with its narrow
		# two-entry list. A parity POS Invoice OWNS the GL/SLE rows that carry
		# its name, so Frappe's back-link check (which runs right after
		# on_cancel) must treat them as its own books, not foreign links —
		# hence the same list Sales Invoice uses. Assigned last, on purpose.
		self.ignore_linked_doctypes = (
			"GL Entry",
			"Stock Ledger Entry",
			"Repost Item Valuation",
			"Repost Payment Ledger",
			"Repost Payment Ledger Items",
			"Repost Accounting Ledger",
			"Repost Accounting Ledger Items",
			"Unreconcile Payment",
			"Unreconcile Payment Entries",
			"Payment Ledger Entry",
			"Serial and Batch Bundle",
			"Tax Withholding Entry",
		)

	def validate_pos_opening_entry(self):
		if is_pos_next_owned(self):
			shift = frappe.db.get_value(
				"POS Opening Shift",
				self.posa_pos_opening_shift,
				("name", "pos_profile", "status"),
				as_dict=True,
			)
			if not shift or shift.status != "Open":
				frappe.throw(
					_("POS Opening Shift {0} is not open.").format(
						frappe.bold(self.posa_pos_opening_shift)
					)
				)
			if shift.pos_profile != self.pos_profile:
				frappe.throw(
					_("POS Opening Shift {0} belongs to POS Profile {1}, not {2}.").format(
						frappe.bold(self.posa_pos_opening_shift),
						frappe.bold(shift.pos_profile),
						frappe.bold(self.pos_profile),
					)
				)
			return
		super().validate_pos_opening_entry()
