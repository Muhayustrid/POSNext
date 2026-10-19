// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.listview_settings["POS Monthly Target"] = {
	formatters: {
		month_start(value) {
			if (!value) return "";
			const m = moment(value);
			return m.isValid() ? m.format("MMMM YYYY") : value;
		},
	},
	onload(listview) {
		listview.page.add_inner_button(__("Outlet Targets"), () => {
			frappe.set_route("outlet-targets");
		});
	},
};
