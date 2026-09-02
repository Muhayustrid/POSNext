// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Package", {
	refresh(frm) {
		pn_update_group_options(frm);
		pn_toggle_lifetime_fields(frm);
		pn_filter_warehouse(frm);
	},
	groups_add(frm) {
		pn_update_group_options(frm);
	},
	groups_remove(frm) {
		pn_update_group_options(frm);
	},
	is_lifetime(frm) {
		pn_toggle_lifetime_fields(frm);
	},
});

function pn_toggle_lifetime_fields(frm) {
	const lifetime = cint(frm.doc.is_lifetime);
	frm.set_df_property("valid_from", "read_only", lifetime);
	frm.set_df_property("valid_upto", "read_only", lifetime);
	if (lifetime) {
		if (frm.doc.valid_from) frm.set_value("valid_from", "");
		if (frm.doc.valid_upto) frm.set_value("valid_upto", "");
	}
	frm.refresh_field("valid_from");
	frm.refresh_field("valid_upto");
}

function pn_filter_warehouse(frm) {
	frm.fields_dict.outlets.grid.get_field("warehouse").get_query = (doc, cdt, cdn) => {
		const row = locals[cdt][cdn];
		return {
			filters: {
				company: row.company || frm.doc.company || "",
				is_group: 0,
			},
		};
	};
}

frappe.ui.form.on("POS Package Outlet", {
	company(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "warehouse", "");
		frappe.model.set_value(cdt, cdn, "pos_profile", "");
		frappe.model.set_value(cdt, cdn, "status", "");
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
