# Copyright (c) 2020, Youssef Restom and contributors
# For license information, please see license.txt

import json
from collections import defaultdict

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from pos_next.invoice_type import get_pos_invoice_doctype


def get_base_value(doc, fieldname, base_fieldname=None, conversion_rate=None):
	"""Return the value for a field in company currency."""

	base_fieldname = base_fieldname or f"base_{fieldname}"
	base_value = doc.get(base_fieldname)

	if base_value not in (None, ""):
		return flt(base_value)

	value = doc.get(fieldname)
	if value in (None, ""):
		return 0

	if conversion_rate is None:
		conversion_rate = (
			doc.get("conversion_rate")
			or doc.get("exchange_rate")
			or doc.get("target_exchange_rate")
			or doc.get("plc_conversion_rate")
			or 1
		)

	return flt(value) * flt(conversion_rate or 1)


class POSClosingShift(Document):
	def validate(self):
		existing = frappe.get_all(
			"POS Closing Shift",
			filters={
				"user": self.user,
				"docstatus": 1,
				"pos_opening_shift": self.pos_opening_shift,
				"name": ["!=", self.name],
			},
			fields=["name"],
			limit_page_length=1,
		)

		if existing:
			frappe.throw(
				_("A submitted POS Closing Shift ({0}) already exists for this opening shift").format(
					frappe.bold(existing[0].name)
				),
				title=_("Duplicate Closing Entry"),
			)

		if frappe.db.get_value("POS Opening Shift", self.pos_opening_shift, "status") != "Open":
			frappe.throw(
				_("Selected POS Opening Shift should be open."),
				title=_("Invalid Opening Entry"),
			)
		self.update_payment_reconciliation()

	def update_payment_reconciliation(self):
		# update the difference values in Payment Reconciliation child table
		# get default precision for site
		precision = frappe.get_cached_value("System Settings", None, "currency_precision") or 3
		for d in self.payment_reconciliation:
			d.difference = +flt(d.closing_amount, precision) - flt(d.expected_amount, precision)

	def on_submit(self):
		opening_entry = frappe.get_doc("POS Opening Shift", self.pos_opening_shift)
		opening_entry.pos_closing_shift = self.name
		opening_entry.set_status()
		self.delete_draft_invoices()
		opening_entry.save()
		# link invoices with this closing shift so ERPNext can block edits
		self._set_closing_entry_invoices()

	def on_cancel(self):
		if frappe.db.exists("POS Opening Shift", self.pos_opening_shift):
			opening_entry = frappe.get_doc("POS Opening Shift", self.pos_opening_shift)
			if opening_entry.pos_closing_shift == self.name:
				opening_entry.pos_closing_shift = ""
				opening_entry.set_status()
				opening_entry.save()
		# remove links from invoices so they can be cancelled
		self._clear_closing_entry_invoices()

	def _set_closing_entry_invoices(self):
		"""Set `pos_closing_entry` on linked invoices."""
		for d in self.pos_transactions:
			invoice = d.get("sales_invoice") or d.get("pos_invoice")
			if not invoice:
				continue
			doctype = "Sales Invoice" if d.get("sales_invoice") else "POS Invoice"
			if frappe.db.has_column(doctype, "pos_closing_entry"):
				frappe.db.set_value(doctype, invoice, "pos_closing_entry", self.name)

	def _clear_closing_entry_invoices(self):
		"""Clear closing shift links so the invoices can be cancelled again.

		POS Next's POS Invoices are never consolidated into Sales Invoices
		(they post their own GL/stock entries at submit), so there are no
		merge logs or consolidated invoices to tear down here.
		"""
		for d in self.pos_transactions:
			pos_invoice = d.get("pos_invoice")
			sales_invoice = d.get("sales_invoice")
			if pos_invoice:
				if frappe.db.has_column("POS Invoice", "pos_closing_entry"):
					frappe.db.set_value("POS Invoice", pos_invoice, "pos_closing_entry", None)
			if sales_invoice:
				if frappe.db.has_column("Sales Invoice", "pos_closing_entry"):
					frappe.db.set_value("Sales Invoice", sales_invoice, "pos_closing_entry", None)

	def delete_draft_invoices(self):
		if frappe.get_value("POS Profile", self.pos_profile, "posa_allow_delete"):
			doctype = get_pos_invoice_doctype()
			# posa_is_printed exists only on Sales Invoice (see
			# submit_printed_invoices) — POS Invoice drafts are always deletable
			printed_cond = "posa_is_printed = 0 and " if doctype == "Sales Invoice" else ""
			data = frappe.db.sql(
				f"""
		select
		    name
		from
		    `tab{doctype}`
		where
		    docstatus = 0 and {printed_cond}posa_pos_opening_shift = %s
		""",
				(self.pos_opening_shift),
				as_dict=1,
			)

			for invoice in data:
				frappe.delete_doc(doctype, invoice.name, force=1)

	@frappe.whitelist()
	def get_payment_reconciliation_details(self):
		company_currency = frappe.get_cached_value("Company", self.company, "default_currency")

		sales_breakdown = defaultdict(float)
		net_breakdown = defaultdict(float)
		payment_breakdown = {}

		def update_payment_breakdown(mode_of_payment, base_amount=0, currency=None, amount=0):
			if not mode_of_payment:
				return

			row = payment_breakdown.setdefault(
				mode_of_payment,
				{"base": 0.0, "currencies": defaultdict(float)},
			)
			row["base"] += flt(base_amount)
			if currency:
				row["currencies"][currency] += flt(amount)

		cash_mode_of_payment = (
			frappe.db.get_value("POS Profile", self.pos_profile, "posa_cash_mode_of_payment") or "Cash"
		)

		for row in self.get("pos_transactions", []):
			invoice = row.get("sales_invoice") or row.get("pos_invoice")
			if not invoice:
				continue

			doctype = "Sales Invoice" if row.get("sales_invoice") else "POS Invoice"
			if not frappe.db.exists(doctype, invoice):
				continue

			invoice_doc = frappe.get_cached_doc(doctype, invoice)
			currency = invoice_doc.get("currency") or company_currency
			conversion_rate = (
				invoice_doc.get("conversion_rate")
				or invoice_doc.get("exchange_rate")
				or invoice_doc.get("target_exchange_rate")
				or invoice_doc.get("plc_conversion_rate")
				or 1
			)

			sales_breakdown[currency] += flt(invoice_doc.get("grand_total") or 0)
			net_breakdown[currency] += flt(invoice_doc.get("net_total") or 0)

			for payment in invoice_doc.get("payments", []):
				update_payment_breakdown(
					payment.mode_of_payment,
					get_base_value(payment, "amount", "base_amount", conversion_rate),
					currency,
					payment.amount,
				)

			change_amount = invoice_doc.get("change_amount") or 0
			if change_amount:
				update_payment_breakdown(
					cash_mode_of_payment,
					-get_base_value(
						invoice_doc,
						"change_amount",
						"base_change_amount",
						conversion_rate,
					),
					currency,
					-change_amount,
				)

		for row in self.get("pos_payments", []):
			payment_entry = row.get("payment_entry")
			if not payment_entry or not frappe.db.exists("Payment Entry", payment_entry):
				continue

			payment_doc = frappe.get_cached_doc("Payment Entry", payment_entry)
			currency = (
				payment_doc.get("paid_from_account_currency")
				or payment_doc.get("paid_to_account_currency")
				or payment_doc.get("party_account_currency")
				or payment_doc.get("currency")
				or company_currency
			)
			base_amount = flt(payment_doc.get("base_paid_amount") or 0)
			paid_amount = flt(payment_doc.get("paid_amount") or 0)
			mode_of_payment = row.get("mode_of_payment") or payment_doc.get("mode_of_payment")

			update_payment_breakdown(mode_of_payment, base_amount, currency, paid_amount)

		mode_summaries = []
		payment_breakdown_copy = payment_breakdown.copy()
		for detail in self.get("payment_reconciliation", []):
			mop = detail.mode_of_payment
			breakdown = payment_breakdown_copy.pop(mop, None)
			currencies = []
			if breakdown:
				currencies = [
					frappe._dict({"currency": currency, "amount": amount})
					for currency, amount in sorted(breakdown["currencies"].items())
					if amount
				]

			base_total = flt(detail.expected_amount) - flt(detail.opening_amount)

			mode_summaries.append(
				frappe._dict(
					{
						"mode_of_payment": mop,
						"base_amount": base_total,
						"opening_amount": flt(detail.opening_amount),
						"expected_amount": flt(detail.expected_amount),
						"difference": flt(detail.difference),
						"currency_breakdown": currencies,
					}
				)
			)

		for mop, breakdown in payment_breakdown_copy.items():
			mode_summaries.append(
				frappe._dict(
					{
						"mode_of_payment": mop,
						"base_amount": breakdown["base"],
						"opening_amount": 0,
						"expected_amount": breakdown["base"],
						"difference": 0,
						"currency_breakdown": [
							frappe._dict({"currency": currency, "amount": amount})
							for currency, amount in sorted(breakdown["currencies"].items())
							if amount
						],
					}
				)
			)

		sales_currency_breakdown = [
			frappe._dict({"currency": currency, "amount": amount})
			for currency, amount in sorted(sales_breakdown.items())
			if amount
		]
		net_currency_breakdown = [
			frappe._dict({"currency": currency, "amount": amount})
			for currency, amount in sorted(net_breakdown.items())
			if amount
		]

		return frappe.render_template(
			"pos_next/pos_next/doctype/pos_closing_shift/closing_shift_details.html",
			{
				"data": self,
				"currency": company_currency,
				"company_currency": company_currency,
				"mode_summaries": mode_summaries,
				"sales_currency_breakdown": sales_currency_breakdown,
				"net_currency_breakdown": net_currency_breakdown,
			},
		)


