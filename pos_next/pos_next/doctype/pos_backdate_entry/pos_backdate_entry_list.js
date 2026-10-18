// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.listview_settings["POS Backdate Entry"] = {
	get_indicator: function (doc) {
		if (doc.docstatus === 0) {
			return [__("Draft"), "grey", "docstatus,=,0"];
		} else if (doc.docstatus === 1) {
			return [__("Submitted"), "blue", "docstatus,=,1"];
		}
	},
};
