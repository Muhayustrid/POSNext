import frappe

# Shift hours live only on Shift Group slots; a profile without a group runs
# unscheduled. Deleting a Custom Field keeps its column, so old values stay
# readable for the v2_26_0 merge patch.
FIELDS = (
	"pos_schedule_enabled",
	"pos_schedule_start",
	"pos_schedule_end",
	"pos_schedule_warning_minutes",
	"pos_schedule_enforce_closing",
)


def execute():
	for fieldname in FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": "POS Profile", "fieldname": fieldname})
		if name:
			frappe.delete_doc("Custom Field", name, force=True, ignore_permissions=True)
	frappe.clear_cache(doctype="POS Profile")
