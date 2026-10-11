# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import json
import uuid

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, flt, today

from pos_next.api.test_production import (
	InvoiceTypeAmbientMixin,
	_make_test_item,
	_seed_stock,
)
from pos_next.api.production import create_production, start_production, finish_production, close_production
from pos_next.services import reject_request

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
		# test users flood User creation and trip frappe's throttle (60/60 days);
		# in_import skips the throttle but also skips _set_defaults, so enabled
		# (a read-only, default-1 field) must be set explicitly or the user is
		# created disabled and drops out of approver_users()/notify()
		was_in_import = frappe.flags.in_import
		frappe.flags.in_import = True
		try:
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": prefix.title(),
					"enabled": 1,
					"send_welcome_email": 0,
					"roles": [{"role": r} for r in roles],
				}
			).insert(ignore_permissions=True)
		finally:
			frappe.flags.in_import = was_in_import
		return email


class TestProductionLoss(InvoiceTypeAmbientMixin, RejectRequestTest):
	"""System-generated Production Loss drafts from Work Orders (anti-manipulation)."""

	def setUp(self):
		super().setUp()
		self.fg = _make_test_item()
		self.mat = _make_test_item()
		self.recipe = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"PL {uuid.uuid4().hex[:6]}",
				"production_item": self.fg,
				"output_qty": 1,
				"items": [{"item_code": self.mat, "qty": 2}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)

	def _produce(self, qty, good, loss=0):
		_seed_stock(self.mat, self.warehouse, qty * 2)
		return create_production(
			recipe=self.recipe.name, qty=qty, good_qty=good, loss_qty=loss, pos_profile=self.pos_profile
		)

	def _drafts(self, work_order):
		return frappe.get_all(
			"POS Reject Request",
			filters={"work_order": work_order},
			fields=["name", "owner", "purpose", "is_system_generated", "reason"],
		)

	def test_finish_with_loss_creates_draft(self):
		operator = self._user(["POSNext Manager"], prefix="pl-op")
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": self.pos_profile,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": operator,
				"default": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=operator)
		_seed_stock(self.mat, self.warehouse, 20)
		with _as_user(operator):
			wo = frappe.get_doc("Work Order", start_production(self.recipe.name, 10, self.pos_profile)["work_order"])
			wo.db_set("posa_operator", operator)
			frappe.db.commit()
		result = finish_production(wo.name, good_qty=8, loss_qty=2)
		drafts = self._drafts(result["work_order"])
		self.assertEqual(len(drafts), 1)
		draft = frappe.get_doc("POS Reject Request", drafts[0].name)
		self.assertEqual(draft.purpose, "Production Loss")
		self.assertTrue(draft.is_system_generated)
		self.assertEqual(draft.owner, operator)
		self.assertEqual(draft.status, "Draft")
		self.assertEqual(draft.reason, f"Auto: production loss on {result['work_order']}")
		self.assertEqual(len(draft.items), 1)
		self.assertEqual(draft.items[0].item_code, self.fg)
		self.assertEqual(flt(draft.items[0].qty), 2)

	def test_finish_without_loss_creates_nothing(self):
		result = self._produce(5, good=5)
		self.assertEqual(self._drafts(result["work_order"]), [])

	def test_close_after_partial_creates_single_request(self):
		_seed_stock(self.mat, self.warehouse, 20)
		started = start_production(self.recipe.name, 10, self.pos_profile)
		finish_production(started["work_order"], good_qty=4)
		closed = close_production(started["work_order"])
		drafts = self._drafts(started["work_order"])
		self.assertEqual(len(drafts), 1)
		draft = frappe.get_doc("POS Reject Request", drafts[0].name)
		self.assertEqual(flt(draft.items[0].qty), 6)
		# the second path (close after finish) stays idempotent
		self.assertEqual(closed.get("reject_request"), drafts[0].name)

	def test_crew_cannot_shrink_system_qty(self):
		result = self._produce(10, good=8, loss=2)
		draft = frappe.get_doc("POS Reject Request", self._drafts(result["work_order"])[0].name)
		draft.items[0].qty = 1
		self.assertRaises(frappe.ValidationError, draft.save)
		draft.reload()
		draft.purpose = "Unsold"
		self.assertRaises(frappe.ValidationError, draft.save)

	def test_submit_requires_own_reason(self):
		result = self._produce(10, good=8, loss=2)
		draft = frappe.get_doc("POS Reject Request", self._drafts(result["work_order"])[0].name)
		self._evidence(draft)
		draft = frappe.get_doc("POS Reject Request", draft.name)
		self.assertRaises(frappe.ValidationError, draft.submit)
		self.assertEqual(frappe.db.get_value("POS Reject Request", draft.name, "docstatus"), 0)

	def test_overdue_draft_notified_once(self):
		from pos_next.tasks.reject_overdue import notify_overdue_drafts

		approver = self._user(["POSNext Reject Approver"], prefix="pl-appr")
		result = self._produce(10, good=8, loss=2)
		name = self._drafts(result["work_order"])[0].name
		frappe.db.set_value("POS Reject Request", name, "deadline_date", add_days(today(), -1))
		markers = frappe.db.count(
			"Comment",
			{
				"reference_doctype": "POS Reject Request",
				"reference_name": name,
				"content": ["like", "Unreported production loss notified%"],
			},
		)
		self.assertEqual(notify_overdue_drafts(), 1)
		self.assertEqual(
			frappe.db.count(
				"Comment",
				{
					"reference_doctype": "POS Reject Request",
					"reference_name": name,
					"content": ["like", "Unreported production loss notified%"],
				},
			),
			markers + 1,
		)
		self.assertEqual(notify_overdue_drafts(), 0)
		self.assertEqual(
			frappe.db.count(
				"Comment",
				{
					"reference_doctype": "POS Reject Request",
					"reference_name": name,
					"content": ["like", "Unreported production loss notified%"],
				},
			),
			markers + 1,
		)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": approver, "document_type": "POS Reject Request", "document_name": name}
			)
		)

	def test_flip_system_flag_and_shrink_rejected(self):
		result = self._produce(10, good=8, loss=2)
		draft = frappe.get_doc("POS Reject Request", self._drafts(result["work_order"])[0].name)
		draft.is_system_generated = 0
		draft.items[0].qty = 1
		self.assertRaises(frappe.ValidationError, draft.save)
		# even without shrinking, clearing the flag alone is refused
		draft.reload()
		draft.is_system_generated = 0
		self.assertRaises(frappe.ValidationError, draft.save)

	def test_crew_cannot_forge_work_order_link(self):
		self._produce(5, good=5)
		work_order = frappe.get_all("Work Order", filters={"posa_recipe": self.recipe.name}, pluck="name")[0]
		with _as_user(self._user(["POSNext Manager"], prefix="pl-fg")):
			self.assertRaises(
				frappe.ValidationError,
				lambda: frappe.get_doc(
					{
						"doctype": "POS Reject Request",
						"purpose": "Production Loss",
						"pos_profile": self.pos_profile,
						"incident_date": today(),
						"reason": "forged",
						"work_order": work_order,
						"is_system_generated": 1,
						"items": [{"item_code": self.fg, "qty": 1}],
					}
				).insert(ignore_permissions=True),
			)
			# the crew lane (no ignore_permissions) can't forge either way
			self.assertRaises(
				frappe.ValidationError,
				lambda: frappe.get_doc(
					{
						"doctype": "POS Reject Request",
						"purpose": "Production Loss",
						"pos_profile": self.pos_profile,
						"incident_date": today(),
						"reason": "forged",
						"work_order": work_order,
						"items": [{"item_code": self.fg, "qty": 1}],
					}
				).insert(),
			)

	def test_decimal_loss_qty_roundtrips(self):
		from pos_next.api.production import _uom_whole_field

		whole_field = _uom_whole_field()
		was_whole = frappe.db.get_value("UOM", "Nos", whole_field)
		frappe.db.set_value("UOM", "Nos", whole_field, 0)
		try:
			result = self._produce(10, good=7.3, loss=2.7)
		finally:
			frappe.db.set_value("UOM", "Nos", whole_field, was_whole)
		draft = frappe.get_doc("POS Reject Request", self._drafts(result["work_order"])[0].name)
		self.assertEqual(flt(draft.items[0].qty), 2.7)
		draft.reason = "burnt batch"
		self._evidence(draft)
		draft.submit()
		self.assertEqual(frappe.db.get_value("POS Reject Request", draft.name, "docstatus"), 1)

	def test_daily_job_backfills_missed_terminal_wo(self):
		from pos_next.tasks.reject_overdue import notify_overdue_drafts

		# a terminal WO with loss but no request: the finish hook's draft was
		# removed, the daily sweep must recreate it
		result = self._produce(10, good=8, loss=2)
		name = self._drafts(result["work_order"])[0].name
		frappe.db.delete("POS Reject Request", name)
		self.assertEqual(notify_overdue_drafts(), 0)
		drafts = self._drafts(result["work_order"])
		self.assertEqual(len(drafts), 1)
		self.assertEqual(flt(frappe.get_doc("POS Reject Request", drafts[0].name).items[0].qty), 2)

	def test_sweep_draft_incident_date_is_wo_end_date(self):
		"""I4: a recreated draft is dated from the WO's actual end, not today."""
		import frappe.utils

		result = self._produce(10, good=8, loss=2)
		wo = frappe.get_doc("Work Order", result["work_order"])
		end = frappe.utils.add_days(frappe.utils.today(), -4)
		wo.db_set("actual_end_date", end)
		frappe.db.delete("POS Reject Request", self._drafts(result["work_order"])[0].name)
		from pos_next.services.reject_request import ensure_production_loss_request

		name = ensure_production_loss_request(frappe.get_doc("Work Order", result["work_order"]))
		self.assertEqual(
			str(frappe.db.get_value("POS Reject Request", name, "incident_date")), str(end)
		)

	def test_cancelled_system_request_blocks_sweep_recreation(self):
		"""I4: a cancelled system request is still idempotency — the sweep
		must not mint a fresh-deadline draft behind the cancel decision."""
		from pos_next.services.reject_request import ensure_production_loss_request

		result = self._produce(10, good=8, loss=2)
		draft = frappe.get_doc("POS Reject Request", self._drafts(result["work_order"])[0].name)
		draft.flags.ignore_permissions = True
		draft.reason = "burnt batch"
		self._evidence(draft)
		draft.submit()
		draft.cancel()
		self.assertEqual(
			ensure_production_loss_request(frappe.get_doc("Work Order", result["work_order"])),
			draft.name,
		)
		self.assertEqual(len(self._drafts(result["work_order"])), 1)



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


