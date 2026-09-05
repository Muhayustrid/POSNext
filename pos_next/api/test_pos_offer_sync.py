# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the POS Offer → Promotional Scheme + Pricing Rules sync engine.

Mocked-frappe style — run via
pos_next/_pn_run_tests.py pos_next.api.test_pos_offer_sync
"""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from pos_next.overrides.pos_offer_sync import (
	guard_pricing_rule,
	guard_promotional_scheme,
	handle_offer_trash,
	sync_offer,
)

DB_PATCH = patch("pos_next.overrides.pos_offer_sync.frappe.db", new_callable=MagicMock)
GET_ALL_PATCH = patch("pos_next.overrides.pos_offer_sync.frappe.get_all")
GET_DOC_PATCH = patch("pos_next.overrides.pos_offer_sync.frappe.get_doc")
NEW_DOC_PATCH = patch("pos_next.overrides.pos_offer_sync.frappe.new_doc")
DELETE_DOC_PATCH = patch("pos_next.overrides.pos_offer_sync.frappe.delete_doc")
RENAME_DOC_PATCH = patch("pos_next.overrides.pos_offer_sync.rename_doc")

SLAB_DOCS = ("Promotional Scheme Price Discount", "Promotional Scheme Product Discount")


def make_offer(**kwargs):
	values = dict(
		name="Promo Gula",
		title="Promo Gula",
		valid_from="2026-09-01",
		valid_to="2026-09-30",
		apply_on="Item Code",
		offer_type="Discount Percentage",
		discount_percentage=50,
		max_discount_amount=20000,
		discount_amount=0,
		free_item=None,
		free_qty=1,
		min_qty=0,
		min_amt=0,
		enabled=1,
	)
	values.update(kwargs)
	offer = frappe._dict(values)
	offer.targets = kwargs.get("targets", [frappe._dict(item_code="GULA-1")])
	offer.companies = kwargs.get(
		"companies",
		[
			frappe._dict(company="Company A", enabled=1, max_usage=10),
			frappe._dict(company="Company B", enabled=1, max_usage=0),
		],
	)
	return offer


class FakeChildDoc:
	"""Double for scheme children and pricing rules created via new_doc/get_doc."""

	def __init__(self, doctype=None):
		self.doctype = doctype
		self.name = None
		self.fields = {}
		self.children = {"items": [], "item_groups": [], "brands": []}
		self.inserted = False  # ORM insert() (hooks + generator)
		self.header_inserted = False  # low-level db_insert() (no hooks)
		self.saved = 0

	def update(self, values):
		for key, value in values.items():
			setattr(self, key, value)
			self.fields[key] = value

	def get(self, key, *args, **kwargs):
		return getattr(self, key, None)

	def set(self, field, value):
		self.children[field] = value if isinstance(value, list) else []

	def append(self, field, row):
		self.children.setdefault(field, []).append(SimpleNamespace(**row))

	def insert(self, *args, **kwargs):
		self.inserted = True
		self.name = self.name or f"NEW-{self.doctype}"
		return self

	def db_insert(self, *args, **kwargs):
		# low-level header insert used by _upsert_scheme (no validate/on_update);
		# deliberately does NOT set `inserted` — only the ORM insert() does.
		self.header_inserted = True
		self.name = self.name or f"NEW-{self.doctype}"
		return self

	def save(self, *args, **kwargs):
		self.saved += 1
		return self


def new_doc_recorder():
	created = []

	def new_doc(doctype):
		if doctype == "Promotional Scheme":
			doc = FakeChildDoc("Promotional Scheme")
		elif doctype == "Pricing Rule":
			doc = FakeChildDoc("Pricing Rule")
		elif doctype in ("Promotional Scheme Price Discount", "Promotional Scheme Product Discount"):
			doc = FakeChildDoc(doctype)
			doc.name = "SLAB-1"
		elif doctype.startswith("Pricing Rule "):
			doc = FakeChildDoc(doctype)
		else:
			raise AssertionError(f"unexpected new_doc on {doctype}")
		created.append(doc)
		return doc

	return new_doc, created


def get_doc_recorder(loaded=None):
	"""Recorder for frappe.get_doc.

	- dict argument → child-row insert done by _sync_scheme_children (eligibility
	  rows and discount slabs); every inserted doc is recorded in `inserted` and
	  slabs get the fixed row name "SLAB-1".
	- (doctype, name) → in-memory load of an existing rule; consults the optional
	  `loaded` map before falling back to a fresh double.
	"""
	inserted = []

	def get_doc(*args, **kwargs):
		if args and isinstance(args[0], dict):
			values = dict(args[0])
			doc = FakeChildDoc(values.get("doctype"))
			if doc.doctype in SLAB_DOCS:
				doc.name = "SLAB-1"
			doc.update(values)
			inserted.append(doc)
			return doc
		name = args[1]
		if loaded is not None and name in loaded:
			return loaded[name]
		doc = FakeChildDoc(args[0])
		doc.name = name
		return doc

	return get_doc, inserted


def existing_rules(rows):
	"""rows: list of (name, company) tuples → get_all side effect for Pricing Rule.

	Answers only the rule-by-promotional_scheme lookup (honouring pluck="name",
	which is how handle_offer_trash queries); everything else returns [].
	"""

	def get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		if doctype == "Pricing Rule" and filters and filters.get("promotional_scheme"):
			if pluck == "name":
				return [name for name, _company in rows]
			return [SimpleNamespace(name=name, company=company) for name, company in rows]
		return []

	return get_all


class TestSyncCreate(unittest.TestCase):
	def test_create_builds_scheme_and_one_rule_per_company(self):
		offer = make_offer()
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_db.get_value.return_value = None  # no scheme owned yet
			mock_db.exists.return_value = False
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			get_doc, _inserted = get_doc_recorder()
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([])

			sync_offer(offer)

			# exactly one Pricing Rule per company, built via new_doc + insert
			rules = [d for d in created if d.doctype == "Pricing Rule"]
			self.assertEqual(2, len(rules))
			self.assertEqual(
				["Company A", "Company B"],
				sorted(r.fields.get("company") for r in rules),
			)
			self.assertTrue(all(r.inserted for r in rules))

	def test_rule_values_stamped(self):
		offer = make_offer()
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_db.get_value.return_value = None
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			get_doc, _inserted = get_doc_recorder()
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([])

			sync_offer(offer)

			rule = [d for d in created if d.doctype == "Pricing Rule"][0]
			self.assertEqual("Promo Gula", rule.fields["title"])
			self.assertEqual(1, rule.fields["selling"])
			self.assertEqual("Price", rule.fields["price_or_product_discount"])
			self.assertEqual("Discount Percentage", rule.fields["rate_or_discount"])
			self.assertEqual(50, rule.fields["discount_percentage"])
			self.assertEqual(20000, rule.fields["pos_offer_max_discount"])
			self.assertEqual("SLAB-1", rule.fields["promotional_scheme_id"])
			self.assertEqual(0, rule.fields["disable"])
			self.assertEqual(0, rule.fields["coupon_code_based"])
			self.assertTrue(rule.inserted)
			# eligibility copied onto the rule
			self.assertEqual(1, len(rule.children.get("items", [])))

	def test_scheme_values_stamped(self):
		offer = make_offer()
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_db.get_value.return_value = None
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			get_doc, _inserted = get_doc_recorder()
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([])

			sync_offer(offer)

			scheme = [d for d in created if d.doctype == "Promotional Scheme"][0]
			self.assertEqual("Promo Gula", scheme.name)
			self.assertEqual("Promo Gula", scheme.pos_offer)
			self.assertEqual(1, scheme.fields["selling"])
			self.assertEqual("Item Code", scheme.fields["apply_on"])
			self.assertEqual("2026-09-30", scheme.fields["valid_upto"])
			# the container must stay company-less (scope lives on the rules) —
			# new_doc() otherwise auto-fills company from the default-company default
			self.assertIsNone(scheme.fields.get("company"))
			# the container header must be written low-level (db_insert): a plain
			# ORM insert trips ERPNext's "Price or product discount slabs are
			# required" validation and fires its scheme→rule generator.
			self.assertTrue(scheme.header_inserted)
			self.assertFalse(scheme.inserted)


class TestSyncUpdate(unittest.TestCase):
	def test_company_added_gets_new_rule(self):
		# existing: PR-A for Company A only; offer lists A + B
		offer = make_offer()
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_db.get_value.return_value = "Promo Gula"  # scheme exists (owned)
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			get_doc, _inserted = get_doc_recorder()
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([("PR-A", "Company A")])
			# scheme update path uses db.set_value (no get_doc needed)

			sync_offer(offer)

			rules = [d for d in created if d.doctype == "Pricing Rule"]
			self.assertEqual(1, len(rules))  # only Company B's rule created
			self.assertEqual("Company B", rules[0].fields["company"])
			# header update via db.set_value on the scheme:
			header_calls = [
				c for c in mock_db.set_value.call_args_list if c.args[0] == "Promotional Scheme"
			]
			self.assertTrue(header_calls)

	def test_company_removed_rule_disabled_not_deleted(self):
		offer = make_offer(companies=[frappe._dict(company="Company A", enabled=1, max_usage=0)])
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc, DELETE_DOC_PATCH as mock_delete:
			mock_db.get_value.return_value = "Promo Gula"
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			get_doc, _inserted = get_doc_recorder()
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([("PR-A", "Company A"), ("PR-B", "Company B")])

			sync_offer(offer)

			disable_calls = [
				c for c in mock_db.set_value.call_args_list
				if c.args[0] == "Pricing Rule" and c.args[1] == "PR-B"
			]
			self.assertTrue(disable_calls)
			self.assertEqual({"disable": 1}, disable_calls[0].args[2])
			# no rule deleted — kept for history, disabled only
			mock_delete.assert_not_called()

	def test_disabled_company_row_disables_its_rule(self):
		offer = make_offer(
			companies=[
				frappe._dict(company="Company A", enabled=0, max_usage=0),
				frappe._dict(company="Company B", enabled=1, max_usage=0),
			]
		)
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_db.get_value.return_value = "Promo Gula"
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			loaded = {"PR-A": FakeChildDoc("Pricing Rule"), "PR-B": FakeChildDoc("Pricing Rule")}
			get_doc, _inserted = get_doc_recorder(loaded=loaded)
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([("PR-A", "Company A"), ("PR-B", "Company B")])

			sync_offer(offer)

			self.assertEqual(1, loaded["PR-A"].fields["disable"])
			self.assertEqual(0, loaded["PR-B"].fields["disable"])

	def test_offer_disabled_disables_all_rules_and_scheme(self):
		offer = make_offer(enabled=0)
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_db.get_value.return_value = "Promo Gula"
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			get_doc, _inserted = get_doc_recorder()
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([])

			sync_offer(offer)

			for rule in [d for d in created if d.doctype == "Pricing Rule"]:
				self.assertEqual(1, rule.fields["disable"])
			header_calls = [
				c for c in mock_db.set_value.call_args_list if c.args[0] == "Promotional Scheme"
			]
			self.assertTrue(header_calls)
			self.assertEqual(1, header_calls[0].args[2]["disable"])


class TestSyncFreeItem(unittest.TestCase):
	def test_free_item_offer_builds_product_rule(self):
		offer = make_offer(
			offer_type="Free Item", free_item="BONUS-1", free_qty=2, max_discount_amount=0
		)
		with DB_PATCH as mock_db, NEW_DOC_PATCH as mock_new_doc, GET_ALL_PATCH as mock_get_all, GET_DOC_PATCH as mock_get_doc:
			mock_db.get_value.return_value = None
			new_doc, created = new_doc_recorder()
			mock_new_doc.side_effect = new_doc
			get_doc, inserted = get_doc_recorder()
			mock_get_doc.side_effect = get_doc
			mock_get_all.side_effect = existing_rules([])

			sync_offer(offer)

			rule = [d for d in created if d.doctype == "Pricing Rule"][0]
			self.assertEqual("Product", rule.fields["price_or_product_discount"])
			self.assertEqual("BONUS-1", rule.fields["free_item"])
			self.assertEqual(2, rule.fields["free_qty"])
			self.assertEqual(0, rule.fields["pos_offer_max_discount"])
			slab = [d for d in inserted if d.doctype == "Promotional Scheme Product Discount"]
			self.assertEqual(1, len(slab))


class TestOfferTrash(unittest.TestCase):
	def test_trash_deletes_rules_disables_and_renames_scheme(self):
		offer = make_offer()
		with DB_PATCH as mock_db, GET_ALL_PATCH as mock_get_all, DELETE_DOC_PATCH as mock_delete, RENAME_DOC_PATCH as mock_rename:
			mock_db.get_value.return_value = "Promo Gula"
			mock_db.exists.return_value = False  # "(DELETED)" name not taken
			mock_get_all.side_effect = existing_rules([("PR-A", "Company A"), ("PR-B", "Company B")])

			handle_offer_trash(offer)

			deleted = sorted(c.args[1] for c in mock_delete.call_args_list)
			self.assertEqual(["PR-A", "PR-B"], deleted)
			header_calls = [
				c for c in mock_db.set_value.call_args_list if c.args[0] == "Promotional Scheme"
			]
			self.assertTrue(header_calls)
			self.assertEqual({"disable": 1, "pos_offer": None}, header_calls[0].args[2])
			mock_rename.assert_called_once_with(
				"Promotional Scheme",
				"Promo Gula",
				"Promo Gula (DELETED)",
				ignore_permissions=True,
				show_alert=False,
			)


class TestOwnershipGuards(unittest.TestCase):
	def test_rule_edit_blocked_when_managed(self):
		doc = frappe._dict(name="PR-A", title="Promo Gula", promotional_scheme="Promo Gula")
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = "Promo Gula"  # owner offer
			frappe.flags.in_pos_offer_sync = False
			with self.assertRaises(frappe.ValidationError):
				guard_pricing_rule(doc)

	def test_rule_edit_allowed_during_sync(self):
		doc = frappe._dict(name="PR-A", title="Promo Gula", promotional_scheme="Promo Gula")
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = "Promo Gula"
			frappe.flags.in_pos_offer_sync = True
			try:
				guard_pricing_rule(doc)  # must not raise
			finally:
				frappe.flags.in_pos_offer_sync = False

	def test_rule_edit_allowed_when_unmanaged(self):
		doc = frappe._dict(name="PR-X", promotional_scheme="Some Other Scheme")
		with DB_PATCH as mock_db:
			mock_db.get_value.return_value = None
			guard_pricing_rule(doc)

	def test_scheme_edit_blocked_when_managed(self):
		doc = frappe._dict(name="Promo Gula", pos_offer="Promo Gula")
		frappe.flags.in_pos_offer_sync = False
		with self.assertRaises(frappe.ValidationError):
			guard_promotional_scheme(doc)

	def test_scheme_edit_allowed_when_unmanaged(self):
		doc = frappe._dict(name="Plain Scheme", pos_offer=None)
		guard_promotional_scheme(doc)
