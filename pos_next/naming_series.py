# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Configurable naming series for POS Next documents (global-only).

Three Data fields on POS Next Global Settings (registered in GLOBAL_FIELDS,
so they are never mirrored to per-profile rows) hold a Frappe naming-series
pattern, e.g. ``INV-.YYYY.-.#####``. An empty value keeps each doctype's meta
autoname. Applied inside each controller's ``autoname()`` hook — which
set_new_name runs before the meta rule — so every creation path (checkout,
offline sync, returns, Desk) is covered without touching the callers.
"""

import frappe
from frappe.model.naming import make_autoname

from pos_next.api.settings_resolver import GLOBAL_DOCTYPE

NAMING_SERIES_FIELDS = {
	"POS Invoice": "pos_invoice_naming_series",
	"POS Opening Shift": "pos_opening_shift_naming_series",
	"POS Closing Shift": "pos_closing_shift_naming_series",
}


def apply_naming_series_setting(doc):
	"""Name ``doc`` from the global naming-series setting; leave unset otherwise.

	Called from the controller's ``autoname()`` hook, which the framework runs
	only while the name is still unset and before meta autoname. A pattern
	without a series placeholder gets Frappe's default ``.#####`` padding, so
	uniqueness never depends on the admin remembering the syntax.
	"""
	fieldname = NAMING_SERIES_FIELDS[doc.doctype]
	series = (frappe.db.get_single_value(GLOBAL_DOCTYPE, fieldname) or "").strip()
	if not series:
		return
	if "#" not in series:
		series += ".#####"
	doc.name = make_autoname(series, doc.doctype, doc)
