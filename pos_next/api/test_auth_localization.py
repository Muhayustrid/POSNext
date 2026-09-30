# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Coverage for the auth + localization endpoints that had no tests.

These endpoints sit on the login/lock path of every POS device, so the
contract locked here is: verify_session_password never destroys the session
(structured response, no AuthenticationError raised), localization endpoints
reject Guest, and change_user_language only accepts locales allowed by POS
Next Global Settings.

All tests are hermetic: frappe.db and settings access are mocked, so no site
data is read or written. Run via:
  ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.api.test_auth_localization
"""

import unittest
from unittest.mock import Mock, patch

import frappe

from pos_next.api.auth import verify_session_password
from pos_next.api.localization import (
    change_user_language,
    get_allowed_locales,
    get_allowed_locales_from_settings,
    get_user_language,
)


class TestVerifySessionPassword(unittest.TestCase):
    """The lock screen re-auth must never raise AuthenticationError, or
    Frappe's error handler clears the session cookies (see api/auth.py)."""

    def test_empty_password_returns_structured_failure(self):
        result = verify_session_password(password=None)
        self.assertFalse(result["verified"])
        self.assertTrue(result["message"])

    def test_wrong_password_returns_structured_failure(self):
        with patch(
            "pos_next.api.auth.check_password",
            side_effect=frappe.AuthenticationError,
        ):
            result = verify_session_password(password="wrong")
        self.assertFalse(result["verified"])
        self.assertEqual(result["message"], "Incorrect password")

    def test_correct_password_returns_verified(self):
        with patch("pos_next.api.auth.check_password", return_value=None):
            result = verify_session_password(password="right")
        self.assertTrue(result["verified"])

    def test_rate_limit_declared(self):
        # brute-force guard on the lock screen (5 attempts / 60 s)
        import inspect

        source = inspect.getsource(verify_session_password)
        self.assertIn("rate_limit", source)


class TestGetUserLanguage(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Guest")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_guest_rejected(self):
        with self.assertRaises(frappe.AuthenticationError):
            get_user_language()

    def test_returns_lowercase_language(self):
        frappe.set_user("Administrator")
        with patch(
            "pos_next.api.localization.frappe.db.get_value",
            return_value="ID",
        ):
            result = get_user_language()
        self.assertTrue(result["success"])
        self.assertEqual(result["locale"], "id")

    def test_falls_back_to_en(self):
        frappe.set_user("Administrator")
        with patch(
            "pos_next.api.localization.frappe.db.get_value",
            return_value=None,
        ):
            result = get_user_language()
        self.assertEqual(result["locale"], "en")


class TestChangeUserLanguage(unittest.TestCase):
    def setUp(self):
        frappe.set_user("Guest")

    def tearDown(self):
        frappe.set_user("Administrator")

    def test_guest_rejected(self):
        with self.assertRaises(frappe.AuthenticationError):
            change_user_language("id")

    def test_disabled_user_rejected(self):
        frappe.set_user("Administrator")
        with patch(
            "pos_next.api.localization.frappe.db.get_value",
            return_value=0,
        ):
            with self.assertRaises(frappe.AuthenticationError):
                change_user_language("id")

    def test_missing_locale_rejected(self):
        frappe.set_user("Administrator")
        with patch(
            "pos_next.api.localization.frappe.db.get_value",
            return_value=1,
        ):
            with self.assertRaises(frappe.ValidationError):
                change_user_language(None)

    def test_unsupported_locale_rejected(self):
        frappe.set_user("Administrator")
        with patch(
            "pos_next.api.localization.frappe.db.get_value",
            return_value=1,
        ):
            with patch(
                "pos_next.api.localization.get_allowed_locales_from_settings",
                return_value={"en", "id"},
            ):
                with self.assertRaises(frappe.ValidationError):
                    change_user_language("ar")

    def test_valid_locale_updates_user_and_commits(self):
        frappe.set_user("Administrator")
        with patch(
            "pos_next.api.localization.frappe.db.get_value",
            return_value=1,
        ), patch(
            "pos_next.api.localization.frappe.db.set_value",
        ) as mock_set, patch(
            "pos_next.api.localization.frappe.db.commit",
        ) as mock_commit, patch(
            "pos_next.api.localization.get_allowed_locales_from_settings",
            return_value={"en", "id"},
        ):
            result = change_user_language("ID")
        self.assertTrue(result["success"])
        self.assertEqual(result["locale"], "id")
        mock_set.assert_called_once_with(
            "User", frappe.session.user, "language", "id"
        )
        mock_commit.assert_called_once()

    def test_db_failure_raises_validation_error(self):
        frappe.set_user("Administrator")
        with patch(
            "pos_next.api.localization.frappe.db.get_value",
            return_value=1,
        ), patch(
            "pos_next.api.localization.frappe.db.set_value",
            side_effect=Exception("db down"),
        ), patch(
            "pos_next.api.localization.frappe.log_error",
        ):
            with self.assertRaises(frappe.ValidationError):
                change_user_language("id")


class TestAllowedLocales(unittest.TestCase):
    def test_rows_win_over_defaults(self):
        settings = Mock()
        settings.allowed_locales = [
            Mock(language="EN"),
            Mock(language="id"),
        ]
        with patch("pos_next.api.localization.frappe.get_doc", return_value=settings):
            self.assertEqual(get_allowed_locales_from_settings(), {"en", "id"})

    def test_empty_rows_fall_back_to_defaults(self):
        settings = Mock()
        settings.allowed_locales = []
        with patch("pos_next.api.localization.frappe.get_doc", return_value=settings):
            self.assertEqual(get_allowed_locales_from_settings(), {"en", "id"})

    def test_exception_falls_back_to_defaults(self):
        with patch(
            "pos_next.api.localization.frappe.get_doc",
            side_effect=Exception("missing single"),
        ):
            self.assertEqual(get_allowed_locales_from_settings(), {"en", "id"})

    def test_endpoint_shape(self):
        with patch(
            "pos_next.api.localization.get_allowed_locales_from_settings",
            return_value={"en", "id"},
        ):
            result = get_allowed_locales()
        self.assertTrue(result["success"])
        self.assertEqual(set(result["locales"]), {"en", "id"})
