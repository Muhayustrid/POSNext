# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Seed the per-Company code gate switches from the retired POS Settings toggle.

`require_refund_code` (POS Settings per profile, else the global single) is no
longer read by the gate — its replacement lives on the Company as
`pos_require_refund_code`. The DB column survives the doctype JSON removal,
so the old value is read here straight from the DB, not via the meta.

Mapping (fail closed): a company's refund switch is 0 only when it has at
least one POS Profile and EVERY enabled profile of it resolves the old toggle
to 0; otherwise 1. `pos_require_discount_code` is always 1 (the discount gate
was hardcoded on before the switches existed).
"""

import frappe

GLOBAL_DOCTYPE = "POS Next Global Settings"


def _old_toggle_for_profile(pos_profile):
	"""Effective value of the retired `require_refund_code` for one profile.

	Reads raw rows/singles (the field is gone from both doctype JSONs, so
	resolver/meta lookups no longer see it). None/'' (unset) means required —
	an empty string must NOT read as 0 (cint("")).
	"""
	row = frappe.db.get_value(
		"POS Settings",
		{"pos_profile": pos_profile, "enabled": 1},
		["name", "require_refund_code"],
		as_dict=True,
	)
	if row and row.get("require_refund_code") not in (None, ""):
		return row.require_refund_code
	# global single tier (raw SQL — tabSingles is not a DocType), else the old
	# meta default of 1
	value = frappe.db.sql(
		"select `value` from `tabSingles` where doctype = %s and field = %s",
		(GLOBAL_DOCTYPE, "require_refund_code"),
	)
	# '' in tabSingles reads as NULL through frappe.db.sql — treat it as unset
	return value[0][0] if value and value[0][0] not in (None, "") else None


def _resolve_refund_switch(toggles) -> int:
	"""Company switch value from the profiles' retired toggle values.

	0 only when at least one profile exists and every toggle explicitly
	resolves to 0; unset (None/''/missing) and mixed values keep the gate on
	(fail closed).
	"""
	toggles = list(toggles)
	if toggles and all(
		toggle is not None and str(toggle) != "" and frappe.utils.cint(toggle) == 0
		for toggle in toggles
	):
		return 0
	return 1


def execute():
	# after_migrate (create_custom_fields) runs AFTER the patches, so the
	# Company columns may not exist yet on this migrate — upsert them here.
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	from pos_next.install import CUSTOM_FIELDS

	create_custom_fields({"Company": CUSTOM_FIELDS["Company"]}, ignore_validate=True, update=True)

	for company in frappe.get_all("Company", pluck="name"):
		profiles = frappe.get_all("POS Profile", filters={"company": company}, pluck="name")
		frappe.db.set_value(
			"Company",
			company,
			{
				"pos_require_discount_code": 1,
				"pos_require_refund_code": _resolve_refund_switch(
					_old_toggle_for_profile(profile) for profile in profiles
				),
			},
		)
