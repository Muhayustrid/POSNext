import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils.nestedset import get_descendants_of

FORBIDDEN_DEBIT_TYPES = {"Stock", "Payable", "Receivable", "Cash", "Bank", "Tax"}


def validate_debit_account(account, company):
	values = frappe.db.get_value(
		"Account", account, ["company", "is_group", "root_type", "account_type"], as_dict=True
	)
	if not values or values.company != company:
		frappe.throw(_("Debit account {0} must belong to company {1}.").format(account, company))
	if (
		values.is_group
		or values.root_type not in ("Expense", "Asset")
		or values.account_type in FORBIDDEN_DEBIT_TYPES
	):
		frappe.throw(_("Account {0} is not an eligible spend debit account.").format(account))


def template_companies(template):
	if isinstance(template, str):
		template = frappe.get_doc("POS Spend Template", template)
	companies = set()
	for row in template.companies:
		if frappe.db.get_value("Company", row.company, "is_group"):
			descendants = get_descendants_of("Company", row.company)
			if descendants:
				companies.update(
					frappe.get_all(
						"Company", filters={"name": ["in", descendants], "is_group": 0}, pluck="name"
					)
				)
		else:
			companies.add(row.company)
	return sorted(companies)


class POSSpendTemplate(Document):
	def validate(self):
		if not self.reference_company:
			self.reference_company = frappe.db.get_single_value(
				"POS Next Global Settings", "spend_reference_company"
			)
		if self.document_type == "Journal Entry" and (
			self.items
			or self.apply_purchase_tax
			or self.update_stock
			or self.payment_timing != "Pay Now"
			or self.debit_account_strategy != "Template Account"
		):
			frappe.throw(_("Journal Entry spend requires Template Account, Pay Now, no items, stock or tax."))
		if not self.payment_sources or sum(bool(row.is_default) for row in self.payment_sources) > 1:
			frappe.throw(_("Choose payment sources with at most one default."))
		for table, key in (
			(self.companies, "company"),
			(self.payment_sources, "mode_of_payment"),
			(self.roles, "role"),
		):
			values = [row.get(key) for row in table]
			if len(values) != len(set(values)):
				frappe.throw(_("Duplicate {0} rows are not allowed.").format(key))
		if self.reference_debit_account:
			validate_debit_account(self.reference_debit_account, self.reference_company)
		for row in self.companies:
			if frappe.db.get_value("Company", row.company, "is_group") and any(
				row.get(f) for f in ("debit_account", "cost_center", "purchase_taxes_template")
			):
				frappe.throw(_("Group company rows define scope only; overrides require a leaf company."))
			if row.debit_account:
				validate_debit_account(row.debit_account, row.company)
			for field, dt in (
				("cost_center", "Cost Center"),
				("purchase_taxes_template", "Purchase Taxes and Charges Template"),
			):
				if row.get(field) and frappe.db.get_value(dt, row.get(field), "company") != row.company:
					frappe.throw(
						_("{0} must belong to company {1}.").format(frappe.unscrub(field), row.company)
					)
		if self.debit_account_strategy == "Template Account" and not self.reference_debit_account:
			overrides = {row.company for row in self.companies if row.debit_account}
			if not template_companies(self) or set(template_companies(self)) - overrides:
				frappe.throw(
					_("Template Account requires a reference debit account or an override for every company.")
				)
		if self.update_stock and self.debit_account_strategy != "Item Default":
			frappe.throw(_("Stock purchases require the Item Default strategy."))
		if self.document_type == "Purchase Invoice":
			from pos_next.services.spend_accounts import allowed_items

			for item in allowed_items(self):
				is_stock_item = frappe.db.get_value("Item", item, "is_stock_item")
				if self.update_stock and not is_stock_item:
					frappe.throw(_("Stock purchase templates may only contain stock items."))
				if is_stock_item and not self.update_stock:
					frappe.throw(_("Stock items require Update Stock on purchase templates."))
		for row in self.items:
			if bool(row.item_code) == bool(row.item_group):
				frappe.throw(_("Choose either an item or an item group on each row."))
		if (
			self.payment_timing == "Pay Later"
			and self.default_supplier
			and frappe.db.get_value("Supplier", self.default_supplier, "posa_is_walk_in_supplier")
		):
			frappe.throw(_("Pay Later requires a supplier that is not a walk-in supplier."))
