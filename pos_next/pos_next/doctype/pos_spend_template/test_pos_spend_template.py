import frappe

from pos_next.tests.spend_helpers import SpendTestCase


class TestSpendTemplate(SpendTestCase):
	def test_je_template_rejects_items_tax_and_pay_later(self):
		for values in (
			{"items": [{"item_group": "All Item Groups"}]},
			{"apply_purchase_tax": 1},
			{"payment_timing": "Pay Later"},
			{"debit_account_strategy": "Item Default"},
			{"update_stock": 1},
		):
			with self.subTest(values=values), self.assertRaises(frappe.ValidationError):
				self.template(**values).validate()

	def test_reference_account_must_belong_to_reference_company(self):
		with self.assertRaises(frappe.ValidationError):
			self.template(reference_debit_account=self.expense[self.a]).validate()

	def test_reference_account_type_rejected(self):
		account = self.expense[self.reference]
		for values in [
			*({"account_type": kind} for kind in ("Stock", "Payable", "Receivable", "Cash", "Bank", "Tax")),
			{"is_group": 1},
			{"root_type": "Income"},
		]:
			with self.subTest(values=values):
				before = frappe.db.get_value("Account", account, list(values), as_dict=True)
				frappe.db.set_value("Account", account, values)
				with self.assertRaises(frappe.ValidationError):
					self.template().validate()
				frappe.db.set_value("Account", account, before)

	def test_template_account_strategy_requires_reference_or_overrides(self):
		with self.assertRaises(frappe.ValidationError):
			self.template(reference_debit_account=None).validate()
		self.template(
			reference_debit_account=None,
			companies=[{"company": self.a, "debit_account": self.expense[self.a]}],
		).validate()

	def test_override_account_must_belong_to_row_company(self):
		with self.assertRaises(frappe.ValidationError):
			self.template(companies=[{"company": self.a, "debit_account": self.expense[self.b]}]).validate()

	def test_override_cost_center_must_belong_to_row_company(self):
		cc = frappe.db.get_value("Cost Center", {"company": self.b, "is_group": 0}, "name")
		with self.assertRaises(frappe.ValidationError):
			self.template(companies=[{"company": self.a, "cost_center": cc}]).validate()

	def test_override_on_group_row_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self.template(
				companies=[{"company": self.group, "debit_account": self.expense[self.group]}]
			).validate()

	def test_group_company_row_covers_descendants(self):
		from pos_next.pos_next.doctype.pos_spend_template.pos_spend_template import template_companies

		self.assertEqual(
			template_companies(self.template(companies=[{"company": self.group}, {"company": self.a}])),
			sorted([self.a, self.b]),
		)

	def test_payment_sources_required_and_one_default(self):
		for rows in ([], [{"mode_of_payment": self.mop, "is_default": 1}] * 2):
			with self.assertRaises(frappe.ValidationError):
				self.template(payment_sources=rows).validate()

	def test_reference_company_defaults_from_global_setting(self):
		frappe.db.set_single_value("POS Next Global Settings", "spend_reference_company", self.reference)
		doc = self.template(reference_company=None)
		doc.validate()
		self.assertEqual(doc.reference_company, self.reference)

	def test_pos_settings_shift_source_must_be_profile_payment(self):
		from pos_next.tests.price_group_helpers import make_test_pos_profile, make_test_warehouse

		profile = make_test_pos_profile(
			f"Spend {frappe.generate_hash(length=6)}",
			self.a,
			make_test_warehouse("spend", self.a),
			payments=[{"mode_of_payment": self.mop, "default": 1}],
		)
		settings = frappe.get_doc(
			{
				"doctype": "POS Settings",
				"pos_profile": profile,
				"posa_shift_spend_sources": [{"mode_of_payment": self.mop}],
			}
		)
		settings.validate()
		other = (
			frappe.get_doc(
				{
					"doctype": "Mode of Payment",
					"mode_of_payment": f"Other {frappe.generate_hash(length=6)}",
					"type": "Cash",
				}
			)
			.insert()
			.name
		)
		settings.posa_shift_spend_sources[0].mode_of_payment = other
		with self.assertRaises(frappe.ValidationError):
			settings.validate()
