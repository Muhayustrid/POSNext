frappe.ui.form.on("Price Group Outlet", {
	company(frm, cdt, cdn) {
		// Clear dependent fields when company changes
		frappe.model.set_value(cdt, cdn, "pos_profile", "")
		frappe.model.set_value(cdt, cdn, "status", "")
	},
})
