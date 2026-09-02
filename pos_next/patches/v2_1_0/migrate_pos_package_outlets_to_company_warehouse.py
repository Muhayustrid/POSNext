import frappe


def execute():
	if not frappe.db.exists("DocType", "POS Package"):
		return
	if not frappe.db.exists("DocType", "POS Package Outlet"):
		frappe.reload_doctype("POS Package Outlet", force=True)
		return

	frappe.reload_doctype("POS Package Outlet", force=True)
	needs_consolidation = _count_legacy_outlets()
	if needs_consolidation:
		_consolidate_legacy_rows()

	_collapse_duplicate_outlets()


def _count_legacy_outlets():
	count = frappe.db.sql(
		"SELECT COUNT(*) FROM `tabPOS Package Outlet` WHERE (company IS NULL OR company = '') AND (warehouse IS NULL OR warehouse = '') AND pos_profile IS NOT NULL AND pos_profile != ''"
	)
	return count[0][0] if count else 0


def _consolidate_legacy_rows():
	rows = frappe.db.get_all(
		"POS Package Outlet",
		filters={"company": ["in", [None, ""]], "warehouse": ["in", [None, ""]]},
		fields=["name", "parent", "pos_profile", "enabled"],
	)

	by_parent = {}
	for r in rows:
		by_parent.setdefault(r["parent"], []).append(r)

	for parent, group in by_parent.items():
		profiles = [g for g in group if g["pos_profile"]]
		if not profiles:
			continue
		by_key = {}
		for g in profiles:
			prof = frappe.db.get_value("POS Profile", g["pos_profile"], ["company", "warehouse"], as_dict=True) or {}
			key = (prof.get("company") or "", prof.get("warehouse") or "")
			by_key.setdefault(key, []).append(g)

		for (company, warehouse), sharing in by_key.items():
			if not company:
				continue
			enabled = 1 if any(cint(g["enabled"]) for g in sharing) else 0
			frappe.db.set_value(
				"POS Package Outlet",
				sharing[0]["name"],
				{"company": company, "warehouse": warehouse, "enabled": enabled},
			)
			for dup in sharing[1:]:
				frappe.delete_doc("POS Package Outlet", dup["name"], ignore_permissions=True)

		remaining = frappe.get_all("POS Package Outlet", filters={"parent": parent}, pluck="name")
		for name in remaining:
			row = frappe.db.get_value("POS Package Outlet", name, ["company", "warehouse", "pos_profile"], as_dict=True)
			if row and row["pos_profile"] and not row["company"]:
				frappe.delete_doc("POS Package Outlet", name, ignore_permissions=True)


def _collapse_duplicate_outlets():
	dupes = frappe.db.sql(
		"""
		SELECT parent, company, warehouse, GROUP_CONCAT(name ORDER BY creation) AS names, COUNT(*) c
		FROM `tabPOS Package Outlet`
		WHERE company IS NOT NULL AND company != '' AND warehouse IS NOT NULL AND warehouse != ''
		GROUP BY parent, company, warehouse HAVING c > 1
		"""
	)
	for parent, company, warehouse, names_csv, _ in dupes:
		names = [n.strip() for n in names_csv.split(",") if n.strip()]
		keep = names[0]
		enabled = 0
		for n in names:
			if frappe.db.get_value("POS Package Outlet", n, "enabled"):
				enabled = 1
				break
		frappe.db.set_value("POS Package Outlet", keep, {"enabled": enabled})
		for n in names[1:]:
			if frappe.db.exists("POS Package Outlet", n):
				frappe.delete_doc("POS Package Outlet", n, ignore_permissions=True)


def cint(val):
	try:
		return int(val) if val is not None else 0
	except Exception:
		return 0
