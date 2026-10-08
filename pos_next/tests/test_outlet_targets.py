# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Tests for the Outlet Targets xlsx export/import endpoints (pos_next.api.outlet_targets)."""

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import get_first_day, nowdate

from pos_next.api.outlet_targets import EXPORT_HEADERS, import_outlet_targets
from pos_next.tests.price_group_helpers import (
	get_default_currency,
	make_test_company,
	make_test_item,
	make_test_pos_profile,
	make_test_warehouse,
)

ADMIN = "Administrator"
USER_NO_ROLE = "hq.xl.nobody@example.com"


class TestOutletTargetsExcel(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user(ADMIN)
		cls.currency = get_default_currency()
		cls.company = cls._make_company("_Test XL Co")
		cls.profile = make_test_pos_profile(
			"HQXL", cls.company, make_test_warehouse("HQXL", cls.company)
		)
		cls.item = make_test_item("_Test XL Item")
		if not frappe.db.exists("User", USER_NO_ROLE):
			frappe.get_doc(
				{"doctype": "User", "email": USER_NO_ROLE, "first_name": "XL", "roles": []}
			).insert(ignore_permissions=True)

	@classmethod
	def _make_company(cls, name):
		if frappe.db.exists("Company", name):
			return name
		company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": name,
				"default_currency": get_default_currency(),
				"country": "Indonesia",
				"abbr": "XL",
			}
		).insert(ignore_permissions=True)
		return company.name

	def _sheet(self, rows):
		"""Header + data rows -> attached File url (xlsx bytes via make_xlsx)."""
		from frappe.utils.xlsxutils import make_xlsx

		data = [list(EXPORT_HEADERS)] + rows
		content = make_xlsx(data, "Outlet Targets").getvalue()
		file_doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "outlet-targets-test.xlsx",
				"attached_to_doctype": "User",
				"attached_to_name": ADMIN,
				"content": content,
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(lambda: frappe.delete_doc("File", file_doc.name, ignore_permissions=True))
		return file_doc.file_url

	def _set_target(self, sales=None, overall=None, month_start=None):
		frappe.call(
			"pos_next.api.hq_monitoring.set_outlet_target",
			company=self.company,
			month_start=month_start or get_first_day(nowdate()),
			target_sales=sales or "",
			overall_target=overall if overall is not None else "",
		)

	def test_export_import_roundtrip(self):
		from pos_next.api.outlet_targets import export_outlet_targets

		month = get_first_day(nowdate())
		frappe.set_user(ADMIN)
		frappe.local.response = frappe._dict()
		export_outlet_targets(month_start=str(month))
		self.assertEqual(frappe.response["type"], "binary")
		content = frappe.response["filecontent"]

		def _from_bytes(data):
			from io import BytesIO

			import openpyxl

			wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
			return [[c.value for c in row] for row in wb.active.iter_rows()]

		rows = _from_bytes(content)
		target_row = next(r for r in rows[1:] if r[0] == self.company)
		target_row[3] = 77777  # Monthly Target
		out_url = self._sheet(rows[1:])

		result = import_outlet_targets(file_url=out_url, month_start=str(month))
		self.assertEqual(result["errors"], [])
		self.assertEqual(result["updated"], 1)
		self.assertEqual(
			frappe.db.get_value(
				"POS Monthly Target",
				{"company": self.company, "month_start": month},
				"target_sales",
			),
			77777,
		)

	def test_import_blank_rows_skipped_and_errors_collected(self):
		month = get_first_day(nowdate())
		url = self._sheet(
			[
				[self.company, self.currency, str(month), 12345, "", "", "", "", "", "", ""],
				["", "", "", "", "", "", "", "", "", "", ""],  # blank -> skipped
				["_No Such Co", self.currency, str(month), 1, "", "", "", "", "", "", ""],  # error
			]
		)
		frappe.set_user(ADMIN)
		result = import_outlet_targets(file_url=url, month_start=str(month))
		self.assertEqual(result["updated"], 1)
		self.assertEqual(result["skipped"], 1)
		self.assertEqual(len(result["errors"]), 1)
		self.assertEqual(result["errors"][0]["company"], "_No Such Co")
		self.assertEqual(
			frappe.db.get_value(
				"POS Monthly Target",
				{"company": self.company, "month_start": month},
				"target_sales",
			),
			12345,
		)

	def test_import_requires_file_and_access(self):
		frappe.set_user(ADMIN)
		with self.assertRaises(frappe.ValidationError):
			import_outlet_targets()
		frappe.set_user(USER_NO_ROLE)
		with self.assertRaises(frappe.PermissionError):
			import_outlet_targets(file_url="whatever", month_start=get_first_day(nowdate()))

	def test_get_outlet_targets_pagination(self):
		from pos_next.api.hq_monitoring import get_outlet_targets

		frappe.set_user(ADMIN)
		full = get_outlet_targets()
		self.assertGreaterEqual(full["total_count"], len(full["rows"]))
		page = get_outlet_targets(page_length=1, start=0)
		self.assertEqual(len(page["rows"]), 1)
		self.assertEqual(page["total_count"], full["total_count"])
		if full["total_count"] > 1:
			second = get_outlet_targets(page_length=1, start=1)
			self.assertNotEqual(page["rows"][0]["company"], second["rows"][0]["company"])

	def test_get_outlet_targets_search_and_company_filter(self):
		from pos_next.api.hq_monitoring import get_outlet_targets

		frappe.set_user(ADMIN)
		out = get_outlet_targets(company=self.company)
		self.assertEqual([r["company"] for r in out["rows"]], [self.company])
		out = get_outlet_targets(search="XL Co")
		self.assertIn(self.company, [r["company"] for r in out["rows"]])
		out = get_outlet_targets(search="_zzz_no_match_")
		self.assertEqual(out["rows"], [])
