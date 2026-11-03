import frappe

from pos_next.tests.spend_helpers import SpendTestCase


class TestSpendAccounts(SpendTestCase):
	def resolver(self):
		from pos_next.services import spend_accounts

		return spend_accounts

	def test_allowed_items_has_one_template_owner(self):
		from pos_next.pos_next.doctype.pos_spend_template import pos_spend_template

		self.assertIs(self.resolver().allowed_items, getattr(pos_spend_template, "allowed_items", None))

	def test_allowed_items_preserves_expansion_and_direct_rows(self):
		first, second, disabled, not_purchase = [self.item() for _ in range(4)]
		child = frappe.get_doc("Item Group", second.item_group)
		child.parent_item_group = first.item_group
		child.save()
		for item in (disabled, not_purchase):
			frappe.db.set_value("Item", item.name, "item_group", first.item_group)
		frappe.db.set_value("Item", disabled.name, "disabled", 1)
		frappe.db.set_value("Item", not_purchase.name, "is_purchase_item", 0)
		template = self.template(
			items=[
				{"item_group": first.item_group},
				{"item_code": first.name},
				{"item_code": disabled.name},
			]
		)
		self.assertEqual(
			self.resolver().allowed_items(template), sorted([first.name, second.name, disabled.name])
		)

	def test_readiness_keeps_reference_company_default(self):
		before = frappe.db.get_single_value("POS Next Global Settings", "spend_reference_company")
		try:
			frappe.db.set_single_value("POS Next Global Settings", "spend_reference_company", self.reference)
			doc = self.template(reference_company=None)
			result = self.resolver().check_readiness(doc, self.a)
			self.assertTrue(result["ready"], result)
			self.assertEqual(doc.reference_company, self.reference)
		finally:
			frappe.db.set_single_value("POS Next Global Settings", "spend_reference_company", before)

	def test_all_scope_keeps_leaf_overrides_and_reports_unready_company(self):
		doc = self.template(
			company_scope="All Companies",
			companies=[{"company": self.a, "debit_account": self.expense[self.a]}],
		)
		frappe.db.set_value("Account", self.expense[self.a], "account_number", "OVERRIDE-ONLY")
		ready = self.resolver().check_readiness(doc, self.a)
		self.assertTrue(ready["ready"], ready)
		self.assertEqual(ready["accounts"]["debit"], self.expense[self.a])
		unready = self.resolver().check_readiness(doc, self.b)
		self.assertFalse(unready["ready"], unready)
		self.assertFalse(any("outside the template scope" in reason for reason in unready["reasons"]))

	def test_resolve_by_number_ignores_name(self):
		frappe.db.set_value("Account", self.expense[self.reference], "account_name", "Biaya Listrik")
		frappe.db.set_value("Account", self.expense[self.a], "account_name", "Biaya PLN Kantor")
		self.assertNotEqual(self.expense[self.reference], self.expense[self.a])
		self.assertEqual(
			self.resolver().resolve_account(self.expense[self.reference], self.a), self.expense[self.a]
		)

	def test_resolve_rejects_type_mismatch(self):
		frappe.db.set_value("Account", self.expense[self.a], "account_type", "Tax")
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_account(self.expense[self.reference], self.a)

	def test_resolve_rejects_disabled(self):
		frappe.db.set_value("Account", self.expense[self.a], "disabled", 1)
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_account(self.expense[self.reference], self.a)

	def test_resolve_rejects_currency(self):
		frappe.db.set_value("Account", self.expense[self.a], "account_currency", "USD")
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_account(self.expense[self.reference], self.a)

	def test_resolve_missing_number(self):
		frappe.db.set_value("Account", self.expense[self.a], "account_number", "OTHER")
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_account(self.expense[self.reference], self.a)

	def test_reference_without_number_needs_override(self):
		frappe.db.set_value("Account", self.expense[self.reference], "account_number", None)
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_account(self.expense[self.reference], self.a)
		doc = self.template(companies=[{"company": self.a, "debit_account": self.expense[self.a]}])
		self.assertEqual(self.resolver().resolve_debit_account(doc, self.a), self.expense[self.a])

	def test_parent_mismatch_is_warning_only(self):
		for company, number in ((self.reference, "PARENT-1"), (self.a, "PARENT-2")):
			parent = frappe.db.get_value("Account", self.expense[company], "parent_account")
			frappe.db.set_value("Account", parent, "account_number", number)
		result = self.resolver().check_readiness(self.template(), self.a)
		self.assertTrue(result["ready"], result)
		self.assertEqual(len(result["warnings"]), 1)

	def item(self, defaults=None):
		group = frappe.get_doc(
			{
				"doctype": "Item Group",
				"item_group_name": f"Spend group {frappe.generate_hash(length=6)}",
				"parent_item_group": "All Item Groups",
			}
		).insert()
		return frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"Spend item {frappe.generate_hash(length=6)}",
				"item_name": "Spend item",
				"item_group": group.name,
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"item_defaults": defaults or [],
			}
		).insert()

	def test_template_account_ignores_item_default(self):
		other = frappe.db.get_value(
			"Account",
			{"company": self.a, "root_type": "Expense", "is_group": 0, "name": ["!=", self.expense[self.a]]},
			"name",
		)
		self.assertTrue(other)
		item = self.item([{"company": self.a, "expense_account": other}])
		self.assertEqual(
			self.resolver().resolve_debit_account(self.template(), self.a, item.name), self.expense[self.a]
		)

	def test_item_default_rejects_company_fallback(self):
		item = self.item()
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_debit_account(
				self.template(document_type="Purchase Invoice", debit_account_strategy="Item Default"),
				self.a,
				item.name,
			)

	def test_payment_account_rejects_non_cash_bank(self):
		frappe.db.set_value("Account", self.cash, "account_type", "Tax")
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_payment_account(self.mop, self.a)

	def test_credit_to_must_be_payable(self):
		frappe.db.set_value("Company", self.a, "default_payable_account", self.expense[self.a])
		self.assertNotEqual(frappe.db.get_value("Account", self.expense[self.a], "account_type"), "Payable")
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_credit_to(None, self.a)

	def test_readiness_lists_every_leaf_company(self):
		doc = self.template(companies=[{"company": self.group}]).insert()
		rows = self.resolver().get_spend_template_readiness(doc.name)
		self.assertEqual([row["company"] for row in rows], sorted([self.a, self.b]))

	def test_readiness_report_filters_template(self):
		from pos_next.pos_next.report.spend_template_readiness.spend_template_readiness import execute

		doc = self.template(companies=[{"company": self.group}]).insert()
		columns, rows = execute({"template": doc.name})
		self.assertEqual(len(columns), 4)
		self.assertEqual({row["company"] for row in rows}, {self.a, self.b})
		self.assertTrue(all(row["template"] == doc.name for row in rows))

	def test_readiness_requires_management_role(self):
		try:
			frappe.set_user("Guest")
			with self.assertRaises(frappe.PermissionError):
				self.resolver().get_spend_template_readiness("missing")
		finally:
			frappe.set_user("Administrator")

	def test_item_and_group_defaults_are_used(self):
		item = self.item([{"company": self.a, "expense_account": self.expense[self.a]}])
		template = self.template(document_type="Purchase Invoice", debit_account_strategy="Item Default")
		self.assertEqual(
			self.resolver().resolve_debit_account(template, self.a, item.name), self.expense[self.a]
		)
		group_item = self.item()
		group = frappe.get_doc("Item Group", group_item.item_group)
		group.append("item_group_defaults", {"company": self.a, "expense_account": self.expense[self.a]})
		group.save()
		self.assertEqual(
			self.resolver().resolve_debit_account(template, self.a, group_item.name), self.expense[self.a]
		)

	def test_readiness_rejects_non_string_template(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Template must be a non-empty name"):
			self.resolver().get_spend_template_readiness(["!=", ""])

	def test_paid_pi_readiness_and_supplier_party_account(self):
		item = self.item()
		payable = frappe.db.get_value("Company", self.a, "default_payable_account")
		self.assertEqual(frappe.db.get_value("Account", payable, "account_type"), "Payable")
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": f"Spend Supplier {frappe.generate_hash(length=6)}",
				"supplier_group": "All Supplier Groups",
				"supplier_type": "Company",
				"accounts": [{"company": self.a, "account": payable}],
			}
		).insert()
		frappe.db.set_value("Company", self.a, "default_payable_account", self.expense[self.a])
		template = self.template(
			document_type="Purchase Invoice", default_supplier=supplier.name, items=[{"item_code": item.name}]
		)
		result = self.resolver().check_readiness(template, self.a)
		self.assertTrue(result["ready"], result)
		self.assertEqual(result["accounts"]["credit_to"], payable)
		self.assertEqual(result["accounts"]["items"][item.name], self.expense[self.a])

	def test_stock_item_without_update_stock_is_not_ready(self):
		item = self.item()
		item.is_stock_item = 1
		item.save()
		for strategy in ("Item Default", "Template Account"):
			with self.subTest(strategy=strategy):
				template = self.template(
					document_type="Purchase Invoice",
					debit_account_strategy=strategy,
					allow_supplier_change=1,
					items=[{"item_code": item.name}],
				)
				with self.assertRaisesRegex(self.resolver().SpendConfigError, "Stock items require"):
					self.resolver().resolve_debit_account(template, self.a, item.name)
				with self.assertRaisesRegex(frappe.ValidationError, "Stock items require"):
					template.validate()
				result = self.resolver().check_readiness(template, self.a)
				self.assertFalse(result["ready"], result)
				self.assertTrue(any("Stock items require" in reason for reason in result["reasons"]), result)
				self.assertNotIn(item.name, result["accounts"]["items"])

	def test_stock_item_with_update_stock_leaves_account_to_erpnext(self):
		item = self.item()
		item.is_stock_item = 1
		item.save()
		template = self.template(
			document_type="Purchase Invoice", debit_account_strategy="Item Default", update_stock=1
		)
		self.assertIsNone(self.resolver().resolve_debit_account(template, self.a, item.name))

	def test_supplier_selected_at_transaction_uses_actual_party_account(self):
		item = self.item()
		payable = frappe.db.get_value("Company", self.a, "default_payable_account")
		party_payable = frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": f"Spend payable {frappe.generate_hash(length=6)}",
				"company": self.a,
				"parent_account": frappe.db.get_value("Account", payable, "parent_account"),
				"account_type": "Payable",
				"account_currency": "IDR",
			}
		).insert()
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": f"Spend Supplier {frappe.generate_hash(length=6)}",
				"supplier_group": "All Supplier Groups",
				"supplier_type": "Company",
				"accounts": [{"company": self.a, "account": party_payable.name}],
			}
		).insert()
		template = self.template(
			document_type="Purchase Invoice", allow_supplier_change=1, items=[{"item_code": item.name}]
		)
		result = self.resolver().check_readiness(template, self.a)
		self.assertTrue(result["ready"], result)
		self.assertEqual(result["accounts"]["credit_to"], payable)
		self.assertNotEqual(payable, party_payable.name)
		self.assertEqual(self.resolver().resolve_credit_to(supplier.name, self.a), party_payable.name)
		frappe.db.set_value("Account", party_payable.name, "disabled", 1)
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_credit_to(supplier.name, self.a)

	def test_readiness_requires_supplier_when_selection_is_locked(self):
		item = self.item()
		result = self.resolver().check_readiness(
			self.template(document_type="Purchase Invoice", items=[{"item_code": item.name}]), self.a
		)
		self.assertFalse(result["ready"], result)
		self.assertTrue(any("supplier" in reason.lower() for reason in result["reasons"]), result)

	def test_disabled_supplier_is_not_ready_or_resolvable(self):
		item = self.item()
		supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": f"Spend Disabled Supplier {frappe.generate_hash(length=6)}",
				"supplier_group": "All Supplier Groups",
				"supplier_type": "Company",
				"disabled": 1,
			}
		).insert()
		template = self.template(
			document_type="Purchase Invoice", default_supplier=supplier.name, items=[{"item_code": item.name}]
		)
		result = self.resolver().check_readiness(template, self.a)
		self.assertFalse(result["ready"], result)
		self.assertTrue(
			any(supplier.name in reason and "disabled" in reason for reason in result["reasons"]), result
		)
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_credit_to(supplier.name, self.a)

	def test_override_type_must_match_reference(self):
		frappe.db.set_value("Account", self.expense[self.a], "account_type", "Expense Account")
		frappe.db.set_value("Account", self.expense[self.reference], "account_type", "")
		with self.assertRaises(self.resolver().SpendConfigError):
			self.resolver().resolve_debit_account(
				self.template(companies=[{"company": self.a, "debit_account": self.expense[self.a]}]), self.a
			)

	def test_missing_reference_is_not_a_database_fallback(self):
		with self.assertRaisesRegex(self.resolver().SpendConfigError, "Reference debit account is missing"):
			self.resolver().resolve_account(None, self.a)

	def test_cost_center_precedence_and_ownership(self):
		resolver = self.resolver()
		cc = frappe.db.get_value("Company", self.a, "cost_center")
		self.assertEqual(resolver.resolve_cost_center(self.template(), self.a), cc)
		foreign = frappe.db.get_value("Company", self.b, "cost_center")
		with self.assertRaises(resolver.SpendConfigError):
			resolver.resolve_cost_center(
				self.template(companies=[{"company": self.a, "cost_center": foreign}]), self.a
			)

	def test_frozen_and_foreign_override_rejected(self):
		for values in ({"freeze_account": "Yes"}, {"company": self.b}):
			with self.subTest(values=values):
				before = frappe.db.get_value("Account", self.expense[self.a], list(values), as_dict=True)
				frappe.db.set_value("Account", self.expense[self.a], values)
				with self.assertRaises(self.resolver().SpendConfigError):
					self.resolver().resolve_debit_account(
						self.template(companies=[{"company": self.a, "debit_account": self.expense[self.a]}]),
						self.a,
					)
				frappe.db.set_value("Account", self.expense[self.a], before)
