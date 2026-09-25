# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""COR-BE-10 acceptance: the per-profile stock block toggle must be honoured.

``posa_block_sale_beyond_available_qty = 0`` means "do not block sales beyond
available stock" for that profile. The old lookup ``cint(value or 1)`` turned
the explicit 0 back into 1, so the toggle could never be switched off. An
unset/empty value must still default to blocked (1).

Run via pos_next/_pn_run_tests.py pos_next.api.test_block_sale_toggle
"""

import unittest
from unittest import mock

import frappe

from pos_next.api.invoices import _should_block

_PROFILE = "TEST-POS-PROFILE"


class TestBlockSaleBeyondAvailableQtyToggle(unittest.TestCase):
    """_should_block must pass the profile's stored value through verbatim."""

    def _patch_lookups(self, profile_value):
        real_get_value = frappe.db.get_value
        real_get_single_value = frappe.db.get_single_value

        def fake_get_value(doctype, *args, **kwargs):
            if doctype == "POS Profile":
                return profile_value
            return real_get_value(doctype, *args, **kwargs)

        def fake_get_single_value(doctype, field, *args, **kwargs):
            # keep the global negative-stock escape hatch out of the way so
            # the profile toggle is the only thing under test
            if doctype == "Stock Settings" and field == "allow_negative_stock":
                return 0
            return real_get_single_value(doctype, field, *args, **kwargs)

        patches = (
            mock.patch("frappe.db.get_value", side_effect=fake_get_value),
            mock.patch("frappe.db.get_single_value", side_effect=fake_get_single_value),
        )
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_explicit_zero_disables_block(self):
        """RED: the old ``or 1`` coerced the explicit 0 back into a block."""
        self._patch_lookups(0)
        self.assertFalse(_should_block(_PROFILE))

    def test_unset_defaults_to_block(self):
        self._patch_lookups(None)
        self.assertTrue(_should_block(_PROFILE))

    def test_empty_defaults_to_block(self):
        self._patch_lookups("")
        self.assertTrue(_should_block(_PROFILE))

    def test_explicit_one_blocks(self):
        self._patch_lookups(1)
        self.assertTrue(_should_block(_PROFILE))

    def test_no_profile_defaults_to_block(self):
        self._patch_lookups(0)
        self.assertTrue(_should_block(None))