class TestRejectApi(RejectRequestTest):
	"""Whitelisted crew endpoints: profile gate, save/submit, attachments."""

	def _crew(self, prefix="api-crew"):
		email = self._user(["POSNext Cashier"], prefix=prefix)
		frappe.get_doc(
			{
				"doctype": "POS Profile User",
				"parent": self.pos_profile,
				"parenttype": "POS Profile",
				"parentfield": "applicable_for_users",
				"user": email,
				"default": 1,
			}
		).insert(ignore_permissions=True)
		frappe.clear_cache(user=email)
		return email

	@staticmethod
	def _b64file(name, kind):
		import base64

		return json.dumps([{"file_name": name, "filedata": base64.b64encode(kind).decode()}])

	def _payload(self, **extra):
		return json.dumps(
			{
				"purpose": "Unsold",
				"pos_profile": self.pos_profile,
				"incident_date": today(),
				"reason": "unsold bread",
				"items": [{"item_code": self.item, "qty": 1}],
				**extra,
			}
		)

	def test_endpoints_gate_foreign_profile(self):
		from pos_next.api.reject_request import get_reject_requests, save_reject_request

		outsider = self._user(["POSNext Cashier"], prefix="api-out")
		with _as_user(outsider):
			self.assertRaises(frappe.PermissionError, get_reject_requests, self.pos_profile)
			self.assertRaises(
				frappe.PermissionError, save_reject_request, self._payload(pos_profile=self.pos_profile)
			)

	def test_save_submit_roundtrip(self):
		from pos_next.api.reject_request import (
			attach_reject_files,
			get_reject_request,
			get_reject_requests,
			save_reject_request,
		)

		crew = self._crew()
		with _as_user(crew):
			draft = save_reject_request(self._payload())
			name = draft["name"]
			self.assertEqual(frappe.db.get_value("POS Reject Request", name, "docstatus"), 0)
			attach_reject_files(name, "evidence", self._b64file("ev.txt", b"e1"))
			result = save_reject_request(json.dumps({"name": name, "reason": "day-old bread"}), submit=1)
			self.assertEqual(result["status"], "Pending")

			rows = get_reject_requests(self.pos_profile)["requests"]
			row = next(r for r in rows if r["name"] == name)
			self.assertEqual(row["status"], "Pending")
			self.assertEqual(row["evidence_count"], 1)
			self.assertEqual(row["purpose"], "Unsold")

			detail = get_reject_request(name)
			self.assertEqual(len(detail["evidence"]), 1)
			self.assertEqual(detail["destruction"], [])
			self.assertEqual(len(detail["items"]), 1)

	def test_destruction_attach_only_when_approved(self):
		from pos_next.api.reject_request import (
			attach_reject_files,
			close_reject_request,
			decide_reject_request,
		)

		crew = self._crew("api-des")
		approver = self._user(["POSNext Reject Approver"], prefix="api-appr")
		doc = self._submitted(1)
		with _as_user(crew):
			self.assertRaises(
				frappe.ValidationError, attach_reject_files, doc.name, "destruction", self._b64file("d.txt", b"d")
			)
			self.assertRaises(
				frappe.ValidationError, attach_reject_files, doc.name, "bogus", self._b64file("x.txt", b"x")
			)
		with _as_user(approver):
			decide_reject_request(doc.name, "Write Off")
		with _as_user(crew):
			attach_reject_files(doc.name, "destruction", self._b64file("d.txt", b"d"))
			result = close_reject_request(doc.name)
		self.assertEqual(result["status"], "Closed")

	def test_evidence_attach_caps_at_five(self):
		from pos_next.api.reject_request import attach_reject_files

		crew = self._crew("api-cap")
		doc = self._doc(1)
		with _as_user(crew):
			# 5 in one call fills the cap, the 6th is refused
			attach_reject_files(
				doc.name,
				"evidence",
				json.dumps([{"file_name": f"ev{i}.txt", "filedata": "ZQ=="} for i in range(5)]),
			)
			self.assertEqual(
				frappe.db.count(
					"File",
					{"attached_to_name": doc.name, "attached_to_field": "evidence"},
				),
				5,
			)
			self.assertRaises(
				frappe.ValidationError,
				attach_reject_files,
				doc.name,
				"evidence",
				self._b64file("ev6.txt", b"e"),
			)

	def test_evidence_only_on_draft(self):
		from pos_next.api.reject_request import attach_reject_files

		crew = self._crew("api-evd")
		doc = self._submitted(1)
		with _as_user(crew):
			self.assertRaises(
				frappe.ValidationError,
				attach_reject_files,
				doc.name,
				"evidence",
				self._b64file("ev.txt", b"e"),
			)

	def test_crew_cannot_edit_submitted_or_forge_fields(self):
		from pos_next.api.reject_request import save_reject_request

		crew = self._crew("api-sec")
		doc = self._submitted(1)
		with _as_user(crew):
			# submitted: no edits at all
			self.assertRaises(
				frappe.ValidationError,
				save_reject_request,
				json.dumps({"name": doc.name, "reason": "rewrite"}),
			)
			# purpose from the client is never trusted — new rows are always Unsold
			forged = save_reject_request(
				self._payload(
					purpose="Production Loss",
					is_system_generated=1,
					work_order="WO-BOGUS-0001",
					owner="attacker@example.com",
					status="Approved",
				)
			)
			values = frappe.db.get_value(
				"POS Reject Request",
				forged["name"],
				["purpose", "is_system_generated", "work_order", "owner", "status"],
				as_dict=True,
			)
			self.assertEqual(values.purpose, "Unsold")
			self.assertEqual(values.is_system_generated, 0)
			self.assertEqual(values.work_order, None)
			self.assertEqual(values.status, "Draft")
			self.assertNotEqual(values.owner, "attacker@example.com")

	def test_get_reject_request_gates_other_outlets(self):
		from pos_next.api.reject_request import get_reject_request

		outsider = self._user(["POSNext Cashier"], prefix="api-out2")
		doc = self._doc(1)
		with _as_user(outsider):
			self.assertRaises(frappe.PermissionError, get_reject_request, doc.name)

	def test_cancel_only_pending(self):
		from pos_next.api.reject_request import cancel_reject_request, decide_reject_request

		crew = self._crew("api-cxl")
		approver = self._user(["POSNext Reject Approver"], prefix="api-cxap")
		doc = self._submitted(1)
		with _as_user(crew):
			cancel_reject_request(doc.name)
		self.assertEqual(frappe.db.get_value("POS Reject Request", doc.name, "status"), "Cancelled")
		with _as_user(approver):
			self.assertRaises(frappe.ValidationError, decide_reject_request, doc.name, "Write Off")

	def test_cancel_refused_when_approved(self):
		from pos_next.api.reject_request import cancel_reject_request, decide_reject_request

		crew = self._crew("api-cxl2")
		approver = self._user(["POSNext Reject Approver"], prefix="api-cxap2")
		doc = self._submitted(1)
		with _as_user(approver):
			decide_reject_request(doc.name, "Write Off")
		with _as_user(crew):
			self.assertRaises(frappe.ValidationError, cancel_reject_request, doc.name)
		self.assertEqual(frappe.db.get_value("POS Reject Request", doc.name, "status"), "Approved")

	def test_endpoints_gate_foreign_profile_crew_actions(self):
		from pos_next.api.reject_request import (
			attach_reject_files,
			cancel_reject_request,
			close_reject_request,
			save_reject_request,
		)

		outsider = self._user(["POSNext Cashier"], prefix="api-out3")
		doc = self._submitted(1)
		with _as_user(outsider):
			self.assertRaises(
				frappe.PermissionError, attach_reject_files, doc.name, "evidence", self._b64file("e.txt", b"e")
			)
			self.assertRaises(frappe.PermissionError, cancel_reject_request, doc.name)
			self.assertRaises(frappe.PermissionError, close_reject_request, doc.name)
			self.assertRaises(
				frappe.PermissionError,
				save_reject_request,
				json.dumps({"name": doc.name, "reason": "hijack"}),
			)

	def test_crew_cannot_set_server_fields_via_save(self):
		"""C1: standard REST/Desk save on a submitted doc must not touch
		server-owned lifecycle fields."""
		crew = self._crew("rej-wr")
		doc = self._submitted(1)
		with _as_user(crew):
			doc.reload()
			doc.status = "Approved"
			self.assertRaises(frappe.UpdateAfterSubmitError, doc.save)

	def test_save_system_draft_ignores_incident_and_items(self):
		from pos_next.api.reject_request import save_reject_request

		crew = self._crew("api-sys")
		recipe = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"Sys Draft {uuid.uuid4().hex[:6]}",
				"production_item": self.item,
				"output_qty": 1,
				"items": [{"item_code": _make_test_item(), "qty": 1}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		recipe.reload()
		wo = frappe.get_doc(
			{
				"doctype": "Work Order",
				"company": self.company,
				"production_item": self.item,
				"qty": 1,
				"bom_no": next(
					r.bom_no for r in recipe.companies if r.company == self.company and r.bom_no
				),
				"wip_warehouse": self.warehouse,
				"fg_warehouse": self.warehouse,
			}
		).insert(ignore_permissions=True)
		doc = self._doc(1, is_system_generated=1, work_order=wo.name)
		with _as_user(crew):
			save_reject_request(
				json.dumps(
					{
						"name": doc.name,
						"incident_date": add_days(today(), -10),
						"items": [{"item_code": self.item, "qty": 9}],
					}
				)
			)
		values = frappe.db.get_value(
			"POS Reject Request", doc.name, ["incident_date", "total_amount"], as_dict=True
		)
		self.assertEqual(str(values.incident_date), str(doc.incident_date))
		self.assertEqual(flt(values.total_amount), flt(doc.total_amount))

	def test_manual_production_loss_refused(self):
		"""I3: the crew lane can never mint a Production Loss request."""
		crew = self._crew("api-pl")
		with _as_user(crew):
			self.assertRaises(
				frappe.ValidationError,
				lambda: frappe.get_doc(
					{
						"doctype": "POS Reject Request",
						"purpose": "Production Loss",
						"pos_profile": self.pos_profile,
						"incident_date": today(),
						"reason": "no WO",
						"items": [{"item_code": self.item, "qty": 1}],
					}
				).insert(),
			)



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
		self.assertRaises(frappe.ValidationError, self._decide, doc, "Rejected", None)
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
		self.assertRaises(frappe.ValidationError, self._decide, doc, "Rejected", "twice")
		self.assertEqual(self._transfers(doc), before)

	def test_charge_crew_requires_notes(self):
		doc = self._submitted(1)
		self.assertRaises(frappe.ValidationError, self._decide, doc, "Charge Crew", None)
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
		from pos_next.services.reject_request import ensure_production_loss_request

		fg = _make_test_item()
		mat = _make_test_item()
		recipe = frappe.get_doc(
			{
				"doctype": "POS Production Recipe",
				"recipe_name": f"Close PL {uuid.uuid4().hex[:6]}",
				"production_item": fg,
				"output_qty": 1,
				"items": [{"item_code": mat, "qty": 2}],
				"companies": [{"company": self.company, "enabled": 1}],
			}
		).insert(ignore_permissions=True)
		_seed_stock(mat, self.warehouse, 20)
		wo_name = start_production(recipe.name, 10, self.pos_profile)["work_order"]
		wo = frappe.get_doc("Work Order", wo_name)
		wo.db_set("produced_qty", 9)  # force a loss so the system draft lane applies
		from pos_next.services.reject_request import ensure_production_loss_request

		name = ensure_production_loss_request(wo)
		doc = frappe.get_doc("POS Reject Request", name)
		doc.reason = "short batch"
		doc.save()
		self._evidence(doc)
		doc.submit()
		self._decide(doc.reload(), "Write Off", None)
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

	def test_draft_save_clears_forged_server_fields(self):
		"""C1: a draft saved with forged status/transfer/issue values comes back
		clean — status Draft, links and decision fields cleared."""
		doc = self._doc(1)
		doc.status = "Approved"
		doc.decision = "Write Off"
		doc.decided_by = "Administrator"
		doc.save(ignore_permissions=True)
		values = frappe.db.get_value(
			"POS Reject Request",
			doc.name,
			["status", "transfer_entry", "issue_entry", "decision", "decided_by"],
			as_dict=True,
		)
		self.assertEqual(values.status, "Draft")
		self.assertIsNone(values.decision)
		self.assertIsNone(values.decided_by)

	def test_double_close_raises_and_makes_one_issue(self):
		"""I1: close_request locks the row; the second close throws and no
		second write-off Stock Entry appears."""
		doc = self._submitted(1)
		self._decide(doc, "Write Off", None)
		self._destruction(doc)
		reject_request.close_request(doc)
		doc.reload()
		self.assertRaises(frappe.ValidationError, reject_request.close_request, doc)
		issues = frappe.get_all(
			"Stock Entry",
			filters={"remarks": ["like", f"{reject_request.ISSUE_REMARK_PREFIX}{doc.name}"]},
			pluck="name",
		)
		self.assertEqual(len(issues), 1)

	def test_submitted_evidence_protected_from_delete(self):
		"""I2: protect_attached_files — crew cannot delete evidence after submit."""
		from frappe.core.doctype.file.file import File

		doc = self._submitted(1)
		file_name = frappe.db.get_value(
			"File",
			{"attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "attached_to_field": "evidence"},
			"name",
		)
		f = frappe.get_doc("File", file_name)
		self.assertRaises(frappe.ValidationError, f.delete)

