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
from frappe.utils import add_days, get_time, nowdate

from pos_next.api.test_production import _make_test_item, _seed_stock
from pos_next.stock_opname_setup import (
	STATE_APPROVED,
	STATE_DRAFT,
	STATE_PENDING_SM,
	STATE_REJECTED,
	WORKFLOW_NAME,
)

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


class _StockOpnameFixture(FrappeTestCase):
	"""Shared fixtures: warehouse + profile + items for both test classes.

	No ``test_`` methods, so unittest collects nothing from this base.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		# Bridge for the shared bench: this worktree's `opname_max_backdate_days`
		# doctype field is not migrated in, so `frappe.db.get_single_value` cannot
		# resolve it. A transient Custom Field stands in until the field is migrated.
		cls._cap_custom_field = None
		if not frappe.get_meta("POS Next Global Settings").has_field("opname_max_backdate_days"):
			cls._cap_custom_field = (
				frappe.get_doc(
					{
						"doctype": "Custom Field",
						"dt": "POS Next Global Settings",
						"fieldname": "opname_max_backdate_days",
						"fieldtype": "Int",
						"label": "Stock Opname Max Backdate Days",
						"default": "2",
					}
				)
				.insert(ignore_permissions=True)
				.name
			)
			frappe.clear_cache(doctype="POS Next Global Settings")
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
		if getattr(cls, "_cap_custom_field", None):
			frappe.db.sql(
				"DELETE FROM tabSingles WHERE doctype = %s AND field = %s",
				("POS Next Global Settings", "opname_max_backdate_days"),
			)
			frappe.delete_doc("Custom Field", cls._cap_custom_field, force=True, ignore_permissions=True)
			frappe.clear_cache(doctype="POS Next Global Settings")
			frappe.db.commit()
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


class TestStockOpnameApi(_StockOpnameFixture):
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
			{
				"item_code",
				"item_name",
				"stock_uom",
				"qty",
				"batch_no",
				"serial_nos",
				"has_batch_no",
				"has_serial_no",
				"custom_uom",
				"custom_qty_after",
				"uoms",
			},
		)
		# native row: the count UOM falls back to stock UOM / native qty
		self.assertEqual(got["items"][0]["custom_uom"], got["items"][0]["stock_uom"])
		self.assertEqual(got["items"][0]["custom_qty_after"], 4)
		self.assertEqual(payload["items"], got["items"])
		qtys = {r["item_code"]: r["qty"] for r in got["items"]}
		self.assertEqual(qtys, {self.plain: 4, self.other_plain: 0})

	def test_payload_has_header_fields(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		got = get_opname(self.profile, payload["name"])
		# read-only detail view header: posting datetime + warehouse (Task 7)
		self.assertEqual(str(got["posting_date"]), str(nowdate()))
		self.assertIsNotNone(got["posting_time"])
		self.assertEqual(got["set_warehouse"], self.warehouse)
		# the blind-count forbidden fields must not leak at header or item level
		forbidden = {
			"current_qty",
			"current_valuation_rate",
			"quantity_difference",
			"amount_difference",
			"custom_qty_before",
			"custom_qty_difference_per_uom",
			"custom_valuation_rate_per_uom",
			"custom_conversion_factor",
			"difference_amount",
			"expense_account",
		}
		self.assertFalse(forbidden & set(got.keys()))
		for item in got["items"]:
			self.assertFalse(forbidden & set(item.keys()))

	def test_payload_owner_fields(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		got = get_opname(self.profile, payload["name"])
		self.assertEqual(got["owner"], self.cashier)
		self.assertEqual(got["owner_fullname"], "SO API Test")
		self.assertTrue(got["creation"])
		self.assertIn(int(got["set_posting_time"]), (0, 1))

	def test_payload_activity_after_reject(self):
		from pos_next.api.stock_opname import (
			attach_opname_files,
			get_opname,
			save_opname,
			submit_opname,
		)

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		submit_opname(self.profile, payload["name"])
		frappe.set_user(self.stock_manager)
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		apply_workflow(doc, "Reject")
		doc.add_comment("Comment", text="Rejected: count again")
		frappe.db.commit()

		self._cashier()
		attach_opname_files(self.profile, payload["name"], link="https://example.com/sheet")
		activity = get_opname(self.profile, payload["name"])["activity"]

		self.assertEqual(activity[0]["type"], "created")
		self.assertEqual(activity[0]["user"], self.cashier)
		self.assertTrue(any(e["type"] == "workflow" and e.get("state") == STATE_PENDING_SM for e in activity))
		self.assertTrue(any(e["type"] == "comment" and e.get("content") == "count again" for e in activity))
		attachments = [e for e in activity if e["type"] == "attachment"]
		self.assertTrue(attachments)
		self.assertNotIn("<", attachments[0]["content"])
		ats = [e["at"] for e in activity]
		self.assertEqual(ats, sorted(ats))

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

	def test_serial_row_duplicate_serials_deduped(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(
			self.profile,
			{
				"items": [
					{"item_code": self.serial_item, "serial_nos": [self.serials[0], self.serials[0], self.serials[1]]}
				]
			},
		)
		got = get_opname(self.profile, payload["name"])
		self.assertEqual(got["items"][0]["qty"], 2)
		self.assertEqual(got["items"][0]["serial_nos"].count(self.serials[0]), 1)

	def test_save_invalid_on_rejected_stays_rejected(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		frappe.set_user(ADMIN)
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		doc.db_set("workflow_state", STATE_REJECTED)
		frappe.db.commit()
		self._cashier()
		fresh = get_opname(self.profile, payload["name"])
		# A batch item without a batch is invalid: the save must fail before any
		# Revise transition, so the doc stays Rejected (I2).
		with self.assertRaises(frappe.ValidationError) as ctx:
			save_opname(
				self.profile,
				{
					"name": payload["name"],
					"modified": fresh["modified"],
					"items": [{"item_code": self.batch_item, "qty": 1}],
				},
			)
		self.assertIn(self.batch_item, str(ctx.exception))
		frappe.set_user(ADMIN)
		self.assertEqual(frappe.db.get_value(DOCTYPE, payload["name"], "workflow_state"), STATE_REJECTED)

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
		with self.assertRaises(TimestampMismatchError) as ctx:
			save_opname(
				self.profile,
				{"name": payload["name"], "modified": first["modified"], "items": [{"item_code": self.plain, "qty": 6}]},
			)
		self.assertIn("modified, reload", str(ctx.exception))

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

	def test_attach_file_and_link_then_remove(self):
		import base64

		from pos_next.api.stock_opname import (
			attach_opname_files,
			get_opname,
			remove_opname_attachment,
			save_opname,
			submit_opname,
		)

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		name = payload["name"]
		self.assertEqual(payload["attachments"], [])
		files = [{"file_name": "count.txt", "filedata": base64.b64encode(uuid.uuid4().hex.encode()).decode()}]
		rows = attach_opname_files(self.profile, name, files=frappe.as_json(files), link="https://example.com/sheet")
		self.assertEqual(sorted(r["file_name"] for r in rows), ["count.txt", "https://example.com/sheet"])
		got = get_opname(self.profile, name)
		self.assertEqual(len(got["attachments"]), 2)
		# attaching must not bump modified, or the next Save hits the stale check
		self.assertEqual(str(got["modified"]), str(payload["modified"]))
		with self.assertRaises(frappe.ValidationError):
			attach_opname_files(self.profile, name, link="javascript:alert(1)")
		# a File of another document cannot be removed through this opname
		frappe.set_user(ADMIN)
		foreign = frappe.get_doc(
			{"doctype": "File", "file_url": "https://example.com/other", "attached_to_doctype": "Item", "attached_to_name": self.plain}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "File", foreign.name, ignore_permissions=True)
		self._cashier()
		with self.assertRaises(frappe.PermissionError):
			remove_opname_attachment(self.profile, name, foreign.name)
		link_row = next(r for r in rows if r["file_name"].startswith("https://"))
		self.assertEqual(len(remove_opname_attachment(self.profile, name, link_row["name"])), 1)
		# pending approval: attachments are frozen
		submit_opname(self.profile, name)
		with self.assertRaises(frappe.ValidationError):
			attach_opname_files(self.profile, name, link="https://example.com/late")

	def test_attach_rejects_foreign_profile(self):
		from pos_next.api.stock_opname import attach_opname_files, save_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		with self.assertRaises(frappe.PermissionError):
			attach_opname_files(self.foreign_profile, payload["name"], link="https://example.com/x")


class TestStockOpnameExcel(_StockOpnameFixture):
	"""Task 4: Excel template download and upload-merge (spec U7, U8)."""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		# Count UOM for the Excel import tests: Pack x12, Dus absent (rejected).
		cls.uom_item = _make_test_item(uoms=[{"uom": "Pack", "conversion_factor": 12}])
		# Stock in cls.warehouse so every fixture item has a Bin there (the
		# template source). Plain + batch via _seed_stock; serials need a
		# serial_no column on the receipt row.
		_seed_stock(cls.plain, cls.warehouse, 5)
		_seed_stock(cls.batch_item, cls.warehouse, 3, batch_no=cls.batch_a)
		_seed_stock(cls.batch_item, cls.warehouse, 2, batch_no=cls.batch_b)
		se = frappe.new_doc("Stock Entry")
		se.purpose = "Material Receipt"
		se.company = cls.company
		se.set_stock_entry_type()
		se.append(
			"items",
			{
				"item_code": cls.serial_item,
				"qty": len(cls.serials),
				"t_warehouse": cls.warehouse,
				"use_serial_batch_fields": 1,
				"basic_rate": 10,
				"serial_no": "\n".join(cls.serials),
			},
		)
		se.insert()
		se.submit()
		frappe.db.commit()

	def _attach(self, rows):
		"""Attach an in-memory workbook as a private File owned by the session user."""
		from frappe.utils.xlsxutils import make_xlsx

		content = make_xlsx(rows, "opname").getvalue()
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"so-{uuid.uuid4().hex[:8]}.xlsx",
				"content": content,
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		return file.file_url

	def _template_data_rows(self, item_group=None):
		self._cashier()
		frappe.local.response = frappe._dict()
		from pos_next.api.stock_opname import download_opname_template

		download_opname_template(self.profile, item_group)
		from io import BytesIO

		from openpyxl import load_workbook

		ws = load_workbook(BytesIO(frappe.local.response["filecontent"])).active
		return [[c.value for c in row] for row in ws.iter_rows()]

	def test_template_has_header_and_blind_columns(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER

		rows = self._template_data_rows()
		self.assertEqual(rows[0], TEMPLATE_HEADER)
		data = rows[1:]
		# Blind count: no counted qty is pre-filled.
		self.assertTrue(all(r[5] in (None, "") for r in data))
		batch_rows = [r for r in data if r[0] == self.batch_item]
		self.assertEqual({r[3] for r in batch_rows}, {self.batch_a, self.batch_b})
		serial_rows = [r for r in data if r[0] == self.serial_item]
		self.assertEqual({r[4] for r in serial_rows}, set(self.serials))
		self.assertEqual(len([r for r in data if r[0] == self.plain]), 1)

	def test_template_item_group_filter_includes_descendants(self):
		from pos_next.api.stock_opname import _template_rows

		frappe.set_user(ADMIN)
		parent = (
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": f"SO-P-{uuid.uuid4().hex[:6]}",
					"parent_item_group": "All Item Groups",
					"is_group": 1,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		child = (
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": f"SO-C-{uuid.uuid4().hex[:6]}",
					"parent_item_group": parent,
					"is_group": 0,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		other = (
			frappe.get_doc(
				{
					"doctype": "Item Group",
					"item_group_name": f"SO-Q-{uuid.uuid4().hex[:6]}",
					"parent_item_group": "All Item Groups",
					"is_group": 0,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		item = _make_test_item(item_group=child)
		_seed_stock(item, self.warehouse, 1)
		frappe.db.commit()

		self.assertIn(item, [r[0] for r in _template_rows(self.warehouse, parent)])
		self.assertIn(item, [r[0] for r in _template_rows(self.warehouse, child)])
		self.assertNotIn(item, [r[0] for r in _template_rows(self.warehouse, other)])

	def test_import_merges_and_keeps_rows_not_in_file(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, get_opname, import_opname_template, save_opname

		self._cashier()
		draft = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 3}]})
		url = self._attach(
			[
				TEMPLATE_HEADER,
				[self.other_plain, "", "", "", "", 4],
				[self.plain, "", "", "", "", ""],
			]
		)
		result = import_opname_template(self.profile, url, name=draft["name"])
		self.assertEqual(result["applied"], 1)
		got = get_opname(self.profile, draft["name"])
		self.assertEqual({r["item_code"]: r["qty"] for r in got["items"]}, {self.plain: 3, self.other_plain: 4})

	def test_import_zero_means_counted_none(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, get_opname, import_opname_template

		self._cashier()
		url = self._attach([TEMPLATE_HEADER, [self.plain, "", "", "", "", 0]])
		result = import_opname_template(self.profile, url)
		self.assertEqual(result["applied"], 1)
		got = get_opname(self.profile, result["name"])
		self.assertEqual(got["items"][0]["qty"], 0)

	def test_import_serial_presence_toggles(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, get_opname, import_opname_template, save_opname

		self._cashier()
		draft = save_opname(
			self.profile,
			{"items": [{"item_code": self.serial_item, "serial_nos": [self.serials[1], self.serials[2]]}]},
		)
		url = self._attach(
			[
				TEMPLATE_HEADER,
				[self.serial_item, "", "", "", self.serials[0], 1],
				[self.serial_item, "", "", "", self.serials[1], 0],
			]
		)
		result = import_opname_template(self.profile, url, name=draft["name"])
		self.assertEqual(result["applied"], 2)
		row = get_opname(self.profile, draft["name"])["items"][0]
		self.assertEqual(sorted(row["serial_nos"]), sorted([self.serials[0], self.serials[2]]))
		self.assertEqual(row["qty"], 2)

	def test_import_all_serials_absent_writes_zero_row(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, get_opname, import_opname_template, save_opname

		self._cashier()
		# Draft has no row for the serial item; the file marks every serial absent.
		draft = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 1}]})
		url = self._attach(
			[
				TEMPLATE_HEADER,
				[self.serial_item, "", "", "", self.serials[0], 0],
				[self.serial_item, "", "", "", self.serials[1], 0],
				[self.serial_item, "", "", "", self.serials[2], 0],
			]
		)
		result = import_opname_template(self.profile, url, name=draft["name"])
		self.assertEqual(result["applied"], 3)
		rows = {r["item_code"]: r for r in get_opname(self.profile, draft["name"])["items"]}
		self.assertIn(self.serial_item, rows)
		self.assertEqual(rows[self.serial_item]["serial_nos"], [])
		self.assertEqual(rows[self.serial_item]["qty"], 0)

	def test_import_reports_bad_rows_and_applies_good(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, import_opname_template

		self._cashier()
		url = self._attach(
			[
				TEMPLATE_HEADER,
				[self.plain, "", "", "", "", 4],  # row 2: good
				[f"NOPE-{uuid.uuid4().hex[:6]}", "", "", "", "", 1],  # row 3: unknown item
				[self.batch_item, "", "", self.foreign_batch, "", 1],  # row 4: batch of another item
				[self.serial_item, "", "", "", "NOPE-SER-1", 1],  # row 5: serial not of item
				[self.other_plain, "", "", "", "", "abc"],  # row 6: non-numeric qty
				[self.plain, "", "", "", "", 9],  # row 7: duplicate key
			]
		)
		result = import_opname_template(self.profile, url)
		self.assertEqual(result["applied"], 1)
		self.assertEqual(len(result["errors"]), 5)
		self.assertEqual(sorted(e["row"] for e in result["errors"]), [3, 4, 5, 6, 7])

	def test_import_into_pending_rejected(self):
		from pos_next.api.stock_opname import (
			TEMPLATE_HEADER,
			get_opname,
			import_opname_template,
			save_opname,
			submit_opname,
		)

		self._cashier()
		draft = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		submit_opname(self.profile, draft["name"])
		url = self._attach([TEMPLATE_HEADER, [self.plain, "", "", "", "", 5]])
		with self.assertRaises(frappe.ValidationError):
			import_opname_template(self.profile, url, name=draft["name"])
		# Rejected target is revised back to Draft and the file applies.
		frappe.set_user(ADMIN)
		doc = frappe.get_doc(DOCTYPE, draft["name"])
		doc.db_set("workflow_state", STATE_REJECTED)
		frappe.db.commit()
		self._cashier()
		url2 = self._attach([TEMPLATE_HEADER, [self.plain, "", "", "", "", 5]])
		result = import_opname_template(self.profile, url2, name=draft["name"])
		self.assertEqual(result["applied"], 1)
		got = get_opname(self.profile, draft["name"])
		self.assertEqual(got["workflow_state"], STATE_DRAFT)
		self.assertEqual(got["items"][0]["qty"], 5)

	def test_import_supports_uom_column(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, get_opname, import_opname_template

		self._cashier()
		url = self._attach([TEMPLATE_HEADER, [self.uom_item, "", "Pack", "", "", 2]])
		result = import_opname_template(self.profile, url)
		self.assertEqual(result["errors"], [])
		self.assertEqual(result["applied"], 1)
		row = get_opname(self.profile, result["name"])["items"][0]
		self.assertEqual(row["custom_uom"], "Pack")
		self.assertEqual(row["custom_qty_after"], 2)
		self.assertEqual(row["qty"], 24)  # 2 Pack x 12

	def test_import_rejects_unknown_uom_per_row(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, import_opname_template

		self._cashier()
		url = self._attach(
			[
				TEMPLATE_HEADER,
				[self.uom_item, "", "Dus", "", "", 1],  # row 2: UOM not on the item
				[self.plain, "", "", "", "", 4],  # row 3: good, still applies
			]
		)
		result = import_opname_template(self.profile, url)
		self.assertEqual(result["applied"], 1)
		self.assertEqual(len(result["errors"]), 1)
		err = result["errors"][0]
		self.assertEqual(err["row"], 2)
		self.assertEqual(err["item_code"], self.uom_item)
		self.assertIn("Dus", err["message"])

	def test_import_serial_row_rejects_uom(self):
		from pos_next.api.stock_opname import TEMPLATE_HEADER, import_opname_template

		self._cashier()
		url = self._attach([TEMPLATE_HEADER, [self.serial_item, "", "Pack", "", self.serials[0], 1]])
		result = import_opname_template(self.profile, url)
		self.assertEqual(result["applied"], 0)
		self.assertEqual(len(result["errors"]), 1)
		self.assertIn(self.serial_item, result["errors"][0]["message"])


class TestStockOpnameUom(_StockOpnameFixture):
	"""Task 2: outlet counts entered in a non-stock UOM convert to native qty.

	The item carries a ``Pack`` UOM Conversion (x12); ``Dus`` exists as a UOM
	but is not on the item, so it must be rejected per row.
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		cls.uom_item = _make_test_item(uoms=[{"uom": "Pack", "conversion_factor": 12}])
		frappe.db.commit()

	def test_uom_converts_to_native_qty(self):
		from pos_next.api.stock_opname import _validated_rows, save_opname

		row = _validated_rows(
			[{"item_code": self.uom_item, "custom_uom": "Pack", "custom_qty_after": 2}], self.warehouse
		)[0]
		self.assertEqual(row["qty"], 24)
		self.assertEqual(row["custom_uom"], "Pack")
		self.assertEqual(row["custom_qty_after"], 2)
		self.assertEqual(row["custom_conversion_factor"], 12)
		# The warehouse_app hook recomputes qty from the stored custom fields on
		# save; the persisted native qty must stay 2 Pack x 12.
		self._cashier()
		payload = save_opname(
			self.profile,
			{"items": [{"item_code": self.uom_item, "custom_uom": "Pack", "custom_qty_after": 2}]},
		)
		stored = frappe.get_doc(DOCTYPE, payload["name"]).items[0]
		self.assertEqual(stored.qty, 24)
		self.assertEqual(stored.custom_uom, "Pack")
		self.assertEqual(stored.custom_qty_after, 2)

	def test_payload_offers_alt_uoms(self):
		"""A reloaded count keeps its UOM options (search sends the same shape)."""
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(
			self.profile,
			{"items": [{"item_code": self.uom_item, "custom_uom": "Pack", "custom_qty_after": 2}]},
		)
		row = get_opname(self.profile, payload["name"])["items"][0]
		self.assertEqual([u["uom"] for u in row["uoms"]], ["Pack"])

	def test_list_paginates(self):
		from pos_next.api.stock_opname import list_opnames, save_opname

		self._cashier()
		first = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 1}]})
		# close it without posting stock, so the one-open-count guard allows a second
		frappe.db.set_value(DOCTYPE, first["name"], "docstatus", 2)
		save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 2}]})
		all_rows = list_opnames(self.profile)
		self.assertGreaterEqual(len(all_rows), 2)
		page1 = list_opnames(self.profile, start=0, page_length=1)
		page2 = list_opnames(self.profile, start=1, page_length=1)
		self.assertEqual([r["name"] for r in page1 + page2], [r["name"] for r in all_rows[:2]])

	def test_unknown_uom_rejected_per_row(self):
		from pos_next.api.stock_opname import _validated_rows

		with self.assertRaises(frappe.ValidationError) as ctx:
			_validated_rows(
				[{"item_code": self.uom_item, "custom_uom": "Dus", "custom_qty_after": 1}], self.warehouse
			)
		self.assertIn(self.uom_item, str(ctx.exception))
		self.assertIn("Dus", str(ctx.exception))

	def test_serial_row_rejects_custom_uom(self):
		from pos_next.api.stock_opname import _validated_rows

		with self.assertRaises(frappe.ValidationError) as ctx:
			_validated_rows(
				[
					{
						"item_code": self.serial_item,
						"serial_nos": [self.serials[0]],
						"custom_uom": "Pack",
						"custom_qty_after": 1,
					}
				],
				self.warehouse,
			)
		self.assertIn(self.serial_item, str(ctx.exception))

	def test_missing_custom_fields_still_posts_stock_qty(self):
		from unittest import mock

		from pos_next.api.stock_opname import _validated_rows

		real_meta = frappe.get_meta("Stock Reconciliation Item")
		real_get_meta = frappe.get_meta

		class _NoUomMeta:
			def has_field(self, fieldname):
				return fieldname != "custom_uom" and real_meta.has_field(fieldname)

			def get_field(self, fieldname):
				return real_meta.get_field(fieldname)

		def fake_get_meta(doctype, *args, **kwargs):
			if doctype == "Stock Reconciliation Item":
				return _NoUomMeta()
			return real_get_meta(doctype, *args, **kwargs)

		with mock.patch.object(frappe, "get_meta", fake_get_meta):
			# A non-stock UOM cannot be represented without the custom fields:
			# reject per row rather than post it as a stock-UOM count.
			with self.assertRaises(frappe.ValidationError) as ctx:
				_validated_rows(
					[{"item_code": self.uom_item, "custom_uom": "Pack", "custom_qty_after": 2}], self.warehouse
				)
			self.assertIn(self.uom_item, str(ctx.exception))
			self.assertIn("UOM", str(ctx.exception))
			row = _validated_rows([{"item_code": self.plain, "qty": 5}], self.warehouse)[0]
		self.assertEqual(row["qty"], 5)
		self.assertNotIn("custom_uom", row)
		self.assertNotIn("custom_qty_after", row)
		self.assertNotIn("custom_conversion_factor", row)

	def test_payload_roundtrips_uom(self):
		from pos_next.api.stock_opname import get_opname, save_opname

		self._cashier()
		payload = save_opname(
			self.profile,
			{"items": [{"item_code": self.uom_item, "custom_uom": "Pack", "custom_qty_after": 2}]},
		)
		row = get_opname(self.profile, payload["name"])["items"][0]
		self.assertEqual(row["custom_uom"], "Pack")
		self.assertEqual(row["custom_qty_after"], 2)
		self.assertEqual(row["qty"], 24)

	def test_draft_clears_hook_system_qty_fields(self):
		from frappe.utils import flt

		from pos_next.api.stock_opname import save_opname

		# 24 native = 2 Pack in stock, so the warehouse_app hook fills a non-zero
		# custom_qty_before / difference on the draft; the outlet must never see it.
		_seed_stock(self.uom_item, self.warehouse, 24)
		frappe.db.commit()
		self._cashier()
		payload = save_opname(
			self.profile,
			{"items": [{"item_code": self.uom_item, "custom_uom": "Pack", "custom_qty_after": 3}]},
		)
		frappe.set_user(ADMIN)
		row = frappe.get_doc(DOCTYPE, payload["name"]).items[0]
		self.assertEqual(flt(row.custom_qty_before), 0)
		self.assertEqual(flt(row.custom_qty_difference_per_uom), 0)


