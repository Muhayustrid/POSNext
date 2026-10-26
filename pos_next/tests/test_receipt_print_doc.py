# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import datetime
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.invoices import _receipt_print_doc


def _fake_invoice(**overrides):
	doc = frappe._dict(
		doctype="Sales Invoice",
		name="PINV-TEST-0001",
		owner="cashier@example.com",
		company="Test Co",
		company_address="Test Co-Billing",
		customer_name="Walk-in Customer",
		currency="IDR",
		pos_queue_number=7,
		posting_date=datetime.date(2026, 10, 24),
		posting_time=datetime.timedelta(hours=14, minutes=58, seconds=6),
		grand_total=12000,
		paid_amount=12000,
		items=[frappe._dict(item_code="AMR", item_name="Americano Ice", qty=1, rate=12000, uom="Nos")],
		payments=[frappe._dict(mode_of_payment="Cash", amount=12000, account="Cash - TC")],
	)
	doc.update(overrides)
	return doc


class TestReceiptPrintDoc(FrappeTestCase):
	def test_carries_what_the_receipt_prints(self):
		lookups = {"User": "Kasir Satu", "Address": "Jl. Raya No.19"}
		with patch(
			"pos_next.api.invoices.frappe.db.get_value",
			side_effect=lambda doctype, *_a, **_k: lookups[doctype],
		):
			doc = _receipt_print_doc(_fake_invoice())

		self.assertTrue(doc["receipt_ready"])
		self.assertEqual(doc["cashier_name"], "Kasir Satu")
		self.assertEqual(doc["company_address_line"], "Jl. Raya No.19")
		self.assertEqual(doc["posting_date"], "2026-10-24")
		self.assertEqual(doc["posting_time"], "14:58:06")
		self.assertEqual(doc["pos_queue_number"], 7)
		self.assertEqual(doc["items"][0]["item_name"], "Americano Ice")
		# Slim rows: only receipt fields travel back with the submit response.
		self.assertNotIn("uom", doc["items"][0])
		self.assertEqual(doc["payments"], [{"mode_of_payment": "Cash", "amount": 12000}])

	def test_never_raises_into_the_submit(self):
		with (
			patch("pos_next.api.invoices.frappe.db.get_value", side_effect=RuntimeError("db down")),
			patch("pos_next.api.invoices.frappe.log_error"),
		):
			self.assertIsNone(_receipt_print_doc(_fake_invoice()))
