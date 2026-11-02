import frappe
from frappe import _
from frappe.utils.nestedset import get_descendants_of

from pos_next.pos_next.doctype.pos_spend_template.pos_spend_template import (
	template_companies,
	validate_debit_account,
)


class SpendConfigError(frappe.ValidationError):
	pass


def _fail(message):
	frappe.throw(message, SpendConfigError)


def _account(name, company):
	account = (
		frappe.db.get_value(
			"Account",
			name,
			[
				"name",
				"company",
				"account_number",
				"root_type",
				"account_type",
				"is_group",
				"disabled",
				"freeze_account",
				"account_currency",
				"parent_account",
			],
			as_dict=True,
		)
		if name
		else None
	)
	if not account or account.company != company:
		_fail(_("Account {0} must belong to company {1}.").format(name or "", company))
	if account.is_group or account.disabled or account.freeze_account == "Yes":
		_fail(_("Account {0} must be a usable, unfrozen leaf account.").format(name))
	if account.account_currency != frappe.db.get_value("Company", company, "default_currency"):
		_fail(_("Account {0} must use the company currency.").format(name))
	return account


def resolve_account(reference_account: str, company: str) -> str:
	reference = frappe.db.get_value(
		"Account", reference_account, ["account_number", "root_type", "account_type", "company"], as_dict=True
	)
	if not reference:
		_fail(_("Reference debit account is missing."))
	if reference.company == company:
		return _account(reference_account, company).name
	if not reference.account_number:
		_fail(
			_("Reference account {0} has no account number; configure a company override.").format(
				reference_account
			)
		)
	matches = frappe.get_all(
		"Account",
		filters={"company": company, "account_number": reference.account_number, "is_group": 0},
		pluck="name",
	)
	if len(matches) != 1:
		_fail(
			_("Company {0} needs exactly one leaf account numbered {1}.").format(
				company, reference.account_number
			)
		)
	account = _account(matches[0], company)
	if (account.root_type, account.account_type) != (reference.root_type, reference.account_type):
		_fail(_("Account {0} has a different root or account type than the reference.").format(account.name))
	return account.name


def _company_row(template, company):
	return next((row for row in template.companies if row.company == company), None)


def _debit(name, company):
	_account(name, company)
	try:
		validate_debit_account(name, company)
	except frappe.ValidationError as exc:
		raise SpendConfigError(str(exc)) from exc
	return name


def resolve_debit_account(template, company: str, item_code: str | None = None) -> str | None:
	is_stock_item = item_code and frappe.db.get_value("Item", item_code, "is_stock_item")
	if is_stock_item and not template.update_stock:
		_fail(_("Stock items require Update Stock on purchase templates."))
	if template.debit_account_strategy == "Template Account":
		row = _company_row(template, company)
		if row and row.debit_account:
			account = _debit(row.debit_account, company)
			if template.reference_debit_account:
				reference = frappe.db.get_value(
					"Account", template.reference_debit_account, ["root_type", "account_type"], as_dict=True
				)
				target = _account(account, company)
				if not reference or (target.root_type, target.account_type) != (
					reference.root_type,
					reference.account_type,
				):
					_fail(
						_(
							"Override account {0} has a different root or account type than the reference."
						).format(account)
					)
			return account
		return _debit(resolve_account(template.reference_debit_account, company), company)
	if not item_code:
		_fail(_("Item Default requires an item."))
	if is_stock_item:
		return None
	from erpnext.setup.doctype.item_group.item_group import get_item_group_defaults
	from erpnext.stock.doctype.item.item import get_item_defaults

	account = (get_item_defaults(item_code, company) or {}).get("expense_account") or (
		get_item_group_defaults(item_code, company) or {}
	).get("expense_account")
	if not account:
		_fail(
			_("Item {0} needs an Item or Item Group expense account for company {1}.").format(
				item_code, company
			)
		)
	return _debit(account, company)


def resolve_payment_account(mode_of_payment: str, company: str) -> str:
	from erpnext.accounts.doctype.sales_invoice.sales_invoice import get_bank_cash_account

	try:
		name = get_bank_cash_account(mode_of_payment, company).get("account")
	except frappe.ValidationError as exc:
		raise SpendConfigError(str(exc)) from exc
	account = _account(name, company)
	if account.account_type not in ("Cash", "Bank"):
		_fail(_("Payment account {0} must be Cash or Bank.").format(name))
	return name


def resolve_cost_center(template, company, pos_profile=None) -> str:
	row = _company_row(template, company)
	profile = frappe.get_doc("POS Profile", pos_profile) if pos_profile else None
	if profile and profile.company != company:
		_fail(_("POS Profile must belong to company {0}.").format(company))
	name = (
		(row.cost_center if row else None)
		or (profile.cost_center if profile else None)
		or frappe.db.get_value("Company", company, "cost_center")
	)
	values = (
		frappe.db.get_value("Cost Center", name, ["company", "is_group", "disabled"], as_dict=True)
		if name
		else None
	)
	if not values or values.company != company or values.is_group or values.disabled:
		_fail(_("Company {0} needs a usable leaf cost center.").format(company))
	return name


