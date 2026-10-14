// Code Gate Group form: block duplicate member rows.
// Server validates the same rule on save.
frappe.ui.form.on("POS Code Gate Group Member", {
	company(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.company) return;
		const duplicate = (frm.doc.companies || []).some(
			(c) => c.company === row.company && c.name !== cdn
		);
		if (duplicate) {
			frappe.show_alert({
				message: __("Company {0} is already in this Code Gate Group", [row.company]),
				indicator: "orange",
			});
			frappe.model.set_value(cdt, cdn, "company", "");
		}
	},
});
