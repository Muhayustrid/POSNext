import frappe
from frappe.utils import now_datetime, nowdate

from pos_next.tests.spend_helpers import SpendTestCase


class SpendTransactionTestCase(SpendTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		super().setUp()
		tag = frappe.generate_hash(length=8)
		self.profile = frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": f"Spend Profile {tag}",
				"company": self.a,
				"currency": "IDR",
				"warehouse": frappe.db.get_value("Warehouse", {"company": self.a, "is_group": 0}, "name"),
				"write_off_account": self.expense[self.a],
				"write_off_cost_center": frappe.db.get_value("Company", self.a, "cost_center"),
				"cost_center": frappe.db.get_value("Company", self.a, "cost_center"),
				"payments": [{"mode_of_payment": self.mop, "default": 1}],
			}
		).insert()
		self.settings = frappe.get_doc(
			{
				"doctype": "POS Settings",
				"pos_profile": self.profile.name,
				"enabled": 1,
				"posa_shift_spend_sources": [{"mode_of_payment": self.mop}],
			}
		).insert()
		self.shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"company": self.a,
				"pos_profile": self.profile.name,
				"user": "Administrator",
				"posting_date": nowdate(),
				"period_start_date": now_datetime(),
				"balance_details": [{"mode_of_payment": self.mop, "amount": 200000}],
			}
		).insert()
		self.shift.submit()
		self.je_template = self.template().insert()
		self.item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": f"Spend Service {tag}",
				"item_name": "Spend service",
				"item_group": "All Item Groups",
				"stock_uom": "Nos",
				"is_stock_item": 0,
				"is_purchase_item": 1,
			}
		).insert()
		self.supplier = frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": f"Spend Vendor {tag}",
				"supplier_group": "All Supplier Groups",
				"supplier_type": "Company",
			}
		).insert()
		self.pi_template = self.template(
			document_type="Purchase Invoice",
			default_supplier=self.supplier.name,
			items=[{"item_code": self.item.name}],
		).insert()

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	def payload(self, **values):
		return {
			"client_request_id": frappe.generate_hash(length=32),
			"template": self.je_template.name,
			"company": self.a,
			"pos_profile": self.profile.name,
			"mode_of_payment": self.mop,
			"lines": [{"description": "Electricity", "amount": 30000}],
			**values,
		}

	def spend(self, **values):
		from pos_next.api.spend import submit_spend

		return submit_spend(self.payload(**values))

	def pi_spend(self, amount=12345, **values):
		return self.spend(
			template=self.pi_template.name,
			lines=[{"item_code": self.item.name, "qty": 1, "rate": amount}],
			**values,
		)

	def gl(self, result):
		rows = frappe.db.sql(
			"""SELECT account, SUM(debit-credit) amount FROM `tabGL Entry`
			WHERE voucher_type=%s AND voucher_no=%s AND is_cancelled=0 GROUP BY account""",
			(result["doctype"], result["name"]),
			as_dict=True,
		)
		return {r.account: float(r.amount) for r in rows if r.amount}

	def close(self):
		from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import _submit_closing

		return frappe.get_doc("POS Closing Shift", _submit_closing(self.shift.name, auto_balance=True))

	def non_shift(self):
		self.settings.set("posa_shift_spend_sources", [])
		self.settings.save()

	def user(self, role="POSNext Cashier"):
		return (
			frappe.get_doc(
				{
					"doctype": "User",
					"email": f"spend-{frappe.generate_hash(length=8)}@example.test",
					"first_name": "Spend",
					"send_welcome_email": 0,
					"roles": [{"role": role}],
				}
			)
			.insert()
			.name
		)
