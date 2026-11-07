// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Monitoring Template", {
	onload(frm) {
		// Fill only an empty scope; an explicit All/Selected is never rewritten.
		if (!frm.doc.company_scope) {
			frm.set_value("company_scope", "Selected");
		}
		for (const [field, table] of [["profile_scope", "pos_profiles"], ["shift_type_scope", "shift_types"]]) {
			if (!frm.doc[field]) {
				frm.set_value(field, (frm.doc[table] || []).length ? "Selected" : "All");
			}
		}
	},
});