@frappe.whitelist()
def get_cashiers(doctype, txt, searchfield, start, page_len, filters):
	cashiers_list = frappe.get_all("POS Profile User", filters=filters, fields=["user"])
	result = []
	for cashier in cashiers_list:
		user_email = frappe.get_value("User", cashier.user, "email")
		if user_email:
			# Return list of tuples in format (value, label) where value is user ID and label shows both ID and email
			result.append([cashier.user, f"{cashier.user} ({user_email})"])
	return result


@frappe.whitelist()
def get_pos_invoices(pos_opening_shift, doctype=None):
	"""Every submitted invoice on the shift, across BOTH invoice doctypes.

	A shift can legitimately hold both: the site-wide invoice type may be
	switched mid-shift (the switch is blocked while a shift is open, but a
	shift reopened on an older doctype, or rows created before the switch,
	still link here). Reading only the current mode's doctype would silently
	drop the other half from every closing total, so both are always read.
	"""
	# whitelist gate: client input is interpolated into the SQL table name
	if doctype in ("Sales Invoice", "POS Invoice"):
		doctypes = [doctype]
	else:
		doctypes = ["POS Invoice", "Sales Invoice"]

	data = []
	for dt in doctypes:
		submit_printed_invoices(pos_opening_shift, dt)
		# A consolidated POS Invoice (legacy data only — parity rows are never
		# consolidated) already had its books posted to the Sales Invoice that
		# absorbed it, so counting it here would double it.
		cond = " and ifnull(consolidated_invoice,'') = ''" if dt == "POS Invoice" else ""
		rows = frappe.db.sql(
			f"""
		select
			name
		from
			`tab{dt}`
		where
			docstatus = 1 and posa_pos_opening_shift = %s{cond}
		""",
			(pos_opening_shift),
			as_dict=1,
		)
		data.extend(frappe.get_doc(dt, d.name).as_dict() for d in rows)

	return data


