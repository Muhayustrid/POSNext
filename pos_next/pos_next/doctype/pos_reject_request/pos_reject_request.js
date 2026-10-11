// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Reject Request", {
	refresh(frm) {
		pos_next_reject.indicators(frm);
		pos_next_reject.decide_buttons(frm);
		pos_next_reject.video_link(frm);
	},
});

frappe.provide("pos_next_reject");

pos_next_reject.decide_buttons = function (frm) {
	if (frm.doc.status !== "Pending" || !frappe.user.has_role("POSNext Reject Approver")) {
		return;
	}
	if (frappe.session.user === frm.doc.owner) {
		// segregation of duties: the owner cannot judge their own request
		return;
	}
	const group = __("Decide");
	frm.add_custom_button(__("Approved"), () => pos_next_reject.decide(frm, "Approved"), group);
	frm.add_custom_button(__("Rejected"), () => pos_next_reject.rejected_dialog(frm), group);
};

pos_next_reject.decide = function (frm, decision, notes, liabilities) {
	frappe
		.call({
			method: "pos_next.api.reject_request.decide_reject_request",
			args: { name: frm.doc.name, decision, notes, liabilities },
		})
		.then(() => frm.reload_doc());
};

pos_next_reject.rejected_dialog = function (frm) {
	const dialog = new frappe.ui.Dialog({
		title: __("Decide: {0}", [__("Rejected")]),
		fields: [
			{
				fieldname: "notes",
				fieldtype: "Small Text",
				label: __("Notes"),
				reqd: 1,
				description: __("Notes are required for {0}", [__("Rejected")]),
			},
			{
				fieldname: "liabilities",
				fieldtype: "Table",
				label: __("Liabilities"),
				options: "POS Reject Liability",
				reqd: 1,
				data: [{ user: frm.doc.owner, amount: frm.doc.total_amount }],
				get_data() {
					return dialog.get_value("liabilities");
				},
			},
			{
				fieldname: "total_html",
				fieldtype: "HTML",
				options: "",
			},
		],
		primary_action_label: __("Decide"),
		primary_action(values) {
			pos_next_reject.decide(frm, "Rejected", values.notes, values.liabilities);
			dialog.hide();
		},
	});

	// running total vs total_amount; "Split Evenly" puts rounding on the last row
	const $total = dialog.$body.find('[data-fieldname="total_html"]');
	const fmt = (v) => format_currency(v, frappe.defaults.get_global_default("currency"));
	const rows = () => dialog.get_value("liabilities") || [];
	const show_total = () => {
		const sum = rows().reduce((s, r) => s + (flt(r.amount) || 0), 0);
		$total.html(`<div class="text-right">${__("Total {0} / {1}", [fmt(sum), fmt(frm.doc.total_amount)])}</div>`);
	};
	dialog.$body.on("change", show_total);
	dialog.fields_dict.liabilities.grid.add_custom_button(__("Split Evenly"), () => {
		const data = rows();
		if (!data.length) return;
		const share = frm.doc.total_amount / data.length;
		data.forEach((r, i) => {
			r.amount = i === data.length - 1 ? frm.doc.total_amount - share * (data.length - 1) : share;
		});
		dialog.fields_dict.liabilities.grid.refresh();
		show_total();
	});

	dialog.show();
	show_total();
};

pos_next_reject.video_link = function (frm) {
	const url = frm.doc.video_link;
	if (!url || !frm.fields_dict.video_link) return;
	// Desk does not autolink URL Data fields in read-only view
	const safe = frappe.utils.escape_html(url);
	const $el = frm.fields_dict.video_link.$wrapper;
	$el.find(".control-value").html(
		`<a href="${safe}" target="_blank" rel="noopener">${safe}</a>`
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
