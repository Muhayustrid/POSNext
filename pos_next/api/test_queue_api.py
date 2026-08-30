# Copyright (c) 2025, POS Next and contributors
# For license information, please see license.txt

"""Integration tests for the queue-number exposure (OpenSpec tasks 2.6 / 2.8).

Covers:
  * ``pos_next.api.shifts.get_current_queue_number`` — returns the shift's
    highest allocated counter, raises ``frappe.DataError`` on an unknown shift,
    and returns 0 when buyer identity is disabled for the shift's profile.
  * ``pos_next.api.bootstrap.get_initial_data`` — the bootstrapped ``shift``
    payload carries ``current_queue_number`` only when ``enable_buyer_identity``
    is on; with the flag off the payload keeps its exact pre-feature shape
    (keys == name / pos_profile / period_start_date / status).

``current_queue_number`` is set directly via ``frappe.db.set_value`` here: the
on-submit allocation lives in ``api/invoices.py`` (parallel work) and this
suite must not depend on it.

Data is prefixed ``_Test POS Next Queue`` / built through the shared
``pos_next.tests.helpers`` fixtures, and FrappeTestCase's per-test transaction
rollback handles cleanup.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, nowdate, nowtime

from pos_next.api.bootstrap import get_initial_data
from pos_next.api.shifts import get_current_queue_number
from pos_next.tests import helpers

USER = "Administrator"


class TestQueueNumberAPI(FrappeTestCase):
	def setUp(self):
		super().setUp()
		self.company = helpers.get_default_company()
		self.warehouse = helpers.make_test_warehouse("queueapi", self.company)
		self.profile = helpers.make_test_pos_profile("queueapi", self.company, self.warehouse)
		self.mop = helpers.get_default_mode_of_payment(self.company)
		self.shift = self._make_open_shift()
		self._set_buyer_identity(1)

	def _make_open_shift(self):
		doc = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"period_start_date": get_datetime(),
				"posting_date": nowdate(),
				"posting_time": nowtime(),
				"user": USER,
				"pos_profile": self.profile,
				"company": self.company,
				"status": "Open",
				"balance_details": [{"mode_of_payment": self.mop, "amount": 0}],
			}
		)
		doc.insert(ignore_permissions=True)
		doc.submit()
		return doc.name

	def _set_queue_counter(self, value):
		# The field is read_only + allow_on_submit; write at the DB level so the
		# test stays independent from the (parallel) allocation on submit.
		frappe.db.set_value("POS Opening Shift", self.shift, "current_queue_number", value)

	def _set_buyer_identity(self, value):
		name = frappe.db.exists("POS Settings", {"pos_profile": self.profile})
		if name:
			frappe.db.set_value("POS Settings", name, {"enabled": 1, "enable_buyer_identity": value})
		else:
			frappe.get_doc(
				{
					"doctype": "POS Settings",
					"pos_profile": self.profile,
					"enabled": 1,
					"enable_buyer_identity": value,
				}
			).insert(ignore_permissions=True)

	# --- 2.6: get_current_queue_number -------------------------------------

	def test_open_shift_returns_highest_number(self):
		self._set_queue_counter(7)
		self.assertEqual(get_current_queue_number(self.shift), 7)

	def test_unset_counter_returns_zero(self):
		# Counter never touched (default 0 / NULL): honest zero, not an error.
		self.assertEqual(get_current_queue_number(self.shift), 0)

	def test_unknown_shift_raises_data_error(self):
		with self.assertRaises(frappe.DataError):
			get_current_queue_number("POS Opening Shift - does not exist - 999999")

	def test_disabled_feature_returns_zero(self):
		self._set_queue_counter(7)
		self._set_buyer_identity(0)
		self.assertEqual(get_current_queue_number(self.shift), 0)

	def test_return_type_is_int(self):
		self._set_queue_counter(4)
		self.assertIsInstance(get_current_queue_number(self.shift), int)

	# --- 2.8: bootstrap gating ----------------------------------------------

	def test_bootstrap_omits_queue_field_when_feature_off(self):
		self._set_queue_counter(7)
		self._set_buyer_identity(0)
		out = get_initial_data()
		self.assertNotIn("current_queue_number", out["shift"])
		# Regression: payload shift dict must equal today's exact (pre-feature) shape.
		self.assertEqual(
			set(out["shift"].keys()),
			{"name", "pos_profile", "period_start_date", "status"},
		)
		self.assertEqual(out["shift"]["name"], self.shift)

	def test_bootstrap_includes_queue_number_when_feature_on(self):
		self._set_queue_counter(7)
		out = get_initial_data()
		self.assertEqual(out["shift"]["current_queue_number"], 7)
