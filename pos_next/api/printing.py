# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Whitelisted helpers for the POS print transport.

The frontend transport calls get_print_config once per session and logs every
print attempt. Logging is fire-and-forget and never blocks a print.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

PRINT_CONFIG_FIELDS = (
	"print_driver",
	"imin_paper_width",
	"imin_custom_dots",
	"imin_cut_paper",
	"print_fallback_enabled",
)


@frappe.whitelist()
def get_print_config(pos_profile):
	"""Resolve the print configuration for a POS Profile."""
	if not pos_profile:
		frappe.throw(_("POS Profile is required"))

	# POS Settings print fields are introduced in Task 7. Until that migration
	# has landed, querying by those column names would raise
	# pymysql.err.OperationalError: (1054, "Unknown column '...'"). Guard on
	# meta so the endpoint still returns transport defaults on a fresh migrate.
	try:
		meta = frappe.get_meta("POS Settings")
	except Exception:
		meta = None
	if meta is not None:
		existing = {df.fieldname for df in meta.get("fields")}
		fields = [f for f in PRINT_CONFIG_FIELDS if f in existing]
	else:
		fields = []

	if fields:
		settings = frappe.db.get_value(
			"POS Settings",
			{"pos_profile": pos_profile, "enabled": 1},
			list(fields),
			as_dict=True,
		)
	else:
		settings = None

	if not settings:
		settings = {field: None for field in PRINT_CONFIG_FIELDS}

	return {
		"driver": getattr(settings, "print_driver", None) or "browser",
		"paper": getattr(settings, "imin_paper_width", None) or "58mm",
		"custom_dots": getattr(settings, "imin_custom_dots", None) or 384,
		"cut": bool(getattr(settings, "imin_cut_paper", None)),
		"fallback_enabled": bool(getattr(settings, "print_fallback_enabled", None)),
	}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def log_print_attempt(**kwargs):
	"""Persist one print attempt. Best-effort; callers must not await its failure."""
	allowed = {
		"reference_doctype",
		"reference_name",
		"driver",
		"status",
		"error_code",
		"error_message",
		"paper_width",
		"duration_ms",
		"pos_profile",
	}
	doc = frappe.get_doc({"doctype": "POS Print Log", **{k: v for k, v in kwargs.items() if k in allowed}})
	doc.flags.ignore_links = True
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def get_print_logs(pos_profile=None, reference_name=None, limit=50):
	filters = {}
	if pos_profile:
		filters["pos_profile"] = pos_profile
	if reference_name:
		filters["reference_name"] = reference_name
	return frappe.get_list(
		"POS Print Log",
		filters=filters,
		fields=["name", "reference_name", "driver", "status", "error_message", "paper_width", "creation"],
		order_by="creation desc",
		limit_page_length=min(int(limit or 50), 200),
	)
