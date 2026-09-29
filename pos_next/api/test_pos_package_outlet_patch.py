# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Tests for the v2_13_0 retire_pos_package_outlet_profile patch.

A legacy outlet row that still carries only a profile name must be
reconverted to its Company + Warehouse pair (or deleted when the profile is
gone), duplicates collapsed, and the pos_profile column rewritten as a
listing of every profile on the pair.

Run via
pos_next/_pn_run_tests.py pos_next.api.test_pos_package_outlet_patch
"""

import unittest

import frappe

from pos_next.api.test_packages import (
	LAPTOP,
	PROFILE,
	_ensure_item,
	_ensure_package,
)
from pos_next.patches.v2_13_0.retire_pos_package_outlet_profile import execute

PKG_CONVERTED = "_PNXT Patch Legacy A"
PKG_ORPHAN = "_PNXT Patch Orphan B"
PARENT_CONVERTED = "_PNXT_PKG_PARENT_PATCH_A"
PARENT_ORPHAN = "_PNXT_PKG_PARENT_PATCH_B"
GONE_PROFILE = "_PNXT Profile That Never Existed"


def _make_package(package_name, parent_item):
	_ensure_item(parent_item, package_name, is_stock_item=False)
	if frappe.db.exists("POS Package", package_name):
		return
	vals = frappe.db.get_value("POS Profile", PROFILE, ["company", "warehouse"], as_dict=True)
	frappe.get_doc(
		{
			"doctype": "POS Package",
			"package_name": package_name,
			"company": vals.company,
			"currency": frappe.db.get_value("Company", vals.company, "default_currency"),
			"parent_item": parent_item,
			"base_price": 500_000.0,
			"items": [{"item_code": LAPTOP, "qty": 1}],
			"outlets": [{"company": vals.company, "warehouse": vals.warehouse, "enabled": 1}],
		}
	).insert(ignore_permissions=True)


def _force_legacy_row(package, profile_name):
	"""Rewind one outlet row to the retired shape: scope columns empty, only
	a profile name — what restored backups and pre-v2_1_0 writes left behind."""
	name = frappe.get_all(
		"POS Package Outlet",
		filters={"parent": package, "parenttype": "POS Package"},
		pluck="name",
		order_by="creation asc",
	)[0]
	frappe.db.set_value(
		"POS Package Outlet",
		name,
		{"company": None, "warehouse": None, "pos_profile": profile_name, "enabled": 1},
		update_modified=False,
	)
	return name


def _insert_raw_outlet_row(parent, company, warehouse, enabled):
	"""A duplicate (company, warehouse) row written straight to the table."""
	name = "pnxt-patch-dup-" + frappe.generate_hash()[:10]
	frappe.db.sql(
		"""
		INSERT INTO `tabPOS Package Outlet`
			(name, parent, parentfield, parenttype, docstatus, idx,
			 company, warehouse, enabled, creation, modified, owner, modified_by)
		VALUES (%s, %s, 'outlets', 'POS Package', 0, 2,
			%s, %s, %s, NOW(), NOW(), 'Administrator', 'Administrator')
		""",
		(name, parent, company, warehouse, enabled),
	)
	return name


class TestRetirePosPackageOutletPatch(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		_ensure_package()
		frappe.db.commit()
		cls.pair = frappe.db.get_value("POS Profile", PROFILE, ["company", "warehouse"], as_dict=True)

		_make_package(PKG_CONVERTED, PARENT_CONVERTED)
		_make_package(PKG_ORPHAN, PARENT_ORPHAN)
		_force_legacy_row(PKG_CONVERTED, PROFILE)
		_force_legacy_row(PKG_ORPHAN, GONE_PROFILE)
		_insert_raw_outlet_row(PKG_CONVERTED, cls.pair["company"], cls.pair["warehouse"], enabled=0)
		frappe.db.commit()

		execute()
		frappe.db.commit()

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		for package in (PKG_CONVERTED, PKG_ORPHAN):
			if frappe.db.exists("POS Package", package):
				frappe.delete_doc("POS Package", package, ignore_permissions=True)
		frappe.db.commit()
		super().tearDownClass()

	def test_legacy_row_is_reconverted_to_its_pair(self):
		rows = frappe.get_all(
			"POS Package Outlet",
			filters={"parent": PKG_CONVERTED, "parenttype": "POS Package"},
			fields=["name", "company", "warehouse", "pos_profile", "status", "enabled"],
		)
		# the legacy row and its raw duplicate collapse into one
		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row["company"], self.pair["company"])
		self.assertEqual(row["warehouse"], self.pair["warehouse"])
		self.assertEqual(row["status"], "Available on all profiles for this warehouse")
		self.assertIn(PROFILE, row["pos_profile"] or "")
		# any-enabled wins over the duplicate's disabled flag
		self.assertEqual(row["enabled"], 1)

	def test_orphan_legacy_row_is_deleted(self):
		self.assertEqual(
			frappe.get_all("POS Package Outlet", filters={"parent": PKG_ORPHAN}, pluck="name"),
			[],
		)

	def test_patch_is_idempotent(self):
		execute()
		frappe.db.commit()
		self.assertEqual(
			len(frappe.get_all("POS Package Outlet", filters={"parent": PKG_CONVERTED}, pluck="name")),
			1,
		)


if __name__ == "__main__":
	unittest.main()
