# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for POS Offer usage quota enforcement.

Mocked-frappe style — run via
pos_next/_pn_run_tests.py pos_next.api.test_pos_offer_usage
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pos_next.overrides.pos_offer_usage import (
	check_quota,
	get_quota_info,
	parse_applied_offer_rules,
	record_offer_usage_on_submit,
	release_offer_usage_on_cancel,
	resolve_offers_from_rules,
	validate_invoice_offers,
)

DB_PATCH = patch("pos_next.overrides.pos_offer_usage.frappe.db", new_callable=MagicMock)
GET_ALL_PATCH = patch("pos_next.overrides.pos_offer_usage.frappe.get_all")
GET_DOC_PATCH = patch("pos_next.overrides.pos_offer_usage.frappe.get_doc")
SESSION_PATCH = patch(
	"pos_next.overrides.pos_offer_usage.frappe.session", MagicMock(user="cashier@example.com"), create=True
)


def make_offer(**kwargs):
	values = dict(
		name="Promo Gula",
		title="Promo Gula",
		enforce_usage_quota=1,
		quota_scope="Global",
		quota_period="Campaign Total",
		global_max_usage=2,
	)
	values.update(kwargs)
	offer = frappe._dict(values)
	offer.companies = kwargs.get("companies", [frappe._dict(company="Company A", enabled=1, max_usage=5)])
	return offer


