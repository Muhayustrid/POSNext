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


class RejectRequestTest(FrappeTestCase):
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
		if not frappe.db.get_value("Company", self.company, "stock_adjustment_account"):
			frappe.db.set_value(
				"Company",
				self.company,
				"stock_adjustment_account",
				frappe.db.get_value(
					"Account", {"company": self.company, "account_type": "Stock Adjustment", "is_group": 0}, "name"
				)
				or frappe.db.get_value("Account", {"company": self.company, "is_group": 0, "root_type": "Expense"}, "name"),
			)
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

	def _transfers(self, doc=None):
		filters = {"remarks": ["like", "POS Reject transfer: %"]}
		if doc:
			filters["remarks"] = ["like", f"POS Reject transfer: {doc.name}%"]
		return frappe.db.count("Stock Entry", filters)

	def _destruction(self, doc, count=1):
		for i in range(count):
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": f"des{i}.txt",
					"attached_to_doctype": doc.doctype,
					"attached_to_name": doc.name,
					"attached_to_field": "destruction",
					"is_private": 1,
					"content": b"d" + bytes([i]),
				}
			).insert(ignore_permissions=True)

	def _user(self, roles, prefix="rej"):
		email = f"{prefix}-{frappe.generate_hash(length=6)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": prefix.title(),
				"send_welcome_email": 0,
				"roles": [{"role": r} for r in roles],
			}
		).insert(ignore_permissions=True)
		return email


class TestUnsoldSubmit(RejectRequestTest):
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
		email = self._user(["POSNext Reject Approver"], prefix="rej-appr")
		doc = self._submitted(1)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": email, "document_type": doc.doctype, "document_name": doc.name}
			)
		)


from contextlib import contextmanager

from pos_next.services import reject_request


@contextmanager
def _as_user(email):
	try:
		frappe.set_user(email)
		yield
	finally:
		frappe.set_user("Administrator")


