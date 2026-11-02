import math

import frappe
from frappe import _
from frappe.utils import flt, nowdate

from pos_next.api.purchase_orders import ATTACHMENT_MAX_BYTES, _purchase_company_options
from pos_next.pos_next.doctype.pos_spend_template.pos_spend_template import template_companies
from pos_next.services.spend_accounts import (
	allowed_items,
	check_readiness,
	resolve_cost_center,
	resolve_credit_to,
	resolve_debit_account,
	resolve_payment_account,
)
from pos_next.utils.authz import is_management_user


def _name(value):
	if not isinstance(value, str) or not value.strip():
		frappe.throw(_("Spend identifiers must be non-empty text."))
	return value


def _scope(company, pos_profile):
	_name(company)
	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	options = _purchase_company_options()
	if company not in {r["company"] for r in options}:
		frappe.throw(_("You are not allowed to act for company {0}").format(company), frappe.PermissionError)
	if not is_management_user() and not any(
		r["company"] == company and r["pos_profile"] == pos_profile for r in options
	):
		frappe.throw(_("POS Profile must match your active shift."), frappe.PermissionError)
	if pos_profile:
		_name(pos_profile)
		profile = frappe.db.get_value("POS Profile", pos_profile, ["company", "disabled"], as_dict=True)
		if not profile or profile.company != company or profile.disabled:
			frappe.throw(_("POS Profile must be enabled and belong to the spend company."))


def _role_allowed(template):
	roles = {r.role for r in template.roles} or {"POSNext Cashier", "POSNext Manager"}
	return frappe.session.user == "Administrator" or bool(roles.intersection(frappe.get_roles()))


def _authorize(template, company):
	if not _role_allowed(template):
		frappe.throw(_("You are not allowed to use this spend template."), frappe.PermissionError)
	if not template.enabled or company not in template_companies(template):
		frappe.throw(_("Spend template is disabled or outside this company."), frappe.PermissionError)


def _shift_sources(pos_profile):
	settings = (
		frappe.db.get_value("POS Settings", {"pos_profile": pos_profile, "enabled": 1}, "name")
		if pos_profile
		else None
	)
	return (
		set(
			frappe.get_all(
				"POS Shift Spend Source",
				filters={"parent": settings, "parenttype": "POS Settings"},
				pluck="mode_of_payment",
			)
		)
		if settings
		else set()
	)


def _shift_for_source(pos_profile, mode_of_payment, user):
	if mode_of_payment not in _shift_sources(pos_profile):
		return None
	rows = frappe.db.sql(
		"""SET STATEMENT innodb_snapshot_isolation=0 FOR
		SELECT name, status, pos_closing_shift FROM `tabPOS Opening Shift`
		WHERE pos_profile=%s AND user=%s AND docstatus=1 AND status='Open'
		ORDER BY creation DESC LIMIT 1 FOR UPDATE""",
		(pos_profile, user),
		as_dict=True,
	)
	if (
		not rows
		or rows[0].pos_closing_shift
		or frappe.db.get_value(
			"POS Closing Shift", {"pos_opening_shift": rows[0].name, "docstatus": 1}, "name", for_update=True
		)
	):
		frappe.throw(_("Open a shift before spending from a shift payment source."))
	return rows[0].name


@frappe.whitelist()
def get_spend_templates(company: str, pos_profile: str | None = None) -> list[dict]:
	_scope(company, pos_profile)
	result = []
	sources = _shift_sources(pos_profile)
	for name in frappe.get_all("POS Spend Template", filters={"enabled": 1}, pluck="name"):
		template = frappe.get_doc("POS Spend Template", name)
		if not _role_allowed(template) or company not in template_companies(template):
			continue
		if template.update_stock or template.payment_timing != "Pay Now" or template.apply_purchase_tax:
			continue
		if not check_readiness(template, company)["ready"]:
			continue
		row = {
			key: template.get(key)
			for key in (
				"name",
				"title",
				"category",
				"document_type",
				"payment_timing",
				"update_stock",
				"default_supplier",
				"allow_supplier_change",
				"require_vendor_name",
				"requires_attachment",
				"max_amount",
				"over_limit_policy",
				"debit_account_strategy",
			)
		}
		row["payment_sources"] = [
			{
				"mode_of_payment": s.mode_of_payment,
				"is_default": s.is_default,
				"is_shift_source": s.mode_of_payment in sources,
			}
			for s in template.payment_sources
		]
		row["items"] = allowed_items(template)
		result.append(row)
	return result


