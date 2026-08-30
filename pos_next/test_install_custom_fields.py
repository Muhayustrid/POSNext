# Copyright (c) 2025, BrainWise and contributors
# For license information, please see license.txt

"""Tests for pos_next.install.setup_custom_fields() (D8 of add-bakery-pos-capabilities).

Covers the three contract points:
  1. a deleted field is recreated from the Python-side CUSTOM_FIELDS list,
  2. a second call is a no-op (no duplicate `tabCustom Field` rows),
  3. an admin relabel made in the Desk is never overwritten.

Why plain `unittest.TestCase` and not `FrappeTestCase`: creating a Custom
Field runs DDL (`ALTER TABLE ... ADD COLUMN`), which implicitly commits on
MariaDB and cannot be rolled back by the test transaction. The tests are
therefore written to leave the site in its intended state (fields present,
spec labels restored) instead of relying on rollback.

Run inside the bench container via the local helper:
    FRAPPE_STREAM_LOGGING=1 ./env/bin/python \
        apps/pos_next/pos_next/_pn_run_tests.py pos_next.test_install_custom_fields
"""

import unittest

import frappe

from pos_next.install import CUSTOM_FIELDS, setup_custom_fields


def _cf_name(spec):
	return f"{spec['dt']}-{spec['fieldname']}"


def _row_count(spec):
	return frappe.db.count("Custom Field", {"dt": spec["dt"], "fieldname": spec["fieldname"]})


def _delete_field(spec):
	name = _cf_name(spec)
	if frappe.db.exists("Custom Field", name):
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=1)
	frappe.db.commit()
	frappe.clear_cache(doctype=spec["dt"])


class TestSetupCustomFields(unittest.TestCase):
	def setUp(self):
		frappe.set_user("Administrator")

	def tearDown(self):
		# Guarantee the fields end up present with their spec labels, since DDL
		# commits cannot be undone by the test.
		setup_custom_fields(quiet=True)
		for spec in CUSTOM_FIELDS:
			frappe.db.set_value(
				"Custom Field", _cf_name(spec), "label", spec["label"], update_modified=False
			)
		frappe.db.commit()
		frappe.clear_cache()

	def test_creates_fields_when_absent(self):
		"""Every field in CUSTOM_FIELDS is recreated after deletion, with matching attrs."""
		for spec in CUSTOM_FIELDS:
			_delete_field(spec)

		created = setup_custom_fields(quiet=True)
		self.assertEqual(created, len(CUSTOM_FIELDS))

		for spec in CUSTOM_FIELDS:
			row = frappe.db.get_value(
				"Custom Field",
				{"dt": spec["dt"], "fieldname": spec["fieldname"]},
				["dt", "fieldname", "fieldtype", "insert_after", "label"],
				as_dict=True,
			)
			self.assertIsNotNone(row, f"{_cf_name(spec)} was not created")
			for key in ("dt", "fieldname", "fieldtype", "insert_after", "label"):
				self.assertEqual(row[key], spec[key], f"{_cf_name(spec)}: {key} mismatch")

	def test_second_call_creates_no_duplicates(self):
		"""Idempotent: a second call inserts nothing and leaves exactly one row per field."""
		setup_custom_fields(quiet=True)  # ensure present
		created = setup_custom_fields(quiet=True)
		self.assertEqual(created, 0)
		for spec in CUSTOM_FIELDS:
			self.assertEqual(_row_count(spec), 1, f"{_cf_name(spec)} has duplicate rows")

	def test_admin_relabel_is_not_overwritten(self):
		"""Insert-if-absent, never update: a relabelled label survives a re-run."""
		setup_custom_fields(quiet=True)  # ensure present
		spec = CUSTOM_FIELDS[0]
		sentinel = "PNXT TEST SENTINEL LABEL"
		frappe.db.set_value("Custom Field", _cf_name(spec), "label", sentinel, update_modified=False)
		frappe.db.commit()

		created = setup_custom_fields(quiet=True)
		self.assertEqual(created, 0)
		label = frappe.db.get_value("Custom Field", _cf_name(spec), "label")
		self.assertEqual(label, sentinel, "setup_custom_fields() overwrote an admin relabel")
