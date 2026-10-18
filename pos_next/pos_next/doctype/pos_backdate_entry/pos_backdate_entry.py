# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, flt, get_datetime, now_datetime, nowtime

from pos_next.api.backdate_invoices import (
	_check_backdate_access,
	_get_return_items,
	_reopen,
	_submit_backdated,
	allow_change_posting_date,
	has_backdate_role,
)
from pos_next.invoice_type import get_pos_invoice_doctype
from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import _submit_closing


class POSBackdateEntry(Document):
	def validate(self):
		self._validate_shift()
		self._validate_access()
		self._validate_structure()
		self._validate_dates()
		self._validate_invoices()

	def _validate_access(self):
		if not has_backdate_role():
			frappe.throw(_("Not permitted to enter backdated invoices"), frappe.PermissionError)
		if not allow_change_posting_date(self.pos_profile):
			frappe.throw(
				_("Backdate entry is disabled for POS Profile {0}").format(self.pos_profile),
				title=_("Backdate Entry"),
			)

	def _validate_shift(self):
		if not self.pos_opening_shift:
			frappe.throw(_("POS Opening Shift is required"))
		shift = frappe.db.get_value(
			"POS Opening Shift",
			self.pos_opening_shift,
			("docstatus", "status", "pos_closing_shift", "pos_profile", "company", "period_start_date", "posting_date"),
			as_dict=True,
		)
		if not shift:
			frappe.throw(_("POS Opening Shift {0} does not exist").format(self.pos_opening_shift))
		if shift.docstatus != 1 or shift.status != "Closed" or not shift.pos_closing_shift:
			frappe.throw(_("Shift {0} is not closed").format(self.pos_opening_shift), title=_("Backdate Entry"))
		if frappe.db.get_value("POS Closing Shift", shift.pos_closing_shift, "docstatus") != 1:
			frappe.throw(
				_("POS Closing Shift {0} is not submitted").format(shift.pos_closing_shift),
				title=_("Backdate Entry"),
			)
		if self.company and self.company != shift.company:
			frappe.throw(
				_("Company {0} does not match shift company {1}").format(self.company, shift.company),
				title=_("Backdate Entry"),
			)
		self.company = shift.company
		if self.pos_profile and self.pos_profile != shift.pos_profile:
			frappe.throw(
				_("POS Profile {0} does not match shift profile {1}").format(self.pos_profile, shift.pos_profile),
				title=_("Backdate Entry"),
			)
		self.pos_profile = shift.pos_profile

	def _validate_structure(self):
		# items/payments invoice_no is a Select (string); invoices keep Int, so compare as ints
		for row in [*self.items, *(self.payments or [])]:
			row.invoice_no = cint(row.invoice_no)

		# Return rows are prefilled with every original line at qty 0; unreturned lines are dropped.
		return_nos = {inv.invoice_no for inv in self.invoices if inv.entry_type == "Return"}
		self.items = [
			item for item in self.items if not (item.invoice_no in return_nos and flt(item.qty) == 0)
		]
		for idx, item in enumerate(self.items, 1):
			item.idx = idx

		if not self.invoices:
			frappe.throw(_("At least one invoice is required"), title=_("Backdate Entry"))
		if not self.items:
			frappe.throw(_("At least one item is required"), title=_("Backdate Entry"))

		inv_numbers = [inv.invoice_no for inv in self.invoices]
		if len(inv_numbers) != len(set(inv_numbers)):
			frappe.throw(_("Duplicate invoice number found in Invoices table"), title=_("Backdate Entry"))

		for inv in self.invoices:
			if not inv.invoice_no or cint(inv.invoice_no) <= 0:
				frappe.throw(_("Invoice No. is required and must be greater than zero"), title=_("Backdate Entry"))

		# one Return or Correction per shift invoice: both against the same original would double-count it
		against_used = {}
		for inv in self.invoices:
			if not inv.against_invoice:
				continue
			if inv.against_invoice in against_used:
				frappe.throw(
					_("Invoice No. {0}: Invoice {1} is already used by Invoice No. {2}").format(
						inv.invoice_no, inv.against_invoice, against_used[inv.against_invoice]
					),
					title=_("Backdate Entry"),
				)
			against_used[inv.against_invoice] = inv.invoice_no

		inv_numbers_set = set(inv_numbers)
		item_inv_numbers = {item.invoice_no for item in self.items}

		# Every invoice must have >=1 item
		for inv in self.invoices:
			if inv.invoice_no not in item_inv_numbers:
				frappe.throw(
					_("Invoice No. {0}: Must have at least one item").format(inv.invoice_no),
					title=_("Backdate Entry"),
				)

		# Every item must belong to an invoice
		for item in self.items:
			if item.invoice_no not in inv_numbers_set:
				frappe.throw(
					_("Item row references unknown Invoice No. {0}").format(item.invoice_no),
					title=_("Backdate Entry"),
				)
			if flt(item.qty) <= 0:
				frappe.throw(
					_("Invoice No. {0}: Quantity must be greater than zero in the grid").format(item.invoice_no),
					title=_("Backdate Entry"),
				)
			disc = flt(item.discount_percentage) or 0
			item.amount = flt(flt(item.qty) * flt(item.rate) * (1 - disc / 100), 2)

		# Payments must belong to an invoice
		for pay in self.payments or []:
			if pay.invoice_no not in inv_numbers_set:
				frappe.throw(
					_("Payment row references unknown Invoice No. {0}").format(pay.invoice_no),
					title=_("Backdate Entry"),
				)

		# Compute grand_total per invoice
		totals = {}
		for item in self.items:
			totals[item.invoice_no] = totals.get(item.invoice_no, 0.0) + flt(item.amount)
		for inv in self.invoices:
			inv.grand_total = flt(totals.get(inv.invoice_no, 0.0), 2)

		# Validate payments sum equals grand_total per invoice
		pay_totals = {}
		for pay in self.payments or []:
			pay_totals[pay.invoice_no] = pay_totals.get(pay.invoice_no, 0.0) + flt(pay.amount)

		for inv in self.invoices:
			p_total = flt(pay_totals.get(inv.invoice_no, 0.0), 2)
			g_total = flt(inv.grand_total, 2)
			if inv.entry_type == "Return":
				# Returns may have negative payment sum (-total) or positive (total)
				if abs(p_total - (-g_total)) > 0.01 and abs(p_total - g_total) > 0.01:
					frappe.throw(
						_("Invoice No. {0}: Total payments ({1}) does not match invoice total ({2})").format(
							inv.invoice_no, p_total, -g_total
						),
						title=_("Backdate Entry"),
					)
			else:
				if abs(p_total - g_total) > 0.01:
					frappe.throw(
						_("Invoice No. {0}: Total payments ({1}) does not match invoice total ({2})").format(
							inv.invoice_no, p_total, g_total
						),
						title=_("Backdate Entry"),
					)

	def _validate_dates(self):
		shift = frappe.db.get_value(
			"POS Opening Shift",
			self.pos_opening_shift,
			("period_start_date", "posting_date"),
			as_dict=True,
		)
		shift_start_dt = get_datetime(shift.period_start_date or shift.posting_date)
		now_dt = add_to_date(now_datetime(), minutes=1)

		for inv in self.invoices:
			ptime = inv.posting_time or nowtime()
			try:
				pdatetime = get_datetime(f"{inv.posting_date} {ptime}")
			except Exception:
				pdatetime = None

			if not pdatetime or pdatetime < shift_start_dt or pdatetime > now_dt:
				frappe.throw(
					_("Invoice No. {0}: Posting date {1} must fall within the shift period ({2} to today)").format(
						inv.invoice_no, pdatetime or inv.posting_date, shift_start_dt
					),
					title=_("Backdate Entry"),
				)

		closing_time = self.closing_time or nowtime()
		try:
			closing_dt = get_datetime(f"{self.closing_date} {closing_time}")
		except Exception:
			closing_dt = None

		max_posting_dt = max(
			get_datetime(f"{inv.posting_date} {inv.posting_time or nowtime()}")
			for inv in self.invoices
		)

		if not closing_dt or closing_dt < max_posting_dt or closing_dt > now_dt:
			frappe.throw(
				_("Closing date/time {0} must be on or after max invoice posting date/time {1} and cannot be in the future").format(
					closing_dt, max_posting_dt
				),
				title=_("Backdate Entry"),
			)

	def _validate_invoices(self):
		for inv in self.invoices:
			if inv.entry_type in ("Return", "Correction"):
				if not inv.against_invoice:
					frappe.throw(
						_("Invoice No. {0}: Against Invoice is required for {1}").format(
							inv.invoice_no, inv.entry_type
						),
						title=_("Backdate Entry"),
					)
				if not inv.against_doctype:
					if frappe.db.exists("POS Invoice", inv.against_invoice):
						inv.against_doctype = "POS Invoice"
					elif frappe.db.exists("Sales Invoice", inv.against_invoice):
						inv.against_doctype = "Sales Invoice"
					else:
						frappe.throw(
							_("Invoice No. {0}: Against invoice {1} does not exist").format(
								inv.invoice_no, inv.against_invoice
							),
							title=_("Backdate Entry"),
						)

				against_info = frappe.db.get_value(
					inv.against_doctype,
					inv.against_invoice,
					("docstatus", "is_return", "posa_pos_opening_shift", "amended_from"),
					as_dict=True,
				)
				if not against_info or against_info.docstatus != 1:
					frappe.throw(
						_("Invoice No. {0}: Against invoice {1} must be submitted").format(
							inv.invoice_no, inv.against_invoice
						),
						title=_("Backdate Entry"),
					)
				if against_info.posa_pos_opening_shift != self.pos_opening_shift:
					frappe.throw(
						_("Invoice No. {0}: Against invoice {1} does not belong to shift {2}").format(
							inv.invoice_no, inv.against_invoice, self.pos_opening_shift
						),
						title=_("Backdate Entry"),
					)
				if against_info.is_return:
					action_verb = "correct" if inv.entry_type == "Correction" else "return"
					frappe.throw(
						_("Invoice No. {0}: Cannot {1} a return invoice {2}. Use Return + new Sale instead.").format(
							inv.invoice_no, action_verb, inv.against_invoice
						),
						title=_("Backdate Entry"),
					)

				if inv.entry_type == "Correction":
					# R10: Corrections blocked when unsafe
					if against_info.amended_from:
						frappe.throw(
							_("Invoice No. {0}: Invoice {1} has already been amended.").format(
								inv.invoice_no, inv.against_invoice
							),
							title=_("Backdate Entry"),
						)
					already_amended = (
						frappe.db.exists("POS Invoice", {"amended_from": inv.against_invoice})
						or frappe.db.exists("Sales Invoice", {"amended_from": inv.against_invoice})
					)
					if already_amended:
						frappe.throw(
							_("Invoice No. {0}: Invoice {1} has already been corrected.").format(
								inv.invoice_no, inv.against_invoice
							),
							title=_("Backdate Entry"),
						)

					has_return = (
						frappe.db.exists("POS Invoice", {"return_against": inv.against_invoice, "docstatus": 1})
						or frappe.db.exists("Sales Invoice", {"return_against": inv.against_invoice, "docstatus": 1})
					)
					if has_return:
						frappe.throw(
							_("Invoice No. {0}: Cannot correct invoice {1} because a submitted return exists against it. Use Return + new Sale instead.").format(
								inv.invoice_no, inv.against_invoice
							),
							title=_("Backdate Entry"),
						)

					has_payment_entry = bool(
						frappe.db.sql(
							"""
							SELECT pe.name
							FROM `tabPayment Entry` pe
							INNER JOIN `tabPayment Entry Reference` per ON per.parent = pe.name
							WHERE per.reference_doctype = %s
								AND per.reference_name = %s
								AND pe.docstatus = 1
							LIMIT 1
							""",
							(inv.against_doctype, inv.against_invoice),
						)
					)
					has_wallet = False
					if frappe.db.table_exists("Wallet Transaction"):
						has_wallet = bool(
							frappe.db.exists(
								"Wallet Transaction",
								{
									"reference_doctype": inv.against_doctype,
									"reference_name": inv.against_invoice,
									"docstatus": 1,
								},
							)
						)
					if has_payment_entry or has_wallet:
						frappe.throw(
							_("Invoice No. {0}: Cannot correct credit invoice {1} with linked Payment Entries. Use Return + new Sale instead.").format(
								inv.invoice_no, inv.against_invoice
							),
							title=_("Backdate Entry"),
						)

				elif inv.entry_type == "Return":
					# Return quantity validation against remaining qty
					return_items = _get_return_items(inv.against_invoice)
					remaining_map = {}
					for r_item in return_items:
						ref_key = r_item.get("against_item_row") or r_item.get("item_code")
						remaining_map[ref_key] = flt(r_item.get("remaining_qty"))

					inv_items = [it for it in self.items if it.invoice_no == inv.invoice_no]
					for item in inv_items:
						ref_key = item.against_item_row or item.item_code
						rem = remaining_map.get(ref_key, 0.0)
						if flt(item.qty) > rem:
							frappe.throw(
								_("Invoice No. {0}: Return qty {1} exceeds remaining qty {2} for item {3}").format(
									inv.invoice_no, item.qty, rem, item.item_code
								),
								title=_("Backdate Entry"),
							)

	def before_cancel(self):
		frappe.throw(
			_("POS Backdate Entry cannot be cancelled. Record a new backdate entry instead."),
			frappe.ValidationError,
			title=_("Backdate Entry"),
		)

	def on_submit(self):
		prev_flag = getattr(frappe.flags, "pos_next_backdate_entry", None)
		frappe.flags.pos_next_backdate_entry = True
		save_pt = "pos_bde_submit"
		frappe.db.savepoint(save_pt)
		try:
			self._execute_submit()
			frappe.db.release_savepoint(save_pt)
		except Exception:
			frappe.db.rollback(save_point=save_pt)
			frappe.db.set_value(self.doctype, self.name, "docstatus", 0, update_modified=False)
			self.docstatus = 0
			raise
		finally:
			frappe.flags.pos_next_backdate_entry = prev_flag

	def _execute_submit(self):
		# 1. Lock POS Opening Shift row
		frappe.db.sql(
			"SELECT name FROM `tabPOS Opening Shift` WHERE name = %s FOR UPDATE",
			self.pos_opening_shift,
		)
		shift = frappe.db.get_value(
			"POS Opening Shift",
			self.pos_opening_shift,
			("docstatus", "status", "pos_closing_shift"),
			as_dict=True,
		)
		if not shift or shift.docstatus != 1 or shift.status != "Closed" or not shift.pos_closing_shift:
			frappe.throw(_("Shift {0} is not closed").format(self.pos_opening_shift), title=_("Backdate Entry"))

		# 2. Cancel submitted closing shift
		reopen_res = _reopen(self.pos_opening_shift)
		self.cancelled_closing_shift = reopen_res["cancelled_closing_shift"]
		self.db_set("cancelled_closing_shift", self.cancelled_closing_shift)

		# 3. Submit backdated invoices in order of invoice_no
		site_doctype = get_pos_invoice_doctype()
		sorted_invoices = sorted(self.invoices, key=lambda x: x.invoice_no)

		for inv in sorted_invoices:
			inv_doctype = site_doctype
			items_for_inv = [it for it in self.items if it.invoice_no == inv.invoice_no]
			payments_for_inv = [p for p in (self.payments or []) if p.invoice_no == inv.invoice_no]

			# If Correction: cancel original invoice first
			if inv.entry_type == "Correction":
				orig_doc = frappe.get_doc(inv.against_doctype, inv.against_invoice)
				orig_doc.flags.ignore_permissions = True
				orig_doc.cancel()

			# Build payload
			payload = {
				"pos_profile": self.pos_profile,
				"posa_pos_opening_shift": self.pos_opening_shift,
				"customer": inv.customer,
				"posting_date": str(inv.posting_date),
				"posting_time": str(inv.posting_time),
				"company": self.company,
				"items": [],
				"payments": [],
			}

			if inv.entry_type == "Return":
				payload["is_return"] = 1
				payload["return_against"] = inv.against_invoice
				payload["update_outstanding_for_self"] = 0
				orig = frappe.get_doc(inv.against_doctype, inv.against_invoice)
				if orig.get("sales_team"):
					payload["sales_team"] = [row.as_dict() for row in orig.sales_team]
				if inv.confirmation_code:
					payload["refund_confirmation_code"] = inv.confirmation_code
			else:
				if inv.confirmation_code:
					payload["discount_confirmation_code"] = inv.confirmation_code

			for it in items_for_inv:
				qty = -abs(flt(it.qty)) if inv.entry_type == "Return" else flt(it.qty)
				disc = flt(it.discount_percentage) or 0
				eff_rate = flt(it.rate) * (1 - disc / 100) if disc else flt(it.rate)
				item_dict = {
					"item_code": it.item_code,
					"qty": qty,
					"rate": eff_rate,
					"price_list_rate": flt(it.rate),
					# package rows are filled client-side without uom; fall back to stock uom
					"uom": it.uom or frappe.get_cached_value("Item", it.item_code, "stock_uom"),
					"conversion_factor": flt(it.conversion_factor) or 1,
					"discount_percentage": disc,
				}
				if it.warehouse:
					item_dict["warehouse"] = it.warehouse
				if inv.entry_type == "Return":
					if inv.against_doctype == "Sales Invoice":
						item_dict["sales_invoice_item"] = it.against_item_row
					else:
						item_dict["pos_invoice_item"] = it.against_item_row
				if it.pos_package:
					item_dict["pos_package"] = it.pos_package
					item_dict["pos_package_instance"] = it.pos_package_instance
					item_dict["pos_package_role"] = it.pos_package_role
					item_dict["pos_package_snapshot"] = it.pos_package_snapshot
				payload["items"].append(item_dict)

			for pay in payments_for_inv:
				p_amt = -abs(flt(pay.amount)) if inv.entry_type == "Return" else flt(pay.amount)
				payload["payments"].append({
					"mode_of_payment": pay.mode_of_payment,
					"amount": p_amt,
				})

			try:
				res = _submit_backdated(payload)
			except Exception as e:
				msg = str(e)
				if not msg.startswith(f"Invoice No. {inv.invoice_no}:"):
					msg = f"Invoice No. {inv.invoice_no}: {msg}"
				frappe.throw(msg, exc=type(e) if isinstance(e, frappe.ValidationError) else frappe.ValidationError)

			created_name = res.get("name") if isinstance(res, dict) else getattr(res, "name", None)
			inv.created_invoice = created_name
			inv.invoice_doctype = inv_doctype
			frappe.db.set_value(
				"POS Backdate Entry Invoice",
				inv.name,
				{"created_invoice": created_name, "invoice_doctype": inv_doctype},
			)

			if inv.entry_type == "Correction":
				frappe.db.set_value(inv_doctype, created_name, "amended_from", inv.against_invoice)
				frappe.get_doc(
					{
						"doctype": "Comment",
						"comment_type": "Info",
						"reference_doctype": inv.against_doctype,
						"reference_name": inv.against_invoice,
						"content": _("Corrected by {0} via {1}").format(created_name, self.name),
					}
				).insert(ignore_permissions=True)

		# 4. Re-close shift
		closing_dt = f"{self.closing_date} {self.closing_time or nowtime()}"
		counted = {
			row.mode_of_payment: row.closing_amount
			for row in (self.closing_payments or [])
			if row.mode_of_payment
		}
		new_closing = _submit_closing(
			self.pos_opening_shift,
			counted=counted,
			period_end_date=closing_dt,
			posting_date=str(self.closing_date),
		)
		self.new_closing_shift = new_closing
		self.db_set("new_closing_shift", new_closing)
