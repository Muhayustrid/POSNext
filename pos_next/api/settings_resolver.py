"""Effective POS Settings resolution: profile row first, global single second.

An enabled POS Settings row always wins whole (no per-field fallback). Without
one, each field falls back to the POS Next Global Settings single, and fields
never persisted there fall back to the single's meta defaults.
"""

import frappe
from frappe.utils import cint, flt

GLOBAL_DOCTYPE = "POS Next Global Settings"

GLOBAL_FIELDS = (
	"invoice_type",
	"monthly_target_basis",
	"overall_target_basis",
	"allow_negative_stock",
	"allowed_locales",
)

_NON_DATA_TYPES = frozenset(
	("Tab Break", "Section Break", "Column Break", "Table", "Table MultiSelect", "HTML", "Button", "Heading", "Fold")
)

_FLOAT_TYPES = frozenset(("Float", "Currency", "Percent"))


def _field_meta(fieldname):
	return frappe.get_meta(GLOBAL_DOCTYPE).get_field(fieldname)


def _coerce(df, value):
	if df.fieldtype in ("Check", "Int"):
		return cint(value)
	if df.fieldtype in _FLOAT_TYPES:
		return flt(value)
	return value


def _persisted_global_values():
	# Deliberately NOT cached on frappe.local: tests (and scripts) write the
	# single via frappe.db.set_single_value/set_value, which invalidates no
	# request cache, and a stale tier-2 read silently serves the meta default
	# instead of the persisted value for the rest of the request.
	return dict(
		frappe.db.sql(
			"select field, value from `tabSingles` where doctype = %s",
			GLOBAL_DOCTYPE,
		)
	)


def _mirror_fields():
	cache = getattr(frappe.local, "_pos_next_mirror_fields", None)
	if cache is None:
		cache = frappe.local._pos_next_mirror_fields = [
			df.fieldname
			for df in frappe.get_meta(GLOBAL_DOCTYPE).fields
			if df.fieldtype not in _NON_DATA_TYPES and df.fieldname not in GLOBAL_FIELDS
		]
	return cache


def _global_value(fieldname):
	"""Tier-2/3 resolution for one field, straight from the global single."""
	df = _field_meta(fieldname)
	if df is None:
		return None
	persisted = _persisted_global_values()
	if fieldname in persisted:
		return _coerce(df, persisted[fieldname])
	if df.fieldtype == "Link" and not df.default:
		return None
	return _coerce(df, df.default)


def _enabled_row(pos_profile, fields):
	if not pos_profile:
		return None
	return frappe.db.get_value(
		"POS Settings", {"pos_profile": pos_profile, "enabled": 1}, fields, as_dict=True
	)


def get_effective_pos_setting(pos_profile, fieldname):
	"""Effective value of one POS Settings field for a POS Profile.

	An enabled row wins as-is (None included); otherwise the global single's
	persisted value, else the single's meta default.
	"""
	row = _enabled_row(pos_profile, ["name", fieldname])
	if row:
		return row.get(fieldname)
	return _global_value(fieldname)


def get_effective_pos_settings(pos_profile, fields=None):
	"""Effective POS Settings dict for a POS Profile.

	The enabled row's values for the requested fields (all mirror fields when
	`fields` is None), else the global fallback per field. Both tiers carry
	"enabled": 1; the global tier also carries "pos_profile".
	"""
	names = list(fields) if fields is not None else _mirror_fields()
	row = _enabled_row(pos_profile, "*")
	if row:
		settings = {name: row.get(name) for name in names}
		settings["enabled"] = 1
		return settings

	settings = {name: _global_value(name) for name in names}
	settings["enabled"] = 1
	settings["pos_profile"] = pos_profile
	return settings


def get_global_barcode_rules(pos_profile=None):
	"""Barcode rules for a POS Profile, else the global single's, else empty."""
	name = frappe.db.get_value("POS Settings", {"pos_profile": pos_profile, "enabled": 1}) if pos_profile else None
	parent = name or GLOBAL_DOCTYPE
	return frappe.get_all(
		"POS Barcode Rules",
		filters={"parent": parent, "parenttype": "POS Settings" if name else GLOBAL_DOCTYPE},
		fields=["name", "barcode_rule", "disable"],
	)
