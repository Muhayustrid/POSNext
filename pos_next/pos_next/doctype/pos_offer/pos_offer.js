// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

// POS Offer 2.0 form: the Targets grid shows only the column matching the
// parent's Apply On. The child fields carry `depends_on: eval:parent.apply_on`
// (same idiom as ERPNext's Pricing Rule children) for the row editor, but the
// grid only re-evaluates column dependencies on changes *inside* the grid —
// so switching Apply On on the parent toggles the columns here explicitly via
// grid-local overrides (not shared-meta mutation, which refresh() wipes).

frappe.ui.form.on("POS Offer", {
	refresh: toggle_target_columns,
	apply_on: toggle_target_columns,
});

function toggle_target_columns(frm) {
	const grid = frm.fields_dict.targets?.grid;
	if (!grid?.set_column_disp_in_list_view) return;

	grid.set_column_disp_in_list_view("item_code", frm.doc.apply_on === "Item Code");
	grid.set_column_disp_in_list_view("item_group", frm.doc.apply_on === "Item Group");
	grid.set_column_disp_in_list_view("brand", frm.doc.apply_on === "Brand");
}
