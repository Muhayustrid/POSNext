# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for Code Gate Group (POS Code Gate Group) member sync.

Mocked-frappe style (like test_pos_profile_group) — run via
pos_next/_pn_run_tests.py pos_next.test_code_gate_group

A group save pushes the two gate switches onto every member Company.
Membership is authoritative in the Members table; the Company's
`pos_code_gate_group` link is a read-only mirror kept in step by
sync_members. Removed members keep their last synced switches — only the
mirror is cleared, so an outlet never silently loses its code protection.
"""

import sys
import unittest
from unittest.mock import Mock, patch

import frappe

from pos_next.pos_next.doctype.pos_code_gate_group.pos_code_gate_group import POSCodeGateGroup

GATE = {
	"pos_require_discount_code": 1,
	"pos_require_refund_code": 0,
}
SYNCED = frappe._dict({"pos_code_gate_group": "Group A", **GATE})

MODULE = "pos_next.pos_next.doctype.pos_code_gate_group.pos_code_gate_group"


def _group(**overrides):
	profiles = overrides.pop("companies", [])
	doc = POSCodeGateGroup(
		frappe._dict(
			{
				"doctype": "POS Code Gate Group",
				"name": "Group A",
				"group_name": "Group A",
				**GATE,
				**overrides,
			}
		)
	)
	# set directly: passing child rows through the constructor would run
	# full child-doc init, which these unit tests don't need
	doc.companies = profiles
	return doc


def _row(company):
	return frappe._dict({"company": company})


def _member_lookup(group=None):
	"""db.get_value side effect: company name -> (name, current group)."""

	def lookup(doctype, name, fields=None, as_dict=False, **kw):
		if fields == ("name", "pos_code_gate_group"):
			if name == "Ghost Company":
				return None
			return (name, group)
		return None

	return lookup


class TestGroupValidation(unittest.TestCase):
	def test_duplicate_member_rejected(self):
		doc = _group(companies=[_row("Outlet"), _row("Outlet")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup()):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_missing_company_rejected(self):
		doc = _group(companies=[_row("Ghost Company")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup()):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_reassignment_rejected(self):
		# a company of another group must be removed there first — never
		# silently moved by adding it to a second group
		doc = _group(companies=[_row("Outlet")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup(group="Group B")):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.validate()

	def test_members_of_group_accepted(self):
		doc = _group(companies=[_row("Outlet"), _row("HQ Outlet")])
		with patch(f"{MODULE}.frappe.db.get_value", side_effect=_member_lookup()):
			doc.validate()


class TestGroupSync(unittest.TestCase):
	def _run_sync(self, companies, linked, stored, allowed=True):
		"""Sync a group and return (set_value mock, allowed mock)."""
		doc = _group(companies=[_row(c) for c in companies])
		set_value = Mock()
		allowed_mock = Mock(return_value=allowed)
		with (
			patch(f"{MODULE}.frappe.get_all", return_value=list(linked)),
			patch(f"{MODULE}.frappe.db.get_value", return_value=stored),
			patch(f"{MODULE}.frappe.db.set_value", set_value),
			patch(f"{MODULE}.frappe.has_permission", allowed_mock),
		):
			doc.sync_members()
		return set_value, allowed_mock

	def test_new_member_gets_switches_and_mirror(self):
		stored = frappe._dict({"pos_code_gate_group": None, **{k: None for k in GATE}})
		set_value, _ = self._run_sync(["Outlet"], linked=[], stored=stored)
		set_value.assert_any_call("Company", "Outlet", "pos_require_discount_code", 1)
		set_value.assert_any_call("Company", "Outlet", "pos_require_refund_code", 0)
		set_value.assert_any_call("Company", "Outlet", "pos_code_gate_group", "Group A")

	def test_in_sync_member_not_rewritten(self):
		set_value, _ = self._run_sync(["Outlet"], linked=["Outlet"], stored=SYNCED)
		set_value.assert_not_called()

	def test_changed_switch_resyncs_member(self):
		stored = frappe._dict({**SYNCED, "pos_require_refund_code": 1})
		set_value, _ = self._run_sync(["Outlet"], linked=["Outlet"], stored=stored)
		set_value.assert_any_call("Company", "Outlet", "pos_require_refund_code", 0)

	def test_removed_member_unlinked_switches_preserved(self):
		# removal only clears the mirror — the company keeps its last synced
		# switches, so it never silently loses code protection
		set_value, allowed = self._run_sync([], linked=["Outlet"], stored=SYNCED)
		set_value.assert_called_once_with("Company", "Outlet", "pos_code_gate_group", "")
		allowed.assert_called_once_with("Company", "write", doc="Outlet")

	def test_unwritable_removed_member_aborts_before_any_write(self):
		# db.set_value bypasses permissions, so write access to every affected
		# company is verified up front: one denied company aborts the whole
		# save before a single write — including the members that were allowed
		doc = _group(companies=[_row("Kept")])
		set_value = Mock()
		with (
			patch(f"{MODULE}.frappe.get_all", return_value=["Removed"]),
			patch(f"{MODULE}.frappe.db.get_value", return_value=SYNCED),
			patch(f"{MODULE}.frappe.db.set_value", set_value),
			patch(f"{MODULE}.frappe.has_permission", Mock(return_value=False)),
		):
			with self.assertRaises(frappe.exceptions.ValidationError):
				doc.sync_members()
		set_value.assert_not_called()

	def test_mixed_membership_reconciled(self):
		kept = frappe._dict(SYNCED)
		new = frappe._dict({"pos_code_gate_group": None, **{k: None for k in GATE}})
		doc = _group(companies=[_row("Kept"), _row("New")])

		def get_value(doctype, name, fields=None, as_dict=False, **kw):
			return kept if name == "Kept" else new

		set_value = Mock()
		with (
			patch(f"{MODULE}.frappe.get_all", return_value=["Kept", "Removed"]),
			patch(f"{MODULE}.frappe.db.get_value", side_effect=get_value),
			patch(f"{MODULE}.frappe.db.set_value", set_value),
			patch(f"{MODULE}.frappe.has_permission", Mock(return_value=True)),
		):
			doc.sync_members()

		set_value.assert_any_call("Company", "Removed", "pos_code_gate_group", "")
		set_value.assert_any_call("Company", "New", "pos_require_discount_code", 1)
		set_value.assert_any_call("Company", "New", "pos_require_refund_code", 0)
		set_value.assert_any_call("Company", "New", "pos_code_gate_group", "Group A")
		# kept member untouched
		self.assertNotIn(
			("Company", "Kept", "pos_require_discount_code", 1),
			[call.args for call in set_value.call_args_list],
		)


class TestGroupMembershipGuard(unittest.TestCase):
	def test_mirror_without_member_row_rejected(self):
		from pos_next.overrides.code_gate_group import validate_company_group_membership

		doc = frappe._dict({"name": "Outlet", "pos_code_gate_group": "Group A"})
		with patch(
			"pos_next.overrides.code_gate_group.frappe.db.exists", return_value=False
		):
			with self.assertRaises(frappe.exceptions.ValidationError):
				validate_company_group_membership(doc)

	def test_mirror_with_member_row_accepted(self):
		from pos_next.overrides.code_gate_group import validate_company_group_membership

		doc = frappe._dict({"name": "Outlet", "pos_code_gate_group": "Group A"})
		with patch(
			"pos_next.overrides.code_gate_group.frappe.db.exists", return_value=True
		):
			validate_company_group_membership(doc)

	def test_no_mirror_skipped(self):
		from pos_next.overrides.code_gate_group import validate_company_group_membership

		doc = frappe._dict({"name": "Outlet", "pos_code_gate_group": ""})
		# must not touch the DB at all
		with patch(
			"pos_next.overrides.code_gate_group.frappe.db.exists",
			side_effect=AssertionError("should not query"),
		):
			validate_company_group_membership(doc)


class TestGroupSeed(unittest.TestCase):
	def test_seed_creates_four_empty_groups_idempotent(self):
		from pos_next.patches.v2_19_0.seed_code_gate_groups import GROUPS, execute

		self.assertEqual(len(GROUPS), 4)
		names = sorted(g["group_name"] for g in GROUPS)
		self.assertEqual(names, ["Group A", "Group B", "Group C", "Group D"])
		# the 2x2 matrix: (discount, refund)
		matrix = {(g["pos_require_discount_code"], g["pos_require_refund_code"]) for g in GROUPS}
		self.assertEqual(matrix, {(1, 0), (0, 1), (0, 0), (1, 1)})

		created = []

		def get_doc(doc):
			created.append(doc["group_name"])
			return Mock()

		with (
			patch("pos_next.patches.v2_19_0.seed_code_gate_groups.frappe.db.exists", return_value=True),
			patch(
				"pos_next.patches.v2_19_0.seed_code_gate_groups.frappe.get_doc",
				side_effect=AssertionError("must not create existing groups"),
			),
			patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields"),
		):
			execute()
		self.assertEqual(created, [])

	def test_seed_upserts_mirror_column_first(self):
		# regression: the first migrate ran execute() before the mirror
		# Custom Field existed (create_custom_fields runs in after_migrate,
		# AFTER post_model_sync patches), crashing sync_members' linked
		# lookup with "Unknown column 'pos_code_gate_group'"
		from pos_next.patches.v2_19_0.seed_code_gate_groups import execute

		with (
			patch("pos_next.patches.v2_19_0.seed_code_gate_groups.frappe.db.exists", return_value=True),
			patch(
				"pos_next.patches.v2_19_0.seed_code_gate_groups.frappe.get_doc",
				side_effect=AssertionError("must not create existing groups"),
			),
			patch(
				"frappe.custom.doctype.custom_field.custom_field.create_custom_fields"
			) as create_fields,
			patch.dict(
				"sys.modules",
				{"pos_next.install": Mock(CUSTOM_FIELDS={"Company": ["mirror"]})},
			),
		):
			execute()
		create_fields.assert_called_once_with(
			{"Company": ["mirror"]}, ignore_validate=True, update=True
		)

	def test_seed_skips_existing(self):
		from pos_next.patches.v2_19_0.seed_code_gate_groups import execute

		created = []

		def get_doc(doc):
			created.append(doc["group_name"])
			return Mock()

		def exists(doctype, name):
			return name == "Group A"

		with (
			patch("pos_next.patches.v2_19_0.seed_code_gate_groups.frappe.db.exists", side_effect=exists),
			patch("pos_next.patches.v2_19_0.seed_code_gate_groups.frappe.get_doc", side_effect=get_doc),
			patch("frappe.custom.doctype.custom_field.custom_field.create_custom_fields"),
		):
			execute()
		self.assertEqual(sorted(created), ["Group B", "Group C", "Group D"])


if __name__ == "__main__":
	unittest.main()
