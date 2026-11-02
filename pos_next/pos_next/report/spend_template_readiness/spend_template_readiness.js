frappe.query_reports["Spend Template Readiness"] = {
	filters: [
		{ fieldname: "template", label: __("Template"), fieldtype: "Link", options: "POS Spend Template" },
	],
};
