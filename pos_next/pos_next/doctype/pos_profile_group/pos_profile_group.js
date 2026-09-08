// Shift Group form: block duplicate member rows.
// Server validates the same rule on save.
frappe.ui.form.on("POS Profile Group Member", {
	pos_profile(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.pos_profile) return;
		const duplicate = (frm.doc.profiles || []).some(
			(p) => p.pos_profile === row.pos_profile && p.name !== cdn
		);
		if (duplicate) {
			frappe.show_alert({
				message: __("POS Profile {0} is already in this Shift Group", [row.pos_profile]),
				indicator: "orange",
			});
			frappe.model.set_value(cdt, cdn, "pos_profile", "");
		}
	},
});
