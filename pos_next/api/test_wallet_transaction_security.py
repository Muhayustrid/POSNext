# Copyright (c) 2024, BrainWise and contributors
# For license information, please see license.txt

"""SEC-01 regression tests: wallet credit helpers must stay internal.

create_wallet_credit / credit_loyalty_points_to_wallet used to be
@frappe.whitelist() endpoints that any logged-in user could hit to mint
arbitrary wallet balance. They are internal now; the gated HTTP entry
point is pos_next.api.wallet.create_manual_wallet_credit.
"""

import unittest

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_years, flt, today

from pos_next.api.wallet import get_or_create_wallet
from pos_next.pos_next.doctype.wallet_transaction.wallet_transaction import (
	create_wallet_credit,
	credit_loyalty_points_to_wallet,
)
from pos_next.tests.price_group_helpers import get_default_company

CUSTOMER = "_SEC01 Wallet Test Customer"
LOYALTY_PROGRAM = "_SEC01 Test Loyalty Program"


class TestWalletTransactionEndpoints(IntegrationTestCase):
	"""Wallet credit helpers: not HTTP-exposed, conversion factor server-side."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()

		if not frappe.db.exists("Customer", CUSTOMER):
			customer_group = (
				frappe.db.get_value("Customer Group", {"is_group": 0}, "name", order_by="creation asc")
				or "All Customer Groups"
			)
			doc = frappe.get_doc(
				{"doctype": "Customer", "customer_name": CUSTOMER, "customer_group": customer_group}
			).insert(ignore_permissions=True)
			# sites may re-code names via naming series — always use doc.name
			cls.customer = doc.name
		else:
			cls.customer = frappe.db.get_value("Customer", {"customer_name": CUSTOMER}, "name") or CUSTOMER

		cls.loyalty_program = cls._make_loyalty_program()
		frappe.db.set_value("Customer", cls.customer, "loyalty_program", cls.loyalty_program)

		cls.wallet = get_or_create_wallet(cls.customer, cls.company, force_create=True)
		if not cls.wallet:
			raise unittest.SkipTest("no wallet account configured on company")

	@classmethod
	def _make_loyalty_program(cls):
		expense_account = frappe.get_cached_value("Company", cls.company, "default_expense_account")
		if not expense_account:
			raise unittest.SkipTest("no default expense account on company")

		if not frappe.db.exists("Loyalty Program", LOYALTY_PROGRAM):
			frappe.get_doc(
				{
					"doctype": "Loyalty Program",
					"loyalty_program_name": LOYALTY_PROGRAM,
					"loyalty_program_type": "Single Tier Program",
					"from_date": add_years(today(), -1),
					"company": cls.company,
					"expense_account": expense_account,
					"conversion_factor": 0.5,
					"collection_rules": [{"tier_name": "Silver", "min_spent": 0, "collection_factor": 100}],
				}
			).insert(ignore_permissions=True)
		return LOYALTY_PROGRAM

	def test_credit_helpers_not_whitelisted(self):
		"""The two minting helpers must not be reachable as HTTP endpoints."""
		self.assertNotIn(create_wallet_credit, frappe.whitelisted)
		self.assertNotIn(credit_loyalty_points_to_wallet, frappe.whitelisted)
		# is_whitelisted is the exact gate frappe's API handler applies before
		# dispatch — an HTTP call now dies with PermissionError (403).
		for fn in (create_wallet_credit, credit_loyalty_points_to_wallet):
			with self.assertRaises(frappe.PermissionError):
				frappe.is_whitelisted(fn)

	def test_conversion_factor_param_removed_from_signature(self):
		"""No client-supplied conversion factor can win — the param is gone."""
		import inspect

		self.assertNotIn(
			"conversion_factor", inspect.signature(credit_loyalty_points_to_wallet).parameters
		)

	def test_create_wallet_credit_internal_path_still_works(self):
		"""Direct (gated) callers still produce a submitted Wallet Transaction."""
		wallet_name = self.wallet.name if hasattr(self.wallet, "name") else self.wallet["name"]
		doc = create_wallet_credit(
			wallet=wallet_name,
			amount=100,
			source_type="Manual Adjustment",
			remarks="SEC-01 internal path test",
			submit=True,
		)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.transaction_type, "Credit")
		self.assertEqual(flt(doc.amount), 100)

	def test_loyalty_credit_uses_program_conversion_factor(self):
		"""Amount = points x program factor (0.5), never the 1.0 client default."""
		doc = credit_loyalty_points_to_wallet(self.customer, self.company, 100)
		self.assertIsNotNone(doc)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.source_type, "Loyalty Program")
		self.assertEqual(flt(doc.amount), 50)
