// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Reject Request", {
	refresh(frm) {
		pos_next_reject.indicators(frm);
		pos_next_reject.decide_buttons(frm);
	},
});

frappe.provide("pos_next_reject");

pos_next_reject.DECISIONS = [
	// Write Off needs no notes; the other two are mandatory (service enforces too)
	{ decision: "Write Off", reqd: false },
	{ decision: "Charge Crew", reqd: true },
	{ decision: "Rejected", reqd: true },
];

pos_next_reject.decide_buttons = function (frm) {
	if (frm.doc.status !== "Pending" || !frappe.user.has_role("POSNext Reject Approver")) {
		return;
	}
	if (frappe.session.user === frm.doc.owner) {
		// segregation of duties: the owner cannot judge their own request
		return;
	}
	const group = __("Decide");
	for (const { decision, reqd } of pos_next_reject.DECISIONS) {
		frm.add_custom_button(__(decision), () => pos_next_reject.prompt_decision(frm, decision, reqd), group);
	}
};

pos_next_reject.prompt_decision = function (frm, decision, reqd) {
	frappe.prompt(
		[
			{
				fieldname: "notes",
				fieldtype: "Small Text",
				label: __("Notes"),
				reqd: reqd ? 1 : 0,
				description: reqd
					? __("Notes are required for {0}", [__(decision)])
					: __("Optional explanation for the crew"),
			},
		],
		(values) => {
			frappe
				.call({
					method: "pos_next.api.reject_request.decide_reject_request",
					args: { name: frm.doc.name, decision: decision, notes: values.notes },
				})
				.then(() => frm.reload_doc());
		},
		__("Decide: {0}", [__(decision)])
	);
};

pos_next_reject.indicators = function (frm) {
	if (frm.doc.is_late) {
		frm.dashboard.add_indicator(__("Late"), "orange");
	}
	if (frm.doc.over_limit) {
		frm.dashboard.add_indicator(__("Over Limit"), "red");
	}
};
