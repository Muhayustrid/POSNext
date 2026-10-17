// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("Stock Reconciliation", {
	before_workflow_action(frm) {
		if (frm.selected_workflow_action !== "Reject") return;
		return prompt_reject_reason().then((reason) =>
			frappe
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
				})
		);
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
		d.on_hide = () => {
			if (settled) return;
			// workflow.js only unfreezes on the apply_workflow path, so release
			// the freeze it took before triggering this handler.
			frappe.dom.unfreeze();
			settle(reject);
		};
	});
}
