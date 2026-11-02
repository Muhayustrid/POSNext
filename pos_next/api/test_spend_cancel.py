import frappe

from pos_next.tests.spend_transaction_helpers import SpendTransactionTestCase


class TestSpendCancel(SpendTransactionTestCase):
	def cancel(self, result, reason="Wrong expense"):
		from pos_next.api.spend import cancel_spend

		return cancel_spend(result["doctype"], result["name"], reason)

	def cashier_spend(self):
		user = self.user()
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", user)
		frappe.set_user(user)
		return self.spend()

	def test_cancel_in_open_shift_reverses_gl_and_drops_from_closing(self):
		from pos_next.pos_next.doctype.pos_closing_shift.pos_closing_shift import (
			make_closing_shift_from_opening,
		)

		result = self.cashier_spend()
		self.assertFalse(frappe.has_permission("Journal Entry", "cancel"))
		cancelled = self.cancel(result)
		self.assertEqual(cancelled["name"], result["name"])
		self.assertEqual(frappe.db.get_value(result["doctype"], result["name"], "docstatus"), 2)
		self.assertEqual(self.gl(result), {})
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": result["doctype"],
					"reference_name": result["name"],
					"content": "Wrong expense",
				},
			)
		)
		frappe.set_user("Administrator")
		preview = make_closing_shift_from_opening(self.shift.as_json())
		self.assertFalse(preview["spend_entries"])
		cash = next(r for r in preview["payment_reconciliation"] if r["mode_of_payment"] == self.mop)
		self.assertEqual(cash["expected_amount"], 200000)

	def test_cancel_pi_in_open_shift(self):
		result = self.pi_spend()
		self.cancel(result)
		self.assertEqual(frappe.db.get_value("Purchase Invoice", result["name"], "status"), "Cancelled")
		self.assertEqual(self.gl(result), {})

	def test_cancel_after_closing_blocked_in_api(self):
		result = self.spend()
		closing = self.close()
		with self.assertRaisesRegex(frappe.ValidationError, "closed shift"):
			self.cancel(result)
		self.assertEqual(frappe.db.get_value(result["doctype"], result["name"], "docstatus"), 1)
		self.assertEqual(frappe.get_doc("POS Closing Shift", closing.name).spend_entries[0].amount, 30000)

	def test_cancel_after_closing_blocked_from_desk(self):
		results = [self.spend(), self.pi_spend()]
		self.close()
		for result in results:
			with self.subTest(doctype=result["doctype"]):
				doc = frappe.get_doc(result["doctype"], result["name"])
				with self.assertRaisesRegex(frappe.ValidationError, "closed shift"):
					doc.cancel()

	def test_unstamped_spend_cancel_allowed(self):
		self.non_shift()
		result = self.spend()
		self.close()
		self.cancel(result)
		self.assertEqual(self.gl(result), {})

	def test_native_unstamped_cancel_allowed(self):
		result = self.spend()
		frappe.db.set_value(
			result["doctype"], result["name"], {"posa_spend_template": None, "posa_pos_opening_shift": None}
		)
		self.close()
		frappe.get_doc(result["doctype"], result["name"]).cancel()
		self.assertEqual(self.gl(result), {})

	def test_other_cashier_cannot_cancel(self):
		result = self.spend()
		other = self.user()
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", other)
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			self.cancel(result)

	def test_reason_required(self):
		result = self.spend()
		for reason in (None, "", "  "):
			with self.subTest(reason=reason), self.assertRaises(frappe.ValidationError):
				self.cancel(result, reason)
		self.assertEqual(frappe.db.get_value(result["doctype"], result["name"], "docstatus"), 1)

	def test_manager_scope_is_required(self):
		result = self.spend()
		manager = self.user("POSNext Manager")
		permission = frappe.get_doc(
			{"doctype": "User Permission", "user": manager, "allow": "Company", "for_value": self.b}
		).insert()
		frappe.set_user(manager)
		with self.assertRaises(frappe.PermissionError):
			self.cancel(result)
		frappe.set_user("Administrator")
		permission.for_value = self.a
		permission.save()
		frappe.set_user(manager)
		self.cancel(result)
		self.assertEqual(self.gl(result), {})

	def test_cashier_cannot_cancel_non_shift_spend(self):
		self.non_shift()
		result = self.cashier_spend()
		with self.assertRaises(frappe.PermissionError):
			self.cancel(result)

	def test_only_submitted_spend_documents_can_be_cancelled(self):
		result = self.spend()
		with self.assertRaises(frappe.ValidationError):
			self.cancel({"doctype": "POS Opening Shift", "name": self.shift.name})
		frappe.db.set_value(result["doctype"], result["name"], "posa_spend_template", None)
		with self.assertRaises(frappe.ValidationError):
			self.cancel(result)
