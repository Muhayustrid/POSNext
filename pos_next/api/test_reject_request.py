# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

SCHEMA = {
	"POS Reject Settings": (
		"deadline_days", "production_loss_deadline_days", "late_mode",
		"monthly_limit_amount", "notify_emails",
	),
	"POS Reject Request": (
		"purpose", "pos_profile", "company", "incident_date", "deadline_date", "reason",
		"video_link", "work_order", "is_system_generated", "items", "total_amount",
		"is_late", "over_limit", "status", "decision", "decision_notes", "decided_by",
		"decided_on", "transfer_entry", "issue_entry", "closed_on",
	),
	"POS Reject Request Item": (
		"item_code", "item_name", "qty", "uom", "valuation_rate", "amount",
	),
	"POS Settings": ("reject_warehouse",),
}


class TestRejectSchema(FrappeTestCase):
	def test_doctypes_and_fields_exist(self):
		for doctype, fields in SCHEMA.items():
			meta = frappe.get_meta(doctype)
			for fieldname in fields:
				self.assertTrue(meta.has_field(fieldname), f"{doctype}.{fieldname}")
		self.assertTrue(frappe.db.exists("Role", "POSNext Reject Approver"))


from frappe.utils import add_days, flt, today

from pos_next.api.test_production import _make_test_item, _seed_stock


class TestUnsoldSubmit(FrappeTestCase):
	def setUp(self):
		self.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.pos_profile:
			self.skipTest("no POS Profile on this site")
		self.company, self.warehouse = frappe.db.get_value(
			"POS Profile", self.pos_profile, ["company", "warehouse"]
		)
		if not self.warehouse:
			self.warehouse = frappe.db.get_value(
				"Warehouse", {"company": self.company, "is_group": 0, "disabled": 0}, "name"
			)
		self.reject_wh = frappe.db.get_value(
			"Warehouse",
			{"company": self.company, "is_group": 0, "disabled": 0, "name": ["!=", self.warehouse]},
			"name",
		)
		if not self.reject_wh:
			self.skipTest("need a second warehouse")
		row = frappe.db.get_value("POS Settings", {"pos_profile": self.pos_profile}, "name")
		if row:
			frappe.db.set_value("POS Settings", row, {"enabled": 1, "reject_warehouse": self.reject_wh})
		else:
			frappe.get_doc(
				{
					"doctype": "POS Settings",
					"pos_profile": self.pos_profile,
					"enabled": 1,
					"reject_warehouse": self.reject_wh,
				}
			).insert(ignore_permissions=True)
		for field, value in (
			("deadline_days", 3),
			("production_loss_deadline_days", 0),
			("late_mode", "Lenient"),
			("monthly_limit_amount", 0),
			("notify_emails", ""),
		):
			frappe.db.set_single_value("POS Reject Settings", field, value)
		self.item = _make_test_item()
		_seed_stock(self.item, self.warehouse, 10)

	def _bin(self, warehouse):
		return flt(frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": warehouse}, "actual_qty"))

	def _doc(self, qty=3, **extra):
		doc = frappe.get_doc(
			{
				"doctype": "POS Reject Request",
				"purpose": "Unsold",
				"pos_profile": self.pos_profile,
				"incident_date": today(),
				"reason": "unsold bread",
				"items": [{"item_code": self.item, "qty": qty}],
				**extra,
			}
		).insert(ignore_permissions=True)
		return doc

	def _evidence(self, doc, count=1):
		for i in range(count):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"ev{i}.txt",
					"attached_to_doctype": doc.doctype,
					"attached_to_name": doc.name,
					"attached_to_field": "evidence",
					"is_private": 1,
					"content": b"x" + bytes([i]),
				}
			).insert(ignore_permissions=True)

	def _submitted(self, qty=3, **extra):
		doc = self._doc(qty, **extra)
		self._evidence(doc)
		doc.submit()
		return doc

	def _transfers(self):
		return frappe.db.count("Stock Entry", {"remarks": ["like", "POS Reject transfer: %"]})

	def test_submit_moves_stock_to_reject_warehouse(self):
		doc = self._submitted(3)
		self.assertEqual(self._bin(self.warehouse), 7)
		self.assertEqual(self._bin(self.reject_wh), 3)
		self.assertEqual(doc.status, "Pending")
		self.assertEqual(frappe.db.get_value("Stock Entry", doc.transfer_entry, "docstatus"), 1)
		self.assertGreater(doc.total_amount, 0)

	def test_submit_requires_evidence_photo(self):
		doc = self._doc()
		self.assertRaises(frappe.ValidationError, doc.submit)
		doc = self._doc()
		self._evidence(doc, 6)
		self.assertRaises(frappe.ValidationError, doc.submit)

	def test_submit_fails_when_stock_short(self):
		doc = self._doc(11)
		self._evidence(doc)
		before = self._transfers()
		self.assertRaises(frappe.ValidationError, doc.submit)
		self.assertEqual(self._transfers(), before)

	def test_future_incident_date_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._doc(incident_date=add_days(today(), 1))

	def test_bad_video_link_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			self._doc(video_link="drive.google.com/x")
		self._doc(video_link="https://drive.google.com/x")

	def test_deadline_and_late_flag(self):
		frappe.db.set_single_value("POS Reject Settings", "deadline_days", 3)
		incident = add_days(today(), -5)
		doc = self._submitted(1, incident_date=incident)
		self.assertEqual(doc.is_late, 1)
		self.assertEqual(str(doc.deadline_date), add_days(incident, 3))

	def test_over_limit_flag(self):
		frappe.db.set_single_value("POS Reject Settings", "monthly_limit_amount", 1)
		self.assertEqual(self._submitted(3).over_limit, 1)
		frappe.db.set_single_value("POS Reject Settings", "monthly_limit_amount", 0)
		self.assertEqual(self._submitted(1).over_limit, 0)

	def test_client_valuation_is_overwritten(self):
		doc = self._doc(2)
		doc.items[0].valuation_rate = 999999
		doc.save()
		self.assertLess(doc.items[0].valuation_rate, 999999)
		self.assertEqual(doc.total_amount, doc.items[0].qty * doc.items[0].valuation_rate)

	def test_cancel_pending_returns_stock(self):
		doc = self._submitted(3)
		doc.cancel()
		self.assertEqual(self._bin(self.warehouse), 10)
		self.assertEqual(frappe.db.get_value("Stock Entry", doc.transfer_entry, "docstatus"), 2)
		self.assertEqual(doc.status, "Cancelled")

	def test_approvers_notified(self):
		email = f"rej-appr-{frappe.generate_hash(length=6)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": "Appr",
				"send_welcome_email": 0,
				"roles": [{"role": "POSNext Reject Approver"}],
			}
		).insert(ignore_permissions=True)
		doc = self._submitted(1)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": email, "document_type": doc.doctype, "document_name": doc.name}
			)
		)
