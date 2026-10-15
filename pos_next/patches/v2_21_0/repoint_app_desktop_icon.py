# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Point the auto-created "POS Next" app tile at the role-aware landing route.

Frappe creates that Desktop Icon once from the add_to_apps_screen hook and never
refreshes it, so sites installed before the hook route changed keep the old
/app/posnext link and bypass the HQ landing.
"""

import frappe

OLD_ROUTES = ("/app/posnext", "/desk/posnext")
NEW_ROUTE = "/posnext-home"


def execute():
	frappe.db.set_value(
		"Desktop Icon",
		{"app": "pos_next", "icon_type": "App", "link": ("in", OLD_ROUTES)},
		"link",
		NEW_ROUTE,
	)
