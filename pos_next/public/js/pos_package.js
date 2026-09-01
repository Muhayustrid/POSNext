// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Package", {
	refresh(frm) {
		pn_update_group_options(frm);
		pn_toggle_lifetime_fields(frm);
		pn_toggle_outlet_filter(frm);
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
	is_cross_company(frm) {
		pn_toggle_outlet_filter(frm);
	},
});

function pn_toggle_lifetime_fields(frm) {
	const lifetime = cint(frm.doc.is_lifetime);
	frm.set_df_property("valid_from", "read_only", lifetime);
	frm.set_df_property("valid_upto", "read_only", lifetime);
	if (lifetime) {
		frm.set_value("valid_from", "");
		frm.set_value("valid_upto", "");
	}
	frm.refresh_field("valid_from");
	frm.refresh_field("valid_upto");
}

function pn_toggle_outlet_filter(frm) {
	const grid = frm.fields_dict.outlets?.grid;
	if (!grid) return;

	const df = grid.get_field("pos_profile");

	if (cint(frm.doc.is_cross_company)) {
		df.get_query = null;
		frm.set_query("pos_profile", "outlets", () => ({}));
	} else {
		frm.set_query("pos_profile", "outlets", () => ({
			filters: { company: frm.doc.company },
		}));
	}
	frm.refresh_field("outlets");
}

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
