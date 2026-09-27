# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Unit tests for the per-profile cash-mode resolver.

The old fallback returned the generic "Cash" whenever
``posa_cash_mode_of_payment`` was empty, so change given on an outlet that
names its own cash mode ("Cash PKU DELANGGU") minted a phantom second cash
row with a negative expected amount in the closing reconciliation.

Run via pos_next/_pn_run_tests.py pos_next.services.test_cash_mode
"""

import uuid

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.services.cash_mode import get_cash_mode_of_payment


class TestCashModeResolution(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.tag = uuid.uuid4().hex[:6].upper()
		cls.mops = {}
		for short, type_ in (
			("Outlet Cash", "Cash"),
			("Backup Cash", "Cash"),
			("QR", "Bank"),
			("Card", "Bank"),
		):
			full = f"{short} {cls.tag}"
			doc = frappe.get_doc({"doctype": "Mode of Payment", "mode_of_payment": full, "type": type_})
			doc.insert(ignore_permissions=True)
			cls.mops[short] = full

	@classmethod
	def tearDownClass(cls):
		for profile in getattr(cls, "profiles", []):
			if frappe.db.exists("POS Profile", profile):
				frappe.delete_doc("POS Profile", profile, force=1, ignore_permissions=True)
		for full in cls.mops.values():
			if frappe.db.exists("Mode of Payment", full):
				frappe.delete_doc("Mode of Payment", full, force=1, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	@classmethod
	def _profile(cls, payments, cash_field=None):
		"""Minimal profile: the resolver reads only tabPOS Profile and
		tabPOS Payment Method rows, so validators are skipped to keep the
		fixtures hermetic on the shared dev site."""
		name = f"TEST-CASH-MODE-{cls.tag}-{len(getattr(cls, 'profiles', [])) + 1}"
		doc = frappe.get_doc(
			{
				"doctype": "POS Profile",
				"name": name,
				"company": frappe.db.get_single_value("Global Defaults", "default_company"),
				"posa_cash_mode_of_payment": cash_field,
				"payments": payments,
			}
		)
		doc.flags.ignore_validate = True
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		cls.profiles = getattr(cls, "profiles", []) + [name]
		return name

	def _pay(self, mop, default=0):
		return {"mode_of_payment": self.mops[mop], "default": default, "amount": 0}

	def test_configured_field_wins(self):
		profile = self._profile(
			[self._pay("QR", default=1), self._pay("Outlet Cash")],
			cash_field=self.mops["QR"],
		)
		self.assertEqual(get_cash_mode_of_payment(profile), self.mops["QR"])

	def test_outlet_cash_row_beats_non_cash_default(self):
		# THE regression shape: field empty, the profile's default row is a
		# bank mode, the outlet's cash drawer is its own mode
		profile = self._profile([self._pay("QR", default=1), self._pay("Outlet Cash")])
		self.assertEqual(get_cash_mode_of_payment(profile), self.mops["Outlet Cash"])

	def test_default_cash_row_wins_over_first_cash_row(self):
		profile = self._profile([self._pay("Outlet Cash"), self._pay("Backup Cash", default=1)])
		self.assertEqual(get_cash_mode_of_payment(profile), self.mops["Backup Cash"])

	def test_first_cash_row_when_no_default(self):
		profile = self._profile([self._pay("Outlet Cash"), self._pay("Backup Cash")])
		self.assertEqual(get_cash_mode_of_payment(profile), self.mops["Outlet Cash"])

	def test_generic_cash_when_profile_has_no_cash_row(self):
		profile = self._profile([self._pay("QR", default=1), self._pay("Card")])
		self.assertEqual(get_cash_mode_of_payment(profile), "Cash")

	def test_generic_cash_for_missing_profile(self):
		self.assertEqual(get_cash_mode_of_payment(f"NO-SUCH-PROFILE-{self.tag}"), "Cash")
		self.assertEqual(get_cash_mode_of_payment(None), "Cash")