@frappe.whitelist()
def get_payments_entries(pos_opening_shift):
	return frappe.get_all(
		"Payment Entry",
		filters={
			"docstatus": 1,
			"reference_no": pos_opening_shift,
			"payment_type": "Receive",
		},
		fields=[
			"name",
			"mode_of_payment",
			"paid_amount",
			"base_paid_amount",
			"target_exchange_rate",
			"reference_no",
			"posting_date",
			"party",
		],
	)


def _get_cash_mode_of_payment(pos_profile):
	"""Get the cash mode of payment for a POS profile."""
	cash_mode = frappe.get_value("POS Profile", pos_profile, "posa_cash_mode_of_payment")
	return cash_mode or "Cash"


def _aggregate_payment(payments, mode_of_payment, amount, opening_amount=0):
	"""Add or update payment amount for a mode of payment."""
	for pay in payments:
		if pay.mode_of_payment == mode_of_payment:
			pay.expected_amount += flt(amount)
			return
	payments.append(
		frappe._dict(
			{
				"mode_of_payment": mode_of_payment,
				"opening_amount": opening_amount,
				"expected_amount": flt(amount) + opening_amount,
			}
		)
	)


def _aggregate_tax(taxes, account_head, rate, amount):
	"""Add or update tax amount for an account."""
	for tax in taxes:
		if tax.account_head == account_head and tax.rate == rate:
			tax.amount += amount
			return
	taxes.append(
		frappe._dict(
			{
				"account_head": account_head,
				"rate": rate,
				"amount": amount,
			}
		)
	)


