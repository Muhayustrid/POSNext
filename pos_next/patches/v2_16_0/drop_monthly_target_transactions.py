import frappe


def execute():
	"""Remove the retired per-outlet transaction target column."""
	if frappe.db.has_column("POS Monthly Target", "target_transactions"):
		frappe.db.sql_ddl("ALTER TABLE `tabPOS Monthly Target` DROP COLUMN `target_transactions`")
