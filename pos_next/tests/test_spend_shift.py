import threading
from concurrent.futures import ThreadPoolExecutor

import frappe
from frappe.utils import nowdate

from pos_next.tests.spend_transaction_helpers import SpendTransactionTestCase


class TestSpendShift(SpendTransactionTestCase):
	def preview(self):
		from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
			make_closing_shift_from_opening,
		)

		return make_closing_shift_from_opening(frappe.as_json(self.shift.as_dict()))

	def expected(self, closing):
		return next(
			r.get("expected_amount")
			for r in closing.get("payment_reconciliation")
			if r.get("mode_of_payment") == self.mop
		)

	def cash_sale(self):
		customer = frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": f"Spend Buyer {frappe.generate_hash(length=8)}",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
				"customer_type": "Individual",
			}
		).insert()
		income = frappe.db.get_value(
			"Account", {"company": self.a, "root_type": "Income", "is_group": 0}, "name"
		)
		doc = frappe.get_doc(
			{
				"doctype": "Sales Invoice",
				"company": self.a,
				"customer": customer.name,
				"currency": "IDR",
				"conversion_rate": 1,
				"posting_date": nowdate(),
				"is_pos": 1,
				"disable_rounded_total": 1,
				"posa_pos_opening_shift": self.shift.name,
				"items": [
					{
						"item_code": self.item.name,
						"qty": 1,
						"rate": 100000,
						"income_account": income,
						"cost_center": self.profile.cost_center,
					}
				],
				"payments": [{"mode_of_payment": self.mop, "account": self.cash, "amount": 100000}],
			}
		).insert()
		doc.submit()
		return doc

	def test_drawer_spend_lowers_expected(self):
		self.cash_sale()
		self.spend()
		data = self.preview()
		self.assertEqual(self.expected(data), 270000)
		self.assertEqual(len(data["spend_entries"]), 1)
		self.assertEqual(data["spend_entries"][0]["amount"], 30000)

	def test_non_shift_spend_not_counted(self):
		self.non_shift()
		self.spend()
		from pos_next.services.spend_shift import shift_spend

		self.assertEqual(shift_spend(self.shift.name), [])
		self.assertEqual(self.expected(self.preview()), 200000)

	def test_cancelled_spend_not_counted(self):
		result = self.spend()
		frappe.get_doc(result["doctype"], result["name"]).cancel()
		from pos_next.services.spend_shift import shift_spend

		self.assertEqual(shift_spend(self.shift.name), [])
		self.assertEqual(self.expected(self.preview()), 200000)

	def test_je_counts_credit_on_drawer_account_only(self):
		result = self.spend()
		doc = frappe.get_doc(result["doctype"], result["name"])
		doc.cancel()
		copy = frappe.copy_doc(doc)
		copy.posa_client_request_id = None
		copy.posa_pos_opening_shift = self.shift.name
		copy.posa_shift_account = self.cash
		copy.posa_spend_template = self.je_template.name
		copy.mode_of_payment = self.mop
		other = frappe.db.get_value(
			"Account",
			{"company": self.a, "root_type": "Expense", "is_group": 0, "name": ["!=", self.expense[self.a]]},
			"name",
		)
		copy.append(
			"accounts",
			{"account": other, "debit_in_account_currency": 9000, "cost_center": self.profile.cost_center},
		)
		copy.append(
			"accounts",
			{
				"account": self.expense[self.a],
				"credit_in_account_currency": 9000,
				"cost_center": self.profile.cost_center,
			},
		)
		copy.insert().submit()
		from pos_next.services.spend_shift import shift_spend, shift_spend_by_mode

		self.assertEqual(shift_spend(self.shift.name)[0]["amount"], 30000)
		self.assertEqual(shift_spend_by_mode(self.shift.name), {self.mop: 30000})

	def test_closing_uses_stamped_account_after_mapping_change(self):
		self.spend()
		other = (
			frappe.get_doc(
				{
					"doctype": "Account",
					"account_name": f"New Drawer {frappe.generate_hash(length=6)}",
					"company": self.a,
					"parent_account": frappe.db.get_value("Account", self.cash, "parent_account"),
					"account_type": "Cash",
					"account_currency": "IDR",
				}
			)
			.insert()
			.name
		)
		frappe.db.set_value(
			"Mode of Payment Account", {"parent": self.mop, "company": self.a}, "default_account", other
		)
		closing = self.close()
		self.assertEqual(self.expected(closing), 170000)
		self.assertEqual(closing.spend_entries[0].account, self.cash)

	def test_eod_total_expense_and_recap_expense(self):
		from pos_next.pos_next.utils.pos_closing_print import get_sales_recap
		from pos_next.services.sales_recap import build_recap, shift_scope

		self.cash_sale()
		self.spend()
		closing = self.close()
		eod = get_sales_recap(closing)
		self.assertEqual(eod["cash"]["total_expense"], 30000)
		self.assertEqual(eod["cash"]["cash_payment"], 100000)
		self.assertEqual(eod["cash"]["cash_in_hand"], 270000)
		self.assertEqual(eod["methods_grand_total"], 100000)
		recap = build_recap(shift_scope(self.shift.name), self.mop, self.profile.name)
		self.assertEqual(recap["expense"], 30000)
		self.assertIs(recap["expense_supported"], True)
		self.assertEqual(recap["cash_expected"], 270000)
		self.assertEqual(recap["cash_in_hand"], 270000)

	def test_handover_follows_expected(self):
		from pos_next.api.shifts import get_cash_handover

		frappe.db.set_single_value("POS Next Global Settings", "enable_cash_handover", 1)
		self.spend()
		self.close()
		self.assertEqual(get_cash_handover(self.profile.name)["amounts"][self.mop], 170000)

	def test_desk_closing_refreshes_spend_after_preview(self):
		closing = frappe.get_doc(self.preview())
		closing.flags.ignore_permissions = True
		closing.insert()
		self.spend()
		closing.submit()
		self.assertEqual(self.expected(closing), 170000)
		self.assertEqual(closing.spend_entries[0].amount, 30000)

	def test_paid_pi_counts_once(self):
		self.pi_spend()
		from pos_next.services.spend_shift import shift_spend

		self.assertEqual(shift_spend(self.shift.name)[0]["amount"], 12345)
		self.assertEqual(self.expected(self.preview()), 187655)


