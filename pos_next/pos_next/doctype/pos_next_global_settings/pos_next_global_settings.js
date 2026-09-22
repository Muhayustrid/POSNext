// Copyright (c) 2026, Youssef Restom and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Next Global Settings", {
	refresh(frm) {
		// Baseline for the target-basis change confirmation below; refresh
		// re-runs after every save, so the stored values stay authoritative.
		frm.__target_basis_prev = {
			monthly_target_basis: frm.doc.monthly_target_basis,
			overall_target_basis: frm.doc.overall_target_basis,
		};

		frm.add_custom_button(__("POS Settings"), () => frappe.set_route("List", "POS Settings"));
	},

	monthly_target_basis(frm) {
		confirm_target_basis_change(frm, "monthly_target_basis");
	},

	overall_target_basis(frm) {
		confirm_target_basis_change(frm, "overall_target_basis");
	},
});

// Switching a target basis reinterprets every stored target number (they are
// NOT migrated), so make the change explicit: confirm keeps it, cancel
// restores the value the form was loaded/saved with.
function confirm_target_basis_change(frm, fieldname) {
	const prev = (frm.__target_basis_prev || {})[fieldname];
	const value = frm.doc[fieldname];
	if (prev === undefined || prev === value) return;
	frappe.confirm(
		__("Existing target numbers are reinterpreted on the new basis, not migrated. Keep this change?"),
		() => {
			frm.__target_basis_prev[fieldname] = value;
		},
		() => {
			frm.set_value(fieldname, prev);
		}
	);
}