def resolve_credit_to(supplier: str, company: str) -> str:
	if supplier:
		from erpnext.accounts.party import validate_party_frozen_disabled

		try:
			validate_party_frozen_disabled(company, "Supplier", supplier)
		except frappe.ValidationError as exc:
			raise SpendConfigError(str(exc)) from exc
	name = (
		frappe.db.get_value(
			"Party Account", {"parent": supplier, "parenttype": "Supplier", "company": company}, "account"
		)
		if supplier
		else None
	)
	name = name or frappe.db.get_value("Company", company, "default_payable_account")
	account = _account(name, company)
	if account.account_type != "Payable":
		_fail(_("Supplier payable account {0} must have type Payable.").format(name))
	return name


def allowed_items(template):
	items = set()
	for row in template.items:
		if row.item_code:
			items.add(row.item_code)
		elif row.item_group:
			groups = [row.item_group, *get_descendants_of("Item Group", row.item_group)]
			items.update(
				frappe.get_all(
					"Item",
					filters={"item_group": ["in", groups], "disabled": 0, "is_purchase_item": 1},
					pluck="name",
				)
			)
	return sorted(items)


def check_readiness(template, company: str) -> dict:
	if isinstance(template, str):
		template = frappe.get_doc("POS Spend Template", template)
	result = {
		"company": company,
		"ready": False,
		"reasons": [],
		"warnings": [],
		"accounts": {"payments": {}, "items": {}},
	}

	def capture(key, resolver, *args, container=None):
		try:
			value = resolver(*args)
			(result["accounts"] if container is None else container)[key] = value
			return value
		except frappe.ValidationError as exc:
			result["reasons"].append(str(exc))

	if company not in template_companies(template):
		result["reasons"].append(_("Company is outside the template scope."))
	capture("template", lambda: template.validate())
	result["accounts"].pop("template", None)
	if template.debit_account_strategy == "Template Account":
		debit = capture("debit", resolve_debit_account, template, company)
		row = _company_row(template, company)
		if debit and template.reference_debit_account and not (row and row.debit_account):
			parents = [
				frappe.db.get_value("Account", name, "parent_account")
				for name in (template.reference_debit_account, debit)
			]
			numbers = [
				frappe.db.get_value("Account", name, "account_number") if name else None for name in parents
			]
			if numbers[0] != numbers[1]:
				result["warnings"].append(_("Debit account parent number differs from the reference."))
	if not template.payment_sources:
		result["reasons"].append(_("No payment sources are configured."))
	for source in template.payment_sources:
		capture(
			source.mode_of_payment,
			resolve_payment_account,
			source.mode_of_payment,
			company,
			container=result["accounts"]["payments"],
		)
	capture("cost_center", resolve_cost_center, template, company)
	if template.document_type == "Purchase Invoice":
		if not template.default_supplier and not template.allow_supplier_change:
			result["reasons"].append(_("Configure a default supplier for readiness checks."))
		capture("credit_to", resolve_credit_to, template.default_supplier, company)
		items = allowed_items(template)
		if not items:
			result["reasons"].append(_("No purchase items are configured."))
		for item in items:
			values = frappe.db.get_value("Item", item, ["disabled", "is_purchase_item"], as_dict=True)
			if not values or values.disabled or not values.is_purchase_item:
				result["reasons"].append(_("Item {0} is not available for purchasing.").format(item))
			capture(
				item, resolve_debit_account, template, company, item, container=result["accounts"]["items"]
			)
		if template.apply_purchase_tax:
			row = _company_row(template, company)
			tax = row.purchase_taxes_template if row else None
			if (
				not tax
				or frappe.db.get_value("Purchase Taxes and Charges Template", tax, "company") != company
			):
				result["reasons"].append(_("Configure a purchase taxes template for this company."))
			else:
				result["accounts"]["purchase_taxes_template"] = tax
		if template.update_stock:
			result["accounts"]["perpetual_inventory"] = frappe.db.get_value(
				"Company", company, "enable_perpetual_inventory"
			)
	result["ready"] = not result["reasons"]
	return result


@frappe.whitelist()
def get_spend_template_readiness(template: str) -> list[dict]:
	frappe.only_for(("System Manager", "Accounts Manager", "POSNext Manager"))
	if not isinstance(template, str) or not template.strip():
		frappe.throw(_("Template must be a non-empty name."))
	doc = frappe.get_doc("POS Spend Template", template)
	doc.check_permission("read")
	return [check_readiness(doc, company) for company in template_companies(doc)]
