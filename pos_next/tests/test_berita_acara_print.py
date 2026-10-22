# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe
from frappe.model.workflow import apply_workflow

from pos_next.api.test_reject_request import RejectRequestTest, _as_user
from pos_next.reject_request_setup import APPROVER_ROLE
from pos_next.services import reject_request


class TestBeritaAcaraPrint(RejectRequestTest):
	def setUp(self):
		super().setUp()
		self.crew = self._user(["POSNext Cashier"], prefix="crew")
		self.approver = self._user([APPROVER_ROLE], prefix="approver")
		for u in (self.crew, self.approver):
			if not frappe.db.exists("POS Profile User", {"parent": self.pos_profile, "user": u}):
				frappe.get_doc(
					{
						"doctype": "POS Profile User",
						"parent": self.pos_profile,
						"parenttype": "POS Profile",
						"parentfield": "applicable_for_users",
						"user": u,
						"default": 1,
					}
				).insert(ignore_permissions=True)
			frappe.clear_cache(user=u)

	def _approved_doc(self, qty=3):
		with _as_user(self.crew):
			doc = self._doc(qty=qty)
			self._evidence(doc)
			doc = apply_workflow(doc, "Submit")
		with _as_user(self.approver):
			reject_request.set_liability(doc, "Company", None)
			doc = apply_workflow(doc, "Approve")
		return doc

	def test_berita_acara_renders_code_and_items(self):
		doc = self._approved_doc(qty=3)
		html = frappe.get_print("POS Reject Request", doc.name, "Berita Acara Reject")
		self.assertIn("BERITA ACARA", html)
		self.assertIn(doc.approval_code, html)
		for item in doc.items:
			self.assertIn(item.item_name, html)

	def test_letter_head_defaults_from_company(self):
		letter_head_name = frappe.db.get_value("Letter Head", {"is_default": 1}, "name")
		if not letter_head_name:
			letter_head_name = frappe.db.get_value("Letter Head", {}, "name")
		if not letter_head_name:
			lh = frappe.get_doc(
				{
					"doctype": "Letter Head",
					"letter_head_name": "Test Letter Head",
					"content": "<p>Test Letter Head</p>",
				}
			).insert(ignore_permissions=True)
			letter_head_name = lh.name
		frappe.db.set_value("Company", self.company, "default_letter_head", letter_head_name)

		with _as_user(self.crew):
			doc = self._doc(qty=1)
			self._evidence(doc)
		self.assertEqual(doc.letter_head, letter_head_name)
