from contextlib import contextmanager
from unittest import mock

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.invoice_type import (
	POS_INVOICE,
	SALES_INVOICE,
	get_pos_invoice_doctype,
	get_sales_report_doctypes,
)


@contextmanager
def _no_blockers():
	"""Make the switch guard see zero open shifts / pending syncs.

	Site data (posnext.localhost) holds real open POS Opening Shifts, so the
	guard would legitimately block any switch; the allowed-path tests only
	need the zero-count case.
	"""
	with mock.patch("frappe.db.count", return_value=0):
		yield


def _set_invoice_type(value):
	"""Flip the global switch stored on the POS Settings rows."""
	name = frappe.db.get_value("POS Settings", {}, "name")
	if name:
		doc = frappe.get_doc("POS Settings", name)
	else:
		doc = frappe.new_doc("POS Settings")
		doc.pos_profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		doc.enabled = 1
	doc.invoice_type = value
	doc.save(ignore_permissions=True)


class TestInvoiceType(FrappeTestCase):
	def setUp(self):
		self._clear_cache()

	def tearDown(self):
		with _no_blockers():
			_set_invoice_type(SALES_INVOICE)
		self._clear_cache()
		frappe.db.commit()

	@staticmethod
	def _clear_cache():
		try:
			del frappe.local._pos_next_invoice_doctype
		except AttributeError:
			pass  # not cached yet (`in frappe.local` is unreliable on v16)

	def test_defaults_to_sales_invoice(self):
		self.assertEqual(get_pos_invoice_doctype(), SALES_INVOICE)
		self.assertEqual(get_sales_report_doctypes(), ["Sales Invoice"])

	def test_switch_allowed_without_open_shift(self):
		with _no_blockers():
			_set_invoice_type(POS_INVOICE)
		self._clear_cache()
		self.assertEqual(get_pos_invoice_doctype(), POS_INVOICE)
		self.assertEqual(get_sales_report_doctypes(), ["POS Invoice", "Sales Invoice"])

	def test_switch_rejected_with_open_shift(self):
		# fabricate an open shift row without full insert flow
		from frappe.utils import nowdate

		# a profile whose schedule never blocks (enforce_closing=0): insert must
		# not throw for being outside the scheduled shift window. Site data has
		# no schedule-off profile; among enforce=0 profiles only ones with valid
		# schedule times pass validate_schedule_values. (`pos_schedule_start is
		# set` excludes midnight starts like 00:00:00, so filter on end only.)
		profile, company = frappe.db.get_value(
			"POS Profile",
			[
				["disabled", "=", 0],
				["pos_schedule_enforce_closing", "=", 0],
				["pos_schedule_end", "is", "set"],
			],
			("name", "company"),
		)
		# balance_details is mandatory: one row with a real mode_of_payment
		# from the profile's POS Payment Method list
		mode_of_payment = frappe.db.get_value(
			"POS Payment Method",
			{"parent": profile, "parenttype": "POS Profile"},
			"mode_of_payment",
		)
		shift = frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": profile,
				"company": company,
				"user": "Administrator",
				"posting_date": nowdate(),
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": [{"mode_of_payment": mode_of_payment, "amount": 0}],
			}
		).insert(ignore_permissions=True)
		# a concurrent writer (dev server on this shared site) can bump
		# `modified` right after insert; refresh before submit so the
		# timestamp check does not flake
		shift.reload()
		shift.submit()
		try:
			with self.assertRaises(frappe.ValidationError):
				_set_invoice_type(POS_INVOICE)
		finally:
			# shift.cancel() is not staleness-proof: on_submit's db.set_value
			# bumps `modified`, leaving the in-memory doc outdated. Flip
			# docstatus at the DB level and force-delete instead.
			frappe.db.set_value("POS Opening Shift", shift.name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", shift.name, force=1)


class TestCustomPOSInvoice(FrappeTestCase):
	def setUp(self):
		# same schedule-safe filter as test_switch_rejected_with_open_shift: a
		# profile whose schedule never blocks shift insert at the current time
		self.profile = frappe.db.get_value(
			"POS Profile",
			[
				["disabled", "=", 0],
				["pos_schedule_enforce_closing", "=", 0],
				["pos_schedule_end", "is", "set"],
			],
			["name", "company", "warehouse"],
			as_dict=True,
		)
		if not self.profile:
			self.skipTest("no POS Profile")

	def _opening_shift(self):
		# balance_details is mandatory: one row with a real mode_of_payment
		# from the profile's POS Payment Method list
		self.mode_of_payment = frappe.db.get_value(
			"POS Payment Method",
			{"parent": self.profile.name, "parenttype": "POS Profile"},
			"mode_of_payment",
		)
		if not self.mode_of_payment:
			self.skipTest("POS Profile has no Payment Method")
		return frappe.get_doc(
			{
				"doctype": "POS Opening Shift",
				"pos_profile": self.profile.name,
				"company": self.profile.company,
				"user": "Administrator",
				"posting_date": frappe.utils.nowdate(),
				"period_start_date": frappe.utils.now_datetime(),
				"balance_details": [{"mode_of_payment": self.mode_of_payment, "amount": 0}],
			}
		).insert(ignore_permissions=True)

	def test_pos_next_shift_replaces_opening_entry_requirement(self):
		# a REAL item: link validation runs before validate(), so a
		# nonexistent item would abort before the opening-entry gate and the
		# test would pass vacuously
		item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
			pluck="name",
			limit=1,
		)
		if not item:
			self.skipTest("no sales item")
		shift = self._opening_shift()
		# a concurrent writer (dev server on this shared site) can bump
		# `modified` right after insert; refresh before submit so the
		# timestamp check does not flake (same as test_switch_rejected_with_open_shift)
		shift.reload()
		shift.submit()
		try:
			doc = frappe.new_doc("POS Invoice")
			doc.update(
				{
					"customer": frappe.get_all("Customer", pluck="name", limit=1)[0],
					"is_pos": 1,
					"update_stock": 1,
					"pos_profile": self.profile.name,
					"company": self.profile.company,
					"set_warehouse": self.profile.warehouse,
					"posa_pos_opening_shift": shift.name,
					"currency": frappe.db.get_value(
						"Company", self.profile.company, "default_currency"
					),
				}
			)
			doc.append("items", {"item_code": item[0], "qty": 1, "rate": 1})
			doc.append("payments", {"mode_of_payment": self.mode_of_payment, "amount": 1})
			# Opening-entry gate must NOT fire: either insert succeeds or it
			# raises for an unrelated reason
			try:
				doc.insert()
			except Exception as err:
				self.assertNotIn("POS Opening Entry", str(err))
			else:
				frappe.delete_doc("POS Invoice", doc.name, force=1)
		finally:
			# shift.cancel() is not staleness-proof (same as
			# test_switch_rejected_with_open_shift): on_submit's db.set_value
			# bumps `modified`, leaving the in-memory doc outdated. Flip
			# docstatus at the DB level and force-delete instead.
			frappe.db.set_value("POS Opening Shift", shift.name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", shift.name, force=1)

	def test_pos_next_shift_wrong_profile_rejected(self):
		# a REAL item: link validation must not abort before the gate (see above)
		item = frappe.get_all(
			"Item",
			filters={"disabled": 0, "is_sales_item": 1, "is_stock_item": 1},
			pluck="name",
			limit=1,
		)
		if not item:
			self.skipTest("no sales item")
		shift = self._opening_shift()
		shift.reload()
		shift.submit()
		try:
			# only one schedule-safe profile exists on this site, so the
			# cross-profile case is staged at the DB level (raw set_value, no
			# link validation): the shift row now claims a different profile
			# than the invoice's pos_profile — exactly what a client passing
			# another profile's shift name would produce
			frappe.db.set_value(
				"POS Opening Shift",
				shift.name,
				"pos_profile",
				"_PN Wrong Profile",
				update_modified=False,
			)
			doc = frappe.new_doc("POS Invoice")
			doc.update(
				{
					"customer": frappe.get_all("Customer", pluck="name", limit=1)[0],
					"is_pos": 1,
					"update_stock": 1,
					"pos_profile": self.profile.name,
					"company": self.profile.company,
					"set_warehouse": self.profile.warehouse,
					"posa_pos_opening_shift": shift.name,
					"currency": frappe.db.get_value(
						"Company", self.profile.company, "default_currency"
					),
				}
			)
			doc.append("items", {"item_code": item[0], "qty": 1, "rate": 1})
			doc.append("payments", {"mode_of_payment": self.mode_of_payment, "amount": 1})
			with self.assertRaises(frappe.ValidationError) as ctx:
				doc.insert()
			# the override's own clear error, not the generic "POS Opening Entry"
			self.assertIn("belongs to POS Profile", str(ctx.exception))
		finally:
			# staleness-proof teardown (see above)
			frappe.db.set_value("POS Opening Shift", shift.name, "docstatus", 2, update_modified=False)
			frappe.delete_doc("POS Opening Shift", shift.name, force=1)


class TestPOSInvoiceCustomFields(FrappeTestCase):
	def test_columns_exist(self):
		from pos_next.install import CUSTOM_FIELDS, after_migrate

		after_migrate()  # idempotent
		for dt, fields in CUSTOM_FIELDS.items():
			if dt not in ("POS Invoice", "POS Invoice Item", "Sales Invoice Reference",
			              "Offline Invoice Sync"):
				continue
			for f in fields:
				self.assertTrue(
					frappe.db.has_column(dt, f["fieldname"]), f"{dt}.{f['fieldname']} missing"
				)


class TestBootstrapInvoiceType(FrappeTestCase):
	def test_bootstrap_exposes_invoice_type(self):
		# authenticated context is unavailable in unit runner; test the helper path
		from pos_next.api.bootstrap import _get_pos_settings

		profile_name = frappe.db.get_value("POS Profile", [["disabled", "=", 0]])
		if not profile_name:
			self.skipTest("no enabled POS Profile")
		profile = frappe.get_cached_doc("POS Profile", profile_name)
		settings = _get_pos_settings(profile)
		self.assertIn(settings["invoice_type"], ("Sales Invoice", "POS Invoice"))
