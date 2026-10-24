# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Migration patch to consolidate multiple shift profiles into one per outlet (v2_26_0).

Spec decision: D14.
Plan: Task 7.
"""

import datetime
from collections import defaultdict

import frappe


def _format_time(val):
	if val is None:
		return None
	if isinstance(val, str):
		val = val.strip()
		if len(val) == 7:  # "5:00:00"
			return "0" + val
		if len(val) == 5:  # "05:00"
			return val + ":00"
		if len(val) == 4:  # "5:00"
			return "0" + val + ":00"
		return val
	if isinstance(val, datetime.timedelta):
		total_seconds = int(val.total_seconds()) % 86400
		hours = total_seconds // 3600
		minutes = (total_seconds % 3600) // 60
		seconds = total_seconds % 60
		return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
	if isinstance(val, datetime.time):
		return val.strftime("%H:%M:%S")
	return str(val)


def _resolve_profile_schedule(profile_name: str, fallback_slot: bool = False) -> dict | None:
	prof = frappe.db.get_value(
		"POS Profile",
		profile_name,
		[
			"name",
			"pos_profile_group",
			"pos_schedule_enabled",
			"pos_schedule_start",
			"pos_schedule_end",
			"pos_schedule_warning_minutes",
			"pos_schedule_enforce_closing",
		],
		as_dict=True,
	)
	if not prof:
		return None

	start = prof.pos_schedule_start
	end = prof.pos_schedule_end
	warning = prof.pos_schedule_warning_minutes or 0
	enforce = prof.pos_schedule_enforce_closing if prof.pos_schedule_enforce_closing is not None else 1
	group_name = prof.pos_profile_group

	# 00:00:00 comes back as timedelta(0), which is falsy: compare with None.
	if (start is None or end is None) and group_name:
		group_data = frappe.db.get_value(
			"POS Profile Group",
			group_name,
			[
				"pos_schedule_start",
				"pos_schedule_end",
				"pos_schedule_warning_minutes",
				"pos_schedule_enforce_closing",
			],
			as_dict=True,
		)
		if group_data:
			start = group_data.pos_schedule_start if start is None else start
			end = group_data.pos_schedule_end if end is None else end
			if not prof.pos_schedule_warning_minutes and group_data.pos_schedule_warning_minutes:
				warning = group_data.pos_schedule_warning_minutes
			if group_data.pos_schedule_enforce_closing is not None:
				enforce = group_data.pos_schedule_enforce_closing

	if start is None or end is None:
		if not fallback_slot:
			return None
		start = "00:00:00"
		end = "23:59:59"

	start_str = _format_time(start)
	end_str = _format_time(end)
	if start_str == end_str:
		return None

	return {
		"shift_name": group_name or prof.name,
		"start_time": start_str,
		"end_time": end_str,
		"warning_minutes": int(warning),
		"enforce_closing": 1 if enforce else 0,
	}


def _get_pos_profile_user_parentfield() -> str:
	meta = frappe.get_meta("POS Profile")
	for field in meta.fields:
		if field.fieldtype == "Table" and field.options == "POS Profile User":
			return field.fieldname
	return "applicable_for_users"


def merge_company(company: str) -> dict:
	"""Consolidate multiple shift profiles of `company` into one Shift Group."""
	group_name = f"{company} Shifts"
	if frappe.db.exists("POS Profile Group", group_name):
		return {
			"status": "noop",
			"kept": None,
			"disabled": [],
			"group": None,
		}

	# 1. Skip company if any open shift exists
	if frappe.db.exists(
		"POS Opening Shift",
		{"company": company, "docstatus": 1, "status": "Open"},
	):
		frappe.log_error(
			title=f"Shift Group migration skipped for {company}",
			message=f"Company {company} has open POS Opening Shift records.",
		)
		return {
			"status": "skipped_open_shift",
			"kept": None,
			"disabled": [],
			"group": None,
		}

	# 2. Get enabled profiles for this company
	profiles = frappe.get_all(
		"POS Profile",
		filters={"company": company, "disabled": 0},
		fields=["name", "warehouse", "pos_profile_group", "pos_schedule_enabled"],
		order_by="name asc",
	)
	if not profiles:
		return {
			"status": "noop",
			"kept": None,
			"disabled": [],
			"group": None,
		}

	# Group by warehouse
	warehouse_groups = defaultdict(list)
	for p in profiles:
		warehouse_groups[p.warehouse].append(p)

	mergeable = [p_list for p_list in warehouse_groups.values() if len(p_list) > 1]

	if mergeable:
		# Sort mergeable candidate lists by length descending
		mergeable.sort(key=len, reverse=True)
		candidates = mergeable[0]

		# Determine kept profile: the one with the most opening shifts
		def count_opening_shifts(profile_name):
			return frappe.db.count("POS Opening Shift", {"pos_profile": profile_name})

		candidates.sort(key=lambda p: (-count_opening_shifts(p.name), p.name))
		kept = candidates[0]
		disabled = candidates[1:]

		# Build slots from all candidates
		slots = []
		seen_slot_names = set()
		for p in candidates:
			slot_info = _resolve_profile_schedule(p.name, fallback_slot=True)
			if not slot_info:
				continue
			name = slot_info["shift_name"]
			cand_name = name
			ctr = 1
			while cand_name.lower() in seen_slot_names:
				cand_name = f"{name} {ctr}"
				ctr += 1
			seen_slot_names.add(cand_name.lower())
			slot_info["shift_name"] = cand_name
			slots.append(slot_info)

		# Unlink kept profile from old group
		frappe.db.set_value("POS Profile", kept.name, "pos_profile_group", "", update_modified=False)
		frappe.db.delete("POS Profile Group Member", {"pos_profile": kept.name})

		# Disable other profiles and unlink them from groups
		for p in disabled:
			frappe.db.set_value(
				"POS Profile", p.name, {"disabled": 1, "pos_profile_group": ""}, update_modified=False
			)
			frappe.db.delete("POS Profile Group Member", {"pos_profile": p.name})

		# Move POS Profile User rows to kept profile without duplicates
		parentfield = _get_pos_profile_user_parentfield()
		existing_users = set(frappe.get_all("POS Profile User", filters={"parent": kept.name}, pluck="user"))
		for p in disabled:
			users_to_add = frappe.get_all("POS Profile User", filters={"parent": p.name}, pluck="user")
			for u in users_to_add:
				if u not in existing_users:
					frappe.get_doc(
						{
							"doctype": "POS Profile User",
							"parent": kept.name,
							"parenttype": "POS Profile",
							"parentfield": parentfield,
							"user": u,
						}
					).insert(ignore_permissions=True)
					existing_users.add(u)

		# Create new POS Profile Group
		group_doc = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": group_name,
				"pos_schedule_enabled": 1,
				"shifts": [
					{
						"doctype": "POS Shift Slot",
						"shift_name": s["shift_name"],
						"start_time": s["start_time"],
						"end_time": s["end_time"],
						"warning_minutes": s["warning_minutes"],
						"enforce_closing": s["enforce_closing"],
					}
					for s in slots
				],
				"profiles": [
					{
						"doctype": "POS Profile Group Member",
						"pos_profile": kept.name,
					}
				],
			}
		)
		group_doc.insert(ignore_permissions=True)

		frappe.db.set_value("POS Profile", kept.name, "pos_profile_group", group_name, update_modified=False)

		return {
			"status": "merged",
			"kept": kept.name,
			"disabled": [p.name for p in disabled],
			"group": group_name,
		}

	# If exactly 1 profile, check if it has a schedule
	if len(profiles) == 1:
		prof = profiles[0]
		# Only scheduled profiles get a single-slot group
		slot_info = _resolve_profile_schedule(prof.name, fallback_slot=False)
		if not slot_info:
			return {
				"status": "noop",
				"kept": None,
				"disabled": [],
				"group": None,
			}

		frappe.db.set_value("POS Profile", prof.name, "pos_profile_group", "", update_modified=False)
		frappe.db.delete("POS Profile Group Member", {"pos_profile": prof.name})

		group_doc = frappe.get_doc(
			{
				"doctype": "POS Profile Group",
				"group_name": group_name,
				"pos_schedule_enabled": 1,
				"shifts": [
					{
						"doctype": "POS Shift Slot",
						"shift_name": slot_info["shift_name"],
						"start_time": slot_info["start_time"],
						"end_time": slot_info["end_time"],
						"warning_minutes": slot_info["warning_minutes"],
						"enforce_closing": slot_info["enforce_closing"],
					}
				],
				"profiles": [
					{
						"doctype": "POS Profile Group Member",
						"pos_profile": prof.name,
					}
				],
			}
		)
		group_doc.insert(ignore_permissions=True)

		frappe.db.set_value("POS Profile", prof.name, "pos_profile_group", group_name, update_modified=False)

		return {
			"status": "single",
			"kept": prof.name,
			"disabled": [],
			"group": group_name,
		}

	# More than 1 profile, but different warehouses
	return {
		"status": "noop",
		"kept": None,
		"disabled": [],
		"group": None,
	}


def execute():
	for company in frappe.get_all("Company", pluck="name", order_by="name asc"):
		merge_company(company)