def _existing(request_id, data):
	for doctype in ("Journal Entry", "Purchase Invoice"):
		name = frappe.db.get_value(doctype, {"posa_client_request_id": request_id}, "name")
		if not name:
			continue
		doc = frappe.get_doc(doctype, name)
		if (
			doc.company != data["company"]
			or doc.owner != frappe.session.user
			or doc.posa_spend_template != data["template"]
			or doc.posa_pos_profile != data.get("pos_profile")
		):
			frappe.throw(_("This spend request ID has already been used."), frappe.PermissionError)
		return _summary(doc, duplicate=True)


def _summary(doc, duplicate=False):
	return {
		"doctype": doc.doctype,
		"name": doc.name,
		"amount": flt(doc.total_debit if doc.doctype == "Journal Entry" else doc.grand_total),
		"shift": doc.posa_pos_opening_shift or None,
		"duplicate": duplicate,
	}


def _positive(value):
	try:
		number = float(value)
	except (ValueError, TypeError, OverflowError):
		frappe.throw(_("Spend quantities and amounts must be positive finite numbers."))
	if not math.isfinite(number) or number <= 0:
		frappe.throw(_("Spend quantities and amounts must be positive finite numbers."))
	return number


def _files(files):
	if not isinstance(files, list):
		frappe.throw(_("Spend files must be a list."))
	for file in files:
		if (
			not isinstance(file, dict)
			or not isinstance(file.get("file_name"), str)
			or not isinstance(file.get("filedata"), str)
			or not file["file_name"].strip()
			or not file["filedata"].strip()
		):
			frappe.throw(_("File name and data are required"))
		if len(file["filedata"]) * 3 // 4 > ATTACHMENT_MAX_BYTES:
			frappe.throw(_("File {0} exceeds the {1} MB limit").format(file["file_name"], 10))
	return files


@frappe.whitelist()
def submit_spend(data: str | dict) -> dict:
	if isinstance(data, str):
		data = frappe.parse_json(data)
	if not isinstance(data, dict):
		frappe.throw(_("Spend data must be an object."))
	for key in ("client_request_id", "template", "company", "pos_profile", "mode_of_payment"):
		_name(data.get(key))
	if len(data["client_request_id"]) > 64:
		frappe.throw(_("Spend request ID must not exceed 64 characters."))
	_scope(data["company"], data.get("pos_profile"))
	template = frappe.get_doc("POS Spend Template", data["template"])
	_authorize(template, data["company"])
	existing = _existing(data["client_request_id"], data)
	if existing:
		return existing
	if template.update_stock or template.payment_timing != "Pay Now" or template.apply_purchase_tax:
		frappe.throw(_("Stock purchases, purchase taxes and Pay Later are not available yet."))
	if data["mode_of_payment"] not in {r.mode_of_payment for r in template.payment_sources}:
		frappe.throw(_("Select a payment source allowed by this spend template."))
	files = _files(data.get("files", []))
	if template.requires_attachment and not files:
		frappe.throw(_("An attachment is required for this spend template."))
	if template.require_vendor_name and not (data.get("vendor_name") or "").strip():
		frappe.throw(_("Vendor name is required."))
	lines = data.get("lines")
	if not isinstance(lines, list) or not lines or any(not isinstance(r, dict) for r in lines):
		frappe.throw(_("At least one spend line is required."))
	shift = _shift_for_source(data.get("pos_profile"), data["mode_of_payment"], frappe.session.user)
	readiness = check_readiness(template, data["company"])
	if not readiness["ready"]:
		frappe.throw("\n".join(readiness["reasons"]))
	company = data["company"]
	account = resolve_payment_account(data["mode_of_payment"], company)
	cost_center = resolve_cost_center(template, company, data.get("pos_profile"))
	doc = frappe.new_doc(template.document_type)
	doc.company = company
	doc.posting_date = nowdate()
	doc.mode_of_payment = data["mode_of_payment"]
	if template.document_type == "Journal Entry":
		doc.voucher_type = (
			"Cash Entry"
			if frappe.db.get_value("Account", account, "account_type") == "Cash"
			else "Bank Entry"
		)
		doc.user_remark = data.get("remarks") or data.get("vendor_name")
		doc.bill_no = data.get("bill_no")
		if doc.voucher_type == "Bank Entry":
			doc.cheque_no = data.get("bill_no") or data["client_request_id"]
			doc.cheque_date = data.get("bill_date") or nowdate()
		for row in lines:
			amount = _positive(row.get("amount"))
			if not isinstance(row.get("description"), str) or not row["description"].strip():
				frappe.throw(_("A description is required for every expense line."))
			doc.append(
				"accounts",
				{
					"account": resolve_debit_account(template, company),
					"cost_center": cost_center,
					"debit_in_account_currency": amount,
					"user_remark": row["description"],
				},
			)
		total = sum(r.debit_in_account_currency for r in doc.accounts)
		doc.append("accounts", {"account": account, "credit_in_account_currency": total})
	else:
		supplier = data.get("supplier") or template.default_supplier
		_name(supplier)
		if supplier != template.default_supplier and not template.allow_supplier_change:
			frappe.throw(_("Supplier changes are not allowed by this spend template."))
		doc.supplier = supplier
		doc.bill_no = data.get("bill_no")
		doc.bill_date = data.get("bill_date")
		doc.remarks = "\n".join(v for v in (data.get("remarks"), data.get("vendor_name")) if v)
		doc.credit_to = resolve_credit_to(supplier, company)
		doc.is_paid = 1
		doc.cash_bank_account = account
		doc.disable_rounded_total = 1
		doc.currency = frappe.db.get_value("Company", company, "default_currency")
		doc.conversion_rate = 1
		# The receipt amount is the truth: buying pricing rules must not rewrite it.
		doc.ignore_pricing_rule = 1
		allowed = set(allowed_items(template))
		for row in lines:
			_name(row.get("item_code"))
			if row["item_code"] not in allowed:
				frappe.throw(_("Item is not allowed by this spend template."))
			qty, rate = _positive(row.get("qty")), _positive(row.get("rate"))
			doc.append(
				"items",
				{
					"item_code": row["item_code"],
					"qty": qty,
					"rate": rate,
					"expense_account": resolve_debit_account(template, company, row["item_code"]),
					"cost_center": cost_center,
				},
			)
		doc.set_missing_values()
		# No tax in stage 1: native supplier defaults must not add a tax silently.
		doc.set("taxes", [])
		doc.taxes_and_charges = None
		doc.calculate_taxes_and_totals()
		doc.paid_amount = doc.grand_total
		total = doc.grand_total
	if not math.isfinite(total) or total <= 0:
		frappe.throw(_("Spend quantities and amounts must be positive finite numbers."))
	if template.max_amount and total > template.max_amount:
		frappe.throw(_("Spending above the template limit is not available yet."))
	for key, value in {
		"posa_client_request_id": data["client_request_id"],
		"posa_spend_template": template.name,
		"posa_pos_profile": data.get("pos_profile"),
		"posa_pos_opening_shift": shift,
		"posa_shift_account": account if shift else None,
		"posa_vendor_name": data.get("vendor_name"),
	}.items():
		doc.set(key, value)
	try:
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		doc.submit()
		# attach_purchase_files is PO/PR-only and commits; keep spend evidence atomic instead.
		for file in files:
			frappe.get_doc(
				{
					"doctype": "File",
					"attached_to_doctype": doc.doctype,
					"attached_to_name": doc.name,
					"file_name": file["file_name"],
					"is_private": 1,
					"content": file["filedata"],
					"decode": 1,
				}
			).insert(ignore_permissions=True)
	except (frappe.DuplicateEntryError, frappe.UniqueValidationError):
		frappe.db.rollback()
		existing = _existing(data["client_request_id"], data)
		if existing:
			return existing
		raise
	return _summary(doc)