class TestStockOpnameSystemQty(_StockOpnameFixture):
	"""HQ `get_system_qty`: live system qty/difference for a draft opname.

	Seeds stock so the numbers are known (basic_rate 10). Display only: the
	endpoint is HQ-only and must not persist anything (U4 blind count).
	"""

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		_seed_stock(cls.plain, cls.warehouse, 5)
		_seed_stock(cls.batch_item, cls.warehouse, 3, batch_no=cls.batch_a)
		_seed_stock(cls.batch_item, cls.warehouse, 2, batch_no=cls.batch_b)
		frappe.db.commit()

	def _draft(self, items):
		from pos_next.api.stock_opname import save_opname

		self._cashier()
		return save_opname(self.profile, {"items": items})["name"]

	def test_hq_gets_system_qty_and_difference(self):
		from pos_next.api.stock_opname import get_system_qty

		name = self._draft([{"item_code": self.plain, "qty": 4}])
		frappe.set_user(self.stock_manager)
		row = get_system_qty(name)["items"][0]
		self.assertEqual(row["counted_qty"], 4)
		self.assertEqual(row["system_qty"], 5)
		self.assertEqual(row["difference_qty"], -1)
		self.assertEqual(row["valuation_rate"], 10)
		self.assertEqual(row["amount_difference"], -10)

	def test_batch_row_returns_that_batch_qty(self):
		from pos_next.api.stock_opname import get_system_qty

		name = self._draft([{"item_code": self.batch_item, "qty": 1, "batch_no": self.batch_a}])
		frappe.set_user(self.stock_manager)
		row = get_system_qty(name)["items"][0]
		self.assertEqual(row["batch_no"], self.batch_a)
		self.assertEqual(row["system_qty"], 3)
		self.assertEqual(row["difference_qty"], -2)

	def test_outlet_cashier_denied(self):
		from pos_next.api.stock_opname import get_system_qty

		name = self._draft([{"item_code": self.plain, "qty": 4}])
		self._cashier()
		with self.assertRaises(frappe.PermissionError):
			get_system_qty(name)

	def test_db_row_values_unchanged(self):
		from pos_next.api.stock_opname import get_system_qty

		name = self._draft([{"item_code": self.plain, "qty": 4}])
		before = frappe.db.get_value(
			"Stock Reconciliation Item",
			{"parent": name},
			["current_qty", "amount_difference"],
			as_dict=True,
		)
		frappe.set_user(self.stock_manager)
		get_system_qty(name)
		after = frappe.db.get_value(
			"Stock Reconciliation Item",
			{"parent": name},
			["current_qty", "amount_difference"],
			as_dict=True,
		)
		self.assertEqual(after.current_qty, 0)
		self.assertEqual(before, after)


