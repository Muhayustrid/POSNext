# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Keep POS cashiers in the POS SPA: Desk routes redirect them to /pos.

Only page loads of the Desk UI are redirected — /api, /printview, files and
the SPA itself stay reachable, so every POS flow keeps working. A cashier who
also holds a management or HQ role (they approve or monitor in Desk) keeps
Desk access.
"""

import frappe
from werkzeug.exceptions import HTTPException
from werkzeug.utils import redirect

from pos_next.utils.authz import is_desk_blocked

DESK_PREFIXES = ("/app", "/desk")
POS_ROUTE = "/pos"


def _is_desk_path(path):
	path = (path or "").rstrip("/") or "/"
	return any(path == prefix or path.startswith(prefix + "/") for prefix in DESK_PREFIXES)


def block_desk_for_cashiers():
	"""before_request hook."""
	request = getattr(frappe.local, "request", None)
	if not request or request.method not in ("GET", "HEAD") or not _is_desk_path(request.path):
		return
	if not is_desk_blocked():
		return
	# an HTTPException carrying a response is returned as-is by frappe.app
	raise HTTPException(response=redirect(POS_ROUTE))
