# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for Shift Group (POS Profile Group) member sync.

Mocked-frappe style — run via
pos_next/_pn_run_tests.py pos_next.test_pos_profile_group
"""

import unittest
from unittest.mock import Mock, patch

import frappe

from pos_next.install import CUSTOM_FIELDS
from pos_next.pos_next.doctype.pos_profile_group.pos_profile_group import POSProfileGroup

GROUP = {
	"pos_schedule_enabled": 1,
	"pos_schedule_start": "05:00:00",
	"pos_schedule_end": "12:00:00",
	"pos_schedule_warning_minutes": 15,
	"pos_schedule_enforce_closing": 1,
}
SYNCED = frappe._dict({"pos_profile_group": "Group A", **GROUP})

MODULE = "pos_next.pos_next.doctype.pos_profile_group.pos_profile_group"


def _group(**overrides):
	profiles = overrides.pop("profiles", [])
	doc = POSProfileGroup(
		frappe._dict(
			{
				"doctype": "POS Profile Group",
				"name": "Group A",
				"group_name": "Group A",
				"company": "Outlet",
				**GROUP,
				**overrides,
			}
		)
	)
	# set directly: passing child rows through the constructor would run
	# full child-doc init, which these unit tests don't need
	doc.profiles = profiles
	return doc


def _row(profile):
	return frappe._dict({"pos_profile": profile})


def _member_lookup(company="Outlet", group=None):
	"""db.get_value side effect: member table lookups return (company, group)."""
	return lambda doctype, name, fields=None, as_dict=False, **kw: (
		(company, group) if fields == ("company", "pos_profile_group") else None
	)


class TestGroupValidation(unittest.TestCase):
	def test_enabled_group_requires_times(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_group(pos_schedule_start=None).validate()

	def test_disabled_group_skips_time_validation(self):
		_group(pos_schedule_enabled=0, pos_schedule_start=None).validate()

	def test_group_with_members_in_company_passes(self):
		doc = _group(profiles=[_row("Profile 1")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup()):
			doc.validate()

	def test_duplicate_member_rejected(self):
		doc = _group(profiles=[_row("Profile 1"), _row("Profile 1")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup()):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_missing_profile_rejected(self):
		doc = _group(profiles=[_row("Ghost Profile")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup(company=None)):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_cross_company_member_rejected(self):
		doc = _group(profiles=[_row("Outlet Profile")])
		with patch(
			f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup(company="HQ Parent")
		):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_reassignment_rejected(self):
		# a profile of another group must be removed there first — never
		# silently moved by adding it to a second group
		doc = _group(profiles=[_row("Profile 1")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup(group="Group B")):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()


class TestGroupSync(unittest.TestCase):
	def _run_sync(self, profiles, linked, stored):
		"""Sync a group and return (get_doc mock, set_value mock)."""
		doc = _group(profiles=[_row(p) for p in profiles])
		get_doc = Mock()
		set_value = Mock()
		with (
			patch(f"{MODULE}.frappe.get_all", return_value=list(linked)),
			patch(f"{MODULE}.frappe.db.get_value", return_value=stored),
			patch(f"{MODULE}.frappe.get_doc", get_doc),
			patch(f"{MODULE}.frappe.db.set_value", set_value),
		):
			doc.sync_members()
		return get_doc, set_value

	def test_new_member_gets_schedule_and_link(self):
		stored = frappe._dict({"pos_profile_group": None, **{k: None for k in GROUP}})
		get_doc, set_value = self._run_sync(["Profile 1"], linked=[], stored=stored)
		set_value.assert_not_called()
		profile = get_doc.return_value
		profile.update.assert_called_once_with(GROUP)
		self.assertEqual(profile.pos_profile_group, "Group A")
		profile.save.assert_called_once()

	def test_in_sync_member_not_resaved(self):
		get_doc, _ = self._run_sync(["Profile 1"], linked=["Profile 1"], stored=SYNCED)
		get_doc.assert_not_called()

	def test_changed_time_resyncs_member(self):
		stored = frappe._dict({**SYNCED, "pos_schedule_start": "09:00:00"})
		get_doc, _ = self._run_sync(["Profile 1"], linked=["Profile 1"], stored=stored)
		get_doc.return_value.save.assert_called_once()

	def test_removed_member_unlinked_schedule_preserved(self):
		# removal only clears the link — the profile keeps its last synced hours
		_, set_value = self._run_sync([], linked=["Profile 1"], stored=SYNCED)
		set_value.assert_called_once_with("POS Profile", "Profile 1", "pos_profile_group", "")

	def test_mixed_membership_reconciled(self):
		kept = frappe._dict(SYNCED)
		new = frappe._dict({"pos_profile_group": None, **{k: None for k in GROUP}})
		doc = _group(profiles=[_row("Kept"), _row("New")])

		def get_value(doctype, name, fields=None, as_dict=False, **kw):
			return kept if name == "Kept" else new

		get_doc = Mock()
		set_value = Mock()
		with (
			patch(f"{MODULE}.frappe.get_all", return_value=["Kept", "Removed"]),
			patch(f"{MODULE}.frappe.db.get_value", side_effect=get_value),
			patch(f"{MODULE}.frappe.get_doc", get_doc),
			patch(f"{MODULE}.frappe.db.set_value", set_value),
		):
			doc.sync_members()

		set_value.assert_called_once_with("POS Profile", "Removed", "pos_profile_group", "")
		self.assertEqual(get_doc.call_count, 1)
		self.assertEqual(get_doc.call_args[0][1], "New")


class TestNewProfileDefaults(unittest.TestCase):
	def test_schedule_enabled_by_default_for_new_profiles_only(self):
		fields = {f["fieldname"]: f for f in CUSTOM_FIELDS["POS Profile"]}
		# string, not int — Custom Field.default is a Data column
		self.assertEqual(fields["pos_schedule_enabled"]["default"], "1")
		# the default lives in the custom field meta, so existing rows (all 0)
		# are untouched — only new profiles start enabled
		self.assertEqual(fields["pos_profile_group"]["label"], "Shift Group")
		# read-only mirror: membership is edited on the Shift Group only
		self.assertTrue(fields["pos_profile_group"]["read_only"])


class TestCreateProfileScheduleParams(unittest.TestCase):
	"""create_pos_profile must default the schedule on and never disable it
	silently — only an explicit 0 opts out."""

	def test_absent_params_default_enabled(self):
		from pos_next.api.pos_profile import _resolve_schedule_params

		resolved = _resolve_schedule_params({})
		self.assertEqual(resolved["pos_schedule_enabled"], 1)
		self.assertIsNone(resolved["pos_schedule_start"])

	def test_empty_or_none_enabled_still_enabled(self):
		from pos_next.api.pos_profile import _resolve_schedule_params

		for value in ("", None):
			with self.subTest(value=value):
				# absent / blank means "not provided" — never a silent disable
				self.assertEqual(_resolve_schedule_params({"pos_schedule_enabled": value})["pos_schedule_enabled"], 1)

	def test_explicit_zero_opts_out(self):
		from pos_next.api.pos_profile import _resolve_schedule_params

		resolved = _resolve_schedule_params({"pos_schedule_enabled": 0})
		self.assertEqual(resolved["pos_schedule_enabled"], 0)

	def test_full_schedule_passthrough(self):
		from pos_next.api.pos_profile import _resolve_schedule_params

		resolved = _resolve_schedule_params(
			{
				"pos_schedule_enabled": "1",
				"pos_schedule_start": "05:00:00",
				"pos_schedule_end": "12:00:00",
				"pos_schedule_warning_minutes": "15",
				"pos_schedule_enforce_closing": "1",
			}
		)
		self.assertEqual(
			resolved,
			{
				"pos_schedule_enabled": 1,
				"pos_schedule_start": "05:00:00",
				"pos_schedule_end": "12:00:00",
				"pos_schedule_warning_minutes": 15,
				"pos_schedule_enforce_closing": 1,
			},
		)


if __name__ == "__main__":
	unittest.main()