def _process_invoice(invoice, invoice_field, company_currency, cash_mode, payments, taxes, summary):
	"""Process a single invoice and update aggregates."""
	conversion_rate = invoice.get("conversion_rate")
	is_return = invoice.get("is_return", 0)

	base_grand_total = get_base_value(invoice, "grand_total", "base_grand_total", conversion_rate)
	base_net_total = get_base_value(invoice, "net_total", "base_net_total", conversion_rate)

	# Credit returns with no payment rows were added to customer credit —
	# no money entered or left the drawer.  Skip entirely.
	if is_return and not invoice.payments:
		return frappe._dict(
			{
				invoice_field: invoice.name,
				"posting_date": invoice.posting_date,
				"grand_total": 0,
				"transaction_currency": invoice.get("currency") or company_currency,
				"transaction_amount": flt(invoice.get("grand_total")),
				"customer": invoice.customer,
				"is_return": is_return,
				"return_against": invoice.get("return_against"),
			}
		)

	# Build transaction record
	transaction = frappe._dict(
		{
			invoice_field: invoice.name,
			"posting_date": invoice.posting_date,
			"grand_total": base_grand_total,
			"transaction_currency": invoice.get("currency") or company_currency,
			"transaction_amount": flt(invoice.get("grand_total")),
			"customer": invoice.customer,
			"is_return": is_return,
			"return_against": invoice.get("return_against") if is_return else None,
		}
	)

	# Update summary totals
	summary["grand_total"] += base_grand_total
	summary["net_total"] += base_net_total
	summary["total_quantity"] += flt(invoice.total_qty)

	if is_return:
		summary["returns_total"] += abs(base_grand_total)
		summary["returns_count"] += 1
	else:
		summary["sales_total"] += base_grand_total
		summary["sales_count"] += 1

	# Process taxes
	for t in invoice.taxes:
		tax_amount = get_base_value(t, "tax_amount", "base_tax_amount", conversion_rate)
		_aggregate_tax(taxes, t.account_head, t.rate, tax_amount)

	# Process payments
	#
	# Cross-branch return safety net (Layer 3):
	# Return invoices may carry foreign payment modes from the original
	# invoice's POS profile.  Remap unknown modes to the cash mode so the
	# reconciliation table stays clean.
	known_modes = {pay.mode_of_payment for pay in payments}

	# Aggregate each payment row's amount into the reconciliation buckets.
	for p in invoice.payments:
		amount = get_base_value(p, "amount", "base_amount", conversion_rate)
		mode = p.mode_of_payment

		if is_return and mode not in known_modes:
			mode = cash_mode

		_aggregate_payment(payments, mode, amount)

	# Subtract change_amount once from the cash mode.  change_amount is an
	# invoice-level field — the customer overpaid and received change back,
	# so the drawer's net gain is (sum of cash rows - change).  Handling it
	# outside the loop avoids double-subtraction when multiple payment rows
	# share the same cash mode.
	base_change = get_base_value(invoice, "change_amount", "base_change_amount", conversion_rate)
	if base_change:
		_aggregate_payment(payments, cash_mode, -base_change)

	return transaction


