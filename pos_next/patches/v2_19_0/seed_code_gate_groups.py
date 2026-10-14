# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Seed the four Code Gate Groups (discount-only, refund-only, all-off, all-on).

Groups ship WITHOUT members — HQ assigns each Company from the group form.
Idempotent: an existing group (same name) is left untouched, so re-running
never overwrites HQ's membership or switch edits.
"""

import frappe

# the 2x2 matrix: (require discount code, require refund code)
GROUPS = [
	{"group_name": "Group A", "pos_require_discount_code": 1, "pos_require_refund_code": 0},
	{"group_name": "Group B", "pos_require_discount_code": 0, "pos_require_refund_code": 1},
	{"group_name": "Group C", "pos_require_discount_code": 0, "pos_require_refund_code": 0},
	{"group_name": "Group D", "pos_require_discount_code": 1, "pos_require_refund_code": 1},
]


def execute():
	# after_migrate (create_custom_fields) runs AFTER the patches, so the
	# mirror column may not exist yet on this migrate — upsert it here
	# (same as v2_18_0.seed_company_code_switches). Without it the first
	# group insert crashes in sync_members' linked-member lookup.
	from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

	from pos_next.install import CUSTOM_FIELDS

	create_custom_fields({"Company": CUSTOM_FIELDS["Company"]}, ignore_validate=True, update=True)

	for group in GROUPS:
		if frappe.db.exists("POS Code Gate Group", group["group_name"]):
			continue
		frappe.get_doc({"doctype": "POS Code Gate Group", **group}).insert()
