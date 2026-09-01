// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Package", {
	refresh(frm) {
		pn_update_group_options(frm);
	},
	groups_add(frm) {
		pn_update_group_options(frm);
	},
	groups_remove(frm) {
		pn_update_group_options(frm);
	},
});

frappe.ui.form.on("POS Package Group", {
	group_key(frm) {
		pn_update_group_options(frm);
	},
});

function pn_update_group_options(frm) {
	if (!frm.fields_dict.options) return;

	const group_keys = (frm.doc.groups || [])
		.map((row) => row.group_key)
		.filter(Boolean);

	frm.fields_dict.options.grid.update_docfield_property(
		"group_key",
		"options",
		group_keys
	);
	frm.refresh_field("options");
}
