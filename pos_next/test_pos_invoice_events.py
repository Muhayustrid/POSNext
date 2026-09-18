import frappe
from frappe.tests.utils import FrappeTestCase


def _minimal_posi(owned=True):
	"""Minimal POS Invoice; returns None (caller skipTests) when site data is missing."""
	profile = frappe.db.get_value(
		"POS Profile", {"disabled": 0}, ["name", "company", "warehouse"], as_dict=True
	)
	customers = frappe.get_all("Customer", pluck="name", limit=1)
	items = frappe.get_all("Item", pluck="name", limit=1)
	if not (profile and customers and items and frappe.db.exists("Mode of Payment", "Cash")):
		return None
	doc = frappe.new_doc("POS Invoice")
	doc.update(
		{
			"customer": customers[0],
			"is_pos": 1,
			"update_stock": 1,
			"pos_profile": profile.name,
			"company": profile.company,
			"set_warehouse": profile.warehouse,
			"currency": frappe.db.get_value("Company", profile.company, "default_currency"),
		}
	)
	if owned:
		doc.posa_pos_opening_shift = "POSA-OS-DOES-NOT-EXIST"
	doc.append(
		"items", {"item_code": items[0], "qty": 1, "rate": 1, "warehouse": profile.warehouse}
	)
	doc.append("payments", {"mode_of_payment": "Cash", "amount": 1})
	return doc


class TestPOSInvoiceEventGuards(FrappeTestCase):
	def test_owned_invoice_requires_real_shift(self):
		doc = _minimal_posi(owned=True)
		if doc is None:
			self.skipTest("missing site data (POS Profile/Customer/Item/MOP Cash)")
		with self.assertRaises(frappe.ValidationError) as ctx:
			doc.insert()
		self.assertIn("POS Opening Shift", str(ctx.exception))

	def test_unowned_invoice_skips_pos_next_gates(self):
		# built-in-POS invoice: no posa_pos_opening_shift -> must not trip the
		# pos_next ownership checks (insert may still fail on unrelated ERPNext
		# validation, but never on POS Opening Shift / pos_next gates)
		doc = _minimal_posi(owned=False)
		if doc is None:
			self.skipTest("missing site data (POS Profile/Customer/Item/MOP Cash)")
		try:
			doc.insert()
		except Exception as e:
			self.assertNotIn("POS Opening Shift", str(e))

	def test_consolidated_flag_snippet(self):
		from pos_next.overrides.queue_counter import bump_queue_counter

		doc = frappe._dict(
			doctype="Sales Invoice",
			is_consolidated=1,
			company="X",
			posting_date="2026-09-10",
			name="SI-CONS-1",
			pos_queue_number="Q-CONS-1",  # would create a counter row without the guard
		)
		bump_queue_counter(doc)  # must be a no-op, not raise
		self.assertFalse(frappe.db.exists("POS Queue Counter", {"company": "X"}))