class TestStockOpnameSearch(_StockOpnameFixture):
	"""Task 4 blind search: no system qty, zero-stock items included, barcode match."""

	@staticmethod
	def _token(item_code):
		# The unique single-token tail of the generated code ("PRD-T-xxxx": the
		# fulltext index tokenizes on the dashes).
		return item_code.rsplit("-", 1)[-1]

	def _search(self, term, **kwargs):
		from pos_next.api.stock_opname import search_opname_items

		self._cashier()
		return search_opname_items(self.profile, search_term=term, **kwargs)

	def test_blind_search_has_no_system_qty(self):
		rows = self._search(self._token(self.plain))
		row = next(r for r in rows if r["item_code"] == self.plain)
		# exactly the fields the count UI needs — never actual_qty/current_qty
		self.assertEqual(
			set(row.keys()),
			{"item_code", "item_name", "stock_uom", "has_batch_no", "has_serial_no", "uoms"},
		)

	def test_blind_search_finds_zero_stock_item(self):
		# No stock is seeded in this class: the item has no Bin row yet is returned.
		rows = self._search(self._token(self.plain))
		self.assertIn(self.plain, [r["item_code"] for r in rows])

	def test_blind_search_barcode_match(self):
		frappe.set_user(ADMIN)
		barcode = f"BC{uuid.uuid4().hex[:10]}"
		item = frappe.get_doc("Item", self.plain)
		item.append("barcodes", {"barcode": barcode})
		item.save(ignore_permissions=True)
		frappe.db.commit()
		rows = self._search(barcode)
		self.assertIn(self.plain, [r["item_code"] for r in rows])


