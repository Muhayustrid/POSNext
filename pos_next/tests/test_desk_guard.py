# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""POS cashiers are kept out of Desk (utils/desk_guard + authz.is_desk_blocked).

Run via pos_next/_pn_run_tests.py pos_next.tests.test_desk_guard
"""

from types import SimpleNamespace
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase
from werkzeug.exceptions import HTTPException

from pos_next.utils import desk_guard
from pos_next.utils.authz import is_desk_blocked


class TestDeskGuard(FrappeTestCase):
	def _run(self, path, method="GET", blocked=True):
		request = SimpleNamespace(path=path, method=method)
		with (
			mock.patch.object(frappe.local, "request", request, create=True),
			mock.patch.object(desk_guard, "is_desk_blocked", return_value=blocked),
		):
			try:
				desk_guard.block_desk_for_cashiers()
			except HTTPException as e:
				return e.get_response().headers.get("Location")
		return None

	def test_cashier_desk_pages_redirect_to_pos(self):
		for path in ("/app", "/app/", "/app/stock-reconciliation/MAT-001", "/desk", "/desk/posnext"):
			self.assertEqual(self._run(path), "/pos", path)

	def test_pos_api_and_print_stay_open(self):
		for path in ("/pos", "/pos/sale", "/api/method/frappe.auth.get_logged_user", "/printview", "/apps"):
			self.assertIsNone(self._run(path), path)
		self.assertIsNone(self._run("/app", method="POST"))

	def test_non_blocked_user_keeps_desk(self):
		self.assertIsNone(self._run("/app", blocked=False))

	def test_is_desk_blocked_by_role(self):
		cases = {
			("POSNext Cashier",): True,
			("POSNext Cashier", "Purchase User", "Stock User"): True,
			("POSNext Cashier", "POSNext Manager"): False,
			("POSNext Cashier", "Stock Manager"): False,
			("POSNext Manager",): False,
			("Sales User",): False,
		}
		for roles, expected in cases.items():
			with mock.patch("pos_next.utils.authz.frappe.get_roles", return_value=list(roles)):
				self.assertEqual(is_desk_blocked("someone@example.com"), expected, roles)
		self.assertFalse(is_desk_blocked("Administrator"))
