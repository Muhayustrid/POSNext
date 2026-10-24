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
	"pos_schedule_warning_minutes": 15,
	"pos_schedule_enforce_closing": 1,
}
SYNCED = frappe._dict({"pos_profile_group": "Group A", **GROUP})

MODULE = "pos_next.pos_next.doctype.pos_profile_group.pos_profile_group"


def _group(**overrides):
	profiles = overrides.pop("profiles", [])
	# no company field on the doc: groups are company-neutral
	doc = POSProfileGroup(
		frappe._dict(
			{
				"doctype": "POS Profile Group",
				"name": "Group A",
				"group_name": "Group A",
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


def _member_lookup(companies, group=None):
	"""db.get_value side effect: profile name -> (company, current group)."""

	def lookup(doctype, name, fields=None, as_dict=False, **kw):
		if fields == ("company", "pos_profile_group"):
			return companies.get(name), group
		return None

	return lookup


class TestGroupValidation(unittest.TestCase):
	def test_negative_warning_minutes_rejected(self):
		with self.assertRaises(frappe.exceptions.ValidationError):
			_group(pos_schedule_warning_minutes=-5).validate()

	def test_members_of_different_companies_accepted(self):
		doc = _group(profiles=[_row("Outlet Profile"), _row("HQ Profile")])
		lookup = _member_lookup({"Outlet Profile": "Outlet", "HQ Profile": "HQ Parent"})
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=lookup):
			doc.validate()

	def test_duplicate_member_rejected(self):
		doc = _group(profiles=[_row("Profile 1"), _row("Profile 1")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup({"Profile 1": "Outlet"})):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_missing_profile_rejected(self):
		doc = _group(profiles=[_row("Ghost Profile")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup({})):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_reassignment_rejected(self):
		# a profile of another group must be removed there first — never
		# silently moved by adding it to a second group
		doc = _group(profiles=[_row("Profile 1")])
		with patch(
			f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup({"Profile 1": "Outlet"}, group="Group B")
		):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()


class TestGroupSync(unittest.TestCase):
	def _run_sync(self, profiles, linked, stored, allowed=True):
		"""Sync a group and return (get_doc mock, set_value mock, allowed mock)."""
		doc = _group(profiles=[_row(p) for p in profiles])
		get_doc = Mock()
		set_value = Mock()
		allowed_mock = Mock(return_value=allowed)
		with (
			patch(f"{MODULE}.frappe.get_all", return_value=list(linked)),
			patch(f"{MODULE}.frappe.db.get_value", return_value=stored),
			patch(f"{MODULE}.frappe.get_doc", get_doc),
			patch(f"{MODULE}.frappe.db.set_value", set_value),
			patch(f"{MODULE}.frappe.has_permission", allowed_mock),
		):
			doc.sync_members()
		return get_doc, set_value, allowed_mock

	def test_new_member_gets_link_only(self):
		stored = frappe._dict({"pos_profile_group": None, **{k: None for k in GROUP}})
		get_doc, set_value, _ = self._run_sync(["Profile 1"], linked=[], stored=stored)
		set_value.assert_not_called()
		profile = get_doc.return_value
		# slots are read from the group at shift open: no schedule is pushed
		profile.update.assert_not_called()
		self.assertEqual(profile.pos_profile_group, "Group A")
		profile.save.assert_called_once()

	def test_in_sync_member_not_resaved(self):
		get_doc, _, _ = self._run_sync(["Profile 1"], linked=["Profile 1"], stored=SYNCED)
		get_doc.assert_not_called()

	def test_removed_member_unlinked_schedule_preserved(self):
		# removal only clears the link — the profile keeps its last synced hours
		_, set_value, allowed = self._run_sync([], linked=["Profile 1"], stored=SYNCED)
		set_value.assert_called_once_with("POS Profile", "Profile 1", "pos_profile_group", "")
		allowed.assert_called_once_with("POS Profile", "write", doc="Profile 1")

	def test_cross_company_members_both_synced(self):
		fresh = frappe._dict({"pos_profile_group": None, **{k: None for k in GROUP}})
		doc = _group(profiles=[_row("Outlet Profile"), _row("HQ Profile")])
		mocks = {}

		def get_doc_mock(doctype, name):
			mocks[name] = Mock()
			return mocks[name]

		with (
			patch(f"{MODULE}.frappe.get_all", return_value=[]),
			patch(f"{MODULE}.frappe.db.get_value", return_value=fresh),
			patch(f"{MODULE}.frappe.get_doc", side_effect=get_doc_mock),
			patch(f"{MODULE}.frappe.db.set_value", Mock()),
			patch(f"{MODULE}.frappe.has_permission", Mock(return_value=True)),
		):
			doc.sync_members()
		self.assertEqual(sorted(mocks), ["HQ Profile", "Outlet Profile"])
		for profile in mocks.values():
			profile.update.assert_not_called()
			profile.save.assert_called_once()

	def test_unwritable_removed_member_aborts_before_any_write(self):
		# db.set_value bypasses permissions, so write access to every removed
		# profile is verified up front: one denied company aborts the whole
		# save before a single write — including the members that were allowed
		doc = _group(profiles=[_row("Kept Profile")])
		get_doc = Mock()
		set_value = Mock()
		with (
			patch(f"{MODULE}.frappe.get_all", return_value=["Removed Profile"]),
			patch(f"{MODULE}.frappe.db.get_value", return_value=SYNCED),
			patch(f"{MODULE}.frappe.get_doc", get_doc),
			patch(f"{MODULE}.frappe.db.set_value", set_value),
			patch(f"{MODULE}.frappe.has_permission", Mock(return_value=False)),
		):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.sync_members()
		set_value.assert_not_called()
		get_doc.assert_not_called()

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
			patch(f"{MODULE}.frappe.has_permission", Mock(return_value=True)),
		):
			doc.sync_members()

		set_value.assert_called_once_with("POS Profile", "Removed", "pos_profile_group", "")
		self.assertEqual(get_doc.call_count, 1)
		self.assertEqual(get_doc.call_args[0][1], "New")


class TestNewProfileDefaults(unittest.TestCase):
	def test_profile_has_no_schedule_fields(self):
		fields = {f["fieldname"]: f for f in CUSTOM_FIELDS["POS Profile"]}
		# shift hours live only on the Shift Group slots
		for field in ("pos_schedule_enabled", "pos_schedule_start", "pos_schedule_end"):
			self.assertNotIn(field, fields)
		self.assertEqual(fields["pos_profile_group"]["label"], "Shift Group")
		# read-only mirror: membership is edited on the Shift Group only
		self.assertTrue(fields["pos_profile_group"]["read_only"])


class TestMultiCompanyDocTypeMeta(unittest.TestCase):
	def _fields(self, doctype):
		import json
		from pathlib import Path

		import pos_next.pos_next

		path = (
			Path(pos_next.pos_next.__file__).parent
			/ "doctype"
			/ doctype.replace(" ", "_").lower()
			/ f"{doctype.replace(' ', '_').lower()}.json"
		)
		fields = {f["fieldname"]: f for f in json.loads(path.read_text())["fields"]}
		return fields

	def test_member_rows_show_fetched_company(self):
		fields = self._fields("POS Profile Group Member")
		self.assertEqual(fields["company"]["fetch_from"], "pos_profile.company")
		self.assertTrue(fields["company"]["read_only"])
		self.assertTrue(fields["company"]["in_list_view"])

	def test_group_keeps_only_slot_wide_schedule_settings(self):
		# hours live on the Shift Slots; warning + enforce are group-wide
		group = self._fields("POS Profile Group")
		for field in ("pos_schedule_enabled", "pos_schedule_start", "pos_schedule_end"):
			self.assertNotIn(field, group)
		self.assertIn("pos_schedule_warning_minutes", group)
		self.assertIn("pos_schedule_enforce_closing", group)
		slot = self._fields("POS Shift Slot")
		self.assertNotIn("warning_minutes", slot)
		self.assertNotIn("enforce_closing", slot)

	def test_group_company_field_retired_not_required(self):
		# hidden + optional: the column stays for existing records (no
		# destructive migration) but new groups never need a company
		fields = self._fields("POS Profile Group")
		self.assertNotIn("reqd", fields["company"])
		self.assertEqual(fields["company"]["hidden"], 1)


if __name__ == "__main__":
	unittest.main()
