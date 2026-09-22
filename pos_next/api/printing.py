# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Whitelisted helpers for the POS print transport.

The frontend transport calls get_print_config once per session and logs every
print attempt. Logging is fire-and-forget and never blocks a print.

update_print_config is the single write path for the print knobs (the Direct
Print page is its UI) and clamps through the same table as the read path, so
a stored row can never hold a value get_print_config would silently change.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

from pos_next.api.settings_resolver import get_effective_pos_settings

PRINT_CONFIG_FIELDS = (
	"print_driver",
	"imin_paper_width",
	"imin_custom_dots",
	"imin_cut_paper",
	"imin_print_copies",
	"imin_copy_delay_ms",
	"imin_feed_dots",
	"imin_tail_dots",
	"imin_font_scale",
	"imin_crew_font_scale",
	"imin_crew_slip_enabled",
	"imin_line_spacing",
	"imin_side_margin",
	"imin_top_margin",
	"imin_queue_gap",
	"imin_eod_print_copies",
	"imin_eod_copy_delay_ms",
	"imin_eod_feed_dots",
	"imin_eod_tail_dots",
	"imin_eod_font_scale",
	"imin_eod_line_spacing",
	"imin_eod_side_margin",
	"imin_eod_top_margin",
	"print_fallback_enabled",
)

# Sensible operational bounds so a mis-typed value cannot make the cashier
# wait for ever (copies) or block the lane (delay).
MAX_COPIES = 5
MAX_COPY_DELAY_MS = 10000
MAX_FEED_DOTS = 500
MAX_TAIL_DOTS = 200
MIN_LINE_SPACING = 50
MAX_LINE_SPACING = 150
MAX_SIDE_MARGIN_DOTS = 64
MAX_TOP_MARGIN_DOTS = 128
MAX_QUEUE_GAP_DOTS = 128

# Integer knobs: fieldname -> (response key, default, lo, hi).
#
# Feed vs tail: the tail is white space INSIDE the bitmap, the feed the
# advance after it — head->cutter clearance ~= tail + feed. The SDK clamps
# printAndFeedPaper to 0..255, so part of the gap living in the raster keeps
# it safe on builds where the feed ceiling matters. Side/top margins and the
# scales are physical answers, so a deliberate 0 (edge-to-edge) survives;
# only None/garbage falls back to the default. (VERIFY ON DEVICE: 24 tail
# dots / 3 mm is a starting value, not a measured one.)
_INT_KNOBS = {
	"imin_print_copies": ("copies", 1, 1, MAX_COPIES),
	"imin_copy_delay_ms": ("copy_delay_ms", 800, 0, MAX_COPY_DELAY_MS),
	"imin_feed_dots": ("feed_dots", 160, 8, MAX_FEED_DOTS),
	"imin_tail_dots": ("tail_dots", 24, 0, MAX_TAIL_DOTS),
	"imin_font_scale": ("font_scale", 100, 60, 250),
	"imin_crew_font_scale": ("crew_font_scale", 100, 60, 250),
	"imin_line_spacing": ("line_spacing", 100, MIN_LINE_SPACING, MAX_LINE_SPACING),
	"imin_side_margin": ("side_margin", 16, 0, MAX_SIDE_MARGIN_DOTS),
	"imin_top_margin": ("top_margin", 0, 0, MAX_TOP_MARGIN_DOTS),
	"imin_queue_gap": ("queue_gap", 40, 0, MAX_QUEUE_GAP_DOTS),
	"imin_eod_print_copies": ("eod_copies", 1, 1, MAX_COPIES),
	"imin_eod_copy_delay_ms": ("eod_copy_delay_ms", 800, 0, MAX_COPY_DELAY_MS),
	"imin_eod_feed_dots": ("eod_feed_dots", 160, 8, MAX_FEED_DOTS),
	"imin_eod_tail_dots": ("eod_tail_dots", 24, 0, MAX_TAIL_DOTS),
	"imin_eod_font_scale": ("eod_font_scale", 100, 60, 250),
	"imin_eod_line_spacing": ("eod_line_spacing", 100, MIN_LINE_SPACING, MAX_LINE_SPACING),
	"imin_eod_side_margin": ("eod_side_margin", 16, 0, MAX_SIDE_MARGIN_DOTS),
	"imin_eod_top_margin": ("eod_top_margin", 0, 0, MAX_TOP_MARGIN_DOTS),
}

# Copy counts treat 0 as unset ("or default"); every other knob keeps a
# deliberate 0.
_OR_DEFAULT_KNOBS = {"imin_print_copies", "imin_eod_print_copies"}

_SELECT_KNOBS = {
	"print_driver": ("browser", "qz", "imin"),
	"imin_paper_width": ("58mm", "80mm", "custom"),
}
_CHECK_KNOBS = ("imin_cut_paper", "imin_crew_slip_enabled", "print_fallback_enabled")


def _get_setting(settings, fieldname):
	if isinstance(settings, dict):
		return settings.get(fieldname)
	return getattr(settings, fieldname, None)


def _clamp_int(value, default, lo, hi):
	"""default on None/garbage, then clamp into [lo, hi]."""
	try:
		n = default if value is None else int(value)
	except (TypeError, ValueError):
		n = default
	return max(lo, min(n, hi))