class TestStockOpnamePosting(_StockOpnameFixture):
	"""Task 5: guarded posting date/time on outlet save and submit (amended D6)."""

	def _save(self, **extra):
		from pos_next.api.stock_opname import save_opname

		self._cashier()
		payload = {"items": [{"item_code": self.plain, "qty": 4}]}
		payload.update(extra)
		return save_opname(self.profile, payload)

	def test_future_posting_rejected(self):
		# tomorrow is a future datetime in the site timezone -> rejected
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._save(posting_date=add_days(nowdate(), 1), posting_time="10:00:00")
		self.assertIn("future", str(ctx.exception).lower())

	def test_backdate_beyond_cap_rejected(self):
		# default cap is 2 days: 5 days back is rejected, 2 days back is accepted
		with self.assertRaises(frappe.ValidationError):
			self._save(posting_date=add_days(nowdate(), -5), posting_time="09:00:00")
		ok = add_days(nowdate(), -2)
		payload = self._save(posting_date=ok, posting_time="09:00:00")
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		self.assertEqual(str(doc.posting_date), ok)
		self.assertEqual(get_time(doc.posting_time), get_time("09:00:00"))
		self.assertEqual(doc.set_posting_time, 1)

	@staticmethod
	def _set_cap(value):
		frappe.db.set_single_value("POS Next Global Settings", "opname_max_backdate_days", value)

	def test_backdate_cap_from_global_setting(self):
		from pos_next.api.stock_opname import save_opname

		self.addCleanup(self._set_cap, "")
		self._set_cap(5)
		self._cashier()
		ok = add_days(nowdate(), -4)
		payload = save_opname(
			self.profile,
			{"items": [{"item_code": self.plain, "qty": 4}], "posting_date": ok, "posting_time": "09:00:00"},
		)
		self.assertEqual(str(frappe.get_doc(DOCTYPE, payload["name"]).posting_date), ok)
		with self.assertRaises(frappe.ValidationError):
			save_opname(
				self.profile,
				{
					"items": [{"item_code": self.plain, "qty": 4}],
					"posting_date": add_days(nowdate(), -6),
					"posting_time": "09:00:00",
				},
			)

	def test_backdate_cap_default(self):
		from pos_next.api.stock_opname import save_opname

		self.addCleanup(self._set_cap, "")
		self._set_cap("")
		self._cashier()
		with self.assertRaises(frappe.ValidationError):
			save_opname(
				self.profile,
				{
					"items": [{"item_code": self.plain, "qty": 4}],
					"posting_date": add_days(nowdate(), -3),
					"posting_time": "09:00:00",
				},
			)

	def test_frozen_stock_blocks_outlet(self):
		from pos_next.api.stock_opname import save_opname

		prev = frappe.db.get_singles_dict("Stock Settings")
		self.addCleanup(
			frappe.db.set_single_value, "Stock Settings", "stock_frozen_upto", prev.get("stock_frozen_upto") or ""
		)
		self.addCleanup(
			frappe.db.set_single_value, "Stock Settings", "stock_auth_role", prev.get("stock_auth_role") or ""
		)
		frappe.db.set_single_value("Stock Settings", "stock_frozen_upto", nowdate())
		frappe.db.set_single_value("Stock Settings", "stock_auth_role", "Stock Manager")
		frappe.clear_cache(doctype="Stock Settings")

		yesterday = add_days(nowdate(), -1)
		# the outlet user has no stock_auth_role -> blocked on a frozen date
		with self.assertRaises(frappe.ValidationError):
			self._save(posting_date=yesterday, posting_time="09:00:00")
		# a user holding the auth role (Administrator holds every role) may post it
		frappe.set_user(ADMIN)
		payload = save_opname(
			self.profile,
			{"items": [{"item_code": self.plain, "qty": 4}], "posting_date": yesterday, "posting_time": "09:00:00"},
		)
		self.assertEqual(str(frappe.get_doc(DOCTYPE, payload["name"]).posting_date), yesterday)

	def test_submit_preserves_outlet_date(self):
		from pos_next.api.stock_opname import submit_opname

		yesterday = add_days(nowdate(), -1)
		payload = self._save(posting_date=yesterday, posting_time="08:30:00")
		submit_opname(self.profile, payload["name"])
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		self.assertEqual(str(doc.posting_date), yesterday)
		self.assertEqual(get_time(doc.posting_time), get_time("08:30:00"))
		self.assertEqual(doc.set_posting_time, 1)

	def test_submit_stamps_now_when_unset(self):
		from pos_next.api.stock_opname import submit_opname

		payload = self._save()
		submit_opname(self.profile, payload["name"])
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		self.assertEqual(str(doc.posting_date), str(nowdate()))
		self.assertEqual(doc.set_posting_time, 1)


