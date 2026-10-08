// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.query_reports["POS Outlet Performance Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
		},
		{
			fieldname: "include_descendants",
			label: __("Include Sub-Companies"),
			fieldtype: "Check",
			depends_on: "company",
		},
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
			fieldname: "show_empty",
			label: __("Show Empty Outlets"),
			fieldtype: "Check",
		},
	],
};
