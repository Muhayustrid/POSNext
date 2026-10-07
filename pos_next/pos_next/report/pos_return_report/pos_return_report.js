// Copyright (c) 2026, BrainWise and contributors
// For license information, please see license.txt

frappe.query_reports["POS Return Report"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			on_change: function () {
				// Company changed: drop the stale profile, then re-run the report.
				frappe.query_report.set_filter_value("pos_profile", "");
				frappe.query_report.refresh();
			},
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
			fieldname: "pos_profile",
			label: __("POS Profile"),
			fieldtype: "Link",
			options: "POS Profile",
			get_query: function () {
				const company = frappe.query_report.get_filter_value("company");
				return company ? { filters: { company: company } } : {};
			},
		},
		{
			fieldname: "cashier",
			label: __("Cashier"),
			fieldtype: "Link",
			options: "User",
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: ["Invoice", "Item"],
			default: "Invoice",
		},
	],
};
