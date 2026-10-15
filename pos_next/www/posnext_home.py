# Landing route for the POSNext app tile / default app: HQ roles land on the HQ
# dashboard, everyone else (e.g. cashiers) on the workspace, since the HQ page
# is role-gated.

import frappe

from pos_next.api.hq_monitoring import HQ_ROLES

no_cache = True


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Log in to access this page."), frappe.PermissionError)

	is_hq = set(frappe.get_roles()) & set(HQ_ROLES)
	frappe.local.flags.redirect_location = "/desk/hq-sales-monitoring" if is_hq else "/desk/posnext"
	raise frappe.Redirect
