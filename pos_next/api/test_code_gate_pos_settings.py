# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Code gate switches live in POS Settings: an enabled profile row wins,
otherwise POS Next Global Settings. Only a System Manager may change what a
profile resolves to. Also covers the v2_32_0 migration from Company.

Run via pos_next/_pn_run_tests.py pos_next.api.test_code_gate_pos_settings
"""

import uuid
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.discount_code import get_status
from pos_next.overrides.discount_code import (
	_gate_company,
	record_code_usage_on_submit,
	validate_invoice_discounts,
)
from pos_next.pos_next.doctype.pos_settings.pos_settings import update_pos_settings
from pos_next.services.confirmation_code import generate_codes_for
from pos_next.tests.price_group_helpers import (
	get_default_company,
	make_test_pos_profile,
	make_test_warehouse,
)

GLOBAL = "POS Next Global Settings"
SWITCHES = ("require_discount_code", "require_refund_code")
DISCOUNT_DOCTYPE = "POS Discount Confirmation Code"
REFUND_DOCTYPE = "POS Refund Code"


class TestCodeGatePOSSettings(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.company = get_default_company()
		warehouse = make_test_warehouse("_Gate PS", cls.company)
		cls.profile = make_test_pos_profile("_Gate PS", cls.company, warehouse)
		cls.bare_profile = make_test_pos_profile("_Gate PS Bare", cls.company, warehouse)
		frappe.db.delete("POS Settings", {"pos_profile": ["in", [cls.profile, cls.bare_profile]]})
		cls.row = (
			frappe.get_doc({"doctype": "POS Settings", "pos_profile": cls.profile, "enabled": 1})
			.insert(ignore_permissions=True)
			.name
		)
		cls.global_backup = {f: frappe.db.get_single_value(GLOBAL, f) for f in SWITCHES}

	@classmethod
	def tearDownClass(cls):
		frappe.set_user("Administrator")
		frappe.db.delete("POS Settings", {"pos_profile": ["in", [cls.profile, cls.bare_profile]]})
		for field, value in cls.global_backup.items():
			frappe.db.set_single_value(GLOBAL, field, value)
		frappe.db.commit()
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		for field in SWITCHES:
			frappe.db.set_value("POS Settings", self.row, field, 1)
			frappe.db.set_single_value(GLOBAL, field, 1)

	def _sale(self, profile, **fields):
		return frappe._dict(
			is_pos=1,
			company=self.company,
			pos_profile=profile,
			items=[frappe._dict(item_code="IT1", discount_percentage=10)],
			discount_confirmation_code="",
			**fields,
		)

	def _return(self, profile, code=""):
		return frappe._dict(
			is_pos=1,
			is_return=1,
			company=self.company,
			pos_profile=profile,
			name="ACC-SINV-PSRET",
			items=[frappe._dict(item_code="IT1", qty=-1)],
			refund_confirmation_code=code,
		)

	# ---- resolution ------------------------------------------------------

	def test_profile_row_switch_off_opens_its_gate_only(self):
		frappe.db.set_value("POS Settings", self.row, "require_discount_code", 0)
		validate_invoice_discounts(self._sale(self.profile), "validate")  # must not raise
		with self.assertRaises(frappe.ValidationError):
			validate_invoice_discounts(self._return(self.profile), "validate")
		self.assertEqual(
			get_status(company=self.company, pos_profile=self.profile),
			{"enabled": False, "discount_code_required": False, "refund_code_required": True},
		)

	def test_profile_row_wins_over_global(self):
		frappe.db.set_single_value(GLOBAL, "require_refund_code", 0)
		with self.assertRaises(frappe.ValidationError):
			validate_invoice_discounts(self._return(self.profile), "validate")
		validate_invoice_discounts(self._return(self.bare_profile), "validate")  # global off

	def test_disabled_row_falls_back_to_global(self):
		frappe.db.set_value("POS Settings", self.row, {"enabled": 0, "require_refund_code": 0})
		try:
			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(self._return(self.profile), "validate")
		finally:
			frappe.db.set_value("POS Settings", self.row, "enabled", 1)

	def test_doc_without_profile_uses_global(self):
		frappe.db.set_single_value(GLOBAL, "require_discount_code", 0)
		validate_invoice_discounts(self._sale(None), "validate")  # must not raise
		frappe.db.set_single_value(GLOBAL, "require_discount_code", 1)
		with self.assertRaises(frappe.ValidationError):
			validate_invoice_discounts(self._sale(None), "validate")

	def test_unset_global_value_fails_closed(self):
		frappe.db.sql("delete from `tabSingles` where doctype=%s and field='require_refund_code'", GLOBAL)
		# no persisted value: the meta default ("1") applies
		with self.assertRaises(frappe.ValidationError):
			validate_invoice_discounts(self._return(self.bare_profile), "validate")

	def test_gate_off_skips_usage_stamp_on_return(self):
		frappe.db.set_value("POS Settings", self.row, "require_refund_code", 0)
		code = generate_codes_for(REFUND_DOCTYPE, 1)["codes"][0]
		with patch("pos_next.overrides.discount_code.frappe.db.set_value") as mock_set:
			record_code_usage_on_submit(self._return(self.profile, code), "on_submit")
			mock_set.assert_not_called()

	def test_gate_on_accepts_refund_code_and_rejects_discount_code(self):
		refund = generate_codes_for(REFUND_DOCTYPE, 1)["codes"][0]
		validate_invoice_discounts(self._return(self.profile, refund), "validate")  # must not raise
		discount = generate_codes_for(DISCOUNT_DOCTYPE, 1)["codes"][0]
		with self.assertRaises(frappe.ValidationError):
			validate_invoice_discounts(self._return(self.profile, discount), "validate")

	def test_forged_company_still_rejected(self):
		other = frappe.get_all("Company", filters={"name": ["!=", self.company]}, pluck="name", limit=1)
		if not other:
			self.skipTest("needs a second company")
		with self.assertRaises(frappe.ValidationError):
			_gate_company(frappe._dict(company=other[0], pos_profile=self.profile))

	# ---- System Manager guard ----------------------------------------------

	def _manager(self):
		email = f"gate-ps.{uuid.uuid4().hex[:8]}@example.com"
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Gate", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		user.add_roles("POSNext Manager")
		self.addCleanup(frappe.delete_doc, "User", email, force=True, ignore_permissions=True)
		return email

	def test_manager_cannot_flip_switch_on_row(self):
		frappe.set_user(self._manager())
		doc = frappe.get_doc("POS Settings", self.row)
		doc.require_refund_code = 0
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_manager_cannot_disable_row_onto_different_global(self):
		frappe.db.set_single_value(GLOBAL, "require_discount_code", 0)
		frappe.set_user(self._manager())
		doc = frappe.get_doc("POS Settings", self.row)
		doc.enabled = 0
		with self.assertRaises(frappe.PermissionError):
			doc.save()

	def test_manager_new_row_must_match_global(self):
		frappe.set_user(self._manager())
		doc = frappe.get_doc(
			{"doctype": "POS Settings", "pos_profile": self.bare_profile, "require_refund_code": 0}
		)
		with self.assertRaises(frappe.PermissionError):
			doc.insert()

	def test_manager_spa_save_keeps_switches_and_other_fields(self):
		frappe.db.set_value("POS Settings", self.row, "require_refund_code", 0)
		frappe.set_user(self._manager())
		update_pos_settings(
			self.profile, {"require_refund_code": 1, "require_discount_code": 0, "max_discount_allowed": 7}
		)
		frappe.set_user("Administrator")
		row = frappe.db.get_value(
			"POS Settings", self.row, ["require_refund_code", "require_discount_code", "max_discount_allowed"]
		)
		self.assertEqual(row, (0, 1, 7))

	def test_system_manager_can_flip(self):
		doc = frappe.get_doc("POS Settings", self.row)
		doc.require_refund_code = 0
		doc.save()
		self.assertEqual(frappe.db.get_value("POS Settings", self.row, "require_refund_code"), 0)


class TestMoveCodeSwitchesPatch(FrappeTestCase):
	"""v2_32_0 copies the Company switches onto the POS Settings rows. The
	Company columns are gone after the real migrate, so the test plants them
	inside the transaction and rolls everything back."""

	def test_rows_copy_their_company_switches_and_global_turns_on(self):
		from pos_next.patches.v2_32_0 import move_code_switches_to_pos_settings as mod

		company = get_default_company()
		warehouse = make_test_warehouse("_Gate PS", company)
		profile = make_test_pos_profile("_Gate PS Patch", company, warehouse)
		frappe.db.delete("POS Settings", {"pos_profile": profile})
		row = frappe.get_doc({"doctype": "POS Settings", "pos_profile": profile, "enabled": 1}).insert(
			ignore_permissions=True
		)
		frappe.db.commit()
		try:
			columns = frappe.db.get_table_columns("Company")
			# planted with ALTER (implicit commit) only when missing, dropped below
			planted = [
				c for c in ("pos_require_discount_code", "pos_require_refund_code") if c not in columns
			]
			for column in planted:
				frappe.db.sql_ddl(f"alter table `tabCompany` add column `{column}` int(1) default 1")
			frappe.client_cache.delete_value("table_columns::tabCompany")
			frappe.db.sql(
				"update `tabCompany` set pos_require_discount_code=0, pos_require_refund_code=1 where name=%s",
				company,
			)
			frappe.db.set_single_value(GLOBAL, "require_refund_code", 0)
			with (
				patch.object(mod.frappe, "delete_doc"),
				patch.object(mod.frappe.db, "exists", return_value=False),
			):
				mod.execute()
			self.assertEqual(
				frappe.db.get_value(
					"POS Settings", row.name, ["require_discount_code", "require_refund_code"]
				),
				(0, 1),
			)
			self.assertEqual(frappe.db.get_single_value(GLOBAL, "require_refund_code"), 1)
		finally:
			frappe.db.rollback()
			for column in planted:
				frappe.db.sql_ddl(f"alter table `tabCompany` drop column `{column}`")
			frappe.client_cache.delete_value("table_columns::tabCompany")
			frappe.db.delete("POS Settings", {"pos_profile": profile})
			frappe.db.commit()
