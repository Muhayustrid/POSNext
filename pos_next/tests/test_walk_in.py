"""Integration tests for POS walk-in customer server validation.

Tests validate_walk_in_customer_name function and doc_event hook.

Port notes (vs selling_additional's test_walk_in.py):
- POS Invoice is retired in pos_next (D9); every case now builds a Sales Invoice.
- The walk-in name field is `buyer_name` (a Sales Invoice custom field installed by
  pos_next/install.py), not `custom_walk_in_customer_name`.
- The source guarded the validator behind ERPNext's `is_created_using_pos` flag. pos_next
  never sets that flag and the validator is self-gating on `buyer_name`, so the guard was
  dropped: validation now FIRES on plain Sales Invoices. The source's
  "ordinary Sales Invoice bypasses validation" case is therefore retargeted to assert the
  opposite contract (fires on a plain invoice), per the gate-audit fix.
"""

import frappe
from frappe.tests import IntegrationTestCase

from pos_next.tests import helpers
from pos_next.walk_in import validate_walk_in_customer_name


class TestWalkInCustomerValidation(IntegrationTestCase):
	def setUp(self):
		super().setUp()
		self.company = helpers.get_default_company()
		self.currency = helpers.get_default_currency(self.company)
		self.uom = helpers.base_uom()
		self.item = helpers.make_test_item("walkin1", self.uom)
		self.warehouse = helpers.make_test_warehouse("walkin1", self.company)
		self.customer = self._make_customer("walkin1")

	def _make_customer(self, suffix):
		name = f"_Test POS Next Walk-in Customer {suffix}"
		if frappe.db.exists("Customer", name):
			return name
		customer_group = (
			frappe.db.get_value("Customer Group", {"is_group": 0}, "name", order_by="creation asc")
			or "All Customer Groups"
		)
		return (
			frappe.get_doc(
				{
					"doctype": "Customer",
					"customer_name": name,
					"customer_group": customer_group,
					"territory": "All Territories",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def _make_sales_invoice(self, **overrides):
		payload = {
			"doctype": "Sales Invoice",
			"company": self.company,
			"customer": self.customer,
			"currency": self.currency,
			"items": [{"item_code": self.item, "qty": 1, "rate": 100}],
		}
		payload.update(overrides)
		return frappe.get_doc(payload)

	def test_empty_or_none_walk_in_name_passes(self):
		"""Empty or None walk-in customer name passes (the validator is self-gating)."""
		pos_profile = helpers.make_test_pos_profile("walkin_empty", self.company, self.warehouse)
		frappe.db.set_value("POS Profile", pos_profile, "customer", self.customer)

		inv1 = self._make_sales_invoice(
			pos_profile=pos_profile,
			buyer_name=None,
		)
		validate_walk_in_customer_name(inv1)

		inv2 = self._make_sales_invoice(
			pos_profile=pos_profile,
			buyer_name="   ",
		)
		validate_walk_in_customer_name(inv2)

		sinv = self._make_sales_invoice(
			pos_profile=pos_profile,
			buyer_name="",
		)
		validate_walk_in_customer_name(sinv)

	def test_non_empty_name_requires_existing_profile(self):
		"""A non-empty walk-in name with missing or nonexistent pos_profile raises ValidationError."""
		inv_no_profile = self._make_sales_invoice(
			pos_profile=None,
			buyer_name="John Doe",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"requires an existing POS Profile"):
			validate_walk_in_customer_name(inv_no_profile)

		inv_fake_profile = self._make_sales_invoice(
			pos_profile="Nonexistent Profile 123",
			buyer_name="John Doe",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"requires an existing POS Profile"):
			validate_walk_in_customer_name(inv_fake_profile)

	def test_disabled_profile_raises(self):
		"""A disabled POS Profile raises ValidationError when walk-in name is set."""
		pos_profile = helpers.make_test_pos_profile("walkin_disabled", self.company, self.warehouse)
		frappe.db.set_value("POS Profile", pos_profile, "customer", self.customer)
		frappe.db.set_value("POS Profile", pos_profile, "disabled", 1)

		inv = self._make_sales_invoice(
			pos_profile=pos_profile,
			buyer_name="Jane Doe",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"is disabled"):
			validate_walk_in_customer_name(inv)

	def test_profile_without_customer_raises(self):
		"""A POS Profile without a default customer raises ValidationError."""
		pos_profile = helpers.make_test_pos_profile("walkin_nocust", self.company, self.warehouse)
		frappe.db.set_value("POS Profile", pos_profile, "customer", None)

		inv = self._make_sales_invoice(
			pos_profile=pos_profile,
			buyer_name="Jane Doe",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"has no default Customer"):
			validate_walk_in_customer_name(inv)

	def test_missing_or_disabled_customer_raises(self):
		"""A missing or disabled default Customer on POS Profile raises ValidationError."""
		pos_profile = helpers.make_test_pos_profile("walkin_custdis", self.company, self.warehouse)
		frappe.db.set_value("POS Profile", pos_profile, "customer", "Nonexistent Customer XYZ")

		inv_fake_cust = self._make_sales_invoice(
			pos_profile=pos_profile,
			buyer_name="Jane Doe",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"does not exist"):
			validate_walk_in_customer_name(inv_fake_cust)

		frappe.db.set_value("POS Profile", pos_profile, "customer", self.customer)
		# IntegrationTestCase has NO per-test rollback (frappe/tests/classes/integration_test_case.py
		# registers only addClassCleanup(_rollback_db)), so this disable would otherwise leak into
		# every later test in this class and make their explicit re-enables load-bearing instead of
		# defensive.
		self.addCleanup(frappe.db.set_value, "Customer", self.customer, "disabled", 0)
		frappe.db.set_value("Customer", self.customer, "disabled", 1)

		inv_dis_cust = self._make_sales_invoice(
			pos_profile=pos_profile,
			buyer_name="Jane Doe",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"is disabled"):
			validate_walk_in_customer_name(inv_dis_cust)

	def test_mismatched_invoice_customer_raises(self):
		"""Invoice customer != profile default customer raises ValidationError."""
		pos_profile = helpers.make_test_pos_profile("walkin_mismatch", self.company, self.warehouse)
		frappe.db.set_value("POS Profile", pos_profile, "customer", self.customer)
		other_cust = self._make_customer("walkin_other")

		inv = self._make_sales_invoice(
			pos_profile=pos_profile,
			customer=other_cust,
			buyer_name="Jane Doe",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"applies only to default Customer"):
			validate_walk_in_customer_name(inv)

	def test_valid_walk_in_passes_and_preserves_field_value(self):
		"""An enabled profile, enabled default Customer, and matching invoice Customer pass and preserve walk-in name."""
		pos_profile = helpers.make_test_pos_profile("walkin_valid", self.company, self.warehouse)
		frappe.db.set_value("POS Profile", pos_profile, "customer", self.customer)
		frappe.db.set_value("POS Profile", pos_profile, "disabled", 0)
		frappe.db.set_value("Customer", self.customer, "disabled", 0)

		inv = self._make_sales_invoice(
			pos_profile=pos_profile,
			customer=self.customer,
			buyer_name=" Budi Santoso ",
		)
		validate_walk_in_customer_name(inv)
		self.assertEqual(inv.buyer_name, " Budi Santoso ")

	def test_pos_sales_invoice_follows_rules(self):
		"""A POS-context Sales Invoice (valid profile + matching customer) passes; without a profile it raises.

		Retargeted from the source's ``is_created_using_pos = 1`` case: pos_next's validator has no
		such guard, so the same rules now apply to every Sales Invoice with a walk-in name.
		"""
		pos_profile = helpers.make_test_pos_profile("walkin_sinv_pos", self.company, self.warehouse)
		frappe.db.set_value("POS Profile", pos_profile, "customer", self.customer)
		frappe.db.set_value("POS Profile", pos_profile, "disabled", 0)
		frappe.db.set_value("Customer", self.customer, "disabled", 0)

		sinv_valid = self._make_sales_invoice(
			pos_profile=pos_profile,
			customer=self.customer,
			buyer_name="Budi Santoso",
		)
		validate_walk_in_customer_name(sinv_valid)

		sinv_invalid = self._make_sales_invoice(
			pos_profile=None,
			customer=self.customer,
			buyer_name="Budi Santoso",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"requires an existing POS Profile"):
			validate_walk_in_customer_name(sinv_invalid)

	def test_validation_fires_on_plain_sales_invoice(self):
		"""A plain (non-POS) Sales Invoice with a walk-in name is validated, not bypassed.

		Retargeted from the source's "ordinary Sales Invoice bypasses validation" case. The source
		app skipped validation when ERPNext's Desk POS left ``is_created_using_pos`` unset; in
		pos_next no code sets that flag, so keeping the guard would silently disable the validator.
		Dropped guard + self-gating body means: non-empty buyer_name without a valid profile throws.
		"""
		sinv = self._make_sales_invoice(
			pos_profile=None,
			buyer_name="Any Name Without Profile",
		)
		with self.assertRaisesRegex(frappe.ValidationError, r"requires an existing POS Profile"):
			validate_walk_in_customer_name(sinv)

	def test_validation_is_wired_through_doc_events(self):
		"""Every other test calls the function directly; this one pins that Frappe will actually call it.

		Without this, the whole module could pass while `doc_events` pointed at a stale path or a
		typo'd attribute — the tests would exercise a boundary nothing ever invokes.

		Retargeted: pos_next hooks wire the walk-in handler under Sales Invoice only (POS Invoice is
		retired, D9), so only that doctype is asserted.
		"""
		composed = frappe.get_doc_hooks()
		for doctype in ("Sales Invoice",):
			handlers = composed.get(doctype, {}).get("validate") or []
			self.assertIn(
				"pos_next.walk_in.validate_walk_in_customer_name",
				handlers,
				msg=f"validate_walk_in_customer_name is not a composed {doctype}.validate handler: {handlers}",
			)

		resolved = frappe.get_attr("pos_next.walk_in.validate_walk_in_customer_name")
		self.assertIs(
			resolved,
			validate_walk_in_customer_name,
			msg="The hook path resolves to a different object than the function under test",
		)
