// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("Stock Reconciliation", {
	refresh(frm) {
		// HQ-only: show the live system qty the outlet cannot see, so approval is
		// not blind. Display only; the endpoint re-checks the role server-side.
		const open_states = [
			"Draft",
			"Approval Pending by Stock Manager",
			"Approval Pending by Accounts Manager",
			"Rejected",
		];
		if (
			frm.doc.docstatus !== 0 ||
			!open_states.includes(frm.doc.workflow_state) ||
			!frappe.user.has_role(["Stock Manager", "Accounts Manager", "System Manager"])
		)
			return;
		frm.add_custom_button(__("Show System Qty"), () => show_system_qty(frm));
	},

	before_workflow_action(frm) {
		if (frm.selected_workflow_action !== "Reject") return;
		// workflow.js freezes before this hook; the freeze overlay sits above the
		// prompt and swallows its clicks, so lift it while the prompt is open.
		frappe.dom.unfreeze();
		return prompt_reject_reason().then((reason) => {
			// re-take the freeze workflow.js releases after apply_workflow
			frappe.dom.freeze();
			return frappe
				.xcall("frappe.desk.form.utils.add_comment", {
					reference_doctype: frm.doctype,
					reference_name: frm.doc.name,
					content: "Rejected: " + reason,
					comment_email: frappe.session.user,
					comment_by: frappe.session.user_fullname,
				})
				.catch((err) => {
					// workflow.js only unfreezes on the apply_workflow path, so a
					// failed comment must release the freeze itself.
					frappe.dom.unfreeze();
					throw err;
				});
		});
	},
});

// Resolves with the reason, rejects when the prompt is cancelled (Frappe then
// aborts the transition because before_workflow_action rejected).
function prompt_reject_reason() {
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
			__("Reject Stock Opname"),
			__("Reject")
		);
		// frappe.prompt hides the dialog before its callback runs, so the submit
		// path settles before hide(); on_hide then only catches a cancel.
		d.set_primary_action(__("Reject"), () => {
			const values = d.get_values();
			if (!values) return; // required reason missing: keep the dialog open
			settle(resolve, values.reason);
			d.hide();
		});
		// cancel: the freeze was already lifted before the prompt opened
		d.on_hide = () => settle(reject);
	});
}

// Read-only table of live system qty vs the counted qty (HQ approval aid).
function show_system_qty(frm) {
	frappe
		.call({
			method: "pos_next.api.stock_opname.get_system_qty",
			args: { name: frm.doc.name },
		})
		.then((r) => {
			if (!r.message) return;
			const currency = r.message.currency;
			const rows = r.message.items
				.map(
					(row) => `<tr>
				<td>${frappe.utils.escape_html(row.item_name || row.item_code)}</td>
				<td>${frappe.utils.escape_html(row.batch_no || "")}</td>
				<td class="text-right">${format_number(row.counted_qty)}</td>
				<td class="text-right">${format_number(row.system_qty)}</td>
				<td class="text-right">${format_number(row.difference_qty)}</td>
				<td class="text-right">${format_currency(row.amount_difference, currency)}</td>
			</tr>`
				)
				.join("");
			frappe.msgprint({
				title: __("System Quantity (live)"),
				wide: true,
				message: `<table class="table table-bordered table-sm">
				<thead><tr>
					<th>${__("Item")}</th>
					<th>${__("Batch")}</th>
					<th class="text-right">${__("Counted")}</th>
					<th class="text-right">${__("System")}</th>
					<th class="text-right">${__("Difference")}</th>
					<th class="text-right">${__("Amount difference")}</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>`,
			});
		});
}
