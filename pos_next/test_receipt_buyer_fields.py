# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

"""Integration tests for buyer_name and queue_number in the POS Next Receipt
print format (task 2.13).

Each test submits a minimal Sales Invoice carrying (or not carrying) the two
Custom Fields, renders the receipt print format's Jinja directly, and asserts
the presence or absence of a stable CSS class wrapper introduced by the
print format:

    buyer-name   — wrapper div around the buyer name line
    queue-number — wrapper div around the queue-number line

When the field is absent or zero, the Jinja guard suppresses the whole <div>,
so neither marker class should appear in the rendered HTML.

Rendering strategy note: frappe.get_print wraps the output in
frappe/www/printview.html which calls include_style on a bundled asset.
The asset-bundle map is not loaded in this bare unittest runner (it only
initialises during web-app boot), so get_print raises in the wrapper before
ever reaching the receipt body. We therefore render the format's stored
`html` field directly via frappe.render_template, which exercises exactly
the buyer_name / queue_number guards this task adds.

All test data is prefixed with _PNXT_TEST_ so it can be cleaned up safely.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import flt, nowdate

ITEM_CODE = "_PNXT_TEST_ITEM_A"
ITEM_PRICE = 50.0
CUSTOMER = "_PNXT_TEST_CUSTOMER"
POS_PROFILE_PREFIX = "_PNXT_TEST_RECEIPT_PROFILE_"


# ---------------------------------------------------------------------------
# Fixture helpers (copied and stripped down from test_promotions.py)
# ---------------------------------------------------------------------------


def _resolve_company():
	if frappe.db.exists("Company", "_Test Company"):
		return "_Test Company"
	default = frappe.defaults.get_global_default("company")
	if default:
		return default
	return frappe.db.get_value("Company", {"name": ["!=", ""]}, "name")


def _resolve_warehouse(company):
	if company == "_Test Company" and frappe.db.exists("Warehouse", "_Test Warehouse - _TC"):
		return "_Test Warehouse - _TC"
	return frappe.db.get_value(
		"Warehouse",
		{"company": company, "is_group": 0, "disabled": 0},
		"name",
		order_by="creation asc",
	)


def _resolve_price_list(company):
	if frappe.db.exists("Price List", "Standard Selling"):
		return "Standard Selling"
	return frappe.db.get_value("Price List", {"selling": 1, "enabled": 1}, "name")


def _resolve_item_group():
	for candidate in ("_Test Item Group", "Products"):
		if frappe.db.exists("Item Group", candidate):
			ig = frappe.get_cached_doc("Item Group", candidate)
			if not ig.is_group:
				return candidate
	return frappe.db.get_value("Item Group", {"is_group": 0}, "name", order_by="creation asc")


def _resolve_customer_group():
	if frappe.db.exists("Customer Group", "_Test Customer Group"):
		return "_Test Customer Group"
	return frappe.db.get_value(
		"Customer Group", {"is_group": 0}, "name", order_by="creation asc"
	)


def _resolve_territory():
	if frappe.db.exists("Territory", "_Test Territory"):
		return "_Test Territory"
	return frappe.db.get_value("Territory", {"is_group": 0}, "name", order_by="creation asc")


def _resolve_cost_center(company):
	return frappe.db.get_value(
		"Cost Center",
		{"company": company, "is_group": 0, "disabled": 0},
		"name",
		order_by="creation asc",
	)


def _resolve_mode_of_payment(company):
	"""Find a Mode of Payment with an account configured for `company`."""
	mop_with_account = frappe.db.sql(
		"""
		SELECT DISTINCT parent FROM `tabMode of Payment Account`
		WHERE company = %s LIMIT 1
		""",
		(company,),
	)
	if mop_with_account:
		return mop_with_account[0][0]
	if not frappe.db.exists("Mode of Payment", "Cash"):
		frappe.get_doc(
			{
				"doctype": "Mode of Payment",
				"mode_of_payment": "Cash",
				"type": "Cash",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)
	default_cash_account = frappe.get_cached_value("Company", company, "default_cash_account")
	if not default_cash_account:
		default_cash_account = frappe.db.get_value(
			"Account",
			{"company": company, "account_type": "Cash", "is_group": 0},
			"name",
			order_by="creation asc",
		)
	mop_doc = frappe.get_doc("Mode of Payment", "Cash")
	mop_doc.append("accounts", {"company": company, "default_account": default_cash_account})
	mop_doc.save(ignore_permissions=True)
	return "Cash"


def _ensure_item(company, warehouse, price_list):
	"""Create ITEM_CODE + Item Price + top up stock if not already present."""
	if not frappe.db.exists("Item", ITEM_CODE):
		item_group = _resolve_item_group()
		item = frappe.get_doc(
			{
				"doctype": "Item",
				"item_code": ITEM_CODE,
				"item_name": "Test Item A",
				"item_group": item_group,
				"stock_uom": "Nos",
				"is_stock_item": 1,
			}
		)
		item.flags.from_integration = True
		item.insert(ignore_permissions=True)

	if not frappe.db.exists("Item Price", {"item_code": ITEM_CODE, "price_list": price_list}):
		frappe.get_doc(
			{
				"doctype": "Item Price",
				"item_code": ITEM_CODE,
				"price_list": price_list,
				"price_list_rate": ITEM_PRICE,
			}
		).insert(ignore_permissions=True)

	from erpnext.stock.doctype.stock_entry.test_stock_entry import make_stock_entry

	current = (
		frappe.db.get_value(
			"Bin", {"item_code": ITEM_CODE, "warehouse": warehouse}, "actual_qty"
		)
		or 0
	)
	if current < 10:
		try:
			make_stock_entry(
				item_code=ITEM_CODE,
				target=warehouse,
				qty=50,
				rate=ITEM_PRICE / 2,
				company=company,
			)
		except Exception:
			frappe.db.rollback()


def _ensure_customer():
	if not frappe.db.exists("Customer", CUSTOMER):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": "Test Customer",
				"customer_type": "Individual",
				"customer_group": _resolve_customer_group(),
				"territory": _resolve_territory(),
			}
		).insert(ignore_permissions=True)


def _ensure_pos_profile(company, warehouse, price_list):
	"""Create a deterministic POS Profile with CUSTOMER as default customer.

	The walk_in.py validate hook requires a pos_profile with a default
	customer when buyer_name is set, so this profile is needed for all
	buyer_name tests.
	"""
	_ensure_customer()
	mode_of_payment = _resolve_mode_of_payment(company)
	profile_name = f"{POS_PROFILE_PREFIX}{company}"

	if frappe.db.exists("POS Profile", profile_name):
		profile = frappe.get_doc("POS Profile", profile_name)
		profile.warehouse = warehouse
		profile.selling_price_list = price_list
		profile.customer = CUSTOMER
		profile.save(ignore_permissions=True)
		return profile.name

	profile = frappe.get_doc(
		{
			"doctype": "POS Profile",
			"name": profile_name,
			"company": company,
			"warehouse": warehouse,
			"selling_price_list": price_list,
			"currency": frappe.get_cached_value("Company", company, "default_currency"),
			"customer": CUSTOMER,
			"write_off_account": frappe.get_cached_value("Company", company, "write_off_account"),
			"write_off_cost_center": _resolve_cost_center(company),
			"disable_rounded_total": 1,
			"disabled": 0,
		}
	)
	profile.append("payments", {"mode_of_payment": mode_of_payment, "default": 1, "amount": 0})
	profile.insert(ignore_permissions=True)
	return profile.name


def _make_submitted_invoice(buyer_name=None, queue_number=None):
	"""Create and submit a minimal Sales Invoice, optionally setting the two
	custom fields, and return its name.

	A POS Profile is always set so that the walk_in.py validation hook
	(which requires a pos_profile when buyer_name is present) is satisfied.
	"""
	company = _resolve_company()
	warehouse = _resolve_warehouse(company)
	price_list = _resolve_price_list(company)
	_ensure_item(company, warehouse, price_list)
	pos_profile = _ensure_pos_profile(company, warehouse, price_list)

	doc = frappe.get_doc(
		{
			"doctype": "Sales Invoice",
			"company": company,
			"customer": CUSTOMER,
			"pos_profile": pos_profile,
			"currency": frappe.get_cached_value("Company", company, "default_currency"),
			"selling_price_list": price_list,
			"posting_date": nowdate(),
			"disable_rounded_total": 1,
			"items": [
				{
					"item_code": ITEM_CODE,
					"qty": 1,
					"rate": ITEM_PRICE,
					"price_list_rate": ITEM_PRICE,
					"uom": "Nos",
					"warehouse": warehouse,
					"conversion_factor": 1,
				}
			],
		}
	)
	if buyer_name is not None:
		doc.buyer_name = buyer_name
	if queue_number is not None:
		doc.queue_number = queue_number
	doc.insert(ignore_permissions=True)
	doc.submit()
	return doc.name


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestReceiptBuyerFields(FrappeTestCase):
	"""Verify buyer_name and queue_number render (or suppress) correctly in
	the POS Next Receipt print format."""

	def setUp(self):
		self._created_invoices = []

	def tearDown(self):
		for name in self._created_invoices:
			try:
				doc = frappe.get_doc("Sales Invoice", name)
				if doc.docstatus == 1:
					doc.cancel()
				frappe.delete_doc("Sales Invoice", name, force=True, ignore_permissions=True)
			except Exception:
				pass
		super().tearDown()

	def _print(self, invoice_name):
		pf_html = frappe.db.get_value("Print Format", "POS Next Receipt", "html")
		doc = frappe.get_doc("Sales Invoice", invoice_name)
		return frappe.render_template(pf_html, {"doc": doc})

	# ── buyer_name ──────────────────────────────────────────────────────────

	def test_buyer_name_renders_when_present(self):
		name = _make_submitted_invoice(buyer_name="Budi")
		self._created_invoices.append(name)
		html = self._print(name)
		self.assertIn("Budi", html)
		self.assertIn('class="buyer-name"', html)

	def test_buyer_name_absent_renders_no_marker(self):
		"""No buyer_name -> the buyer-name wrapper div must not appear at all."""
		name = _make_submitted_invoice()
		self._created_invoices.append(name)
		html = self._print(name)
		self.assertNotIn('class="buyer-name"', html)
		self.assertNotIn("Buyer:", html)

	def test_empty_string_buyer_name_renders_no_marker(self):
		"""Empty-string buyer_name must behave like absent."""
		name = _make_submitted_invoice(buyer_name="")
		self._created_invoices.append(name)
		html = self._print(name)
		self.assertNotIn('class="buyer-name"', html)
		self.assertNotIn("Buyer:", html)

	# ── queue_number ────────────────────────────────────────────────────────

	def test_queue_number_renders_when_present(self):
		name = _make_submitted_invoice(queue_number=7)
		self._created_invoices.append(name)
		html = self._print(name)
		self.assertIn("No. 7", html)
		self.assertIn('class="queue-number', html)

	def test_queue_number_absent_renders_no_marker(self):
		"""No queue_number -> the queue-number wrapper div must not appear at all."""
		name = _make_submitted_invoice()
		self._created_invoices.append(name)
		html = self._print(name)
		self.assertNotIn('class="queue-number', html)
		self.assertNotIn("No. ", html)

	def test_queue_number_zero_renders_no_marker(self):
		"""queue_number=0 (falsy Int) must suppress the block entirely."""
		name = _make_submitted_invoice(queue_number=0)
		self._created_invoices.append(name)
		html = self._print(name)
		self.assertNotIn('class="queue-number', html)
		self.assertNotIn("No. 0", html)

	# ── both together ───────────────────────────────────────────────────────

	def test_both_fields_render_together(self):
		name = _make_submitted_invoice(buyer_name="Siti", queue_number=3)
		self._created_invoices.append(name)
		html = self._print(name)
		self.assertIn("Siti", html)
		self.assertIn('class="buyer-name"', html)
		self.assertIn("No. 3", html)
		self.assertIn('class="queue-number', html)
