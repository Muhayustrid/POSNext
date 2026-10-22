// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.listview_settings["POS Reject Request"] = {
	add_fields: ["workflow_state", "is_late", "over_limit"],
	get_indicator(doc) {
		const colors = {
			Draft: "grey",
			"Pending Approval": "orange",
			"Awaiting Destruction": "blue",
			"Destruction Submitted": "purple",
			Completed: "green",
			Cancelled: "red",
		};
		const state = doc.workflow_state || "Draft";
		const label = [__(state)];
		if (doc.is_late) {
			label.push(__("Late"));
		}
		if (doc.over_limit) {
			label.push(__("Over Limit"));
		}
		return [label.join(" · "), colors[state] || "grey", "workflow_state,=," + state];
	},
};
