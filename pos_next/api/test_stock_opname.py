# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Tests for the outlet Stock Opname API (Task 3).

Single-role POSNext Cashier on their own POS Profile; a second profile (and
a foreign warehouse) for access-denied cases. Batch item with two batches,
serial item with three seeded serials.

Run via pos_next/_pn_run_tests.py pos_next.api.test_stock_opname
"""

import unittest
import uuid

import frappe
from frappe.exceptions import TimestampMismatchError
from frappe.model.workflow import WorkflowTransitionError, apply_workflow
from frappe.tests.utils import FrappeTestCase

from pos_next.api.test_production import _make_test_item, _seed_stock
from pos_next.stock_opname_setup import STATE_APPROVED, STATE_DRAFT, STATE_PENDING_SM, STATE_REJECTED

ADMIN = "Administrator"
DOCTYPE = "Stock Reconciliation"


def _make_user(role):
	email = f"so-api-{uuid.uuid4().hex[:10]}@example.com"
	frappe.get_doc({"doctype": "User", "email": email, "first_name": "SO API Test"}).insert(
		ignore_permissions=True
	)
	frappe.get_doc(
		{
			"doctype": "Has Role",
			"parent": email,
			"parenttype": "User",
			"parentfield": "roles",
			"role": role,
		}
	).insert(ignore_permissions=True)
	frappe.clear_cache(user=email)
	return email


class TestStockOpnameApi(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		cls.company = "_Test Company"
		cls.warehouse = (
			frappe.get_doc(
				{"doctype": "Warehouse", "warehouse_name": f"SO-A-{uuid.uuid4().hex[:8]}", "company": cls.company}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.foreign_warehouse = (
			frappe.get_doc(
				{"doctype": "Warehouse", "warehouse_name": f"SO-A-{uuid.uuid4().hex[:8]}", "company": cls.company}
			)
			.insert(ignore_permissions=True)
			.name
		)
		cls.cashier = _make_user("POSNext Cashier")
		cls.stock_manager = _make_user("Stock Manager")
		template = frappe.db.get_value(
			"POS Profile",
			[["disabled", "=", 0], ["pos_schedule_enforce_closing", "=", 0]],
			"name",
			order_by="creation asc",
		)
		if not template:
			raise unittest.SkipTest("no schedule-safe POS Profile")
		cls.profile = cls._clone_profile(template, cls.warehouse, cls.cashier)
		cls.foreign_profile = cls._clone_profile(template, cls.foreign_warehouse, cls.stock_manager)
		cls.plain = _make_test_item()
		cls.other_plain = _make_test_item()
		cls.batch_item = _make_test_item(has_batch_no=1)
		cls.other_batch_item = _make_test_item(has_batch_no=1)
		cls.batch_a = (
			frappe.new_doc("Batch", item=cls.batch_item, batch_id=f"SO-B-{uuid.uuid4().hex[:6]}")
			.insert(ignore_permissions=True)
			.name
		)
		cls.batch_b = (
			frappe.new_doc("Batch", item=cls.batch_item, batch_id=f"SO-B-{uuid.uuid4().hex[:6]}")
			.insert(ignore_permissions=True)
			.name
		)
		cls.foreign_batch = (
			frappe.new_doc("Batch", item=cls.other_batch_item, batch_id=f"SO-B-{uuid.uuid4().hex[:6]}")
			.insert(ignore_permissions=True)
			.name
		)
		cls.serial_item = _make_test_item(has_serial_no=1)
		cls.serials = []
		for _i in range(3):
			cls.serials.append(
				frappe.get_doc(
					{
						"doctype": "Serial No",
						"serial_no": f"SO-S-{uuid.uuid4().hex[:8]}",
						"item_code": cls.serial_item,
						"company": cls.company,
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
		frappe.db.commit()

	@classmethod
	def _clone_profile(cls, template, warehouse, user):
		src = frappe.get_doc("POS Profile", template)
		profile = frappe.copy_doc(src)
		profile.name = f"SO-A-{uuid.uuid4().hex[:8]}"
		profile.company = cls.company
		profile.warehouse = warehouse
		profile.pos_schedule_enabled = 0
		profile.pos_schedule_enforce_closing = 0
		profile.applicable_for_users = []
		profile.insert(ignore_permissions=True, set_name=profile.name)
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": profile.name,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": user,
			}
		).insert(ignore_permissions=True)
		return profile.name

	@classmethod
	def tearDownClass(cls):
		frappe.set_user(ADMIN)
		for profile in (cls.profile, cls.foreign_profile):
			if frappe.db.exists("POS Profile", profile):
				frappe.delete_doc("POS Profile", profile, force=True, ignore_permissions=True)
		for user in (cls.cashier, cls.stock_manager):
			if frappe.db.exists("User", user):
				frappe.delete_doc("User", user, force=True, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		frappe.set_user(ADMIN)
		for name in frappe.get_all(DOCTYPE, {"set_warehouse": ("in", [self.warehouse, self.foreign_warehouse])}, pluck="name"):
			frappe.delete_doc(DOCTYPE, name, force=True, ignore_permissions=True)
		frappe.set_user(ADMIN)
		super().tearDown()

	def _cashier(self):
		frappe.set_user(self.cashier)

	def test_save_get_roundtrip_is_blind(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(
			self.profile,
			{"items": [{"item_code": self.plain, "qty": 4}, {"item_code": self.other_plain, "qty": 0}]},
		)
		got = get_opname(self.profile, payload["name"])
		self.assertEqual(
			set(got["items"][0].keys()),
			{"item_code", "item_name", "stock_uom", "qty", "batch_no", "serial_nos", "has_batch_no", "has_serial_no"},
		)
		self.assertEqual(payload["items"], got["items"])
		qtys = {r["item_code"]: r["qty"] for r in got["items"]}
		self.assertEqual(qtys, {self.plain: 4, self.other_plain: 0})

	def test_batch_item_requires_own_batch(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		with self.assertRaises(frappe.ValidationError):
			save_opname(self.profile, {"items": [{"item_code": self.batch_item, "qty": 2}]})
		with self.assertRaises(frappe.ValidationError):
			save_opname(
				self.profile,
				{"items": [{"item_code": self.batch_item, "qty": 1, "batch_no": self.foreign_batch}]},
			)
		payload = save_opname(
			self.profile, {"items": [{"item_code": self.batch_item, "qty": 2, "batch_no": self.batch_a}]}
		)
		row = frappe.get_doc(DOCTYPE, payload["name"]).items[0]
		self.assertEqual(row.use_serial_batch_fields, 1)
		got = get_opname(self.profile, payload["name"])
		self.assertEqual(got["items"][0]["batch_no"], self.batch_a)

	def test_serial_item_qty_is_serial_count_and_unknown_rejected(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(
			self.profile, {"items": [{"item_code": self.serial_item, "serial_nos": self.serials[:2]}]}
		)
		got = get_opname(self.profile, payload["name"])
		self.assertEqual(got["items"][0]["qty"], 2)
		self.assertEqual(sorted(got["items"][0]["serial_nos"]), sorted(self.serials[:2]))
		with self.assertRaises(frappe.ValidationError) as ctx:
			save_opname(
				self.profile, {"items": [{"item_code": self.serial_item, "serial_nos": ["NOPE-1"]}], "name": payload["name"], "modified": payload["modified"]}
			)
		self.assertIn("NOPE-1", str(ctx.exception))

	def test_serial_empty_list_roundtrips_as_zero_qty_serial_row(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.serial_item, "serial_nos": []}]})
		got = get_opname(self.profile, payload["name"])
		self.assertEqual(got["items"][0]["qty"], 0)
		self.assertEqual(got["items"][0]["serial_nos"], [])

	def test_submit_sets_posting_now_and_state(self):
		from pos_next.api.stock_opname import save_opname, submit_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		result = submit_opname(self.profile, payload["name"])
		self.assertEqual(result["workflow_state"], STATE_PENDING_SM)
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		self.assertEqual(str(doc.posting_date), str(frappe.utils.today()))
		self.assertEqual(doc.docstatus, 0)

	def test_pending_is_read_only_for_outlet(self):
		from pos_next.api.stock_opname import delete_opname, save_opname, submit_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		submit_opname(self.profile, payload["name"])
		with self.assertRaises(frappe.ValidationError):
			save_opname(
				self.profile,
				{"name": payload["name"], "modified": payload["modified"], "items": [{"item_code": self.plain, "qty": 5}]},
			)
		with self.assertRaises(frappe.ValidationError):
			delete_opname(self.profile, payload["name"])

	def test_save_on_rejected_revises_to_draft(self):
		from pos_next.api.stock_opname import list_opnames, save_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		frappe.set_user(ADMIN)
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		doc.db_set("workflow_state", STATE_REJECTED)
		doc.add_comment("Comment", text="Rejected: salah rak")
		frappe.db.commit()
		self._cashier()
		rows = list_opnames(self.profile)
		row = next(r for r in rows if r["name"] == payload["name"])
		self.assertEqual(row["reject_reason"], "salah rak")
		# The HQ comment bumps modified, so reload the snapshot first —
		# saving on the pre-reject snapshot must still fail (U9).
		with self.assertRaises(TimestampMismatchError):
			save_opname(
				self.profile,
				{"name": payload["name"], "modified": payload["modified"], "items": [{"item_code": self.plain, "qty": 7}]},
			)
		from pos_next.api.stock_opname import get_opname

		fresh = get_opname(self.profile, payload["name"])
		again = save_opname(
			self.profile,
			{"name": payload["name"], "modified": fresh["modified"], "items": [{"item_code": self.plain, "qty": 7}]},
		)
		self.assertEqual(again["workflow_state"], STATE_DRAFT)

	def test_foreign_profile_denied(self):
		from pos_next.api import stock_opname

		self._cashier()
		payload = stock_opname.save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		name = payload["name"]
		with self.assertRaises(frappe.PermissionError):
			stock_opname.list_opnames(self.foreign_profile)
		with self.assertRaises(frappe.PermissionError):
			stock_opname.get_opname(self.foreign_profile, name)
		with self.assertRaises(frappe.PermissionError):
			stock_opname.save_opname(
				self.foreign_profile, {"name": name, "modified": payload["modified"], "items": [{"item_code": self.plain, "qty": 1}]}
			)
		with self.assertRaises(frappe.PermissionError):
			stock_opname.submit_opname(self.foreign_profile, name)
		with self.assertRaises(frappe.PermissionError):
			stock_opname.delete_opname(self.foreign_profile, name)
		with self.assertRaises(frappe.PermissionError):
			stock_opname.get_item_batches(self.foreign_profile, self.batch_item)
		with self.assertRaises(frappe.PermissionError):
			stock_opname.get_item_serials(self.foreign_profile, self.serial_item)

	def test_stale_save_rejected(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		first = get_opname(self.profile, payload["name"])
		save_opname(
			self.profile,
			{"name": payload["name"], "modified": first["modified"], "items": [{"item_code": self.plain, "qty": 5}]},
		)
		with self.assertRaises(TimestampMismatchError):
			save_opname(
				self.profile,
				{"name": payload["name"], "modified": first["modified"], "items": [{"item_code": self.plain, "qty": 6}]},
			)

	def test_cashier_cannot_approve(self):
		from pos_next.api.stock_opname import save_opname, submit_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		submit_opname(self.profile, payload["name"])
		with self.assertRaises(WorkflowTransitionError):
			apply_workflow(frappe.get_doc(DOCTYPE, payload["name"]), "Approve")

	def test_owner_cannot_self_approve(self):
		from pos_next.api.stock_opname import save_opname, submit_opname

		# The stock manager is assigned to the profile so they own the doc.
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": self.profile,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": self.stock_manager,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.stock_manager)
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		submit_opname(self.profile, payload["name"])
		with self.assertRaisesRegex(frappe.ValidationError, "Self approval is not allowed"):
			apply_workflow(frappe.get_doc(DOCTYPE, payload["name"]), "Approve")
