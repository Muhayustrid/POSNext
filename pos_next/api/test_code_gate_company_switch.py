# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Unit tests for the per-Company code gate switches and the refund pool.

DB-backed (real Company rows via frappe.db.set_value, restored in tearDown) —
run via pos_next/_pn_run_tests.py pos_next.api.test_code_gate_company_switch

Covers:
- switch x gate combinations (discount pool on sales, refund pool on returns)
- cross-pool rejection both directions
- NULL switch / missing company fail closed
- usage stamp only when the gate is on
- Company validate guard (non-System-Manager change rejected, System Manager allowed)
- migration patch mapping from the retired POS Settings require_refund_code
"""

import unittest
from unittest.mock import MagicMock, patch

import frappe

from pos_next.overrides.discount_code import (
	_gate_company,
	discount_code_required,
	record_code_usage_on_submit,
	refund_code_required,
	validate_code,
	validate_invoice_discounts,
)
from pos_next.patches.v2_18_0.seed_company_code_switches import _resolve_refund_switch
from pos_next.services.confirmation_code import generate_codes_for

DISCOUNT_DOCTYPE = "POS Discount Confirmation Code"
REFUND_DOCTYPE = "POS Refund Code"
CODE_COMPANY_DOCTYPE = "POS Discount Code Company"
COMPANY = None  # resolved in setUpClass


def _two_companies_with_switches():
	"""(switch-off, switch-on) pair of real companies for gate-company tests.

	The primary test company is "on"; the second company is the "off" one
	(tests flip its switch, restore in a finally)."""
	second = None
	for name in frappe.get_all("Company", pluck="name"):
		if name != COMPANY:
			second = name
			break
	if not second:
		second = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "_Test Gate Co",
				"abbr": "TGC",
				"default_currency": "IDR",
				"country": "Indonesia",
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("Company", COMPANY, "pos_require_discount_code", 1)
	frappe.db.set_value("Company", COMPANY, "pos_require_refund_code", 1)
	frappe.db.set_value("Company", second, "pos_require_discount_code", 1)
	frappe.db.set_value("Company", second, "pos_require_refund_code", 1)
	return second, COMPANY


def _profile_for_company(company):
	"""An existing POS Profile of `company`, or a throwaway one."""
	name = frappe.db.get_value(
		"POS Profile", {"company": company}, "name", order_by="creation asc"
	)
	if name:
		return name
	# warehouse is mandatory; reuse the test helpers the other suites use
	from pos_next.tests.price_group_helpers import make_test_pos_profile, make_test_warehouse

	return make_test_pos_profile("_Gate Co", company, make_test_warehouse("_Gate Co", company))


def _code_row(**overrides):
	row = {
		"name": "CODE-1",
		"code": "ABCD2345",
		"status": "Active",
		"valid_from": None,
		"valid_upto": None,
		"company_scope": "All Outlets",
		"used_count": 0,
	}
	row.update(overrides)
	return frappe._dict(row)


class TestCompanyCodeSwitch(unittest.TestCase):
	"""Gate x switch matrix against the real default test company."""

	@classmethod
	def setUpClass(cls):
		global COMPANY
		COMPANY = frappe.db.get_value("Company", {}, "name", order_by="creation asc")
		frappe.db.set_value("Company", COMPANY, "pos_require_discount_code", 1)
		frappe.db.set_value("Company", COMPANY, "pos_require_refund_code", 1)

	def tearDown(self):
		frappe.db.set_value("Company", COMPANY, "pos_require_discount_code", 1)
		frappe.db.set_value("Company", COMPANY, "pos_require_refund_code", 1)

	@staticmethod
	def _doc(**fields):
		fields.setdefault("is_pos", 1)
		fields.setdefault("company", COMPANY)
		return frappe._dict(fields)

	def _discount_doc(self, **fields):
		items = fields.pop("items", [frappe._dict(item_code="IT1", discount_percentage=10)])
		return self._doc(items=items, discount_confirmation_code=fields.pop("discount_confirmation_code", ""), **fields)

	# ---- discount gate -------------------------------------------------

	def test_discount_gate_on_blocks_without_code(self):
		doc = self._discount_doc()
		with self.assertRaises(frappe.ValidationError):
			validate_invoice_discounts(doc, "validate")

	def test_discount_gate_off_skips_check(self):
		frappe.db.set_value("Company", COMPANY, "pos_require_discount_code", 0)
		doc = self._discount_doc()
		validate_invoice_discounts(doc, "validate")  # must not raise

	def test_discount_gate_off_accepts_code_and_skips_usage_stamp(self):
		# a discounted sale carrying a code still passes, but nothing is audited
		frappe.db.set_value("Company", COMPANY, "pos_require_discount_code", 0)
		result = generate_codes_for(DISCOUNT_DOCTYPE, 1)
		doc = self._discount_doc(discount_confirmation_code=result["codes"][0], name="ACC-SINV-SW1")
		validate_invoice_discounts(doc, "validate")  # must not raise

		with patch("pos_next.overrides.discount_code.frappe.db.set_value") as mock_set:
			record_code_usage_on_submit(doc, "on_submit")
			mock_set.assert_not_called()

	def test_discount_gate_on_audits_usage(self):
		result = generate_codes_for(DISCOUNT_DOCTYPE, 1)
		doc = self._discount_doc(discount_confirmation_code=result["codes"][0], name="ACC-SINV-SW2")
		record_code_usage_on_submit(doc, "on_submit")  # must not raise
		self.assertEqual(
			frappe.db.get_value(DISCOUNT_DOCTYPE, {"code": result["codes"][0]}, "used_count"), 1
		)

	# ---- refund gate ---------------------------------------------------

	def _return_doc(self, code_value, name="ACC-SINV-RET"):
		return self._doc(
			is_return=1,
			name=name,
			items=[frappe._dict(item_code="IT1", qty=-1)],
			refund_confirmation_code=code_value,
		)

	def test_refund_gate_on_blocks_without_code(self):
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_invoice_discounts(self._return_doc(""), "validate")
		self.assertIn("required", str(ctx.exception))

	def test_refund_gate_on_passes_with_refund_code(self):
		result = generate_codes_for(REFUND_DOCTYPE, 1)
		validate_invoice_discounts(self._return_doc(result["codes"][0]), "validate")  # must not raise

	def test_refund_gate_on_rejects_discount_code(self):
		# the pools never validate each other's codes
		result = generate_codes_for(DISCOUNT_DOCTYPE, 1)
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_invoice_discounts(self._return_doc(result["codes"][0]), "validate")
		self.assertIn("not valid", str(ctx.exception))

	def test_discount_sale_rejects_refund_code(self):
		result = generate_codes_for(REFUND_DOCTYPE, 1)
		doc = self._discount_doc(discount_confirmation_code=result["codes"][0])
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_invoice_discounts(doc, "validate")
		self.assertIn("not valid", str(ctx.exception))

	def test_refund_gate_off_skips_check(self):
		frappe.db.set_value("Company", COMPANY, "pos_require_refund_code", 0)
		validate_invoice_discounts(self._return_doc(""), "validate")  # must not raise

	def test_refund_gate_off_skips_usage_stamp(self):
		frappe.db.set_value("Company", COMPANY, "pos_require_refund_code", 0)
		result = generate_codes_for(REFUND_DOCTYPE, 1)
		doc = self._return_doc(result["codes"][0], name="ACC-SINV-RET2")
		with patch("pos_next.overrides.discount_code.frappe.db.set_value") as mock_set:
			record_code_usage_on_submit(doc, "on_submit")
			mock_set.assert_not_called()

	def test_refund_gate_on_audits_usage(self):
		result = generate_codes_for(REFUND_DOCTYPE, 1)
		doc = self._return_doc(result["codes"][0], name="ACC-SINV-RET3")
		record_code_usage_on_submit(doc, "on_submit")  # must not raise
		self.assertEqual(
			frappe.db.get_value(REFUND_DOCTYPE, {"code": result["codes"][0]}, "used_count"), 1
		)

	# ---- fail closed ---------------------------------------------------

	def test_null_switch_fails_closed(self):
		# A Check column is NOT NULL on real rows, but a NULL can reach the
		# gate through a dropped/restored column — the resolver must treat it
		# as required.
		with patch(
			"pos_next.overrides.discount_code.frappe.db.get_value", return_value=None
		) as mock_get:
			self.assertTrue(discount_code_required(COMPANY))
			self.assertTrue(refund_code_required(COMPANY))
			self.assertEqual(mock_get.call_args_list[0][0], ("Company", COMPANY, "pos_require_discount_code"))
		with patch("pos_next.overrides.discount_code.frappe.db.get_value", return_value=None):
			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(self._discount_doc(), "validate")
			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(self._return_doc(""), "validate")

	def test_invoice_without_company_fails_closed(self):
		doc = self._discount_doc(company=None)
		with self.assertRaises(frappe.ValidationError):
			validate_invoice_discounts(doc, "validate")

	def test_unknown_company_fails_closed(self):
		self.assertTrue(discount_code_required("No Such Company"))
		self.assertTrue(refund_code_required("No Such Company"))

	# ---- gate company (profile wins over the client-supplied company) ----

	def test_forged_company_cannot_pick_switch_off_outlet(self):
		# sale: doc.company points at a switch-OFF outlet, the doc's profile
		# belongs to a switch-ON one — the mismatch itself is rejected
		off_company, on_company = _two_companies_with_switches()
		profile = _profile_for_company(on_company)
		frappe.db.set_value("Company", off_company, "pos_require_discount_code", 0)
		try:
			doc = self._discount_doc(company=off_company)
			doc.pos_profile = profile
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_invoice_discounts(doc, "validate")
			self.assertIn("does not match", str(ctx.exception))
		finally:
			frappe.db.set_value("Company", off_company, "pos_require_discount_code", 1)

	def test_forged_company_cannot_pick_switch_off_outlet_on_return(self):
		off_company, on_company = _two_companies_with_switches()
		profile = _profile_for_company(on_company)
		frappe.db.set_value("Company", off_company, "pos_require_refund_code", 0)
		try:
			doc = self._return_doc("")
			doc.company = off_company
			doc.pos_profile = profile
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_invoice_discounts(doc, "validate")
			self.assertIn("does not match", str(ctx.exception))
		finally:
			frappe.db.set_value("Company", off_company, "pos_require_refund_code", 1)

	def test_profile_company_wins_scope_and_usage_stamp(self):
		# a scoped code valid only for the PROFILE's company passes when the
		# doc's company agrees with the profile — and the usage stamp
		# re-checks against the same gate company
		on_company, _ = _two_companies_with_switches()
		profile = _profile_for_company(on_company)
		result = generate_codes_for(DISCOUNT_DOCTYPE, 1)
		code_name = frappe.db.get_value(DISCOUNT_DOCTYPE, {"code": result["codes"][0]}, "name")
		frappe.db.set_value(
			DISCOUNT_DOCTYPE, code_name, "company_scope", "Selected Outlets", update_modified=False
		)
		child = frappe.get_doc(
			{
				"doctype": CODE_COMPANY_DOCTYPE,
				"parenttype": DISCOUNT_DOCTYPE,
				"parent": code_name,
				"parentfield": "companies",
				"company": on_company,
			}
		).insert(ignore_permissions=True)
		try:
			doc = self._discount_doc(company=on_company)
			doc.pos_profile = profile
			doc.discount_confirmation_code = result["codes"][0]
			doc.name = "ACC-SINV-GATECO"
			validate_invoice_discounts(doc, "validate")  # must not raise

			record_code_usage_on_submit(doc, "on_submit")  # must not raise
			self.assertEqual(
				frappe.db.get_value(DISCOUNT_DOCTYPE, code_name, "used_count"), 1
			)
		finally:
			frappe.delete_doc(CODE_COMPANY_DOCTYPE, child.name, force=1, ignore_permissions=True)
			frappe.db.set_value(
				DISCOUNT_DOCTYPE, code_name, "company_scope", "All Outlets", update_modified=False
			)
			frappe.db.set_value(DISCOUNT_DOCTYPE, code_name, "used_count", 0, update_modified=False)
			frappe.db.rollback()

	def test_company_profile_mismatch_rejected(self):
		# doc.company and the profile's company disagree: rejected outright,
		# even with a code that would be valid for the profile's outlet
		on_company, off_company = _two_companies_with_switches()
		profile = _profile_for_company(on_company)
		result = generate_codes_for(DISCOUNT_DOCTYPE, 1)
		try:
			doc = self._discount_doc(company=off_company)
			doc.pos_profile = profile
			doc.discount_confirmation_code = result["codes"][0]
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_invoice_discounts(doc, "validate")
			self.assertIn("does not match", str(ctx.exception))

			ret = self._return_doc("")
			ret.company = off_company
			ret.pos_profile = profile
			with self.assertRaises(frappe.ValidationError) as ctx:
				validate_invoice_discounts(ret, "validate")
			self.assertIn("does not match", str(ctx.exception))
		finally:
			frappe.db.rollback()

	def test_doc_without_profile_uses_doc_company(self):
		off_company, _ = _two_companies_with_switches()
		frappe.db.set_value("Company", off_company, "pos_require_discount_code", 0)
		try:
			doc = self._discount_doc(company=off_company)  # no pos_profile
			validate_invoice_discounts(doc, "validate")  # must not raise
		finally:
			frappe.db.set_value("Company", off_company, "pos_require_discount_code", 1)

	def test_profile_with_unknown_company_falls_back_to_doc_company(self):
		frappe.db.set_value("Company", COMPANY, "pos_require_discount_code", 0)
		try:
			doc = self._discount_doc()  # gate off on the doc's company
			doc.pos_profile = "No Such Profile"
			# unknown profile lookup returns nothing -> doc.company decides -> open
			validate_invoice_discounts(doc, "validate")  # must not raise

			doc2 = self._discount_doc(company=None)
			doc2.pos_profile = "No Such Profile"
			with self.assertRaises(frappe.ValidationError):
				validate_invoice_discounts(doc2, "validate")  # no company anywhere -> fail closed
		finally:
			frappe.db.set_value("Company", COMPANY, "pos_require_discount_code", 1)

	def test_gate_company_helper_prefers_profile(self):
		off_company, on_company = _two_companies_with_switches()
		profile = _profile_for_company(on_company)
		self.assertEqual(_gate_company(frappe._dict(company=on_company, pos_profile=profile)), on_company)
		with self.assertRaises(frappe.ValidationError) as ctx:
			_gate_company(frappe._dict(company=off_company, pos_profile=profile))
		self.assertIn("does not match", str(ctx.exception))
		self.assertEqual(_gate_company(frappe._dict(company=off_company)), off_company)
		self.assertIsNone(_gate_company(frappe._dict(company=None)))
		self.assertIsNone(_gate_company(frappe._dict(company=None, pos_profile="No Such Profile")))


class TestCompanyCodeSwitchGuard(unittest.TestCase):
	"""Only a System Manager may change either Company switch."""

	@classmethod
	def setUpClass(cls):
		cls.company = frappe.db.get_value("Company", {}, "name", order_by="creation asc")
		cls.non_manager = "code.gate.nonmanager@example.com"
		if not frappe.db.exists("User", cls.non_manager):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": cls.non_manager,
					"first_name": "Gate",
					"send_welcome_email": 0,
					"roles": [{"role": "Accounts Manager"}],
				}
			).insert(ignore_permissions=True)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.set_value(
			"Company",
			self.company,
			{"pos_require_discount_code": 1, "pos_require_refund_code": 1},
		)
		frappe.db.rollback()

	def _save_company(self):
		doc = frappe.get_doc("Company", self.company)
		doc.pos_require_refund_code = 0
		doc.save(ignore_permissions=True)

	def test_non_system_manager_change_blocked(self):
		frappe.set_user(self.non_manager)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._save_company()
		self.assertIn("System Manager", str(ctx.exception))

	def test_non_system_manager_unchanged_value_allowed(self):
		# saving the company without touching either switch is not gated
		frappe.set_user(self.non_manager)
		doc = frappe.get_doc("Company", self.company)
		doc.company_name = doc.company_name  # no-op change elsewhere
		doc.save(ignore_permissions=True)  # must not raise

	def test_non_system_manager_new_company_default_allowed(self):
		# a NEW Company reads as changed everywhere; a non-manager may still
		# create one as long as both switches sit at the default
		frappe.set_user(self.non_manager)
		doc = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "_Test Guard Default Co",
				"abbr": "TGD",
				"default_currency": "IDR",
				"country": "Indonesia",
			}
		)
		doc.pos_require_discount_code = 1
		doc.pos_require_refund_code = 1
		try:
			doc.insert(ignore_permissions=True)  # must not raise
		finally:
			if frappe.db.exists("Company", doc.name):
				frappe.delete_doc("Company", doc.name, force=1, ignore_permissions=True)
		frappe.set_user("Administrator")

	def test_non_system_manager_new_company_non_default_blocked(self):
		# creating a Company with a switch already off is a flip — blocked
		frappe.set_user(self.non_manager)
		doc = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "_Test Guard Nondef Co",
				"abbr": "TGN",
				"default_currency": "IDR",
				"country": "Indonesia",
			}
		)
		doc.pos_require_refund_code = 0
		with self.assertRaises(frappe.ValidationError):
			doc.insert(ignore_permissions=True)
		frappe.set_user("Administrator")

	def test_system_manager_change_allowed(self):
		frappe.set_user("Administrator")
		self._save_company()  # must not raise
		self.assertEqual(
			frappe.db.get_value("Company", self.company, "pos_require_refund_code"), 0
		)


class TestSeedCompanyCodeSwitchesPatch(unittest.TestCase):
	"""The v2_18_0 patch maps the retired POS Settings toggle onto the company.

	No Company is created. The company under test is a profile-less existing
	one; its "profile" is a raw `tabPOS Settings` row (no doctype insert, so
	its pos_profile link target never has to exist). Every mutation — that
	row, the global `tabSingles` row, and BOTH switches of EVERY company the
	patch loops over — is snapshotted in setUp and restored in tearDown, which
	runs even when an assertion fails."""

	# a profile-less company: the patch aggregates zero profile toggles for
	# everyone else, so only this company's switch tracks what we plant
	SETTINGS_ROW = "_Test PN Seed Raw Row"
	SENTINEL_PROFILE = "_nonexistent_pn_seed_profile"

	@classmethod
	def setUpClass(cls):
		for name in frappe.get_all("Company", pluck="name"):
			if not frappe.db.get_value("POS Profile", {"company": name}, "name"):
				cls.patch_company = name
				break
		else:
			raise unittest.SkipTest("no profile-less company to test the seed patch against")
		cls.old_column = "require_refund_code"  # retired but still in the DB

	def setUp(self):
		# snapshot everything the patch (and the plants) touch
		self._switch_baseline = frappe.get_all(
			"Company",
			fields=["name", "pos_require_discount_code", "pos_require_refund_code"],
		)
		single = frappe.db.sql(
			"select `value` from `tabSingles` where doctype = %s and field = %s",
			("POS Next Global Settings", self.old_column),
		)
		self._single_baseline = single[0][0] if single else None
		self._plant_row_existed = bool(
			frappe.db.get_value("POS Settings", {"pos_profile": self.SENTINEL_PROFILE}, "name")
		)
		# defensive: drop anything a previously aborted test left behind
		self._remove_planted_row()

	def _remove_planted_row(self):
		# the retired column is NOT NULL — clean via delete, never via NULL
		frappe.db.sql(
			"delete from `tabPOS Settings` where pos_profile = %s", (self.SENTINEL_PROFILE,)
		)
		frappe.db.sql(
			"delete from `tabPOS Settings` where pos_profile = %s",
			(self.SENTINEL_PROFILE + " 2",),
		)
		# sentinel POS Profile rows planted by an aborted run of these tests
		frappe.db.sql(
			"delete from `tabPOS Profile` where name in (%s, %s)",
			(self.SENTINEL_PROFILE, self.SENTINEL_PROFILE + " 2"),
		)

	def _restore_single(self):
		if self._single_baseline is None:
			frappe.db.sql(
				"delete from `tabSingles` where doctype = %s and field = %s",
				("POS Next Global Settings", self.old_column),
			)
		else:
			frappe.db.sql(
				"delete from `tabSingles` where doctype = %s and field = %s",
				("POS Next Global Settings", self.old_column),
			)
			frappe.db.sql(
				"insert into `tabSingles` (doctype, field, value) values (%s, %s, %s)",
				("POS Next Global Settings", self.old_column, self._single_baseline),
			)

	def tearDown(self):
		try:
			# restore every company's switches exactly as the patch loop wrote them
			for row in self._switch_baseline:
				frappe.db.set_value(
					"Company",
					row.name,
					{
						"pos_require_discount_code": row.pos_require_discount_code,
						"pos_require_refund_code": row.pos_require_refund_code,
					},
				)
			self._restore_single()
			self._remove_planted_row()
			# commit: the patch's set_value writes must not leak into the next
			# module if a test fails mid-transaction; the snapshot above makes
			# the committed state identical to the pre-run one.
			frappe.db.commit()
		finally:
			frappe.db.rollback()

	def _write_old_toggle(self, value):
		"""Store the retired toggle the way the old setting did — a raw row
		(the doctype insert would demand a real POS Profile)."""
		frappe.db.sql(
			"insert into `tabPOS Settings` (name, pos_profile, enabled, `require_refund_code`) "
			"values (%s, %s, 1, %s)",
			(self.SETTINGS_ROW, self.SENTINEL_PROFILE, value),
		)

	def _run_patch(self):
		from pos_next.patches.v2_18_0.seed_company_code_switches import execute

		execute()

	def test_all_profiles_off_seeds_switch_off(self):
		# the "profile" is a raw settings row; the patch reads it per profile
		# name, so plant it under the real profile-less company's ONLY profile
		# path: give the company a throwaway raw POS Profile row + settings row
		frappe.db.sql(
			"insert into `tabPOS Profile` (name, company, warehouse, disabled) "
			"values (%s, %s, '', 1)",
			(self.SENTINEL_PROFILE, self.patch_company),
		)
		self._write_old_toggle(0)
		try:
			self._run_patch()
			self.assertEqual(
				frappe.db.get_value("Company", self.patch_company, "pos_require_refund_code"), 0
			)
		finally:
			frappe.db.sql("delete from `tabPOS Profile` where name = %s", (self.SENTINEL_PROFILE,))

	def test_mixed_profiles_seed_switch_on(self):
		# a second profile whose toggle is 1 (the column default) keeps the
		# switch on despite this profile's 0
		profile2 = self.SENTINEL_PROFILE + " 2"
		frappe.db.sql(
			"insert into `tabPOS Profile` (name, company, warehouse, disabled) values (%s, %s, '', 1)",
			(profile2, self.patch_company),
		)
		frappe.db.sql(
			"insert into `tabPOS Profile` (name, company, warehouse, disabled) values (%s, %s, '', 1)",
			(self.SENTINEL_PROFILE, self.patch_company),
		)
		self._write_old_toggle(0)
		frappe.db.sql(
			"insert into `tabPOS Settings` (name, pos_profile, enabled, `require_refund_code`) "
			"values (%s, %s, 1, 1)",
			(self.SETTINGS_ROW + " 2", profile2),
		)
		try:
			self._run_patch()
			self.assertEqual(
				frappe.db.get_value("Company", self.patch_company, "pos_require_refund_code"), 1
			)
		finally:
			frappe.db.sql("delete from `tabPOS Settings` where pos_profile = %s", (profile2,))
			frappe.db.sql(
				"delete from `tabPOS Profile` where name in (%s, %s)",
				(self.SENTINEL_PROFILE, profile2),
			)

	def test_unset_toggle_fails_closed(self):
		# a global single whose value is '' must read as unset: cint('') would
		# call it 0 and silently switch the gate off. (A tinyint settings
		# column coerces '' to 0 on write, so '' is only reachable in the
		# varchar tabSingles.) A profile with no settings row falls to the
		# single tier.
		frappe.db.sql(
			"insert into `tabPOS Profile` (name, company, warehouse, disabled) "
			"values (%s, %s, '', 1)",
			(self.SENTINEL_PROFILE, self.patch_company),
		)
		frappe.db.sql(
			"insert into `tabSingles` (doctype, field, value) values (%s, %s, '')",
			("POS Next Global Settings", self.old_column),
		)
		try:
			self._run_patch()
			self.assertEqual(
				frappe.db.get_value("Company", self.patch_company, "pos_require_refund_code"), 1
			)
		finally:
			frappe.db.sql("delete from `tabPOS Profile` where name = %s", (self.SENTINEL_PROFILE,))

	def test_no_settings_row_falls_back_to_global(self):
		# a profile without its own settings row: the raw tabSingles read
		# answers 0
		frappe.db.sql(
			"insert into `tabPOS Profile` (name, company, warehouse, disabled) "
			"values (%s, %s, '', 1)",
			(self.SENTINEL_PROFILE, self.patch_company),
		)
		frappe.db.sql(
			"insert into `tabSingles` (doctype, field, value) values (%s, %s, '0')",
			("POS Next Global Settings", self.old_column),
		)
		try:
			self._run_patch()
			self.assertEqual(
				frappe.db.get_value("Company", self.patch_company, "pos_require_refund_code"), 0
			)
		finally:
			frappe.db.sql("delete from `tabPOS Profile` where name = %s", (self.SENTINEL_PROFILE,))

	def test_nothing_set_seeds_switch_on(self):
		self._run_patch()
		self.assertEqual(
			frappe.db.get_value("Company", self.patch_company, "pos_require_refund_code"), 1
		)
		self.assertEqual(
			frappe.db.get_value("Company", self.patch_company, "pos_require_discount_code"), 1
		)

	def test_pure_resolver_mapping(self):
		# the in-memory mapping, pinned separately from the DB paths above
		self.assertEqual(_resolve_refund_switch([1, 0]), 1)
		self.assertEqual(_resolve_refund_switch([0, 0]), 0)
		self.assertEqual(_resolve_refund_switch([]), 1)
		self.assertEqual(_resolve_refund_switch([None, 0]), 1)
		self.assertEqual(_resolve_refund_switch([""]), 1)
		self.assertEqual(_resolve_refund_switch(["", 0]), 1)
		self.assertEqual(_resolve_refund_switch(["0"]), 0)
		self.assertEqual(_resolve_refund_switch(["1", "0"]), 1)


if __name__ == "__main__":
	unittest.main()
