frappe.ui.form.on("POS Spend Template", {
	setup(frm) {
		frm.set_query("reference_debit_account", () => ({
			filters: { company: frm.doc.reference_company, is_group: 0 },
		}));
	},
	company_scope(frm) {
		frm.set_df_property("companies", "description", frm.doc.company_scope === "All Companies"
			? __("Applies to all non-group companies, including new ones. Company rows are optional overrides.")
			: __("Select companies or a group company to include its descendants."));
	},
	refresh(frm) {
		frm.trigger("company_scope");
		if (frm.is_new()) return;
		frm.add_custom_button(__("Check Readiness"), async () => {
			if (frm.is_dirty()) {
				frappe.msgprint(__("Save the template before checking readiness."));
				return;
			}
			const { message: rows } = await frappe.call({
				method: "pos_next.services.spend_accounts.get_spend_template_readiness",
				args: { template: frm.doc.name },
				freeze: true,
			});
			const escape = frappe.utils.escape_html;
			const headers = [__("Company"), __("Status"), __("Accounts"), __("Reasons"), __("Warnings")];
			const body = (rows || []).map((row) => {
				const accounts = Object.entries(row.accounts).map(([key, value]) =>
					`${escape(key)}: ${escape(typeof value === "object" ? JSON.stringify(value) : String(value ?? ""))}`,
				).join("<br>");
				return `<tr><td>${escape(row.company)}</td><td>${escape(row.ready ? __("Ready") : __("Not Ready"))}</td><td>${accounts}</td><td>${row.reasons.map(escape).join("<br>")}</td><td>${row.warnings.map(escape).join("<br>")}</td></tr>`;
			}).join("");
			const dialog = new frappe.ui.Dialog({
				title: __("Spend Template Readiness"),
				size: "extra-large",
				fields: [{ fieldname: "results", fieldtype: "HTML" }],
			});
			dialog.fields_dict.results.$wrapper.html(`<div class="table-responsive"><table class="table table-bordered"><thead><tr>${headers.map((title) => `<th>${escape(title)}</th>`).join("")}</tr></thead><tbody>${body}</tbody></table></div>`);
			dialog.show();
		});
	},
});
