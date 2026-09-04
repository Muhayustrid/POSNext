// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.ui.form.on("POS Discount Restriction", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.require_confirmation_code) return;

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
				],
				(values) => {
					frm
						.call("generate_codes", {
							count: values.count,
							company: values.company,
						})
						.then((r) => {
							const codes = (r.message && r.message.codes) || [];
							if (!codes.length) return;
							frappe.msgprint({
								title: __("Confirmation Codes Generated"),
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
										"Each code is one-time use. Share it only with the intended cashier/customer."
									)}</p>`,
								indicator: "green",
							});
						});
				},
				__("Generate Confirmation Codes")
			);
		});
	},
});
