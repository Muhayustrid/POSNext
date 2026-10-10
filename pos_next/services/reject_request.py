# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""POS Reject Request lifecycle: submit (Unsold), validation, deadline, limit,
stock transfer to the reject warehouse and notifications.

Design: docs/superpowers/specs/2026-10-11-pos-reject-request-design.md
"""

import math
from collections import defaultdict

import frappe
from frappe import _
from frappe.desk.doctype.notification_log.notification_log import make_notification_logs
from frappe.utils import add_days, flt, get_first_day, get_last_day, get_url_to_form, getdate, today

from pos_next.api.settings_resolver import get_effective_pos_setting
from pos_next.services.production import _pick_batch, _resolve_profile, _stock_qty

# Canonical English, never translated: cancel matches on it.
TRANSFER_REMARK_PREFIX = "POS Reject transfer: "
APPROVER_ROLE = "POSNext Reject Approver"
MAX_EVIDENCE = 5


def validate_request(doc):
	doc.company, warehouse = _resolve_profile(doc.pos_profile)
	_price_items(doc, warehouse)

	incident = getdate(doc.incident_date)
	if incident > getdate(today()):
		frappe.throw(_("Incident date cannot be in the future"))

	settings = frappe.get_doc("POS Reject Settings")
	days = settings.deadline_days
	if doc.purpose == "Production Loss" and (settings.production_loss_deadline_days or 0) > 0:
		days = settings.production_loss_deadline_days
	doc.deadline_date = add_days(incident, days or 0)

	if doc.video_link and not doc.video_link.lower().startswith(("http://", "https://")):
		frappe.throw(_("Video link must start with http:// or https://"))

	if doc._action != "submit":
		return

	evidence = frappe.db.count(
		"File",
		{
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": "evidence",
		},
	)
	if not 1 <= evidence <= MAX_EVIDENCE:
		frappe.throw(_("Attach 1 to {0} evidence photos").format(MAX_EVIDENCE))

	doc.is_late = 1 if getdate(today()) > getdate(doc.deadline_date) else 0
	limit = flt(settings.monthly_limit_amount)
	doc.over_limit = 1 if limit > 0 and flt(doc.total_amount) + _month_total(doc, incident) > limit else 0


def _price_items(doc, warehouse):
	"""uom/valuation_rate/amount are server-owned: client values are overwritten."""
	total = 0.0
	for row in doc.items:
		qty = flt(row.qty)
		if qty <= 0 or not math.isfinite(qty):
			frappe.throw(_("Row {0}: quantity must be greater than zero").format(row.idx))
		stock_uom, item_rate = frappe.db.get_value("Item", row.item_code, ["stock_uom", "valuation_rate"])
		bin_rate = frappe.db.get_value("Bin", {"item_code": row.item_code, "warehouse": warehouse}, "valuation_rate")
		row.uom = stock_uom
		row.valuation_rate = flt(bin_rate) or flt(item_rate)
		row.amount = flt(qty * row.valuation_rate)
		total += row.amount
	doc.total_amount = total


def _month_total(doc, incident):
	return flt(
		frappe.db.sql(
			"""select sum(total_amount) from `tabPOS Reject Request`
			where pos_profile = %s and docstatus != 2 and name != %s
				and incident_date between %s and %s""",
			(doc.pos_profile, doc.name or "", get_first_day(incident), get_last_day(incident)),
		)[0][0]
	)


def on_submit_request(doc):
	doc.db_set("status", "Pending")
	if doc.purpose == "Unsold":
		_transfer_to_reject_warehouse(doc)
	# Task 3: Strict + is_late -> apply_decision(doc, "Charge Crew", ...) goes here.
	notify(approver_users(), _("New reject request {0}").format(doc.name), doc)
	doc.add_comment("Comment", text=_("Submitted, waiting for a decision"))


def before_cancel_request(doc):
	if doc.status != "Pending":
		frappe.throw(_("Only a Pending reject request can be cancelled"))


def on_cancel_request(doc):
	if doc.transfer_entry:
		se = frappe.get_doc("Stock Entry", doc.transfer_entry)
		if se.docstatus == 1:
			se.flags.ignore_permissions = True
			se.cancel()
	doc.db_set("status", "Cancelled")


def _transfer_to_reject_warehouse(doc):
	source = _resolve_profile(doc.pos_profile)[1]
	target = get_effective_pos_setting(doc.pos_profile, "reject_warehouse")
	if not target:
		frappe.throw(_("Set Reject Warehouse in POS Settings for {0}").format(doc.pos_profile))

	needed = defaultdict(float)
	for row in doc.items:
		needed[row.item_code] += flt(row.qty)
	for item_code, qty in needed.items():
		available = _stock_qty(item_code, source)
		if available < qty:
			frappe.throw(
				_("Item {0} has only {1} in {2}, cannot reject {3}").format(item_code, available, source, qty)
			)

	se = frappe.new_doc("Stock Entry")
	se.company = doc.company
	se.purpose = "Material Transfer"
	se.set_stock_entry_type()
	se.remarks = TRANSFER_REMARK_PREFIX + doc.name
	for row in doc.items:
		line = {
			"item_code": row.item_code,
			"qty": row.qty,
			"s_warehouse": source,
			"t_warehouse": target,
			"use_serial_batch_fields": 1,
		}
		if frappe.db.get_value("Item", row.item_code, "has_batch_no"):
			line["batch_no"] = _pick_batch(row.item_code, source, flt(row.qty))
		se.append("items", line)
	se.flags.ignore_permissions = True
	se.insert()
	se.submit()
	doc.db_set("transfer_entry", se.name)


def approver_users():
	users = frappe.get_all("Has Role", filters={"role": APPROVER_ROLE, "parenttype": "User"}, pluck="parent")
	if not users:
		return []
	return frappe.get_all(
		"User", filters={"name": ["in", users], "enabled": 1, "user_type": "System User"}, pluck="name"
	)


def notify(users, subject, doc):
	"""Notification Log for users; HO approver notices also go by email to the
	addresses in POS Reject Settings (best effort: never blocks the request).
	Logs are written inline: the enqueued variant is a background job."""
	if users:
		make_notification_logs(
			frappe._dict(
				type="Alert",
				subject=subject,
				document_type=doc.doctype,
				document_name=doc.name,
				from_user=frappe.session.user,
			),
			users,
		)
	if set(users) != set(approver_users()):
		return
	emails = frappe.db.get_single_value("POS Reject Settings", "notify_emails") or ""
	recipients = [e.strip() for e in emails.splitlines() if e.strip()]
	if not recipients:
		return
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=f'<a href="{get_url_to_form(doc.doctype, doc.name)}">{doc.name}</a>',
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title="POS Reject Request email failed")