def _normalized_print_config(settings):
	"""Clamped transport config from a POS Settings row (or an all-None dict).

	Bounds come from _INT_KNOBS so the read and write paths can never drift
	apart. Percent knobs are relative to the CSS as authored at 96 DPI
	(100 = unchanged); the line-spacing band is deliberately narrow because
	50% starts colliding lines and 150% wastes paper.
	"""
	out = {}
	for fieldname, (key, default, lo, hi) in _INT_KNOBS.items():
		raw = _get_setting(settings, fieldname)
		if fieldname in _OR_DEFAULT_KNOBS:
			raw = raw or default
		out[key] = _clamp_int(raw, default, lo, hi)

	raw_fallback = _get_setting(settings, "print_fallback_enabled")
	return {
		"driver": _get_setting(settings, "print_driver") or "browser",
		"paper": _get_setting(settings, "imin_paper_width") or "58mm",
		"custom_dots": _get_setting(settings, "imin_custom_dots") or 384,
		"cut": bool(_get_setting(settings, "imin_cut_paper")),
		"crew_slip_enabled": bool(_get_setting(settings, "imin_crew_slip_enabled")),
		# Check defaulting to 1 means "unset is enabled": when the row is
		# missing or NULL, fallback stays on so a broken iMin/QZ chain still
		# reaches the browser driver. Only an explicit 0 disables it. `cut`
		# keeps the strict bool() — cutting on an uncut-capable printer is
		# worse than not cutting.
		"fallback_enabled": True if raw_fallback is None else bool(raw_fallback),
		**out,
	}


def _resolve_settings_row(pos_profile):
	"""(resolved_profile, effective_settings_or_None) with the meta guard applied.

	The meta guard keeps the endpoint answering transport defaults on a site
	whose POS Settings print columns have not migrated yet, instead of raising
	pymysql's Unknown column error. Without a profile the newest enabled row is
	probed first; with neither an enabled row nor a profile, the resolver's
	global single tier answers.
	"""
	resolved_profile = pos_profile
	if not resolved_profile:
		resolved_profile = frappe.db.get_value(
			"POS Settings", {"enabled": 1}, "pos_profile", order_by="modified desc"
		)

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
		settings = get_effective_pos_settings(resolved_profile, fields)
	else:
		settings = None

	return resolved_profile, settings


@frappe.whitelist()
def get_print_config(pos_profile):
	"""Resolve the print configuration for a POS Profile.

	A null/empty pos_profile is a supported caller state, not an error: the
	Direct Print diagnostic page has no shift/invoice in context (no open
	shift -> bootstrap returns pos_profile: None). Fall back to the first
	enabled POS Settings row on the site, then to pure transport defaults.
	The response reports which profile was actually used via `pos_profile`,
	so callers (and logs) can see when the fallback fired.
	"""
	resolved_profile, settings = _resolve_settings_row(pos_profile)
	if not settings:
		settings = {field: None for field in PRINT_CONFIG_FIELDS}

	cfg = _normalized_print_config(settings)
	cfg["pos_profile"] = resolved_profile
	return cfg


@frappe.whitelist()
def update_print_config(pos_profile, config):
	"""Write print knobs for a POS Profile. The Direct Print page is the UI.

	Only PRINT_CONFIG_FIELDS are accepted; every value is clamped through the
	same table the read path uses before it is stored. Permission gate matches
	update_pos_settings: a user assigned to the profile, or POS Settings write
	permission. Returns the fresh resolved config for the caller's transport.
	"""
	from json import JSONDecodeError, loads

	if isinstance(config, str):
		try:
			config = loads(config)
		except (JSONDecodeError, ValueError):
			frappe.throw(_("Invalid print config payload"))
	if not isinstance(config, dict):
		config = {}

	if not pos_profile:
		frappe.throw(
			_("No POS Profile in context — open a shift on this device before editing print settings.")
		)

	has_access = frappe.db.exists(
		"POS Profile User", {"parent": pos_profile, "user": frappe.session.user}
	)
	if not has_access and not frappe.has_permission("POS Settings", "write"):
		frappe.throw(_("You don't have permission to update print settings for this POS Profile"))

	updates = {}
	for key, value in config.items():
		if key in _INT_KNOBS:
			# not `_`: a bare throwaway shadows the module-level gettext `_`
			# and turns every later frappe.throw(_(...)) into UnboundLocalError
			transport_key, default, lo, hi = _INT_KNOBS[key]
			if key in _OR_DEFAULT_KNOBS:
				value = value or default
			updates[key] = _clamp_int(value, default, lo, hi)
		elif key == "imin_custom_dots":
			# 0 is stored as "unset"; the read path answers the 384 default.
			updates[key] = _clamp_int(value, 384, 0, 576)
		elif key in _SELECT_KNOBS:
			if value in _SELECT_KNOBS[key]:
				updates[key] = value
		elif key in _CHECK_KNOBS:
			updates[key] = 1 if value in (True, 1, "1") else 0

	if not updates:
		frappe.throw(_("No valid print settings in the request"))

	existing = frappe.db.exists("POS Settings", {"pos_profile": pos_profile})
	if existing:
		doc = frappe.get_doc("POS Settings", existing)
	else:
		doc = frappe.new_doc("POS Settings")
		doc.pos_profile = pos_profile
		doc.enabled = 1
	doc.update(updates)
	doc.save()

	return get_print_config(pos_profile)


@frappe.whitelist()
def get_latest_closing_shift():
	"""Name of the most recent submitted POS Closing Shift, or None."""
	names = frappe.get_all(
		"POS Closing Shift",
		filters={"docstatus": 1},
		order_by="creation desc",
		limit_page_length=1,
		pluck="name",
	)
	return names[0] if names else None


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
