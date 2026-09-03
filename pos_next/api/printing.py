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
	"imin_print_copies",
	"imin_copy_delay_ms",
	"imin_feed_dots",
	"imin_tail_dots",
	"imin_copy_labels",
	"imin_font_scale",
	"print_fallback_enabled",
)

# Sensible operational bounds so a mis-typed value cannot make the cashier
# wait for ever (copies) or block the lane (delay).
MAX_COPIES = 5
MAX_COPY_DELAY_MS = 10000
MAX_FEED_DOTS = 500
MAX_TAIL_DOTS = 200


@frappe.whitelist()
def get_print_config(pos_profile):
	"""Resolve the print configuration for a POS Profile.

	A null/empty pos_profile is a supported caller state, not an error: the
	Direct Print diagnostic page has no shift or invoice in context (no open
	shift -> bootstrap returns pos_profile: None). Fall back to the first
	enabled POS Settings row on the site, then to pure transport defaults.
	The response reports which profile was actually used via `pos_profile`,
	so callers (and logs) can see when the fallback fired.
	"""
	resolved_profile = pos_profile
	if not resolved_profile:
		resolved_profile = frappe.db.get_value(
			"POS Settings", {"enabled": 1}, "pos_profile", order_by="modified desc"
		)

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

	if fields and resolved_profile:
		settings = frappe.db.get_value(
			"POS Settings",
			{"pos_profile": resolved_profile, "enabled": 1},
			list(fields),
			as_dict=True,
		)
	else:
		settings = None

	if not settings:
		settings = {field: None for field in PRINT_CONFIG_FIELDS}

	# A Check field defaulting to 1 means "unset is enabled": when the POS
	# Settings row is missing or the column is NULL, fallback stays on so a
	# broken iMin/QZ chain still reaches the browser driver. Only an explicit
	# 0 disables it. `cut` keeps the strict bool() — default-off is correct
	# there (cutting on an uncut-capable printer is worse than not cutting).
	raw_fallback = getattr(settings, "print_fallback_enabled", None)

	try:
		copies = int(getattr(settings, "imin_print_copies", None) or 1)
	except (TypeError, ValueError):
		copies = 1
	copies = max(1, min(copies, MAX_COPIES))

	try:
		delay = getattr(settings, "imin_copy_delay_ms", None)
		delay = 800 if delay is None else int(delay)
	except (TypeError, ValueError):
		delay = 800
	delay = max(0, min(delay, MAX_COPY_DELAY_MS))

	try:
		feed = getattr(settings, "imin_feed_dots", None)
		feed = 160 if feed is None else int(feed)
	except (TypeError, ValueError):
		feed = 160
	feed = max(8, min(feed, MAX_FEED_DOTS))

	# Tail is white space INSIDE the bitmap. Together with feed it forms the
	# clearance between the last printed line and the tear bar:
	# head->cutter ~= tailDots + feedDots. The SDK clamps printAndFeedPaper to
	# 0..255 dots, so some of that gap living in the raster keeps it safe even
	# on builds where the feed ceiling matters.
	# VERIFY ON DEVICE: 24 dots (3mm) is a starting value, not a measured one.
	try:
		tail = getattr(settings, "imin_tail_dots", None)
		tail = 24 if tail is None else int(tail)
	except (TypeError, ValueError):
		tail = 24
	tail = max(0, min(tail, MAX_TAIL_DOTS))

	# Crew vs customer distinction: when copies>1 and this is truthy the second
	# bitmap gets a CREW COPY banner so the outlet copy is unmistakable.
	raw_label = getattr(settings, "imin_copy_labels", None)
	copy_labels = True if raw_label is None else bool(raw_label)

	# Font scale on top of the fixed 205/96 DPI translation. 100 = as authored
	# at 96 DPI (already ~2.1x bigger than what printed before the translation
	# existed); raise for chunkier text. Percent, clamped to a sane band.
	try:
		font_scale = getattr(settings, "imin_font_scale", None)
		font_scale = 100 if font_scale is None else int(font_scale)
	except (TypeError, ValueError):
		font_scale = 100
	font_scale = max(60, min(font_scale, 250))

	return {
		"pos_profile": resolved_profile,
		"driver": getattr(settings, "print_driver", None) or "browser",
		"paper": getattr(settings, "imin_paper_width", None) or "58mm",
		"custom_dots": getattr(settings, "imin_custom_dots", None) or 384,
		"cut": bool(getattr(settings, "imin_cut_paper", None)),
		"copies": copies,
		"copy_delay_ms": delay,
		"feed_dots": feed,
		"tail_dots": tail,
		"copy_labels": copy_labels,
		"font_scale": font_scale,
		"fallback_enabled": True if raw_fallback is None else bool(raw_fallback),
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
