import frappe
from frappe import _

from pos_next.services.spend_accounts import get_spend_template_readiness


def execute(filters=None):
	frappe.only_for(("System Manager", "Accounts Manager", "POSNext Manager"))
	filters = frappe._dict(filters or {})
	columns = [
		{
			"fieldname": "template",
			"label": _("Template"),
			"fieldtype": "Link",
			"options": "POS Spend Template",
			"width": 220,
		},
		{
			"fieldname": "company",
			"label": _("Company"),
			"fieldtype": "Link",
			"options": "Company",
			"width": 220,
		},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 120},
		{"fieldname": "reasons", "label": _("Reasons"), "fieldtype": "Data", "width": 500},
	]
	templates = frappe.get_list(
		"POS Spend Template",
		filters={"name": filters.template} if filters.template else {},
		pluck="name",
		limit_page_length=0,
	)
	rows = []
	for template in templates:
		for result in get_spend_template_readiness(template):
			rows.append(
				{
					"template": template,
					"company": result["company"],
					"status": _("Ready") if result["ready"] else _("Not Ready"),
					"reasons": "; ".join(result["reasons"] + result["warnings"]),
				}
			)
	return columns, rows
