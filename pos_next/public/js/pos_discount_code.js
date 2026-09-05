// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Discount Confirmation Code", {
	refresh(frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__("Generate Codes"), () => {
			frappe.prompt(
				[
					{
						fieldname: "count",
						fieldtype: "Int",
						label: __("How many codes"),
						reqd: 1,
						default: 1,
					},
					{
						fieldname: "company",
						fieldtype: "Link",
						options: "Company",
						label: __("Restrict to Company (optional)"),
					},
					{
						fieldname: "notes",
						fieldtype: "Small Text",
						label: __("Notes (optional)"),
					},
				],
				(values) => {
					frappe
						.call({
							method:
								"pos_next.pos_next.doctype.pos_discount_confirmation_code.pos_discount_confirmation_code.generate_codes",
							args: {
								count: values.count,
								company: values.company,
								notes: values.notes,
							},
						})
						.then((r) => {
							const codes = (r.message && r.message.codes) || [];
							if (!codes.length) return;
							frappe.msgprint({
								title: __("Discount Codes Generated"),
								message:
									"<ul>" +
									codes
										.map(
											(code) =>
												`<li style="font-family: monospace; font-size: 14px;"><b>${code}</b></li>`
										)
										.join("") +
									"</ul>" +
									`<p class="text-muted">${__(
										"Each code stays usable until it is disabled, for every manual discount. Share it only with the intended outlet."
									)}</p>`,
								indicator: "green",
							});
						});
				},
				__("Generate Discount Codes")
			);
		});
	},
});