class TestSpendClosingRace(SpendTransactionTestCase):
	expected = TestSpendShift.expected

	# These tests need committed fixtures visible to both real connections.
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.db.rollback()
		frappe.db.delete("Mode of Payment", {"name": cls.mop})
		for company in (cls.a, cls.b, cls.group):
			frappe.db.delete("Expense Claim Account", {"company": company})
			frappe.delete_doc("Company", company, force=True, ignore_permissions=True)
		frappe.db.commit()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.db.delete("GL Entry", {"company": self.a})
		for doctype, filters in (
			("Journal Entry", {"company": self.a}),
			("POS Closing Shift", {"pos_opening_shift": self.shift.name}),
			("POS Opening Shift", {"name": self.shift.name}),
			("POS Settings", {"name": self.settings.name}),
			("POS Profile", {"name": self.profile.name}),
			("POS Spend Template", {"name": ["in", [self.je_template.name, self.pi_template.name]]}),
			("Item", {"name": self.item.name}),
			("Supplier", {"name": self.supplier.name}),
		):
			for name in frappe.get_all(doctype, filters=filters, pluck="name"):
				for field in frappe.get_meta(doctype).get_table_fields():
					frappe.db.delete(field.options, {"parent": name, "parenttype": doctype})
				frappe.db.delete(doctype, {"name": name})
		frappe.db.commit()
		frappe.clear_cache()

	def race(self, close_first, desk=False, cancel=False):
		import time

		from pos_next.api.spend import cancel_spend, submit_spend
		from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import _submit_closing

		spend = self.spend() if cancel else None
		frappe.db.commit()
		site, sites_path = frappe.local.site, frappe.local.sites_path
		payload = self.payload()
		preview = TestSpendShift.preview(self) if desk else None
		ready = threading.Event()
		connection = []

		def contender():
			frappe.init(site=site, sites_path=sites_path)
			frappe.connect()
			frappe.flags.in_test = True
			try:
				frappe.db.sql("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
				connection.append(frappe.db.sql("SELECT CONNECTION_ID()")[0][0])
				# Pin a snapshot before the winning transaction creates its voucher/closing.
				frappe.db.sql("SELECT name FROM `tabJournal Entry` LIMIT 1")
				ready.set()
				if close_first:
					try:
						if cancel:
							cancel_spend(spend["doctype"], spend["name"], "Wrong expense")
						else:
							submit_spend(payload)
					except frappe.ValidationError as error:
						if cancel:
							self.assertIn("closed shift", str(error))
							self.assertEqual(frappe.db.sql("SELECT @@innodb_snapshot_isolation")[0][0], 1)
						return "refused"
					return "accepted"
				if desk:
					closing = frappe.get_doc(preview)
					closing.insert().submit()
				else:
					name = _submit_closing(self.shift.name, auto_balance=True)
					closing = frappe.get_doc("POS Closing Shift", name)
				self.assertEqual(frappe.db.sql("SELECT @@innodb_snapshot_isolation")[0][0], 1)
				return self.expected(closing), [r.amount for r in closing.spend_entries]
			finally:
				frappe.db.rollback()
				frappe.destroy()

		frappe.db.get_value("POS Opening Shift", self.shift.name, "name", for_update=True)
		if close_first:
			self.close()
		with ThreadPoolExecutor(max_workers=1) as pool:
			future = pool.submit(contender)
			try:
				self.assertTrue(ready.wait(15), "second connection did not start")
				deadline = time.monotonic() + 15
				while not any(
					r.Id == connection[0] and "FOR UPDATE" in (r.Info or "").upper()
					for r in frappe.db.sql("SHOW FULL PROCESSLIST", as_dict=True)
				):
					if future.done():
						self.fail(f"contender did not wait for the opening lock: {future.result()}")
					self.assertLess(time.monotonic(), deadline, "contender did not reach the row lock")
					threading.Event().wait(0.02)
				if not close_first:
					if cancel:
						cancel_spend(spend["doctype"], spend["name"], "Wrong expense")
					else:
						self.spend()
				frappe.db.commit()
				return future.result(timeout=30)
			finally:
				frappe.db.rollback()

	def test_closing_committed_while_cancel_waits_refuses_with_closed_shift_message(self):
		self.assertEqual(self.race(close_first=True, cancel=True), "refused")
		self.assertEqual(
			frappe.db.get_value("Journal Entry", {"posa_pos_opening_shift": self.shift.name}, "docstatus"),
			1,
		)

	def test_cancel_committed_while_closing_waits_is_excluded(self):
		self.assertEqual(self.race(close_first=False, cancel=True), (200000, []))

	def test_spend_committed_while_closing_waits_is_included(self):
		self.assertEqual(self.race(close_first=False), (170000, [30000]))

	def test_desk_closing_includes_spend_committed_while_waiting(self):
		self.assertEqual(self.race(close_first=False, desk=True), (170000, [30000]))

	def test_closing_committed_while_spend_waits_refuses_spend(self):
		self.assertEqual(self.race(close_first=True), "refused")
		self.assertFalse(frappe.db.count("Journal Entry", {"posa_pos_opening_shift": self.shift.name}))