@frappe.whitelist()
def get_spend_history(
	company: str,
	pos_profile: str | None = None,
	from_date=None,
	to_date=None,
	status: str | None = None,
	limit: int = 50,
) -> list[dict]:
	from urllib.parse import urlencode

	from frappe.utils import cint, getdate

	from pos_next.api.purchase_orders import _attachments_summary

	_name(company)
	companies = {r["company"] for r in _purchase_company_options()}
	if company not in companies:
		frappe.throw(_("You are not allowed to act for company {0}").format(company), frappe.PermissionError)
	limit = cint(limit)
	if limit < 1:
		frappe.throw(_("History limit must be a positive integer."))
	limit = min(limit, 200)
	if pos_profile:
		_name(pos_profile)
		if frappe.db.get_value("POS Profile", pos_profile, "company") != company:
			frappe.throw(_("POS Profile must belong to the spend company."))
	from_date = getdate(from_date) if from_date else None
	to_date = getdate(to_date) if to_date else None
	if from_date and to_date and from_date > to_date:
		frappe.throw(_("From Date cannot be after To Date"))
	conditions = ["v.company = %(company)s", "COALESCE(v.posa_spend_template, '') != ''"]
	params = {"company": company, "limit": limit, "status": status}
	for field, value, operator in (
		("posa_pos_profile", pos_profile, "="),
		("posting_date", from_date, ">="),
		("posting_date", to_date, "<="),
	):
		if value:
			key = f"{field}_{len(params)}"
			conditions.append(f"v.{field} {operator} %({key})s")
			params[key] = value
	queries = []
	for doctype, amount, bill_no in (
		("Purchase Invoice", "grand_total", "bill_no"),
		("Journal Entry", "total_debit", "bill_no"),
		("Payment Entry", "paid_amount", "reference_no"),
	):
		native_status = (
			"CASE v.docstatus WHEN 0 THEN 'Draft' WHEN 1 THEN 'Submitted' ELSE 'Cancelled' END"
			if doctype == "Journal Entry"
			else "v.status"
		)
		queries.append(
			f"""SELECT '{doctype}' AS doctype, v.name, v.posting_date,
			v.posa_spend_template AS template, t.category, v.{amount} AS amount,
			v.mode_of_payment, v.docstatus, {native_status} AS status,
			v.posa_pos_opening_shift AS shift, v.posa_vendor_name AS vendor_name,
			v.{bill_no} AS bill_no, v.owner, v.creation, v.company,
			v.posa_spend_template, v.posa_pos_opening_shift
			FROM `tab{doctype}` v
			LEFT JOIN `tabPOS Spend Template` t ON t.name = v.posa_spend_template
			WHERE {" AND ".join(conditions)}"""
		)
	rows = frappe.db.sql(
		f"""SELECT * FROM ({" UNION ALL ".join(queries)}) history
		{"WHERE status = %(status)s" if status else ""}
		ORDER BY creation DESC, name DESC, doctype DESC LIMIT %(limit)s""",
		params,
		as_dict=True,
	)
	for row in rows:
		row.can_cancel = _can_cancel(row, companies)
		row.attachments = _attachments_summary(row.doctype, row.name)
		for attachment in row.attachments:
			attachment.file_url = "/api/method/pos_next.api.spend.download_spend_attachment?" + urlencode(
				{"file": attachment.name}
			)
		for key in ("company", "creation", "posa_spend_template", "posa_pos_opening_shift"):
			row.pop(key)
	return rows


