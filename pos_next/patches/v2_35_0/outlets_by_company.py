import frappe


def execute():
	"""Outlets are companies: Price Group and POS Package outlet rows lose their warehouse.

	Rows of one document that now name the same company collapse into the oldest; a package
	row stays enabled if any collapsed row was. The old warehouse column is left in the table.
	Package rows get the company-wide profile listing and status.
	"""
	for doctype in ("Price Group Outlet", "POS Package Outlet"):
		if not frappe.db.table_exists(doctype):
			continue
		has_enabled = frappe.db.has_column(doctype, "enabled")
		dupes = frappe.db.sql(
			f"""select parent, company, group_concat(name order by creation, idx) names
			from `tab{doctype}` where ifnull(company, '') != ''
			group by parent, company having count(*) > 1""",
			as_dict=True,
		)
		for d in dupes:
			keep, *drop = d.names.split(",")
			if has_enabled and frappe.db.exists(doctype, {"name": ["in", drop], "enabled": 1}):
				frappe.db.set_value(doctype, keep, "enabled", 1, update_modified=False)
			frappe.db.delete(doctype, {"name": ["in", drop]})

	if not frappe.db.table_exists("POS Package Outlet"):
		return
	by_company = {}
	for p in frappe.get_all("POS Profile", fields=["name", "company"], order_by="name asc"):
		by_company.setdefault(p.company, []).append(p.name)
	for row in frappe.get_all("POS Package Outlet", fields=["name", "company"]):
		profiles = by_company.get(row.company, [])
		frappe.db.set_value(
			"POS Package Outlet",
			row.name,
			{
				"pos_profile": ", ".join(profiles) or None,
				"status": "Linked" if profiles else "No POS Profile",
			},
			update_modified=False,
		)
