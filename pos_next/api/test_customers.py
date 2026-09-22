# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

import unittest
from unittest.mock import MagicMock, Mock, patch

from pos_next.api.customers import (
	_get_customer_assignment_context,
	create_customer,
	get_customers,
	get_default_loyalty_program_from_settings,
)


class TestCustomersAPI(unittest.TestCase):
	@patch("pos_next.api.customers.frappe.logger")
	@patch("pos_next.api.customers.frappe.get_list")
	@patch("pos_next.api.customers.frappe.db")
	def test_get_customers_applies_search_term_filters(self, mock_db, mock_get_list, mock_logger):
		mock_logger.return_value = Mock()
		mock_get_list.return_value = []

		get_customers(search_term="john", limit=10)

		mock_get_list.assert_called_once()
		kwargs = mock_get_list.call_args.kwargs
		self.assertEqual(kwargs["filters"], {"disabled": 0})
		self.assertEqual(
			kwargs["or_filters"],
			[
				["Customer", "name", "like", "%john%"],
				["Customer", "customer_name", "like", "%john%"],
				["Customer", "mobile_no", "like", "%john%"],
				["Customer", "email_id", "like", "%john%"],
			],
		)

	@patch("pos_next.api.settings_resolver.frappe.db")
	def test_get_default_loyalty_program_from_settings_uses_explicit_pos_profile(self, mock_db):
		# Explicit MagicMock: patch auto-derives the child mock class from the
		# target attribute, and an AsyncMock here returns a coroutine instead
		# of the value on Python 3.14. The row comes back dict-shaped because
		# the settings resolver reads it with .get().
		mock_db.get_value = MagicMock(
			return_value={"name": "PS-0001", "default_loyalty_program": "LOYALTY-A"}
		)

		result = get_default_loyalty_program_from_settings(pos_profile="POS-A")

		self.assertEqual(result, "LOYALTY-A")
		filters = mock_db.get_value.call_args[0][1]
		self.assertEqual(filters, {"pos_profile": "POS-A", "enabled": 1})
		self.assertIn("default_loyalty_program", mock_db.get_value.call_args[0][2])

	@patch("pos_next.api.customers.frappe.get_cached_value")
	@patch("pos_next.api.customers.frappe.get_all")
	def test_get_default_loyalty_program_from_settings_skips_ambiguous_company_context(
		self,
		mock_get_all,
		mock_get_cached_value,
	):
		mock_get_all.return_value = [
			Mock(pos_profile="POS-1", default_loyalty_program="LOYALTY-A"),
			Mock(pos_profile="POS-2", default_loyalty_program="LOYALTY-B"),
		]
		mock_get_cached_value.side_effect = ["Company A", "Company A"]

		result = get_default_loyalty_program_from_settings(company="Company A")

		self.assertIsNone(result)

	@patch("pos_next.api.settings_resolver.frappe.get_meta")
	@patch("pos_next.api.settings_resolver.frappe.db")
	@patch("pos_next.api.customers.frappe.get_all")
	def test_get_default_loyalty_program_from_settings_falls_back_to_global_default(
		self,
		mock_get_all,
		mock_resolver_db,
		mock_get_meta,
	):
		# The company scan finds no row default: the global single's persisted
		# default_loyalty_program still applies.
		mock_get_all.return_value = []
		mock_get_meta.return_value.get_field.return_value = Mock(fieldtype="Link", default=None)
		# Explicit MagicMock child: patch auto-derives AsyncMock on 3.14.
		mock_resolver_db.sql = MagicMock(return_value=[("default_loyalty_program", "LOYALTY-G")])

		result = get_default_loyalty_program_from_settings(company="Company A")

		self.assertEqual(result, "LOYALTY-G")

	@patch(
		"pos_next.api.customers.frappe.local",
		new=Mock(form_dict={"company": "Company A", "pos_profile": "POS-A"}),
	)
	@patch(
		"pos_next.api.customers.frappe.flags",
		new=Mock(pos_next_customer_company=None, pos_next_customer_pos_profile=None),
	)
	def test_get_customer_assignment_context_uses_request_context(self):
		company, pos_profile = _get_customer_assignment_context()

		self.assertEqual(company, "Company A")
		self.assertEqual(pos_profile, "POS-A")

	@patch(
		"pos_next.api.customers.frappe.flags",
		new=Mock(pos_next_customer_company=None, pos_next_customer_pos_profile=None),
	)
	@patch("pos_next.api.customers.frappe.get_doc")
	@patch("pos_next.api.customers.get_default_loyalty_program_from_settings")
	@patch("pos_next.api.customers.frappe.has_permission")
	def test_create_customer_uses_pos_profile_for_loyalty_assignment(
		self,
		mock_has_permission,
		mock_get_loyalty,
		mock_get_doc,
	):
		mock_has_permission.return_value = True
		mock_get_loyalty.return_value = "LOYALTY-A"

		customer_doc = Mock()
		customer_doc.as_dict.return_value = {"name": "CUST-0001", "loyalty_program": "LOYALTY-A"}
		mock_get_doc.return_value = customer_doc

		result = create_customer(
			customer_name="John Doe",
			customer_group="Individual",
			territory="All Territories",
			pos_profile="POS-A",
		)

		mock_get_loyalty.assert_called_once_with(company=None, pos_profile="POS-A")
		customer_doc.insert.assert_called_once_with()
		self.assertEqual(result["loyalty_program"], "LOYALTY-A")
