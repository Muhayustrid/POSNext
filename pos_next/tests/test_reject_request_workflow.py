# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from contextlib import contextmanager

import frappe
from frappe.model.workflow import apply_workflow
from frappe.utils import add_days, flt, today

from pos_next.api.test_production import _seed_stock
from pos_next.api.test_reject_request import RejectRequestTest, _as_user
from pos_next.reject_request_setup import (
	APPROVER_ROLE,
	STATE_AWAITING,
	STATE_CANCELLED,
	STATE_COMPLETED,
	STATE_DRAFT,
	STATE_PENDING,
	STATE_PROOF,
)
from pos_next.services import reject_request
from pos_next.services.confirmation_code import CODE_ALPHABET


class TestRejectRequestWorkflow(RejectRequestTest):
	def setUp(self):
		super().setUp()
		self.crew = self._user(["POSNext Cashier"], prefix="crew")
		self.approver = self._user([APPROVER_ROLE], prefix="approver")
		self.dual_user = self._user(["POSNext Cashier", APPROVER_ROLE], prefix="dual")
		for u in (self.crew, self.approver, self.dual_user):
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

	def _draft_doc(self, qty=3, user=None, **extra):
		user = user or self.crew
		with _as_user(user):
			doc = self._doc(qty=qty, **extra)
			self._evidence(doc)
			return doc

	def _pending_doc(self, qty=3, user=None, **extra):
		doc = self._draft_doc(qty=qty, user=user, **extra)
		with _as_user(user or self.crew):
			doc = apply_workflow(doc, "Submit")
		return doc

	def _approved_doc(self, qty=3, borne_by="Company", liabilities=None, user=None, **extra):
		doc = self._pending_doc(qty=qty, user=user, **extra)
		with _as_user(self.approver):
			reject_request.set_liability(doc, borne_by, liabilities)
			doc = apply_workflow(doc, "Approve")
		return doc

	def _proof_doc(self, qty=3, **extra):
		doc = self._approved_doc(qty=qty, **extra)
		with _as_user(self.crew):
			doc.video_link = "https://example.com/video"
			doc.save()
			doc = apply_workflow(doc, "Send Proof")
		return doc

	def test_submit_moves_to_pending_without_stock_entry(self):
		doc = self._draft_doc(qty=3)
		with _as_user(self.crew):
			doc = apply_workflow(doc, "Submit")
		self.assertEqual(doc.workflow_state, STATE_PENDING)
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(self._transfers(doc), 0)

	def test_submit_blocks_when_stock_short(self):
		doc = self._draft_doc(qty=20)  # stock is 10
		with _as_user(self.crew):
			with self.assertRaises(frappe.ValidationError) as ctx:
				apply_workflow(doc, "Submit")
			self.assertIn(self.item, str(ctx.exception))

	def test_approve_requires_borne_by(self):
		doc = self._pending_doc(qty=3)
		with _as_user(self.approver):
			with self.assertRaises(frappe.ValidationError) as ctx:
				apply_workflow(doc, "Approve")
			self.assertIn("Borne By", str(ctx.exception))

	def test_approve_sets_code_and_submits(self):
		doc = self._pending_doc(qty=3)
		with _as_user(self.approver):
			reject_request.set_liability(doc, "Company", None)
			doc = apply_workflow(doc, "Approve")

		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.workflow_state, STATE_AWAITING)
		self.assertEqual(len(doc.approval_code), 8)
		self.assertTrue(set(doc.approval_code).issubset(set(CODE_ALPHABET)))
		self.assertEqual(doc.decided_by, self.approver)
		self.assertIsNotNone(doc.decided_on)

	def test_crew_liability_defaults_to_owner_full_amount(self):
		doc = self._pending_doc(qty=3)
		with _as_user(self.approver):
			reject_request.set_liability(doc, "Crew", None)

		doc.reload()
		self.assertEqual(doc.borne_by, "Crew")
		self.assertEqual(len(doc.liabilities), 1)
		self.assertEqual(doc.liabilities[0].user, doc.owner)
		self.assertAlmostEqual(flt(doc.liabilities[0].amount), flt(doc.total_amount), places=2)

	def test_strict_late_forces_crew(self):
		frappe.db.set_single_value("POS Reject Settings", "late_mode", "Strict")
		frappe.db.set_single_value("POS Reject Settings", "deadline_days", 3)
		doc = self._draft_doc(qty=3, incident_date=add_days(today(), -5))
		with _as_user(self.crew):
			doc = apply_workflow(doc, "Submit")

		self.assertEqual(doc.is_late, 1)
		self.assertEqual(doc.borne_by, "Crew")

		with _as_user(self.approver):
			with self.assertRaises(frappe.ValidationError):
				reject_request.set_liability(doc, "Company", None)

	def test_return_from_pending_back_to_draft_with_reason(self):
		doc = self._pending_doc(qty=3)
		with _as_user(self.approver):
			reject_request.record_return_reason(doc, "Foto bukti kurang jelas")
			doc = apply_workflow(doc, "Return")

		self.assertEqual(doc.workflow_state, STATE_DRAFT)
		self.assertEqual(doc.docstatus, 0)
		self.assertEqual(doc.return_reason, "Foto bukti kurang jelas")

		# crew resubmits -> return_reason is cleared
		with _as_user(self.crew):
			doc = apply_workflow(doc, "Submit")
		self.assertEqual(doc.workflow_state, STATE_PENDING)
		self.assertIsNone(doc.return_reason)

	def test_return_from_proof_back_to_awaiting(self):
		doc = self._proof_doc(qty=3)
		with _as_user(self.approver):
			reject_request.record_return_reason(doc, "Video buram")
			doc = apply_workflow(doc, "Return")

		self.assertEqual(doc.workflow_state, STATE_AWAITING)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.return_reason, "Video buram")

	def test_send_proof_requires_video_or_file(self):
		doc = self._approved_doc(qty=3)
		with _as_user(self.crew):
			with self.assertRaises(frappe.ValidationError):
				apply_workflow(doc, "Send Proof")

			doc.reload()
			doc.video_link = "https://example.com/destruction.mp4"
			doc.save()
			doc = apply_workflow(doc, "Send Proof")
		self.assertEqual(doc.workflow_state, STATE_PROOF)

	def test_complete_issues_from_profile_warehouse(self):
		doc = self._proof_doc(qty=3)
		initial_bin = self._bin(self.warehouse)
		with _as_user(self.approver):
			doc = apply_workflow(doc, "Complete")

		self.assertEqual(doc.workflow_state, STATE_COMPLETED)
		self.assertTrue(doc.issue_entry)
		self.assertAlmostEqual(self._bin(self.warehouse), initial_bin - 3, places=2)

	def test_complete_twice_issues_once(self):
		doc = self._proof_doc(qty=3)
		with _as_user(self.approver):
			doc = apply_workflow(doc, "Complete")

		# second attempt: re-saving or triggering on_state_change on Completed doc should NOT throw and NOT create a second stock entry
		with _as_user(self.approver):
			reject_request.on_state_change(doc)
			# Also test _issue_stock directly is idempotent: returns existing without throwing
			existing_se = reject_request._issue_stock(doc)
			self.assertEqual(existing_se.name, doc.issue_entry)

		# exactly one issue entry created
		issues = frappe.db.count("Stock Entry", {"remarks": ["like", f"POS Reject write-off: {doc.name}%"]})
		self.assertEqual(issues, 1)

	def test_complete_blocks_when_stock_dropped(self):
		doc = self._proof_doc(qty=3)
		# reduce available stock from 10 to 1
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"company": self.company,
				"purpose": "Material Issue",
				"stock_entry_type": "Material Issue",
				"items": [
					{
						"item_code": self.item,
						"qty": 9,
						"s_warehouse": self.warehouse,
						"expense_account": frappe.db.get_value("Company", self.company, "stock_adjustment_account"),
					}
				],
			}
		).insert(ignore_permissions=True)
		se.submit()

		with _as_user(self.approver):
			with self.assertRaises(frappe.ValidationError) as ctx:
				apply_workflow(doc, "Complete")
			self.assertIn(self.item, str(ctx.exception))

		doc.reload()
		self.assertEqual(doc.workflow_state, STATE_PROOF)

	def test_owner_with_approver_role_cannot_approve_via_save(self):
		doc = self._pending_doc(qty=3, user=self.dual_user)
		with _as_user(self.dual_user):
			doc.workflow_state = STATE_AWAITING
			doc.borne_by = "Company"
			with self.assertRaises(frappe.PermissionError):
				doc.submit()

	def test_crew_cannot_edit_items_after_approve(self):
		doc = self._approved_doc(qty=3)
		with _as_user(self.crew):
			doc.items[0].qty = 99
			with self.assertRaises(frappe.ValidationError):
				doc.save()

	def test_legacy_transfer_completes_from_reject_warehouse(self):
		doc = self._doc(qty=2)
		self._evidence(doc)
		# legacy transfer to reject_wh
		_seed_stock(self.item, self.warehouse, 10)
		se = frappe.get_doc(
			{
				"doctype": "Stock Entry",
				"company": self.company,
				"purpose": "Material Transfer",
				"stock_entry_type": "Material Transfer",
				"items": [{"item_code": self.item, "qty": 2, "s_warehouse": self.warehouse, "t_warehouse": self.reject_wh}],
			}
		).insert(ignore_permissions=True)
		se.submit()
		doc.db_set("transfer_entry", se.name)
		doc.workflow_state = STATE_PROOF
		doc.docstatus = 1
		doc.borne_by = "Company"
		doc.approval_code = "LEGACY01"
		doc.db_update()
		self._destruction(doc)

		initial_profile_bin = self._bin(self.warehouse)
		initial_reject_bin = self._bin(self.reject_wh)

		with _as_user(self.approver):
			doc = apply_workflow(doc, "Complete")

		self.assertEqual(doc.workflow_state, STATE_COMPLETED)
		# Stock issued from reject warehouse, not profile warehouse
		self.assertAlmostEqual(self._bin(self.reject_wh), initial_reject_bin - 2, places=2)
		self.assertAlmostEqual(self._bin(self.warehouse), initial_profile_bin, places=2)

	def test_production_loss_never_moves_stock(self):
		wo_name = f"WO-TEST-{frappe.generate_hash(length=6)}"
		frappe.db.sql(
			"""INSERT INTO `tabWork Order` (name, company, production_item, qty, produced_qty, status, docstatus)
			   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
			(wo_name, self.company, self.item, 10, 8, "Completed", 1),
		)
		doc = self._doc(qty=2, purpose="Production Loss", is_system_generated=1, work_order=wo_name)
		doc.flags.ignore_permissions = True
		self._evidence(doc)
		doc.workflow_state = STATE_PROOF
		doc.docstatus = 1
		doc.borne_by = "Company"
		doc.approval_code = "PROD0001"
		doc.db_update()
		self._destruction(doc)

		initial_bin = self._bin(self.warehouse)
		with _as_user(self.approver):
			doc = apply_workflow(doc, "Complete")

		self.assertEqual(doc.workflow_state, STATE_COMPLETED)
		self.assertIsNone(doc.issue_entry)
		self.assertAlmostEqual(self._bin(self.warehouse), initial_bin, places=2)

	def test_invalid_transition_rejected(self):
		doc = self._draft_doc(qty=1)
		doc.workflow_state = STATE_COMPLETED
		with _as_user(self.crew):
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.save()
			self.assertIn("Invalid workflow transition", str(ctx.exception))

	def test_approver_transition_requires_approver_role(self):
		doc = self._pending_doc(qty=1)
		with _as_user(self.crew):
			doc.workflow_state = STATE_AWAITING
			doc.borne_by = "Company"
			with self.assertRaises(frappe.PermissionError):
				doc.save()

	def test_server_derived_field_lock(self):
		doc = self._draft_doc(qty=1)
		server_fields = {
			"is_late": 1,
			"over_limit": 1,
			"total_amount": 99999,
			"deadline_date": "2099-01-01",
			"company": "Other Company",
			"return_reason": "Forced return reason",
			"letter_head": "Other Letter Head",
			"work_order": "WO-FORGED-001",
			"approval_code": "FORGED99",
			"decided_by": "hacker@example.com",
			"decided_on": "2099-01-01 00:00:00",
			"issue_entry": "SE-FORGED-001",
			"closed_on": "2099-01-01 00:00:00",
		}
		for field, forged_value in server_fields.items():
			d = frappe.get_doc("POS Reject Request", doc.name)
			d.set(field, forged_value)
			with _as_user(self.crew):
				with self.assertRaises(frappe.ValidationError, msg=f"Field {field} should be locked"):
					d.save()

	def test_unconfigured_uom_rejected(self):
		if not frappe.db.exists("UOM", "Kg"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "Kg"}).insert(ignore_permissions=True)
		# Ensure Kg is NOT configured for self.item
		item_doc = frappe.get_doc("Item", self.item)
		item_doc.uoms = [u for u in item_doc.uoms if u.uom != "Kg"]
		if item_doc.stock_uom == "Kg":
			item_doc.stock_uom = "Nos"
		item_doc.save(ignore_permissions=True)

		with _as_user(self.crew):
			with self.assertRaises(frappe.ValidationError) as ctx:
				self._doc(qty=1, items=[{"item_code": self.item, "qty": 1, "uom": "Kg"}])
			self.assertIn("not configured", str(ctx.exception))

	def test_alternate_uom_converted_to_stock_uom(self):
		# Ensure Box UOM exists and is configured for the item with conversion factor 10
		if not frappe.db.exists("UOM", "Box"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "Box"}).insert(ignore_permissions=True)
		item_doc = frappe.get_doc("Item", self.item)
		if not any(r.uom == "Box" for r in item_doc.uoms):
			item_doc.append("uoms", {"uom": "Box", "conversion_factor": 10})
			item_doc.save(ignore_permissions=True)

		doc = self._draft_doc(qty=2, items=[{"item_code": self.item, "qty": 2, "uom": "Box"}])
		doc.reload()
		# 2 Box with conversion factor 10 should convert to 20 in stock UOM
		self.assertEqual(doc.items[0].qty, 20)
		self.assertEqual(doc.items[0].uom, item_doc.stock_uom)

