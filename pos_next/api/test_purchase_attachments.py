# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Attachment (evidence file) tests for the POS purchase flow.

Covers the whole gate: attach to a draft PO, attach to a SUBMITTED Purchase
Receipt (surat jalan evidence lands after the POS submits in one step), the
summary riding attachments, role-less denial, doctype allowlist and the
per-file size cap."""

import json
import unittest

import frappe
from frappe import PermissionError, ValidationError
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.purchase_orders import attach_purchase_files, get_purchase_order
from pos_next.api.purchase_receipts import get_purchase_receipt, save_purchase_receipt
from pos_next.api import purchase_orders as po_module

ADMIN = "Administrator"

# a tiny valid PNG header + padding — File content only needs bytes, the
# controller never sniffs the type
# real 1x1 PNG bytes (correct CRCs) — File.validate decodes images via Pillow
PNG_BASE = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"


class TestPurchaseAttachments(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		cls.company = frappe.db.get_value("Company", {}, "name")
		cls.warehouse = frappe.db.get_value(
			"Warehouse", {"company": cls.company, "is_group": 0, "disabled": 0}, "name"
		)
		if not (cls.company and cls.warehouse):
			raise unittest.SkipTest("no company + warehouse pair on this site")
		cls.supplier_group = frappe.db.get_value("Supplier Group", {"is_group": 0}, "name")
		cls.item = (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": f"_Test Attach Item {frappe.generate_hash(length=6)}",
					"item_group": frappe.db.get_value("Item Group", {"is_group": 0}, "name"),
					"stock_uom": "Unit",
					"is_stock_item": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.supplier = (
			frappe.get_doc(
				{
					"doctype": "Supplier",
					"supplier_name": f"_Test Attach Supplier {frappe.generate_hash(length=6)}",
					"supplier_group": cls.supplier_group,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.files = []
		cls.purchase_docs = []

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		for name in cls.files:
			if frappe.db.exists("File", name):
				frappe.delete_doc("File", name, force=1, ignore_permissions=True)
		for dt, name in [
			*[("Purchase Receipt", name) for name in [getattr(cls, "pr", None)] if name],
			*[("Purchase Order", name) for name in cls.purchase_docs],
			("Supplier", cls.supplier),
			("Item", cls.item),
		]:
			if name and frappe.db.exists(dt, name):
				st = frappe.db.get_value(dt, name, "docstatus")
				if st == 1:
					frappe.get_doc(dt, name).cancel()
				frappe.delete_doc(dt, name, force=1, ignore_permissions=True)
		# deletions must survive IntegrationTestCase's class-cleanup rollback
		frappe.db.commit()
		super().tearDownClass()

	def _data(self):
		return {
			"supplier": self.supplier,
			"company": self.company,
			"set_warehouse": self.warehouse,
			"items": [{"item_code": self.item, "qty": 1, "rate": 10}],
		}

	def _attach(self, doctype, name, filedata=PNG_BASE, filename="surat-jalan.png"):
		res = attach_purchase_files(
			doctype,
			name,
			json.dumps([{"file_name": filename, "filedata": filedata}]),
		)
		self.files.extend(row["name"] for row in res)
		return res

	def test_attach_to_draft_po_and_summary(self):
		po = save_receipt_free_po(self._data())
		self.po = po["name"]
		self.purchase_docs.append(po["name"])
		res = self._attach("Purchase Order", self.po, filename="penawaran.png")
		self.assertEqual(len(res), 1)
		self.assertEqual(res[0]["file_name"], "penawaran.png")
		self.assertTrue(res[0]["file_url"].startswith("/private/files/"))
		frappe.set_user(ADMIN)
		summary = get_purchase_order(self.po)
		self.assertEqual(summary["attachment_count"], 1)
		self.assertEqual(summary["attachments"][0]["file_name"], "penawaran.png")

	def test_attach_to_submitted_pr_and_summary(self):
		# the POS receive flow submits in one step — evidence lands AFTER submit
		pr = save_purchase_receipt(json.dumps(self._data()), submit=1)
		self.pr = pr["name"]
		self.assertEqual(pr["docstatus"], 1)
		res = self._attach("Purchase Receipt", self.pr, filename="surat-jalan.png")
		self.assertEqual(res[0]["file_name"], "surat-jalan.png")
		frappe.set_user(ADMIN)
		summary = get_purchase_receipt(self.pr)
		self.assertEqual(summary["attachment_count"], 1)
		self.assertTrue(summary["attachments"][0]["file_url"].startswith("/private/files/"))

	def test_permission_denied_without_role(self):
		po = save_receipt_free_po(self._data())
		self.po = po["name"]
		self.purchase_docs.append(po["name"])
		user = f"attach.deny.{frappe.generate_hash(length=6)}@example.com"
		frappe.get_doc({"doctype": "User", "email": user, "first_name": "Attach Deny"}).insert(
			ignore_permissions=True
		)
		try:
			frappe.set_user(user)
			with self.assertRaises(PermissionError):
				self._attach("Purchase Order", po["name"])
		finally:
			frappe.set_user(ADMIN)
			frappe.delete_doc("User", user, force=1, ignore_permissions=True)

	def test_rejects_other_doctype(self):
		with self.assertRaises(ValidationError):
			attach_purchase_files(
				"Sales Invoice", "whatever", json.dumps([{"file_name": "x.png", "filedata": PNG_BASE}])
			)

	def test_oversized_file_rejected(self):
		po = save_receipt_free_po(self._data())
		self.po = po["name"]
		self.purchase_docs.append(po["name"])
		original = po_module.ATTACHMENT_MAX_BYTES
		po_module.ATTACHMENT_MAX_BYTES = 16  # one PNG already exceeds this
		try:
			with self.assertRaises(ValidationError):
				self._attach("Purchase Order", po["name"])
		finally:
			po_module.ATTACHMENT_MAX_BYTES = original


def save_receipt_free_po(data):
	"""A minimal draft PO without stock side effects (update stock happens on PR)."""
	from pos_next.api.purchase_orders import save_purchase_order

	return save_purchase_order(json.dumps(data))
