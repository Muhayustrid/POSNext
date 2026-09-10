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
	doc = frappe.get_doc("POS Next Invoice Settings", "POS Next Invoice Settings")
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
		# frappe.db.get_single_value caches per connection
		frappe.db.value_cache.pop("POS Next Invoice Settings", None)

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
