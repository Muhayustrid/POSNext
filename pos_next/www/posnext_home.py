# Landing route for the POSNext app tile / default app: HQ roles land on the HQ
# dashboard, everyone else on the workspace, since the HQ page is role-gated;
# Desk-blocked cashiers go straight to the POS.

import frappe

from pos_next.api.hq_monitoring import HQ_ROLES
from pos_next.utils.authz import is_desk_blocked

no_cache = True


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.throw(frappe._("Log in to access this page."), frappe.PermissionError)

	is_hq = set(frappe.get_roles()) & set(HQ_ROLES)
	if is_desk_blocked():
		# cashiers have no Desk (utils/desk_guard) — straight to the POS
		frappe.local.flags.redirect_location = "/pos"
	else:
		frappe.local.flags.redirect_location = "/desk/hq-sales-monitoring" if is_hq else "/desk/posnext"
	raise frappe.Redirect
