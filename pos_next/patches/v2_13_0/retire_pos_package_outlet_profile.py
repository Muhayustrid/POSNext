import frappe


def execute():
	"""Finish retiring the per-POS-Profile outlet model on POS Package.

	v2_1_0 consolidated the legacy outlet rows that existed then; this sweep
	clears any that appeared since (restored backups, manual writes) and
	rebinds the read-only ``pos_profile`` column to its new meaning: a
	listing of every POS Profile on the outlet's Company + Warehouse,
	mirroring Price Group Outlet.
	"""
	if not frappe.db.exists("DocType", "POS Package"):
		return

	frappe.reload_doctype("POS Package Outlet", force=True)

	_consolidate_legacy_rows()
	_collapse_duplicate_outlets()
	_rewrite_profile_listings()


def _consolidate_legacy_rows():
	"""Derive Company + Warehouse for rows that still carry only a profile.

	A row whose profile no longer resolves (deleted profile, empty company)
	is dropped — the package then falls back to the company-wide rule, the
	same recovery v2_1_0 applied.
	"""
	rows = frappe.get_all(
		"POS Package Outlet",
		filters={"company": ["in", [None, ""]], "warehouse": ["in", [None, ""]]},
		fields=["name", "parent", "pos_profile", "enabled"],
	)

	by_parent = {}
	for row in rows:
		by_parent.setdefault(row["parent"], []).append(row)

	for parent, group in by_parent.items():
		by_key = {}
		for row in group:
			if not row["pos_profile"]:
				continue
			profile = frappe.db.get_value(
				"POS Profile", row["pos_profile"], ["company", "warehouse"], as_dict=True
			)
			if not profile or not profile.get("company"):
				frappe.delete_doc("POS Package Outlet", row["name"], ignore_permissions=True)
				continue
			key = (profile["company"], profile.get("warehouse") or "")
			by_key.setdefault(key, []).append(row)

		for (company, warehouse), sharing in by_key.items():
			enabled = 1 if any(row["enabled"] for row in sharing) else 0
			frappe.db.set_value(
				"POS Package Outlet",
				sharing[0]["name"],
				{"company": company, "warehouse": warehouse, "enabled": enabled},
			)
			for dup in sharing[1:]:
				frappe.delete_doc("POS Package Outlet", dup["name"], ignore_permissions=True)


def _collapse_duplicate_outlets():
	# NULL and empty warehouse normalize to the same scope in eligibility, so
	# ifnull() collapses both under one key.
	dupes = frappe.db.sql(
		"""
		SELECT parent, company, ifnull(warehouse, '') AS warehouse_key,
			GROUP_CONCAT(name ORDER BY creation) AS names, COUNT(*) c
		FROM `tabPOS Package Outlet`
		WHERE company IS NOT NULL AND company != ''
		GROUP BY parent, company, ifnull(warehouse, '') HAVING c > 1
		"""
	)
	for parent, company, warehouse_key, names_csv, _ in dupes:
		names = [n.strip() for n in names_csv.split(",") if n.strip()]
		keep = names[0]
		enabled = 0
		for name in names:
			if frappe.db.get_value("POS Package Outlet", name, "enabled"):
				enabled = 1
				break
		frappe.db.set_value("POS Package Outlet", keep, {"enabled": enabled})
		for name in names[1:]:
			if frappe.db.exists("POS Package Outlet", name):
				frappe.delete_doc("POS Package Outlet", name, ignore_permissions=True)


def _rewrite_profile_listings():
	"""Repoint the display column: one row lists every matching profile."""
	profiles = frappe.get_all("POS Profile", fields=["name", "company", "warehouse"])
	by_pair = {}
	for profile in profiles:
		by_pair.setdefault((profile["company"], profile["warehouse"] or ""), []).append(profile["name"])

	for row in frappe.get_all(
		"POS Package Outlet",
		filters={"company": ["is", "set"]},
		fields=["name", "company", "warehouse"],
	):
		matching = sorted(by_pair.get((row["company"], row["warehouse"] or ""), []))
		if matching:
			values = {
				"pos_profile": ", ".join(matching),
				"status": "Available on all profiles for this warehouse",
			}
		else:
			values = {"pos_profile": None, "status": "No POS Profile matches this warehouse"}
		frappe.db.set_value("POS Package Outlet", row["name"], values, update_modified=False)
