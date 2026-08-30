// Promotion form UX glue:
// - Options reference Choice Groups by their raw server key (grp_xxxxxxxx).
//   Instead of asking the operator to copy that key, the Choice Group column
//   of the Options grid becomes a dropdown of the groups in this document,
//   showing labels and storing keys.
// - Group keys are pre-generated on the client the moment a group row is
//   added, so groups and their options can be filled in one save. The server
//   only fills MISSING keys (Promotion._ensure_group_keys), so client keys
//   pass through untouched.
// - Outlet warehouses are filtered to the row's company (Price Group pattern),
//   because the server rejects a warehouse from another company.

frappe.ui.form.on("Promotion", {
	refresh(frm) {
		set_intro_help(frm);
		ensure_group_keys(frm);
		set_choice_group_options(frm);
		set_outlet_warehouse_filter(frm);
	},
});

frappe.ui.form.on("Promotion Choice Group", {
	// Grid add/remove events are triggered with the CHILD doctype, so they
	// must be registered here (same pattern as ERPNext's items_add), not on
	// the Promotion form.
	choice_groups_add(frm) {
		ensure_group_keys(frm);
		set_choice_group_options(frm);
	},
	choice_groups_remove(frm) {
		set_choice_group_options(frm);
	},
	label(frm) {
		set_choice_group_options(frm);
	},
});

function set_intro_help(frm) {
	frm.set_df_property(
		"intro_help",
		"options",
		`<div class="text-muted" style="max-width: 640px">
			${__(
				"One promotion is one sellable package: the customer pays Base Price, always receives the Fixed Components, picks from the Customer Choices, and can only order it at the listed Outlets."
			)}
		</div>`
	);
}

function ensure_group_keys(frm) {
	for (const group of frm.doc.choice_groups || []) {
		if (!group.group_key) {
			frappe.model.set_value(group.doctype, group.name, "group_key", make_group_key(frm));
		}
	}
}

function make_group_key(frm) {
	// Same format as the server-side key (Promotion._ensure_group_keys):
	// "grp_" + 8 hex chars, unique within this document.
	const taken = new Set(
		(frm.doc.choice_groups || []).map((group) => group.group_key).filter(Boolean)
	);
	let key;
	do {
		key = "grp_" + Math.random().toString(16).slice(2, 10);
	} while (taken.has(key));
	return key;
}

function set_choice_group_options(frm) {
	const groups = (frm.doc.choice_groups || []).filter((group) => group.group_key);

	let select_options;
	if (groups.length) {
		const label_counts = {};
		for (const group of groups) {
			const label = group.label || __("Untitled group");
			label_counts[label] = (label_counts[label] || 0) + 1;
		}
		select_options = groups.map((group) => {
			const label = group.label || __("Untitled group");
			return {
				// Disambiguate duplicate labels with the raw key.
				label: label_counts[label] > 1 ? `${label} (${group.group_key})` : label,
				value: group.group_key,
			};
		});
	} else {
		select_options = [{ label: __("Add a choice group first"), value: "", disabled: true }];
	}

	const signature = JSON.stringify(select_options);
	if (frm._choice_group_signature === signature) {
		return;
	}
	frm._choice_group_signature = signature;

	frm.fields_dict.options.grid.update_docfield_property(
		"choice_group_key",
		"options",
		select_options
	);
	frm.refresh_field("options");
}

function set_outlet_warehouse_filter(frm) {
	frm.fields_dict.outlets.grid.get_field("warehouse").get_query = function (doc, cdt, cdn) {
		const row = locals[cdt][cdn];
		return {
			filters: {
				company: row.company || "",
				is_group: 0,
			},
		};
	};
}
