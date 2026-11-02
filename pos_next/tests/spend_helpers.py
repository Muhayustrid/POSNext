import unittest

import frappe


class SpendTestCase(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		frappe.set_user("Administrator")
		cls.addClassCleanup(frappe.db.rollback)
		tag = frappe.generate_hash(length=6)
		cls.group = cls.company(f"Spend Group {tag}", f"SG{tag}", is_group=1)
		cls.reference = cls.group
		cls.a = cls.company(f"Spend A {tag}", f"SA{tag}", parent_company=cls.group)
		cls.b = cls.company(f"Spend B {tag}", f"SB{tag}", parent_company=cls.group)
		cls.expense = {}
		for company in (cls.group, cls.a, cls.b):
			account = frappe.db.get_value(
				"Account", {"company": company, "root_type": "Expense", "is_group": 0}, "name"
			)
			frappe.db.set_value("Account", account, "account_number", "SPEND-100")
			cls.expense[company] = account
		cls.cash = frappe.db.get_value(
			"Account", {"company": cls.a, "account_type": "Cash", "is_group": 0}, "name"
		)
		cls.mop = (
			frappe.get_doc(
				{
					"doctype": "Mode of Payment",
					"mode_of_payment": f"Spend Cash {tag}",
					"type": "Cash",
					"accounts": [{"company": cls.a, "default_account": cls.cash}],
				}
			)
			.insert()
			.name
		)

	@staticmethod
	def company(name, abbr, **values):
		return (
			frappe.get_doc(
				{
					"doctype": "Company",
					"company_name": name,
					"abbr": abbr,
					"default_currency": "IDR",
					"country": "Indonesia",
					**values,
				}
			)
			.insert()
			.name
		)

	def setUp(self):
		frappe.db.savepoint("spend_test")

	def tearDown(self):
		frappe.db.rollback(save_point="spend_test")
		frappe.clear_cache()

	def template(self, **values):
		return frappe.get_doc(
			{
				"doctype": "POS Spend Template",
				"title": f"Spend {frappe.generate_hash(length=8)}",
				"category": "Test",
				"enabled": 1,
				"document_type": "Journal Entry",
				"payment_timing": "Pay Now",
				"debit_account_strategy": "Template Account",
				"reference_company": self.reference,
				"reference_debit_account": self.expense[self.reference],
				"payment_sources": [{"mode_of_payment": self.mop, "is_default": 1}],
				"companies": [{"company": self.a}],
				**values,
			}
		)
