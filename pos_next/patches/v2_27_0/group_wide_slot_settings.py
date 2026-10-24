import frappe
from frappe.utils import cint


def execute():
	"""Move per-slot warning/enforce onto the Shift Group (now group-wide).

	The slot columns stay in the table after the fields left the DocType, so
	read them raw. The strictest value wins and is never lowered: re-runs are no-ops.
	"""
	if not frappe.db.has_column("POS Shift Slot", "warning_minutes"):
		return

	rows = frappe.db.sql(
		"""SELECT parent, MAX(IFNULL(warning_minutes, 0)) AS warning,
			MAX(IFNULL(enforce_closing, 0)) AS enforce
		FROM `tabPOS Shift Slot`
		WHERE parenttype = 'POS Profile Group'
		GROUP BY parent""",
		as_dict=True,
	)
	for row in rows:
		current = frappe.db.get_value(
			"POS Profile Group",
			row.parent,
			["pos_schedule_warning_minutes", "pos_schedule_enforce_closing"],
			as_dict=True,
		)
		if not current:
			continue
		frappe.db.set_value(
			"POS Profile Group",
			row.parent,
			{
				"pos_schedule_warning_minutes": max(cint(current.pos_schedule_warning_minutes), cint(row.warning)),
				"pos_schedule_enforce_closing": max(cint(current.pos_schedule_enforce_closing), cint(row.enforce)),
			},
			update_modified=False,
		)
