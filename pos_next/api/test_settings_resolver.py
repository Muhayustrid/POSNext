# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Integration tests for the effective-settings resolver.

Read-only against the shared dev site (same style as
api/test_backdate_invoices.py): no rows are created or modified, so no
tearDown restore is needed.

Run via
pos_next/_pn_run_tests.py pos_next.api.test_settings_resolver
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.settings_resolver import (
	GLOBAL_DOCTYPE,
	_coerce,
	_mirror_fields,
	get_effective_pos_setting,
	get_effective_pos_settings,
)
from pos_next.pos_next.doctype.pos_settings.pos_settings import get_pos_settings as get_pos_settings_doclevel

NO_PROFILE = "PN-NO-SUCH-PROFILE"

_NON_DATA_TYPES = ("Tab Break", "Section Break", "Column Break", "Table", "Table MultiSelect")


def _persisted_values():
	return dict(
		frappe.db.sql("select field, value from `tabSingles` where doctype = %s", GLOBAL_DOCTYPE)
	)


class TestSettingsResolver(FrappeTestCase):
	def test_enabled_row_wins_whole(self):
		profile = frappe.db.get_value("POS Settings", {"enabled": 1}, "pos_profile")
		if not profile:
			self.skipTest("no enabled POS Settings row on this site")

		fields = ["tax_inclusive", "print_mode", "search_limit", "allow_return"]
		row = frappe.db.get_value(
			"POS Settings", {"pos_profile": profile, "enabled": 1}, fields, as_dict=True
		)
		for fieldname in fields:
			self.assertEqual(get_effective_pos_setting(profile, fieldname), row[fieldname], fieldname)

	def test_global_fallback_for_unknown_profile(self):
		persisted = _persisted_values()
		settings = get_effective_pos_settings(NO_PROFILE, fields=list(persisted) or None)
		self.assertEqual(settings["enabled"], 1)
		self.assertEqual(settings["pos_profile"], NO_PROFILE)

		meta = frappe.get_meta(GLOBAL_DOCTYPE)
		for fieldname, value in persisted.items():
			df = meta.get_field(fieldname)
			if not df or df.fieldtype in _NON_DATA_TYPES:
				continue
			self.assertEqual(settings[fieldname], _coerce(df, value), fieldname)

	def test_unset_field_uses_meta_default(self):
		persisted = _persisted_values()
		candidates = [f for f in _mirror_fields() if f not in persisted]
		if not candidates:
			self.skipTest("every mirror field is persisted on this site")

		settings = get_effective_pos_settings(NO_PROFILE)
		meta = frappe.get_meta(GLOBAL_DOCTYPE)
		for fieldname in candidates:
			df = meta.get_field(fieldname)
			expected = None if df.fieldtype == "Link" and not df.default else _coerce(df, df.default)
			self.assertEqual(settings[fieldname], expected, fieldname)

	def test_get_pos_settings_does_not_auto_create(self):
		result = get_pos_settings_doclevel(NO_PROFILE)
		self.assertTrue(result)
		self.assertFalse(frappe.db.exists("POS Settings", {"pos_profile": NO_PROFILE}))
