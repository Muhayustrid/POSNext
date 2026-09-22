// Copyright (c) 2024, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Settings", {
	refresh(frm) {
		// Set query for loyalty program filtered by POS Profile company
		frm.set_query("default_loyalty_program", function () {
			if (!frm.doc.__company) {
				return { filters: {} };
			}
			return {
				filters: {
					company: frm.doc.__company,
				},
			};
		});

		// Fetch company when form loads
		if (frm.doc.pos_profile) {
			fetch_pos_profile_company(frm);
		}

		// Baseline for the target-basis change confirmation below; refresh
		// re-runs after every save, so the stored values stay authoritative.
		frm.__target_basis_prev = {
			monthly_target_basis: frm.doc.monthly_target_basis,
			overall_target_basis: frm.doc.overall_target_basis,
		};
	},

	pos_profile(frm) {
		// Clear loyalty program when POS Profile changes
		frm.set_value("default_loyalty_program", "");
		frm.doc.__company = null;

		if (frm.doc.pos_profile) {
			fetch_pos_profile_company(frm);
		}
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

function fetch_pos_profile_company(frm) {
	frappe.db.get_value("POS Profile", frm.doc.pos_profile, "company", (r) => {
		if (r && r.company) {
			frm.doc.__company = r.company;
		}
	});
}