# Admin-defined flow replacing `POS Next Stock Opname` for the class below:
# Open -> Area Check -> HQ Check -> Done, with a Returned branch back to Open.
CUSTOM_WORKFLOW = "SO Test Flow"
CUSTOM_STATES = (
	("Open", 0, "POSNext Cashier"),
	("Area Check", 0, "Stock Manager"),
	("HQ Check", 0, "Stock Manager"),
	("Done", 1, "Accounts Manager"),
	("Returned", 0, "POSNext Cashier"),
	("Void", 2, "Stock Manager"),
)
CUSTOM_TRANSITIONS = (
	("Open", "Send", "Area Check", "POSNext Cashier"),
	("Area Check", "Pass", "HQ Check", "Stock Manager"),
	("HQ Check", "Pass", "Done", "Stock Manager"),
	("Area Check", "Return", "Returned", "Stock Manager"),
	("Area Check", "Return", "Returned", "Accounts Manager"),
	("HQ Check", "Return", "Returned", "Stock Manager"),
	("HQ Check", "Return", "Returned", "Accounts Manager"),
	("Returned", "Fix", "Open", "POSNext Cashier"),
)


class TestStockOpnameCustomWorkflow(_StockOpnameFixture):
	"""Task 1: the API follows the *active* workflow, never hardcoded states.

	An admin-defined flow (states/actions nothing like the shipped one) drives
	editable/returned/actions; activating it deactivates `POS Next Stock
	Opname`, which tearDownClass restores.
	"""

	@classmethod
	def _remove_workflow(cls):
		frappe.set_user(ADMIN)
		if frappe.db.exists("Workflow", CUSTOM_WORKFLOW):
			frappe.delete_doc("Workflow", CUSTOM_WORKFLOW, force=True, ignore_permissions=True)
		frappe.cache.hdel("workflow", DOCTYPE)

	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		cls._remove_workflow()
		# The custom state names need Workflow State masters: the doc's
		# workflow_state is a Link and would fail validation without them.
		cls._created_states = []
		for state, _doc_status, _role in CUSTOM_STATES:
			if not frappe.db.exists("Workflow State", state):
				frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state}).insert(
					ignore_permissions=True
				)
				cls._created_states.append(state)
		# ignore_links: the custom action names have no Workflow Action Master rows.
		frappe.get_doc(
			{
				"doctype": "Workflow",
				"workflow_name": CUSTOM_WORKFLOW,
				"document_type": DOCTYPE,
				"workflow_state_field": "workflow_state",
				"is_active": 1,
				"send_email_alert": 0,
				"states": [
					{"state": state, "doc_status": doc_status, "allow_edit": role}
					for state, doc_status, role in CUSTOM_STATES
				],
				"transitions": [
					{"state": frm, "action": action, "next_state": to, "allowed": role, "allow_self_approval": 1}
					for frm, action, to, role in CUSTOM_TRANSITIONS
				],
			}
		).insert(ignore_permissions=True, ignore_links=True)
		# Activating this flow deactivated `POS Next Stock Opname`; refresh the cache.
		frappe.cache.hdel("workflow", DOCTYPE)
		_seed_stock(cls.plain, cls.warehouse, 5)
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		cls._remove_workflow()
		for state in getattr(cls, "_created_states", []):
			if frappe.db.exists("Workflow State", state):
				frappe.delete_doc("Workflow State", state, force=True, ignore_permissions=True)
		if frappe.db.exists("Workflow", WORKFLOW_NAME):
			frappe.db.set_value("Workflow", WORKFLOW_NAME, "is_active", 1)
		frappe.cache.hdel("workflow", DOCTYPE)
		frappe.db.commit()
		super().tearDownClass()

	def tearDown(self):
		# This flow can submit the doc (docstatus 1); the shared teardown only
		# deletes drafts, so cancel the submitted ones first.
		frappe.set_user(ADMIN)
		for name in frappe.get_all(
			DOCTYPE,
			{"docstatus": 1, "set_warehouse": ("in", [self.warehouse, self.foreign_warehouse])},
			pluck="name",
		):
			frappe.get_doc(DOCTYPE, name).cancel()
		super().tearDown()

	def test_custom_flow_full_cycle(self):
		from pos_next.api.stock_opname import get_opname, save_opname, submit_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		self.assertTrue(payload["editable"])
		self.assertEqual(payload["actions"], ["Send"])

		result = submit_opname(self.profile, payload["name"], action="Send")
		self.assertEqual(result["workflow_state"], "Area Check")
		got = get_opname(self.profile, payload["name"])
		self.assertFalse(got["editable"])
		self.assertEqual(got["actions"], [])

		# HQ returns the count with a reason comment
		frappe.set_user(self.stock_manager)
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		apply_workflow(doc, "Return")
		doc.add_comment("Comment", text="Rejected: too low")
		frappe.db.commit()

		self._cashier()
		got = get_opname(self.profile, payload["name"])
		self.assertTrue(got["returned"])
		self.assertTrue(got["editable"])
		self.assertEqual(got["reject_reason"], "too low")

		again = save_opname(
			self.profile,
			{"name": payload["name"], "modified": got["modified"], "items": [{"item_code": self.plain, "qty": 6}]},
		)
		self.assertEqual(again["workflow_state"], "Open")

		submit_opname(self.profile, payload["name"], action="Send")

		frappe.set_user(self.stock_manager)
		apply_workflow(frappe.get_doc(DOCTYPE, payload["name"]), "Pass")  # Area Check -> HQ Check
		apply_workflow(frappe.get_doc(DOCTYPE, payload["name"]), "Pass")  # HQ Check -> Done (submits)
		doc = frappe.get_doc(DOCTYPE, payload["name"])
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.workflow_state, "Done")

	def test_list_flags_for_extra_stage(self):
		from pos_next.api.stock_opname import list_opnames, save_opname, submit_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		submit_opname(self.profile, payload["name"], action="Send")
		frappe.set_user(self.stock_manager)
		apply_workflow(frappe.get_doc(DOCTYPE, payload["name"]), "Pass")  # -> HQ Check
		frappe.db.commit()

		self._cashier()
		row = next(r for r in list_opnames(self.profile) if r["name"] == payload["name"])
		self.assertEqual(row["workflow_state"], "HQ Check")
		self.assertFalse(row["editable"])
		self.assertFalse(row["returned"])
		self.assertFalse(row["processing"])

	def test_pending_stage_not_editable_for_hq(self):
		"""Spec N14: an approval stage is never editable, even for its allow_edit role."""
		from pos_next.api.stock_opname import get_opname, save_opname, submit_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		submit_opname(self.profile, payload["name"], action="Send")  # -> Area Check

		# Stock Manager holds Area Check's allow_edit role; give it access to the
		# profile too, so it reaches the editability check like a real HQ user.
		row = frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": self.profile,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": self.stock_manager,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.delete_doc, "POS Profile User", row.name, force=1, ignore_permissions=True)
		frappe.set_user(self.stock_manager)
		got = get_opname(self.profile, payload["name"])
		self.assertEqual(got["workflow_state"], "Area Check")
		self.assertFalse(got["editable"])
		with self.assertRaisesRegex(frappe.ValidationError, "can no longer be edited"):
			save_opname(
				self.profile,
				{
					"name": payload["name"],
					"modified": payload["modified"],
					"items": [{"item_code": self.plain, "qty": 9}],
				},
			)

	def test_submit_with_unknown_action_rejected(self):
		from pos_next.api.stock_opname import save_opname, submit_opname

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		with self.assertRaises(frappe.ValidationError):
			submit_opname(self.profile, payload["name"], action="Pass")

	def test_no_active_workflow(self):
		from pos_next.api.stock_opname import save_opname, submit_opname

		def restore():
			frappe.set_user(ADMIN)
			frappe.db.set_value("Workflow", CUSTOM_WORKFLOW, "is_active", 1)
			frappe.cache.hdel("workflow", DOCTYPE)
			frappe.db.commit()

		self.addCleanup(restore)
		frappe.set_user(ADMIN)
		frappe.db.set_value("Workflow", {"document_type": DOCTYPE}, "is_active", 0)
		frappe.cache.hdel("workflow", DOCTYPE)
		frappe.db.commit()

		self._cashier()
		payload = save_opname(self.profile, {"items": [{"item_code": self.plain, "qty": 4}]})
		self.assertTrue(payload["editable"])
		self.assertEqual(payload["actions"], [])
		with self.assertRaisesRegex(frappe.ValidationError, "No active workflow"):
			submit_opname(self.profile, payload["name"])
