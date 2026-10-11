// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Reject Request", {
	refresh(frm) {
		pos_next_reject.indicators(frm);
		pos_next_reject.decide_buttons(frm);
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
	frm.add_custom_button(__("Approved"), () => pos_next_reject.approved_dialog(frm), group);
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

pos_next_reject.approved_dialog = function (frm) {
	frappe.prompt(
		[
			{
				fieldname: "notes",
				fieldtype: "Small Text",
				label: __("Notes"),
				description: __("Optional explanation for the crew"),
			},
		],
		(values) => pos_next_reject.decide(frm, "Approved", values.notes),
		__("Approve"),
		__("Approve")
	);
};

pos_next_reject.rejected_dialog = function (frm) {
	let manually_edited = false;
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
				// grid's own Add Row; programmatic changes never fire this
				on_add_row() {
					if (!manually_edited) pos_next_reject.redistribute(dialog, frm);
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

	// user keystrokes bubble a change event; programmatic grid edits do not
	dialog.$body.on("change", 'input[data-fieldname="amount"]', () => {
		manually_edited = true;
		pos_next_reject.show_total(dialog, frm);
	});

	dialog.show();
	pos_next_reject.show_total(dialog, frm);
};

pos_next_reject.show_total = function (dialog, frm) {
	const rows = dialog.get_value("liabilities") || [];
	const sum = rows.reduce((s, r) => s + (flt(r.amount) || 0), 0);
	const fmt = (v) => format_currency(v, frappe.defaults.get_global_default("currency"));
	dialog.$body
		.find('[data-fieldname="total_html"]')
		.html(`<div class="text-right">${__("Total {0} / {1}", [fmt(sum), fmt(frm.doc.total_amount)])}</div>`);
};

pos_next_reject.redistribute = function (dialog, frm) {
	const rows = dialog.get_value("liabilities") || [];
	const n = rows.length;
	if (!n) return;
	const precision = frappe.boot.sysdefaults && frappe.boot.sysdefaults.currency_precision != null
		? flt(frappe.boot.sysdefaults.currency_precision)
		: 2;
	const total = frm.doc.total_amount;
	const share = flt(total / n, precision);
	rows.forEach((r, i) => {
		// last row absorbs rounding
		r.amount = i === n - 1 ? total - share * (n - 1) : share;
	});
	dialog.fields_dict.liabilities.grid.refresh();
	pos_next_reject.show_total(dialog, frm);
};

pos_next_reject.indicators = function (frm) {
	if (frm.doc.is_late) {
		frm.dashboard.add_indicator(__("Late"), "orange");
	}
	if (frm.doc.over_limit) {
		frm.dashboard.add_indicator(__("Over Limit"), "red");
	}
};
