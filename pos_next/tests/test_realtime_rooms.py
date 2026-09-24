# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""PERF-06/SEC-NEW-04 regression: transaction realtime events are room-scoped.

Every emitter must publish into `pos_profile:<name>` and never broadcast with
user=None; the socket join gate must require a POS Profile User row. The emit
tests are pure mocks (no DB); the gate tests only mock the existence lookup.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_realtime_rooms
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.utils import flt

from pos_next.realtime_events import (
	emit_customer_event,
	emit_invoice_created_event,
	emit_pos_profile_updated_event,
	emit_stock_update_event,
	has_pos_profile_access,
)

PROFILE = "Test Register"


class FakeDoc:
	"""Minimal stand-in for a hooked Document (frappe._dict cannot carry an
	`items` list — the key collides with dict.items, and the emitter reads
	doc.items as an attribute)."""

	def __init__(self, **fields):
		self.__dict__.update(fields)

	def get(self, key, default=None):
		return self.__dict__.get(key, default)


def _publish_calls(publish_mock):
	"""Normalize publish_realtime calls to kwargs for assertion."""
	return [call.kwargs for call in publish_mock.call_args_list]


class TestStockUpdateRoom(unittest.TestCase):
	def _invoice(self, **overrides):
		doc = FakeDoc(
			doctype="Sales Invoice",
			name="ACC-SINV-0001",
			update_stock=1,
			is_pos=1,
			pos_profile=PROFILE,
			items=[
				frappe._dict(item_code="ITEM-1", warehouse="WH-1", is_stock_item=1, stock_qty=1)
			],
		)
		doc.__dict__.update(overrides)
		return doc

	def test_publishes_to_profile_room_without_user_broadcast(self):
		doc = self._invoice()
		with patch("frappe.publish_realtime") as publish, patch(
			"pos_next.realtime_events.get_stock_quantities",
			return_value=[{"item_code": "ITEM-1", "actual_qty": 5, "warehouse": "WH-1"}],
		):
			emit_stock_update_event(doc, "on_submit")

		calls = _publish_calls(publish)
		self.assertEqual(len(calls), 1)
		call = calls[0]
		self.assertEqual(call["event"], "pos_stock_update")
		self.assertEqual(call["room"], f"pos_profile:{PROFILE}")
		self.assertNotIn("user", call)  # no user=None broadcast
		self.assertTrue(call["after_commit"])

	def test_profile_less_invoice_is_not_broadcast(self):
		doc = self._invoice(pos_profile=None)
		with patch("frappe.publish_realtime") as publish, patch(
			"pos_next.realtime_events.get_stock_quantities"
		) as stock:
			emit_stock_update_event(doc, "on_submit")

		publish.assert_not_called()
		stock.assert_not_called()  # room resolved before any stock work

	def test_non_pos_invoice_is_skipped(self):
		doc = self._invoice(is_pos=0)
		with patch("frappe.publish_realtime") as publish:
			emit_stock_update_event(doc, "on_submit")
		publish.assert_not_called()


class TestInvoiceCreatedRoom(unittest.TestCase):
	def test_publishes_to_invoice_profile_room(self):
		doc = FakeDoc(
			doctype="Sales Invoice",
			name="ACC-SINV-0002",
			is_pos=1,
			is_consolidated=0,
			grand_total=flt(150),
			customer="CUST-001",
			pos_profile=PROFILE,
		)
		with patch("frappe.publish_realtime") as publish:
			emit_invoice_created_event(doc, "after_insert")

		calls = _publish_calls(publish)
		self.assertEqual(len(calls), 1)
		call = calls[0]
		self.assertEqual(call["event"], "pos_invoice_created")
		self.assertEqual(call["room"], f"pos_profile:{PROFILE}")
		self.assertNotIn("user", call)

	def test_consolidated_invoice_is_skipped(self):
		doc = FakeDoc(
			is_pos=1, is_consolidated=1, pos_profile=PROFILE, name="X", grand_total=1, customer="C"
		)
		with patch("frappe.publish_realtime") as publish:
			emit_invoice_created_event(doc, "after_insert")
		publish.assert_not_called()


class TestProfileUpdatedRoom(unittest.TestCase):
	def test_publishes_to_own_profile_room(self):
		doc = MagicMock()
		doc.name = PROFILE
		doc.has_value_changed.return_value = True
		doc.get.return_value = [SimpleNamespace(item_group="All Item Groups")]
		with patch("frappe.publish_realtime") as publish:
			emit_pos_profile_updated_event(doc, "on_update")

		calls = _publish_calls(publish)
		self.assertEqual(len(calls), 1)
		call = calls[0]
		self.assertEqual(call["event"], "pos_profile_updated")
		self.assertEqual(call["room"], f"pos_profile:{PROFILE}")
		self.assertNotIn("user", call)


class TestCustomerEventRooms(unittest.TestCase):
	def test_fans_out_to_enabled_profile_rooms_only(self):
		doc = FakeDoc(
			doctype="Customer",
			name="CUST-042",
			customer_name="Walk-in",
			mobile_no="08123",
			email_id=None,
			disabled=0,
		)
		with patch("frappe.publish_realtime") as publish, patch(
			"frappe.get_all", return_value=["Register A", "Register B"]
		) as get_all:
			emit_customer_event(doc, "after_insert")

		get_all.assert_called_once()
		calls = _publish_calls(publish)
		self.assertEqual(
			sorted(call["room"] for call in calls),
			["pos_profile:Register A", "pos_profile:Register B"],
		)
		for call in calls:
			self.assertEqual(call["event"], "pos_customer_changed")
			self.assertNotIn("user", call)


class TestRoomJoinGate(unittest.TestCase):
	def test_requires_pos_profile_user_row(self):
		with patch("frappe.db.exists", return_value=1) as exists, patch(
			"frappe.session"
		) as session:
			session.user = "cashier@example.com"
			self.assertTrue(has_pos_profile_access(PROFILE))
			exists.assert_called_with(
				"POS Profile User", {"parent": PROFILE, "user": "cashier@example.com"}
			)

	def test_rejects_user_without_profile_row(self):
		with patch("frappe.db.exists", return_value=0), patch("frappe.session") as session:
			session.user = "outsider@example.com"
			self.assertFalse(has_pos_profile_access(PROFILE))

	def test_rejects_non_string_profile(self):
		with patch("frappe.db.exists") as exists:
			self.assertFalse(has_pos_profile_access(None))
			self.assertFalse(has_pos_profile_access(""))
			self.assertFalse(has_pos_profile_access({"name": PROFILE}))
		exists.assert_not_called()


class TestPublishGuard(unittest.TestCase):
	def test_empty_profile_is_dropped_never_broadcast(self):
		# import late so the test patches the exact helper the emitters use
		from pos_next.realtime_events import publish_to_profile

		with patch("frappe.publish_realtime") as publish:
			publish_to_profile("pos_invoice_created", {}, None)
			publish_to_profile("pos_invoice_created", {}, "")
		publish.assert_not_called()
