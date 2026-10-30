# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Shift slots now link an HRMS Shift Type (slot hours follow it).

Creates one Shift Type per distinct slot name from the slot's own hours, with
both check-in buffers 0 (HRMS refuses a Shift Type whose duration plus
buffers reaches 24h). A full-day slot (e.g. 00:00-23:59:59) ends one minute
earlier for the same reason. A same-named Shift Type (or another slot) with
different hours stops the migration so HQ can resolve it by hand.
"""

import datetime

import frappe
from frappe import _

from pos_next.shift_schedule import _as_time_delta, _format_time_str

DAY = datetime.timedelta(days=1)


def _fit_day(start, end):
	"""Shorten a slot that HRMS would round to a 24h shift."""
	duration = (end - start) % DAY
	if duration.total_seconds() >= 1439.5 * 60:
		end = (start - datetime.timedelta(minutes=1)) % DAY
	return end


def execute():
	slots = frappe.db.sql(
		"""SELECT shift_name, start_time, end_time FROM `tabPOS Shift Slot`
		WHERE parenttype = 'POS Profile Group' AND IFNULL(shift_name, '') != ''""",
		as_dict=True,
	)
	wanted = {}
	for slot in slots:
		start, end = _as_time_delta(slot.start_time), _as_time_delta(slot.end_time)
		if start is None or end is None:
			continue
		hours = (_format_time_str(start), _format_time_str(_fit_day(start, end)))
		if wanted.setdefault(slot.shift_name, hours) != hours:
			frappe.throw(
				_(
					"Shift slot {0} has different hours in different Shift Groups. Make them equal first."
				).format(slot.shift_name)
			)

	# check every name before creating anything: a stop leaves no half migration
	for name, hours in wanted.items():
		existing = frappe.db.get_value("Shift Type", name, ["start_time", "end_time"])
		if existing and tuple(_format_time_str(v) for v in existing) != hours:
			frappe.throw(
				_(
					"Shift Type {0} already exists with other hours ({1}-{2}); the shift slot uses {3}-{4}."
				).format(name, *map(_format_time_str, existing), *hours)
			)

	for name, (start, end) in wanted.items():
		if not frappe.db.exists("Shift Type", name):
			frappe.get_doc(
				{
					"doctype": "Shift Type",
					"name": name,
					"start_time": start,
					"end_time": end,
					"begin_check_in_before_shift_start_time": 0,
					"allow_check_out_after_shift_end_time": 0,
				}
			).insert(ignore_permissions=True)
		frappe.db.sql(
			"""UPDATE `tabPOS Shift Slot` SET start_time = %s, end_time = %s
			WHERE parenttype = 'POS Profile Group' AND shift_name = %s""",
			(start, end, name),
		)
