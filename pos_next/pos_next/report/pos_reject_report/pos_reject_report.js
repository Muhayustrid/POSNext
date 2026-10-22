frappe.query_reports["POS Reject Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "pos_profile",
			label: __("POS Profile"),
			fieldtype: "Link",
			options: "POS Profile",
		},
		{
			fieldname: "purpose",
			label: __("Purpose"),
			fieldtype: "Link",
			options: "POS Reject Purpose",
		},
		{
			fieldname: "borne_by",
			label: __("Borne By"),
			fieldtype: "Select",
			options: ["", "Company", "Crew"],
		},
		{
			fieldname: "user",
			label: __("Liable Crew"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Per Request", "Per Liability"],
			default: "Per Request",
		},
	],
};