@frappe.whitelist(methods=["GET"])
def download_spend_attachment(file: str):
	from pos_next.api.purchase_orders import _assert_purchase_company

	if frappe.session.user == "Guest":
		frappe.throw(_("Authentication required"), frappe.AuthenticationError)
	attachment = frappe.get_doc("File", _name(file))
	if (
		attachment.attached_to_doctype not in ("Purchase Invoice", "Journal Entry", "Payment Entry")
		or not attachment.attached_to_name
		or attachment.is_folder
	):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	voucher = frappe.db.get_value(
		attachment.attached_to_doctype,
		attachment.attached_to_name,
		["company", "posa_spend_template"],
		as_dict=True,
	)
	if not voucher or not voucher.posa_spend_template:
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	_assert_purchase_company(voucher.company)
	frappe.response.update(
		{
			"type": "download",
			"filename": attachment.file_name,
			"filecontent": attachment.get_content(),
			"display_content_as": "attachment",
		}
	)


def _can_cancel(doc, companies):
	if (
		doc.doctype not in ("Purchase Invoice", "Journal Entry")
		or doc.docstatus != 1
		or not doc.posa_spend_template
		or doc.company not in companies
	):
		return False
	shift = doc.posa_pos_opening_shift
	if shift and frappe.db.exists("POS Closing Shift", {"pos_opening_shift": shift, "docstatus": 1}):
		return False
	if is_management_user():
		return True
	if doc.owner != frappe.session.user or not shift:
		return False
	opening = frappe.db.get_value(
		"POS Opening Shift", shift, ["docstatus", "status", "pos_closing_shift"], as_dict=True
	)
	return bool(
		opening and opening.docstatus == 1 and opening.status == "Open" and not opening.pos_closing_shift
	)


@frappe.whitelist()
def cancel_spend(doctype: str, name: str, reason: str) -> dict:
	from frappe.utils import escape_html

	from pos_next.overrides.spend_guard import before_cancel

	if doctype not in ("Purchase Invoice", "Journal Entry"):
		frappe.throw(_("Only Purchase Invoices and Journal Entries can be cancelled here."))
	_name(name)
	if not isinstance(reason, str) or not reason.strip():
		frappe.throw(_("A cancellation reason is required."))
	companies = {r["company"] for r in _purchase_company_options()}
	doc = frappe.get_doc(doctype, name)
	if doc.company not in companies:
		frappe.throw(_("You are not allowed to cancel this spend."), frappe.PermissionError)
	if not doc.posa_spend_template or doc.docstatus != 1:
		frappe.throw(_("Only submitted POS Spend documents can be cancelled."))
	before_cancel(doc)
	if not _can_cancel(doc, companies):
		frappe.throw(_("You are not allowed to cancel this spend."), frappe.PermissionError)
	doc.add_comment("Comment", escape_html(reason.strip()))
	doc.flags.ignore_permissions = True
	doc.cancel()
	return _summary(doc)
