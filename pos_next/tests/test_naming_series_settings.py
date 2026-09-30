# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Naming-series settings: controllers must name documents from the global.

The naming series is global-only (GLOBAL_FIELDS tier, never mirrored), so the
global single decides for every profile — including profiles that already have
an enabled POS Settings row. The runner rolls back once per class, not per
test, so each test resets the globals it touches first.

Run via
pos_next/_pn_run_tests.py pos_next.tests.test_naming_series_settings
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from pos_next.api.settings_resolver import GLOBAL_DOCTYPE

INVOICE_FIELD = "pos_invoice_naming_series"
OPENING_FIELD = "pos_opening_shift_naming_series"
CLOSING_FIELD = "pos_closing_shift_naming_series"


def _set_global(fieldname, value):
	frappe.db.set_single_value(GLOBAL_DOCTYPE, fieldname, value)


class TestNamingSeriesSettings(FrappeTestCase):
	def setUp(self):
		for fieldname in (INVOICE_FIELD, OPENING_FIELD, CLOSING_FIELD):
			_set_global(fieldname, "")

	def _profile_and_company(self):
		profile = frappe.db.get_value("POS Profile", {}, "name")
		if not profile:
			self.skipTest("no POS Profile on this site")
		return profile, frappe.db.get_value("POS Profile", profile, "company")

	def _opening_shift(self, profile, company, mode_of_payment):
		return frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": profile,
				"company": company,
				"user": frappe.session.user,
				"period_start_date": nowdate(),
				"period_end_date": nowdate(),
				"balance_details": [{"mode_of_payment": mode_of_payment, "amount": 0}],
			}
		).insert(ignore_permissions=True)

	def test_opening_shift_uses_global_series(self):
		profile, company = self._profile_and_company()
		mode_of_payment = frappe.db.get_value("Mode of Payment", {}, "name")
		if not mode_of_payment:
			self.skipTest("no Mode of Payment on this site")

		_set_global(OPENING_FIELD, "TSTOS-.YYYY.-")
		doc = self._opening_shift(profile, company, mode_of_payment)
		self.assertRegex(doc.name, r"^TSTOS-\d{4}-\d{5}$")

	def test_opening_shift_empty_keeps_meta_default(self):
		profile, company = self._profile_and_company()
		mode_of_payment = frappe.db.get_value("Mode of Payment", {}, "name")
		if not mode_of_payment:
			self.skipTest("no Mode of Payment on this site")

		doc = self._opening_shift(profile, company, mode_of_payment)
		self.assertTrue(doc.name.startswith("POSA-OS-"), doc.name)

	def test_invoice_and_closing_named_from_global(self):
		_set_global(INVOICE_FIELD, "TSTINV-.YYYY.-")
		_set_global(CLOSING_FIELD, "TSTCS-.YYYY.-")

		invoice = frappe.new_doc("POS Invoice")
		invoice.autoname()
		self.assertRegex(invoice.name, r"^TSTINV-\d{4}-\d{5}$")

		closing = frappe.new_doc("POS Closing Shift")
		closing.autoname()
		self.assertRegex(closing.name, r"^TSTCS-\d{4}-\d{5}$")

	def test_invoice_empty_leaves_naming_to_meta(self):
		invoice = frappe.new_doc("POS Invoice")
		invoice.autoname()
		self.assertFalse(invoice.get("name"))
