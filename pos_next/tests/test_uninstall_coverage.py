# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""REL-03 coverage (static, no uninstall is ever executed): uninstall.py must
clean up EVERY object the app installs.

The old uninstall removed a hand-written list of 10 custom fields and leaked
20+ newer fields (pos_coupon_code, pos_queue_*, pos_schedule_*, Company
targets, offer/one-time stashes, ...), the POSNext roles and their Custom
DocPerms, and the POSNext workspace + its sidebar.

These tests import install + uninstall and assert that the uninstall cleanup
structures cover everything install creates. Deletion wiring is checked by
mocking the deletion calls; nothing touches the database.

Run via pos_next/_pn_run_tests.py pos_next.tests.test_uninstall_coverage
"""

import json
import unittest
from pathlib import Path
from unittest import mock

import frappe

from pos_next import install, uninstall


def _covered_pairs():
    return {
        (doctype, fieldname)
        for doctype, fieldnames in uninstall.CLEANUP_CUSTOM_FIELDS.items()
        for fieldname in fieldnames
    }


class TestUninstallCoverage(unittest.TestCase):
    def test_every_installed_custom_field_is_cleaned(self):
        """Every fieldname of CUSTOM_FIELDS + PRICE_GROUP_CUSTOM_FIELDS must
        appear in the uninstall cleanup structure."""
        expected = {
            (doctype, df["fieldname"])
            for source in (install.CUSTOM_FIELDS, install.PRICE_GROUP_CUSTOM_FIELDS)
            for doctype, fields in source.items()
            for df in fields
        }
        self.assertEqual(
            expected - _covered_pairs(),
            set(),
            "uninstall must clean every custom field install creates",
        )

    def test_legacy_handwritten_fields_still_cleaned(self):
        """The old explicit list (pre-fixture era fields) must stay covered."""
        covered = _covered_pairs()
        for pair in (
            ("Sales Invoice", "posa_pos_opening_shift"),
            ("Sales Invoice", "posa_is_printed"),
            ("Sales Invoice Item", "pos_package"),
            ("Sales Invoice Item", "pos_package_instance"),
            ("Sales Invoice Item", "pos_package_role"),
            ("Sales Invoice Item", "pos_package_snapshot"),
            ("Price List", install.PRICE_LIST_OWNER_FIELD),
            ("Item Price", install.ITEM_PRICE_OWNER_FIELD),
            ("POS Profile", install.PROFILE_OWNER_FIELD),
            ("POS Profile", install.PROFILE_PREVIOUS_PRICE_LIST_FIELD),
        ):
            self.assertIn(pair, covered)

    def test_remove_custom_fields_wires_the_full_structure(self):
        """remove_custom_fields must consume CLEANUP_CUSTOM_FIELDS (the single
        test-inspectable source), via frappe's exists-guarded deleter."""
        # defense in depth: an implementation that does NOT route through the
        # mocked deleter must hit a hard "nothing exists" wall, never the
        # real database (this test must never actually delete anything)
        with mock.patch(
            "frappe.custom.doctype.custom_field.custom_field.delete_custom_fields"
        ) as deleter, mock.patch("frappe.db.exists", return_value=False):
            uninstall.remove_custom_fields()
        deleter.assert_called_once_with(uninstall.CLEANUP_CUSTOM_FIELDS)

    def test_docperm_cleanup_covers_fixture_roles_and_mirror_rows(self):
        with mock.patch("frappe.db.delete") as db_delete:
            uninstall.remove_custom_docperms()
        filters = [call.args[1] for call in db_delete.call_args_list]
        by_role = next(f for f in filters if "role" in f)
        self.assertEqual(
            set(by_role["role"][1]),
            set(uninstall.UNINSTALL_ROLES),
            "Custom DocPerm rows of both POSNext roles must be deleted",
        )
        by_prefix = [f for f in filters if "name" in f]
        self.assertTrue(
            any(install.MIRROR_NAME_PREFIX in f["name"][1] for f in by_prefix),
            "the posnext-mirror:: Custom DocPerm rows must be deleted too",
        )

    def test_role_and_workspace_cleanup_wiring(self):
        with mock.patch("frappe.db.exists", return_value=True), mock.patch(
            "frappe.db.delete"
        ), mock.patch("frappe.delete_doc") as delete_doc:
            uninstall.remove_roles()
            uninstall.remove_workspaces()
        deleted = {(call.args[0], call.args[1]) for call in delete_doc.call_args_list}
        expected = (
            {("Role", role) for role in uninstall.UNINSTALL_ROLES}
            | {("Workspace", ws) for ws in uninstall.UNINSTALL_WORKSPACES}
            | {("Workspace Sidebar", sb) for sb in uninstall.UNINSTALL_WORKSPACE_SIDEBARS}
        )
        self.assertEqual(
            expected, deleted, "roles, workspace and its sidebar must all be deleted"
        )

    def test_fixture_docperm_roles_match_uninstall_roles(self):
        """The roles shipped via the custom_docperm fixture are exactly the
        roles the uninstall removes."""
        fixture_path = Path(frappe.get_app_path("pos_next")) / "fixtures" / "custom_docperm.json"
        with open(fixture_path, encoding="utf-8") as handle:
            entries = json.load(handle)
        self.assertEqual(
            {entry.get("role") for entry in entries},
            set(uninstall.UNINSTALL_ROLES),
        )
