// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.listview_settings["POS Reject Request"] = {
	add_fields: ["is_late", "over_limit"],
	get_indicator(doc) {
		const colors = {
			Draft: "grey",
			Pending: "orange",
			Approved: "blue",
			Rejected: "red",
			Closed: "green",
			Cancelled: "red",
		};
		const label = [__(doc.status)];
		if (doc.is_late) {
			label.push(__("Late"));
		}
		if (doc.over_limit) {
			label.push(__("Over Limit"));
		}
		return [label.join(" · "), colors[doc.status] || "grey", "status,=," + doc.status];
	},
};
