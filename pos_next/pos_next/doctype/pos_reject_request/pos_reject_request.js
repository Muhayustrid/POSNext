// Copyright (c) 2026, POS Next and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Reject Request", {
	refresh(frm) {
		pos_next_reject.indicators(frm);
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Berita Acara"), () => {
				const url = `/printview?doctype=POS%20Reject%20Request&name=${encodeURIComponent(frm.doc.name)}&format=Berita%20Acara%20Reject`;
				window.open(url, "_blank");
			});
		}
	},

	before_workflow_action(frm) {
		const action = frm.selected_workflow_action;
		if (action === "Approve" && frm.doc.workflow_state === "Pending Approval") {
			frappe.dom.unfreeze();
			return pos_next_reject.prompt_approve(frm).then((res) => {
				frappe.dom.freeze();
				return frappe
					.xcall("pos_next.api.reject_request.set_reject_liability", {
						name: frm.doc.name,
						borne_by: res.borne_by,
						liabilities: res.liabilities,
					})
					.catch((err) => {
						frappe.dom.unfreeze();
						throw err;
					});
			});
		}

		if (action === "Return") {
			frappe.dom.unfreeze();
			return pos_next_reject.prompt_return_reason(action).then((reason) => {
				frappe.dom.freeze();
				return frappe
					.xcall("pos_next.api.reject_request.record_return_reason", {
						name: frm.doc.name,
						reason: reason,
					})
					.catch((err) => {
						frappe.dom.unfreeze();
						throw err;
					});
			});
		}
	},
});

frappe.provide("pos_next_reject");

pos_next_reject.prompt_return_reason = function (action) {
	return new Promise((resolve, reject) => {
		let settled = false;
		const settle = (fn, value) => {
			if (settled) return;
			settled = true;
			fn(value);
		};
		const d = frappe.prompt(
			[{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 }],
			() => {},
			action,
			action
		);
		d.set_primary_action(action, () => {
			const values = d.get_values();
			if (!values) return;
			settle(resolve, values.reason);
			d.hide();
		});
		d.on_hide = () => settle(reject);
	});
};

pos_next_reject.prompt_approve = function (frm) {
	return new Promise((resolve, reject) => {
		let settled = false;
		let manually_edited = false;
		const settle = (fn, value) => {
			if (settled) return;
			settled = true;
			fn(value);
		};

		const default_borne_by = frm.doc.borne_by || "Company";

		const dialog = new frappe.ui.Dialog({
			title: __("Approve"),
			fields: [
				{
					fieldname: "borne_by",
					fieldtype: "Select",
					label: __("Borne By"),
					options: "\nCompany\nCrew",
					default: default_borne_by,
					reqd: 1,
					onchange() {
						const val = dialog.get_value("borne_by");
						const is_crew = val === "Crew";
						dialog.set_df_property("liabilities", "hidden", !is_crew);
						dialog.set_df_property("total_html", "hidden", !is_crew);
						if (is_crew) {
							if (dialog.fields_dict.liabilities && dialog.fields_dict.liabilities.grid) {
								dialog.fields_dict.liabilities.grid.refresh();
							}
							pos_next_reject.show_total(dialog, frm);
						}
					},
				},
				{
					fieldname: "liabilities",
					fieldtype: "Table",
					label: __("Liabilities"),
					hidden: default_borne_by !== "Crew",
					fields: [
						{ fieldname: "user", fieldtype: "Link", options: "User", label: __("User"), reqd: 1, in_list_view: 1 },
						{ fieldname: "amount", fieldtype: "Currency", label: __("Amount"), reqd: 1, in_list_view: 1 },
					],
					data: [{ user: frm.doc.owner, amount: frm.doc.total_amount }],
					get_data() {
						return dialog.get_value("liabilities");
					},
					on_add_row() {
						if (!manually_edited) pos_next_reject.redistribute(dialog, frm);
					},
				},
				{
					fieldname: "total_html",
					fieldtype: "HTML",
					hidden: default_borne_by !== "Crew",
				},
			],
			primary_action_label: __("Approve"),
			primary_action(values) {
				const borne_by = values.borne_by;
				let liabilities = null;
				if (borne_by === "Crew") {
					liabilities = dialog.get_value("liabilities") || [];
					if (!liabilities.length) {
						frappe.msgprint(__("Liabilities are required when borne by Crew"));
						return;
					}
					const sum = liabilities.reduce((s, r) => s + (flt(r.amount) || 0), 0);
					const precision = frm.doc.precision ? frm.doc.precision("total_amount") || 2 : 2;
					if (Math.abs(sum - flt(frm.doc.total_amount)) > 0.5 * Math.pow(10, -precision)) {
						const fmt = (v) => format_currency(v, erpnext.get_currency(frm.doc.company));
						frappe.msgprint(__("Liability amounts must add up to {0}", [fmt(frm.doc.total_amount)]));
						return;
					}
				}
				settle(resolve, { borne_by, liabilities });
				dialog.hide();
			},
		});

		dialog.$body.on("change", 'input[data-fieldname="amount"]', () => {
			manually_edited = true;
			pos_next_reject.show_total(dialog, frm);
		});

		dialog.on_hide = () => settle(reject);
		dialog.show();
		if (default_borne_by === "Crew") {
			pos_next_reject.show_total(dialog, frm);
		}
	});
};

pos_next_reject.show_total = function (dialog, frm) {
	const rows = dialog.get_value("liabilities") || [];
	const sum = rows.reduce((s, r) => s + (flt(r.amount) || 0), 0);
	const fmt = (v) => format_currency(v, erpnext.get_currency(frm.doc.company));
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