class TestGetQuotaInfo(unittest.TestCase):
	def test_not_enforced_returns_none(self):
		self.assertIsNone(get_quota_info(make_offer(enforce_usage_quota=0), "Company A"))

	def test_global_total_counts_all_rows(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 1
			info = get_quota_info(make_offer(), "Company A")
			self.assertEqual({"pos_offer": "Promo Gula"}, mock_db.count.call_args.args[1])
			self.assertEqual({"scope": "Global", "period": "Campaign Total", "limit": 2, "used": 1, "remaining": 1}, info)

	def test_global_daily_adds_posting_date(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 0
			info = get_quota_info(make_offer(quota_period="Daily"), "Company A", posting_date="2026-09-05")
			self.assertEqual(
				{"pos_offer": "Promo Gula", "posting_date": "2026-09-05"},
				mock_db.count.call_args.args[1],
			)
			self.assertEqual(2, info["remaining"])

	def test_per_company_uses_company_row_limit(self):
		offer = make_offer(quota_scope="Per Company")
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 4
			info = get_quota_info(offer, "Company A")
			self.assertEqual(
				{"pos_offer": "Promo Gula", "company": "Company A"},
				mock_db.count.call_args.args[1],
			)
			self.assertEqual(5, info["limit"])
			self.assertEqual(1, info["remaining"])

	def test_per_company_daily_combines_filters(self):
		offer = make_offer(quota_scope="Per Company", quota_period="Daily")
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 9
			info = get_quota_info(offer, "Company A", posting_date="2026-09-05")
			self.assertEqual(
				{"pos_offer": "Promo Gula", "company": "Company A", "posting_date": "2026-09-05"},
				mock_db.count.call_args.args[1],
			)
			self.assertEqual(0, info["remaining"])

	def test_zero_limit_is_unlimited(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 123
			info = get_quota_info(make_offer(global_max_usage=0), "Company A")
			self.assertIsNone(info["remaining"])


class TestCheckQuota(unittest.TestCase):
	def test_throws_when_exhausted(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 2
			with self.assertRaises(frappe.ValidationError) as ctx:
				check_quota(make_offer(), "Company A")
			self.assertIn("Promo Gula", str(ctx.exception))

	def test_passes_with_remaining(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 1
			check_quota(make_offer(), "Company A")

	def test_passes_when_unlimited(self):
		with DB_PATCH as mock_db:
			mock_db.count.return_value = 99
			check_quota(make_offer(global_max_usage=0), "Company A")


def resolve_side_effect(offers, rules_to_schemes=None):
	"""get_all side effect covering resolve_offers_from_rules's three queries."""
	rules_to_schemes = rules_to_schemes or {}

	def filter_names(filters):
		"""Normalize frappe name filters ([\"in\", [...]] or {\"in\": [...]}) to a list."""
		names = (filters or {}).get("name")
		if isinstance(names, dict):
			names = names.get("in", [])
		if isinstance(names, (list, tuple)) and names and names[0] == "in":
			names = names[1]
		return list(names or [])

	def get_all(doctype, filters=None, fields=None, **kwargs):
		if doctype == "Pricing Rule":
			return [
				SimpleNamespace(name=name, promotional_scheme=rules_to_schemes.get(name))
				for name in filter_names(filters)
			]
		if doctype == "Promotional Scheme":
			# Scheme "S-<offer>" owns offer "<offer>"; "S-Other" owns no offer
			# in the fixture, so its pos_offer is None and PR-OTHER drops out
			# via the implementation's `if row.pos_offer` filter.
			owned = {o.name for o in offers}
			return [
				SimpleNamespace(name=owner, pos_offer=owner[2:] if owner.startswith("S-") and owner[2:] in owned else None)
				for owner in set(rules_to_schemes.values())
			]
		if doctype == "POS Offer":
			names = filter_names(filters)
			return [o for o in offers if o.name in names]
		if doctype == "POS Offer Company":
			rows = []
			for o in offers:
				for row in o.companies:
					rows.append(SimpleNamespace(parent=o.name, company=row.company, enabled=row.enabled, max_usage=row.max_usage))
			return rows
		raise AssertionError(f"unexpected get_all on {doctype}")

	return get_all


class TestResolveOffersFromRules(unittest.TestCase):
	def test_maps_rules_to_offers_and_dedupes(self):
		offer = make_offer(enforce_usage_quota=0)
		with GET_ALL_PATCH as mock_get_all:
			mock_get_all.side_effect = resolve_side_effect(
				[offer], {"PR-A": "S-Promo Gula", "PR-B": "S-Promo Gula", "PR-OTHER": "S-Other"}
			)
			result = resolve_offers_from_rules(["PR-A", "PR-B", "PR-OTHER"])
			self.assertEqual(1, len(result))
			self.assertEqual("Promo Gula", result[0].name)
			self.assertEqual(1, len(result[0].companies))

	def test_empty_input(self):
		self.assertEqual([], resolve_offers_from_rules([]))


class TestValidateHook(unittest.TestCase):
	def make_invoice(self, **kwargs):
		values = dict(is_pos=1, is_return=0, company="Company A", posting_date="2026-09-05", name="INV-1")
		values.update(kwargs)
		return frappe._dict(values)

	def test_skips_returns_and_non_pos(self):
		with GET_ALL_PATCH as mock_get_all:
			validate_invoice_offers(self.make_invoice(is_return=1))
			validate_invoice_offers(self.make_invoice(is_pos=0))
			mock_get_all.assert_not_called()

	def test_skips_without_stash(self):
		with GET_ALL_PATCH as mock_get_all:
			validate_invoice_offers(self.make_invoice(pos_applied_offer_rules=""))
			mock_get_all.assert_not_called()

	def test_throws_when_quota_exhausted(self):
		offer = make_offer()  # global limit 2
		invoice = self.make_invoice(pos_applied_offer_rules='["PR-A"]')
		with GET_ALL_PATCH as mock_get_all, DB_PATCH as mock_db:
			mock_get_all.side_effect = resolve_side_effect([offer], {"PR-A": "S-Promo Gula"})
			mock_db.count.return_value = 2
			with self.assertRaises(frappe.ValidationError):
				validate_invoice_offers(invoice)

	def test_passes_when_not_exhausted(self):
		offer = make_offer()
		invoice = self.make_invoice(pos_applied_offer_rules='["PR-A"]')
		with GET_ALL_PATCH as mock_get_all, DB_PATCH as mock_db:
			mock_get_all.side_effect = resolve_side_effect([offer], {"PR-A": "S-Promo Gula"})
			mock_db.count.return_value = 1
			validate_invoice_offers(invoice)  # must not raise


class TestSubmitAndCancel(unittest.TestCase):
	def make_invoice(self, **kwargs):
		values = dict(is_pos=1, is_return=0, company="Company A", posting_date="2026-09-05", name="INV-1")
		values.update(kwargs)
		return frappe._dict(values)

	def test_submit_locks_rechecks_and_records(self):
		offer = make_offer(enforce_usage_quota=1, global_max_usage=2)
		invoice = self.make_invoice(pos_applied_offer_rules='["PR-A"]')
		with GET_ALL_PATCH as mock_get_all, DB_PATCH as mock_db, GET_DOC_PATCH as mock_get_doc, SESSION_PATCH:
			mock_get_all.side_effect = resolve_side_effect([offer], {"PR-A": "S-Promo Gula"})
			mock_db.count.return_value = 1
			inserted = []

			def fake_get_doc(payload):
				doc = frappe._dict(payload)
				doc.insert = lambda *a, **k: inserted.append(payload) or doc
				return doc

			mock_get_doc.side_effect = fake_get_doc

			record_offer_usage_on_submit(invoice)

			mock_db.get_value.assert_called_with("POS Offer", "Promo Gula", "name", for_update=True)
			self.assertEqual(1, len(inserted))
			self.assertEqual("POS Offer Usage", inserted[0]["doctype"])
			self.assertEqual("Promo Gula", inserted[0]["pos_offer"])
			self.assertEqual("INV-1", inserted[0]["sales_invoice"])
			self.assertEqual("2026-09-05", inserted[0]["posting_date"])

	def test_submit_records_even_without_quota(self):
		offer = make_offer(enforce_usage_quota=0)
		invoice = self.make_invoice(pos_applied_offer_rules='["PR-A"]')
		with GET_ALL_PATCH as mock_get_all, DB_PATCH as mock_db, GET_DOC_PATCH as mock_get_doc, SESSION_PATCH:
			mock_get_all.side_effect = resolve_side_effect([offer], {"PR-A": "S-Promo Gula"})
			inserted = []

			def fake_get_doc(payload):
				doc = frappe._dict(payload)
				doc.insert = lambda *a, **k: inserted.append(payload) or doc
				return doc

			mock_get_doc.side_effect = fake_get_doc

			record_offer_usage_on_submit(invoice)
			self.assertEqual(1, len(inserted))
			mock_db.count.assert_not_called()

	def test_cancel_deletes_rows(self):
		with DB_PATCH as mock_db:
			release_offer_usage_on_cancel(self.make_invoice())
			mock_db.delete.assert_called_once_with("POS Offer Usage", {"sales_invoice": "INV-1"})


class TestParseStash(unittest.TestCase):
	def test_parses_json_list(self):
		self.assertEqual(["PR-A", "PR-B"], parse_applied_offer_rules('["PR-B", "PR-A"]'))

	def test_garbage_returns_empty(self):
		self.assertEqual([], parse_applied_offer_rules("not json"))
		self.assertEqual([], parse_applied_offer_rules(None))
		self.assertEqual([], parse_applied_offer_rules('{"a": 1}'))