class TestDecision(RejectRequestTest):
	def setUp(self):
		super().setUp()
		self.approver = self._user(["POSNext Reject Approver"], prefix="rej-dec")

	def _decide(self, doc, decision, notes=None):
		with _as_user(self.approver):
			return reject_request.apply_decision(doc, decision, notes)

	def test_owner_cannot_decide_own_request(self):
		owner = self._user(["POSNext Cashier", "POSNext Reject Approver"], prefix="rej-own")
		doc = self._submitted(1)
		frappe.db.set_value("POS Reject Request", doc.name, "owner", owner)
		with _as_user(owner):
			self.assertRaises(frappe.PermissionError, reject_request.apply_decision, doc.reload() or doc, "Write Off", None)

	def test_non_approver_cannot_decide(self):
		other = self._user(["POSNext Cashier"], prefix="rej-non")
		doc = self._submitted(1)
		with _as_user(other):
			self.assertRaises(frappe.PermissionError, reject_request.apply_decision, doc, "Write Off", None)

	def test_reject_returns_stock_and_needs_notes(self):
		doc = self._submitted(3)
		self.assertRaises(frappe.ValidationError, reject_request.apply_decision, doc, "Rejected", None)
		self.assertEqual(self._transfers(doc), 1)
		self._decide(doc, "Rejected", "wrong batch")
		self.assertEqual(self._bin(self.warehouse), 10)
		self.assertEqual(self._bin(self.reject_wh), 0)
		self.assertEqual(frappe.db.get_value("POS Reject Request", doc.name, "status"), "Rejected")
		self.assertEqual(self._transfers(doc), 2)

	def test_double_decision_fails(self):
		doc = self._submitted(1)
		self._decide(doc, "Write Off", None)
		before = self._transfers(doc)
		self.assertRaises(frappe.ValidationError, reject_request.apply_decision, doc, "Rejected", "twice")
		self.assertEqual(self._transfers(doc), before)

	def test_charge_crew_requires_notes(self):
		doc = self._submitted(1)
		self.assertRaises(frappe.ValidationError, reject_request.apply_decision, doc, "Charge Crew", None)
		self._decide(doc, "Charge Crew", "crew negligence")
		values = frappe.db.get_value("POS Reject Request", doc.name, ["decision", "status"], as_dict=True)
		self.assertEqual(values.decision, "Charge Crew")
		self.assertEqual(values.status, "Approved")

	def test_strict_late_auto_charges_crew(self):
		frappe.db.set_single_value("POS Reject Settings", "late_mode", "Strict")
		owner = self._user(["POSNext Cashier"], prefix="rej-late")
		doc = self._doc(1, incident_date=add_days(today(), -5))
		frappe.db.set_value("POS Reject Request", doc.name, "owner", owner)
		self._evidence(doc)
		doc.reload().submit()
		self.assertEqual(doc.reload().status, "Approved")
		values = frappe.db.get_value(
			"POS Reject Request", doc.name, ["decision", "decided_by", "decision_notes"], as_dict=True
		)
		self.assertEqual(values.decision, "Charge Crew")
		self.assertEqual(values.decided_by, "Administrator")
		self.assertTrue(values.decision_notes)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": owner, "document_type": doc.doctype, "document_name": doc.name}
			)
		)

	def test_close_requires_destruction_photo(self):
		doc = self._submitted(1)
		self._decide(doc, "Write Off", None)
		self.assertRaises(frappe.ValidationError, reject_request.close_request, doc)

	def test_close_writes_off_reject_stock(self):
		doc = self._submitted(3)
		self._decide(doc, "Write Off", None)
		self._destruction(doc)
		reject_request.close_request(doc)
		self.assertEqual(self._bin(self.reject_wh), 0)
		issue = frappe.db.get_value("POS Reject Request", doc.name, "issue_entry")
		self.assertTrue(issue)
		self.assertEqual(frappe.db.get_value("Stock Entry", issue, "docstatus"), 1)
		self.assertEqual(frappe.db.get_value("Stock Entry", issue, "remarks"), f"POS Reject write-off: {doc.name}")
		values = frappe.db.get_value("POS Reject Request", doc.name, ["status", "closed_on"], as_dict=True)
		self.assertEqual(values.status, "Closed")
		self.assertTrue(values.closed_on)

	def test_close_production_loss_skips_stock(self):
		doc = self._doc(1, purpose="Production Loss")
		self._evidence(doc)
		doc.submit()
		self._decide(doc, "Write Off", None)
		self._destruction(doc)
		reject_request.close_request(doc)
		self.assertFalse(frappe.db.get_value("POS Reject Request", doc.name, "issue_entry"))
		self.assertEqual(frappe.db.get_value("POS Reject Request", doc.name, "status"), "Closed")

	def test_owner_notified_of_decision(self):
		owner = self._user(["POSNext Cashier"], prefix="rej-own2")
		doc = self._submitted(1)
		frappe.db.set_value("POS Reject Request", doc.name, "owner", owner)
		self._decide(doc.reload(), "Write Off", None)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": owner, "document_type": doc.doctype, "document_name": doc.name}
			)
		)

	def test_over_limit_counts_only_submitted(self):
		frappe.db.set_single_value("POS Reject Settings", "monthly_limit_amount", 50)
		# An isolated month so sibling tests' requests can't push this over.
		incident = add_days(today(), -190)
		prior = self._doc(3, incident_date=incident)
		self._evidence(prior)
		doc = self._submitted(3, incident_date=incident)
		self.assertEqual(doc.over_limit, 0)
		prior.submit()
		self.assertEqual(self._submitted(1, incident_date=incident).over_limit, 1)

	def test_outlet_access_gate(self):
		cashier = self._user(["POSNext Cashier"], prefix="rej-gate")
		with _as_user(cashier):
			self.assertRaises(
				frappe.PermissionError,
				lambda: frappe.get_doc(
					{
						"doctype": "POS Reject Request",
						"purpose": "Unsold",
						"pos_profile": self.pos_profile,
						"incident_date": today(),
						"reason": "gate",
						"items": [{"item_code": self.item, "qty": 1}],
					}
				).insert(),
			)
