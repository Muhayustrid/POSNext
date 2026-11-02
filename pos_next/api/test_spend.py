import frappe

from pos_next.tests.spend_transaction_helpers import SpendTransactionTestCase


class TestSpend(SpendTransactionTestCase):
	def history(self, **values):
		from pos_next.api.spend import get_spend_history

		return get_spend_history(self.a, **values)

	def test_history_merges_pi_and_je_newest_first(self):
		je = self.spend(vendor_name="Electric shop", bill_no="EXP-001")
		pi = self.pi_spend(files=[{"file_name": "receipt.txt", "filedata": "dGVzdA=="}])
		rows = self.history()
		self.assertEqual(
			[(r["doctype"], r["name"]) for r in rows],
			[("Purchase Invoice", pi["name"]), ("Journal Entry", je["name"])],
		)
		self.assertEqual(rows[0]["amount"], 12345)
		self.assertEqual(rows[0]["status"], "Paid")
		attachment = rows[0]["attachments"][0]
		self.assertTrue(
			attachment["file_url"].startswith("/api/method/pos_next.api.spend.download_spend_attachment?")
		)
		self.assertEqual(frappe.db.get_value("File", attachment["name"], "attached_to_name"), pi["name"])
		self.assertEqual(rows[1]["template"], self.je_template.name)
		self.assertEqual(rows[1]["category"], "Test")
		self.assertEqual(rows[1]["amount"], 30000)
		self.assertEqual(rows[1]["status"], "Submitted")
		self.assertEqual(rows[1]["mode_of_payment"], self.mop)
		self.assertEqual(rows[1]["shift"], self.shift.name)
		self.assertEqual(rows[1]["vendor_name"], "Electric shop")
		self.assertEqual(rows[1]["bill_no"], "EXP-001")
		self.assertEqual(rows[1]["owner"], "Administrator")
		self.assertTrue(rows[1]["can_cancel"])

	def test_history_includes_payment_entry_without_cancel_action(self):
		from frappe.utils import nowdate

		payment = frappe.get_doc(
			{
				"doctype": "Payment Entry",
				"payment_type": "Pay",
				"company": self.a,
				"party_type": "Supplier",
				"party": self.supplier.name,
				"paid_from": self.cash,
				"paid_to": frappe.db.get_value("Company", self.a, "default_payable_account"),
				"paid_amount": 10000,
				"received_amount": 10000,
				"source_exchange_rate": 1,
				"target_exchange_rate": 1,
				"reference_no": "PE-001",
				"reference_date": nowdate(),
				"mode_of_payment": self.mop,
				"posa_spend_template": self.pi_template.name,
				"posa_pos_profile": self.profile.name,
				"posa_pos_opening_shift": self.shift.name,
				"posa_shift_account": self.cash,
			}
		).insert()
		payment.submit()
		row = self.history()[0]
		self.assertEqual(row["doctype"], "Payment Entry")
		self.assertEqual(row["amount"], 10000)
		self.assertEqual(row["bill_no"], "PE-001")
		self.assertEqual(row["status"], "Submitted")
		self.assertFalse(row["can_cancel"])
		self.close()
		with self.assertRaisesRegex(frappe.ValidationError, "closed shift"):
			payment.cancel()

	def test_history_scoped_to_company(self):
		self.spend()
		manager = self.user("POSNext Manager")
		frappe.get_doc(
			{"doctype": "User Permission", "user": manager, "allow": "Company", "for_value": self.b}
		).insert()
		frappe.set_user(manager)
		with self.assertRaises(frappe.PermissionError):
			self.history()
		from pos_next.api.spend import get_spend_history

		self.assertEqual(get_spend_history(self.b), [])

	def test_manager_downloads_cashier_attachment_only_in_permitted_company(self):
		from urllib.parse import parse_qs, urlsplit

		from frappe.utils.response import as_raw

		cashier = self.user()
		manager = self.user("POSNext Manager")
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", cashier)
		permission = frappe.get_doc(
			{"doctype": "User Permission", "user": manager, "allow": "Company", "for_value": self.a}
		).insert()
		frappe.set_user(cashier)
		for spend in (self.spend, self.pi_spend):
			spend(files=[{"file_name": "receipt.txt", "filedata": "dGVzdA=="}])
		frappe.set_user(manager)
		self.assertFalse(frappe.has_permission("Journal Entry", "read"))
		self.assertFalse(frappe.has_permission("Purchase Invoice", "read"))
		for row in self.history():
			attachment = row["attachments"][0]
			file = frappe.get_doc("File", attachment["name"])
			self.assertEqual(file.owner, cashier)
			self.assertTrue(file.is_private)
			self.assertFalse(file.is_downloadable())
			url = urlsplit(attachment["file_url"])
			self.assertEqual(url.path, "/api/method/pos_next.api.spend.download_spend_attachment")
			from pos_next.api.spend import download_spend_attachment

			download_spend_attachment(**{k: v[0] for k, v in parse_qs(url.query).items()})
			response = as_raw()
			self.assertEqual(response.data, b"test")
			self.assertEqual(response.mimetype, "text/plain")
			self.assertIn("attachment", response.headers["Content-Disposition"])
			frappe.local.response.pop("type", None)
		frappe.set_user("Administrator")
		permission.for_value = self.b
		permission.save()
		frappe.set_user(manager)
		with self.assertRaises(frappe.PermissionError):
			download_spend_attachment(file.name)
		frappe.set_user("Guest")
		with self.assertRaises((frappe.AuthenticationError, frappe.PermissionError)):
			download_spend_attachment(file.name)

	def test_download_refuses_non_spend_and_unattached_files(self):
		from pos_next.api.spend import download_spend_attachment

		result = self.spend(files=[{"file_name": "receipt.txt", "filedata": "dGVzdA=="}])
		file = self.history()[0]["attachments"][0]["name"]
		frappe.db.set_value("File", file, "attached_to_name", None)
		with self.assertRaises(frappe.PermissionError):
			download_spend_attachment(file)
		frappe.db.set_value("File", file, "attached_to_name", result["name"])
		frappe.db.set_value(result["doctype"], result["name"], "posa_spend_template", None)
		with self.assertRaises(frappe.PermissionError):
			download_spend_attachment(file)
		frappe.db.set_value("File", file, {"attached_to_doctype": None, "attached_to_name": None})
		with self.assertRaises(frappe.PermissionError):
			download_spend_attachment(file)

	def test_history_can_cancel_false_after_closing(self):
		self.spend()
		self.close()
		self.assertFalse(self.history()[0]["can_cancel"])

	def test_history_can_cancel_matches_owner_and_manager_rules(self):
		self.spend()
		cashier = self.user()
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", cashier)
		frappe.set_user(cashier)
		self.assertFalse(self.history()[0]["can_cancel"])
		mine = self.spend()
		self.assertEqual(self.history()[0]["name"], mine["name"])
		self.assertTrue(self.history()[0]["can_cancel"])

	def test_history_filters_status_dates_profile_and_global_limit(self):
		from frappe.utils import add_days, nowdate

		from pos_next.api.spend import cancel_spend

		je = self.spend()
		self.pi_spend()
		cancel_spend(je["doctype"], je["name"], "Incorrect")
		self.assertEqual(len(self.history(limit=1)), 1)
		self.assertEqual(self.history(status="Cancelled")[0]["name"], je["name"])
		self.assertFalse(self.history(status="Cancelled")[0]["can_cancel"])
		self.assertEqual(len(self.history(status="Paid")), 1)
		self.assertEqual(self.history(from_date=add_days(nowdate(), 1)), [])
		self.assertEqual(self.history(to_date=add_days(nowdate(), -1)), [])
		self.assertEqual(len(self.history(from_date=nowdate(), to_date=nowdate())), 2)
		other = frappe.copy_doc(self.profile)
		other.name = f"Spend Other {frappe.generate_hash(length=8)}"
		other.insert()
		self.assertEqual(self.history(pos_profile=other.name), [])
		self.assertEqual(len(self.history(pos_profile=self.profile.name)), 2)

	def test_history_keeps_disabled_templates_and_excludes_native_documents(self):
		self.spend()
		native = self.spend()
		frappe.db.set_value(native["doctype"], native["name"], "posa_spend_template", None)
		frappe.db.set_value("POS Spend Template", self.je_template.name, "enabled", 0)
		self.assertEqual(len(self.history()), 1)

	def test_history_input_validation_and_guest(self):
		for limit in (0, -1, "bad"):
			with self.subTest(limit=limit), self.assertRaises(frappe.ValidationError):
				self.history(limit=limit)
		with self.assertRaises(frappe.ValidationError):
			self.history(from_date="2026-11-03", to_date="2026-11-01")
		frappe.set_user("Guest")
		with self.assertRaises((frappe.AuthenticationError, frappe.PermissionError)):
			self.history()

	def test_je_posts_expected_gl(self):
		result = self.spend()
		self.assertEqual(self.gl(result), {self.expense[self.a]: 30000, self.cash: -30000})
		doc = frappe.get_doc(result["doctype"], result["name"])
		self.assertEqual(doc.posa_pos_opening_shift, self.shift.name)
		self.assertEqual(doc.posa_shift_account, self.cash)
		self.assertTrue(doc.posa_client_request_id)

	def test_pi_nonstock_paid_posts_expected_gl(self):
		result = self.pi_spend()
		self.assertEqual(self.gl(result), {self.expense[self.a]: 12345, self.cash: -12345})
		doc = frappe.get_doc(result["doctype"], result["name"])
		self.assertEqual(doc.status, "Paid")
		self.assertEqual(doc.outstanding_amount, 0)

	def test_pi_ignores_buying_pricing_rule(self):
		rule = frappe.get_doc(
			{
				"doctype": "Pricing Rule",
				"title": f"Spend rule {frappe.generate_hash(length=6)}",
				"apply_on": "Item Code",
				"items": [{"item_code": self.item.name}],
				"buying": 1,
				"selling": 0,
				"company": self.a,
				"rate_or_discount": "Rate",
				"rate": 9000,
			}
		).insert()
		self.addCleanup(frappe.delete_doc, "Pricing Rule", rule.name, force=1)
		result = self.pi_spend()
		doc = frappe.get_doc(result["doctype"], result["name"])
		self.assertEqual(doc.items[0].rate, 12345)
		self.assertEqual(doc.paid_amount, 12345)

	def test_paid_pi_not_rounded(self):
		result = self.pi_spend()
		doc = frappe.get_doc(result["doctype"], result["name"])
		self.assertEqual(doc.grand_total, 12345)
		self.assertEqual(doc.paid_amount, 12345)
		self.assertEqual(result["amount"], 12345)

	def test_non_shift_source_not_stamped(self):
		self.close()
		self.non_shift()
		result = self.spend()
		self.assertFalse(result["shift"])
		self.assertFalse(frappe.get_doc(result["doctype"], result["name"]).posa_pos_opening_shift)

	def test_shift_source_requires_open_shift(self):
		frappe.db.set_value("POS Opening Shift", self.shift.name, "docstatus", 2)
		with self.assertRaises(frappe.ValidationError):
			self.spend()

	def test_spend_after_closing_is_refused(self):
		self.close()
		with self.assertRaises(frappe.ValidationError):
			self.spend()

	def test_retry_returns_same_document(self):
		from pos_next.api.spend import submit_spend

		data = self.payload()
		first = submit_spend(data)
		second = submit_spend(data)
		self.assertEqual(first["name"], second["name"])
		self.assertTrue(second["duplicate"])
		self.assertEqual(
			frappe.db.count("Journal Entry", {"posa_client_request_id": data["client_request_id"]}), 1
		)

	def test_duplicate_request_id_hits_unique_index(self):
		from pos_next.api.spend import submit_spend

		result = self.spend()
		doc = frappe.get_doc(result["doctype"], result["name"])
		self.assertEqual(
			submit_spend(self.payload(client_request_id=doc.posa_client_request_id))["name"], doc.name
		)
		copy = frappe.copy_doc(doc)
		copy.posa_client_request_id = doc.posa_client_request_id
		with self.assertRaises((frappe.DuplicateEntryError, frappe.UniqueValidationError)):
			copy.insert()

	def test_cashier_other_company_refused(self):
		frappe.set_user(self.user())
		with self.assertRaises(frappe.PermissionError):
			self.spend(company=self.b)

	def test_cashier_cannot_switch_to_same_company_profile_without_shift_sources(self):
		from pos_next.api.spend import get_spend_templates

		other = frappe.copy_doc(self.profile)
		other.name = f"Spend Other {frappe.generate_hash(length=8)}"
		other.insert()
		user = self.user()
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", user)
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			self.spend(pos_profile=other.name)
		with self.assertRaises(frappe.PermissionError):
			get_spend_templates(self.a, other.name)
		self.assertEqual(self.spend()["shift"], self.shift.name)

	def test_manager_can_spend_without_shift_from_non_shift_source(self):
		user = self.user("POSNext Manager")
		frappe.get_doc(
			{"doctype": "User Permission", "user": user, "allow": "Company", "for_value": self.a}
		).insert()
		self.non_shift()
		frappe.set_user(user)
		self.assertIsNone(self.spend()["shift"])

	def test_role_not_in_template_refused(self):
		self.je_template.append("roles", {"role": "Accounts Manager"})
		self.je_template.save()
		user = self.user()
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", user)
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			self.spend()

	def test_cashier_can_submit_without_native_doc_permissions(self):
		user = self.user()
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", user)
		frappe.set_user(user)
		self.assertFalse(frappe.has_permission("Journal Entry", "create"))
		result = self.spend(files=[{"file_name": "receipt.txt", "filedata": "dGVzdA=="}])
		self.assertEqual(self.gl(result), {self.expense[self.a]: 30000, self.cash: -30000})
		self.assertEqual(
			frappe.db.count(
				"File",
				{
					"attached_to_doctype": result["doctype"],
					"attached_to_name": result["name"],
					"is_private": 1,
				},
			),
			1,
		)

	def test_request_id_cannot_disclose_another_users_document(self):
		from pos_next.api.spend import submit_spend

		data = self.payload()
		submit_spend(data)
		user = self.user()
		frappe.db.set_value("POS Opening Shift", self.shift.name, "user", user)
		frappe.set_user(user)
		with self.assertRaises(frappe.PermissionError):
			submit_spend(data)

	def test_bank_entry_has_native_reference(self):
		account = (
			frappe.get_doc(
				{
					"doctype": "Account",
					"account_name": f"Spend Bank {frappe.generate_hash(length=6)}",
					"company": self.a,
					"parent_account": frappe.db.get_value("Account", self.cash, "parent_account"),
					"account_type": "Bank",
					"account_currency": "IDR",
				}
			)
			.insert()
			.name
		)
		frappe.db.set_value(
			"Mode of Payment Account", {"parent": self.mop, "company": self.a}, "default_account", account
		)
		result = self.spend()
		doc = frappe.get_doc(result["doctype"], result["name"])
		self.assertEqual(doc.voucher_type, "Bank Entry")
		self.assertTrue(doc.cheque_no)
		self.assertTrue(doc.cheque_date)
		self.assertEqual(self.gl(result), {self.expense[self.a]: 30000, account: -30000})

	def test_unready_company_refused_with_reason(self):
		frappe.db.set_value("Account", self.expense[self.a], "disabled", 1)
		with self.assertRaisesRegex(frappe.ValidationError, "account|Account"):
			self.spend()

	def test_stage1_rejects_update_stock_and_pay_later(self):
		for field, value in (("update_stock", 1), ("payment_timing", "Pay Later"), ("apply_purchase_tax", 1)):
			with self.subTest(field=field):
				frappe.db.set_value("POS Spend Template", self.pi_template.name, field, value)
				with self.assertRaises(frappe.ValidationError):
					self.pi_spend()
				frappe.db.set_value(
					"POS Spend Template",
					self.pi_template.name,
					field,
					0 if field != "payment_timing" else "Pay Now",
				)

	def test_required_attachment_enforced(self):
		frappe.db.set_value("POS Spend Template", self.je_template.name, "requires_attachment", 1)
		with self.assertRaises(frappe.ValidationError):
			self.spend()

	def test_get_spend_templates(self):
		from pos_next.api.spend import get_spend_templates

		rows = get_spend_templates(self.a, self.profile.name)
		row = next(r for r in rows if r["name"] == self.je_template.name)
		self.assertTrue(row["payment_sources"][0]["is_shift_source"])
		pi = next(r for r in rows if r["name"] == self.pi_template.name)
		self.assertIn(self.item.name, pi["items"])

	def test_invalid_amounts_and_unallowed_items(self):
		for amount in (0, -10, "nan", "inf"):
			with self.subTest(amount=amount), self.assertRaises(frappe.ValidationError):
				self.spend(lines=[{"description": "Bad", "amount": amount}])
		with self.assertRaises(frappe.ValidationError):
			self.spend(
				template=self.pi_template.name, lines=[{"item_code": "not allowed", "qty": 1, "rate": 1}]
			)

	def test_missing_profile_cannot_bypass_drawer_stamp(self):
		with self.assertRaises(frappe.ValidationError):
			self.spend(pos_profile=None)

	def test_supplier_vendor_limit_and_profile_guards(self):
		with self.assertRaises(frappe.ValidationError):
			self.pi_spend(supplier="not allowed")
		with self.assertRaises(frappe.ValidationError):
			self.spend(pos_profile="not allowed")
		frappe.db.set_value("POS Spend Template", self.je_template.name, "require_vendor_name", 1)
		with self.assertRaises(frappe.ValidationError):
			self.spend()
		frappe.db.set_value(
			"POS Spend Template", self.je_template.name, {"require_vendor_name": 0, "max_amount": 100}
		)
		with self.assertRaises(frappe.ValidationError):
			self.spend()