@frappe.whitelist()
def make_closing_shift_from_opening(opening_shift):
	opening_shift = json.loads(opening_shift)

	opening_shift_name = opening_shift.get("name")
	if not opening_shift_name:
		frappe.throw(_("POS Opening Shift is required"), frappe.MandatoryError)

	# read-level gate, same ownership rule as submit_closing_shift: only the
	# shift owner (or a user with closing-shift read access) may derive the
	# closing data of a shift
	if (
		frappe.db.get_value("POS Opening Shift", opening_shift_name, "user") != frappe.session.user
		and not frappe.has_permission("POS Closing Shift", "read")
	):
		frappe.throw(_("You can only view your own closing shift"), frappe.PermissionError)

	# Initialize closing shift document
	closing_shift = frappe.new_doc("POS Closing Shift")
	closing_shift.update(
		{
			"pos_opening_shift": opening_shift.get("name"),
			"period_start_date": opening_shift.get("period_start_date"),
			"period_end_date": frappe.utils.get_datetime(),
			"pos_profile": opening_shift.get("pos_profile"),
			"user": opening_shift.get("user"),
			"company": opening_shift.get("company"),
		}
	)

	company_currency = frappe.get_cached_value("Company", closing_shift.company, "default_currency")
	cash_mode = _get_cash_mode_of_payment(opening_shift.get("pos_profile"))

	# Initialize collections
	payments = []
	taxes = []
	pos_transactions = []

	# Summary for tracking totals
	summary = {
		"grand_total": 0,
		"net_total": 0,
		"total_quantity": 0,
		"returns_total": 0,
		"returns_count": 0,
		"sales_total": 0,
		"sales_count": 0,
	}

	# Add opening balances to payments
	for detail in opening_shift.get("balance_details", []):
		opening_amount = flt(detail.get("amount"))
		payments.append(
			frappe._dict(
				{
					"mode_of_payment": detail.get("mode_of_payment"),
					"opening_amount": opening_amount,
					"expected_amount": opening_amount,
				}
			)
		)

	# Process invoices. Each row is written to the column matching ITS OWN
	# doctype: a shift can hold both kinds, and a Sales Invoice name stored in
	# the pos_invoice column would point the closing entry at the wrong table.
	invoices = get_pos_invoices(opening_shift.get("name"))
	for invoice in invoices:
		invoice_field = (
			"pos_invoice" if invoice.get("doctype") == "POS Invoice" else "sales_invoice"
		)
		txn = _process_invoice(invoice, invoice_field, company_currency, cash_mode, payments, taxes, summary)
		pos_transactions.append(txn)

	# Process payment entries
	pos_payments_table = []
	for py in get_payments_entries(opening_shift.get("name")):
		pos_payments_table.append(
			frappe._dict(
				{
					"payment_entry": py.name,
					"mode_of_payment": py.mode_of_payment,
					"paid_amount": py.paid_amount,
					"posting_date": py.posting_date,
					"customer": py.party,
				}
			)
		)
		amount = get_base_value(py, "paid_amount", "base_paid_amount")
		_aggregate_payment(payments, py.mode_of_payment, amount)

	# Update closing shift with totals
	closing_shift.grand_total = summary["grand_total"]
	closing_shift.net_total = summary["net_total"]
	closing_shift.total_quantity = summary["total_quantity"]

	# Set child tables (without return info - that's for display only)
	closing_shift.set(
		"pos_transactions",
		[
			{k: v for k, v in txn.items() if k not in ("is_return", "return_against")}
			for txn in pos_transactions
		],
	)
	closing_shift.set("payment_reconciliation", payments)
	closing_shift.set("taxes", taxes)
	closing_shift.set("pos_payments", pos_payments_table)

	# Build response with display-only fields
	result = closing_shift.as_dict()
	result.update(
		{
			"returns_total": summary["returns_total"],
			"returns_count": summary["returns_count"],
			"sales_total": summary["sales_total"],
			"sales_count": summary["sales_count"],
			"pos_transactions": pos_transactions,  # Include return info for display
		}
	)

	return result


@frappe.whitelist()
def submit_closing_shift(closing_shift):
	closing_shift = json.loads(closing_shift)

	opening_shift_name = closing_shift.get("pos_opening_shift")
	if not opening_shift_name:
		frappe.throw(_("POS Opening Shift is required"), frappe.MandatoryError)

	opening_shift = frappe.get_doc("POS Opening Shift", opening_shift_name)

	# The payload is untrusted client JSON: only the shift owner (or a user
	# with closing-shift submit rights) may close a shift at all.
	if (
		opening_shift.user != frappe.session.user
		and not frappe.has_permission("POS Closing Shift", "submit")
	):
		frappe.throw(_("You can only close your own shift"), frappe.PermissionError)

	# The server holds the numbers: rebuild the closing shift from the opening
	# shift's real transactions so expected_amount, totals and taxes can never
	# be forged. The client only contributes the physically counted
	# closing_amount per mode of payment (rows for unknown modes are dropped).
	server_data = make_closing_shift_from_opening(json.dumps(opening_shift.as_dict(), default=str))
	counted = {
		row.get("mode_of_payment"): row.get("closing_amount")
		for row in (closing_shift.get("payment_reconciliation") or [])
		if row.get("mode_of_payment")
	}
	for row in server_data.get("payment_reconciliation") or []:
		mode = row.get("mode_of_payment")
		if mode in counted:
			row["closing_amount"] = counted[mode]

	closing_shift_doc = frappe.get_doc(server_data)
	# kept only AFTER the explicit ownership gate above — the cashier must
	# always be able to close their own shift regardless of role setup
	closing_shift_doc.flags.ignore_permissions = True
	closing_shift_doc.save()
	closing_shift_doc.submit()
	return closing_shift_doc.name


def submit_printed_invoices(pos_opening_shift, doctype):
	if doctype == "POS Invoice":
		# posa_is_printed exists only on Sales Invoice — POS Invoices carry no
		# printed-draft state. Returning early also guarantees we never submit
		# unprinted drafts: without the posa_is_printed filter below,
		# frappe.get_all would match EVERY draft on the shift.
		return
	invoices_list = frappe.get_all(
		doctype,
		filters={
			"posa_pos_opening_shift": pos_opening_shift,
			"docstatus": 0,
			"posa_is_printed": 1,
		},
	)
	for invoice in invoices_list:
		invoice_doc = frappe.get_doc(doctype, invoice.name)
		invoice_doc.submit()
