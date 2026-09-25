# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""SEC-NEW-12b: discount-code endpoints are rate limited.

check_code / validate_confirmation_code let a user probe discount code
values (inline feedback), so brute-force guessing must be throttled. The
frappe.rate_limit decorator only engages when a request context exists, so
these tests fake the minimal request context (method + IP + form_dict.cmd)
that the decorator reads, then assert the 21st call inside the 300 s window
raises frappe.RateLimitExceededError.

The counter lives in redis under `rl:<cmd>:<ip>:<seconds>`; the exact keys
are deleted after each test so no residue leaks between runs.

Run via pos_next/_pn_run_tests.py pos_next.api.test_discount_code_rate_limit
"""

import unittest

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.discount_code import check_code, validate_confirmation_code

LIMIT = 20
SECONDS = 300
IP = "10.255.255.254"


class TestDiscountCodeRateLimit(FrappeTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        item = frappe.get_all("Item", filters={"disabled": 0, "is_sales_item": 1}, pluck="name", limit=1)
        if not item:
            raise unittest.SkipTest("no sales item on site")
        cls.item = item[0]
        companies = frappe.get_all("Company", pluck="name", limit=1)
        if not companies:
            raise unittest.SkipTest("no company on site")
        cls.company = companies[0]

    def setUp(self):
        frappe.set_user("Administrator")
        # the decorator is a no-op without a request context; fake one
        self._orig = {
            "request": getattr(frappe.local, "request", None),
            "request_ip": getattr(frappe.local, "request_ip", None),
            "form_dict": getattr(frappe.local, "form_dict", None),
        }
        frappe.local.request = frappe._dict(method="POST")
        frappe.local.request_ip = IP

    def tearDown(self):
        for cmd in (
            "pos_next.api.discount_code.check_code",
            "pos_next.api.discount_code.validate_confirmation_code",
        ):
            frappe.cache.delete_value(f"rl:{cmd}:{IP}:{SECONDS}")
        frappe.local.request = self._orig["request"]
        frappe.local.request_ip = self._orig["request_ip"]
        frappe.local.form_dict = self._orig["form_dict"]
        frappe.set_user("Administrator")

    def _set_cmd(self, cmd):
        frappe.local.form_dict = frappe._dict(cmd=cmd)

    def test_check_code_is_rate_limited(self):
        self._set_cmd("pos_next.api.discount_code.check_code")
        # LIMIT guesses within the window all go through (returns, no throw)
        for i in range(LIMIT):
            result = check_code(code=f"GUESS-{i}", company=self.company)
            self.assertFalse(result["valid"])
        with self.assertRaises(frappe.RateLimitExceededError):
            check_code(code="GUESS-OVER", company=self.company)

    def test_validate_confirmation_code_is_rate_limited(self):
        self._set_cmd("pos_next.api.discount_code.validate_confirmation_code")
        items = [
            {
                "item_code": self.item,
                "rate": 90,
                "price_list_rate": 100,
                "is_rate_manually_edited": 1,
            }
        ]
        for i in range(LIMIT):
            result = validate_confirmation_code(
                code=f"GUESS-{i}", company=self.company, items=items, additional_discount=10
            )
            self.assertFalse(result["valid"])
        with self.assertRaises(frappe.RateLimitExceededError):
            validate_confirmation_code(
                code="GUESS-OVER", company=self.company, items=items, additional_discount=10
            )

    def test_rate_limit_isolated_per_ip(self):
        """A different client IP starts with a fresh counter."""
        self._set_cmd("pos_next.api.discount_code.check_code")
        for i in range(LIMIT):
            check_code(code=f"GUESS-{i}", company=self.company)
        with self.assertRaises(frappe.RateLimitExceededError):
            check_code(code="GUESS-OVER", company=self.company)

        other_ip = "10.255.255.253"
        frappe.local.request_ip = other_ip
        try:
            result = check_code(code="GUESS-OVER", company=self.company)
            self.assertFalse(result["valid"])
        finally:
            frappe.cache.delete_value(f"rl:pos_next.api.discount_code.check_code:{other_ip}:{SECONDS}")
