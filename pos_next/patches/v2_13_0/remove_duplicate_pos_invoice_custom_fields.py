import frappe

# pos_invoice is shipped in the doctype JSONs of these POS Next-owned
# doctypes; the install.py CUSTOM_FIELDS copies were a legacy way to add
# the field and now only shadow the DocField flags — most visibly the
# POS Transactions grid in POS Closing Shift, whose POS Invoice column
# stayed hidden (in_list_view 0) while Sales Invoice sat empty.
_DUPES = ("Sales Invoice Reference", "Offline Invoice Sync")


def execute():
	for dt in _DUPES:
		for name in frappe.get_all(
			"Custom Field", filters={"dt": dt, "fieldname": "pos_invoice"}, pluck="name"
		):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True)
