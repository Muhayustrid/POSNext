# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Migrate legacy Pass/Fail/N-A texts to snapshot labels (§6.2).

Idempotent: only report rows with an empty `label_options` are touched,
Category / Settings tables are only filled when empty, existing Label
masters are never changed, and review / scope values set by users survive
reruns. Legacy columns are read via SQL only when they still exist; on a
site without monitoring data only the Settings fill runs.
"""

import json

import frappe

from pos_next.outlet_monitoring_setup import DEFAULT_LABELS

_STATUS_TO_SLOT = {"Pass": 0, "Fail": 1, "N/A": 2}
_SLOT_COLORS = ("Green", "Red", "Gray")
_LEGACY_COLUMNS = ("pass_label", "fail_label", "na_label")
_SCOPE_CHILDREN = {
	"profile_scope": "POS Monitoring Template POS Profile",
	"shift_type_scope": "POS Monitoring Template Shift Type",
}


def _norm(text):
	return (text or "").strip()


def _dedupe_keep_order(texts):
	seen = set()
	out = []
	for text in texts:
		key = _norm(text).lower()
		if not key or key in seen:
			continue
		seen.add(key)
		out.append(_norm(text))
	return out


def _label_id_by_norm():
	"""Existing label name keyed by lower(trimmed name)."""
	by_norm = {}
	for name in frappe.get_all("POS Monitoring Label", pluck="name"):
		key = _norm(name).lower()
		if key and key not in by_norm:
			by_norm[key] = name
	return by_norm


def _ensure_label(text, slot, by_norm):
	name = _norm(text)
	if not name:
		return None
	if name.lower() in by_norm:
		return by_norm[name.lower()]
	doc = frappe.get_doc(
		{
			"doctype": "POS Monitoring Label",
			"label_name": name,
			"color": _SLOT_COLORS[slot],
		}
	)
	doc.insert(ignore_permissions=True)
	by_norm[name.lower()] = doc.name
	return doc.name


def execute():
	by_norm = _label_id_by_norm()

	# 1. Collect legacy texts (SQL, only where the old columns survived).
	category_texts = {}
	if frappe.db.table_exists("POS Monitoring Category"):
		has_legacy = all(frappe.db.has_column("POS Monitoring Category", c) for c in _LEGACY_COLUMNS)
		for row in frappe.get_all("POS Monitoring Category", fields=["name"]):
			if has_legacy:
				stored = frappe.db.get_value(
					"POS Monitoring Category", row.name, list(_LEGACY_COLUMNS), as_dict=True
				)
				category_texts[row.name] = (
					_norm(stored.pass_label),
					_norm(stored.fail_label),
					_norm(stored.na_label),
				)
			else:
				category_texts[row.name] = ("", "", "")

	# Settings is a Single: no table, and the legacy fields are gone from meta,
	# so get_single_value / has_column cannot see them. Read tabSingles directly.
	legacy_settings = dict(
		frappe.db.sql(
			"select field, value from tabSingles where doctype = 'POS Monitoring Settings' "
			"and field in ('default_pass_label', 'default_fail_label', 'default_na_label')"
		)
		or []
	)
	settings_texts = tuple(
		_norm(legacy_settings.get(f"default_{slot}_label")) for slot in ("pass", "fail", "na")
	)

	report_rows = []
	if frappe.db.table_exists("POS Monitoring Report Checkpoint"):
		has_legacy = all(
			frappe.db.has_column("POS Monitoring Report Checkpoint", c)
			for c in (*_LEGACY_COLUMNS, "assessment")
		)
		if has_legacy:
			report_rows = frappe.db.sql(
				"select name, category, pass_label, fail_label, na_label, assessment "
				"from `tabPOS Monitoring Report Checkpoint` "
				"where ifnull(label_options, '') = ''",
				as_dict=True,
			)
		else:
			report_rows = frappe.db.sql(
				"select name, category from `tabPOS Monitoring Report Checkpoint` "
				"where ifnull(label_options, '') = ''",
				as_dict=True,
			)
		for row in report_rows:
			row.pass_label = _norm(row.get("pass_label"))
			row.fail_label = _norm(row.get("fail_label"))
			row.na_label = _norm(row.get("na_label"))

	# 2. Create one master Label per unique text; slot priority pass -> fail -> na.
	for slot in range(3):
		trios = [
			*category_texts.values(),
			settings_texts,
			*((r.pass_label, r.fail_label, r.na_label) for r in report_rows),
		]
		for trio in trios:
			if trio[slot]:
				_ensure_label(trio[slot], slot, by_norm)

	# 3a. Category tables: fill only when empty (pass, fail, na order).
	for name, trio in category_texts.items():
		existing = frappe.get_all(
			"POS Monitoring Label Item",
			filters={"parenttype": "POS Monitoring Category", "parent": name},
			pluck="label",
		)
		if existing:
			continue
		for idx, label in enumerate(
			_dedupe_keep_order(by_norm[t.lower()] for t in trio if _norm(t) and _norm(t).lower() in by_norm),
			1,
		):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Label Item",
					"parent": name,
					"parenttype": "POS Monitoring Category",
					"parentfield": "labels",
					"label": label,
					"idx": idx,
				}
			).db_insert()

	# 3b. Settings defaults: fill only when empty; legacy texts else §6.1 trio.
	settings_rows = frappe.get_all(
		"POS Monitoring Label Item",
		filters={"parenttype": "POS Monitoring Settings"},
		pluck="label",
	)
	if not settings_rows:
		options = _dedupe_keep_order(
			by_norm[t.lower()] for t in settings_texts if _norm(t) and _norm(t).lower() in by_norm
		)
		if not options:
			for label_name, color in DEFAULT_LABELS:
				options.append(_ensure_label(label_name, DEFAULT_LABELS.index((label_name, color)), by_norm))
		for idx, label in enumerate(options, 1):
			frappe.get_doc(
				{
					"doctype": "POS Monitoring Label Item",
					"parent": "POS Monitoring Settings",
					"parenttype": "POS Monitoring Settings",
					"parentfield": "default_labels",
					"label": label,
					"idx": idx,
				}
			).db_insert()
		frappe.clear_document_cache("POS Monitoring Settings", "POS Monitoring Settings")
		settings_rows = frappe.get_all(
			"POS Monitoring Label Item",
			filters={"parenttype": "POS Monitoring Settings"},
			pluck="label",
		)

	# 4. Report checkpoints: snapshot from the row's own legacy texts, else the
	# row's Category table, else the Settings defaults. assessment -> label by
	# slot, but never overwrite a label that is already set.
	grouped = {}
	for row in report_rows:
		texts = (row.pass_label, row.fail_label, row.na_label)
		category_options = []
		if row.get("category"):
			category_options = frappe.get_all(
				"POS Monitoring Label Item",
				filters={"parenttype": "POS Monitoring Category", "parent": row.category},
				fields=["label"],
				order_by="idx",
				pluck="label",
			)
		snapshot = _dedupe_keep_order(
			[by_norm[t.lower()] for t in texts if _norm(t) and _norm(t).lower() in by_norm]
			or category_options
			or settings_rows
		)
		if not snapshot:
			continue
		grouped.setdefault(tuple(snapshot), []).append(row.name)
		if (
			row.get("assessment") in _STATUS_TO_SLOT
			and texts[_STATUS_TO_SLOT[row.assessment]]
			and not frappe.db.get_value("POS Monitoring Report Checkpoint", row.name, "label")
		):
			label = by_norm.get(texts[_STATUS_TO_SLOT[row.assessment]].lower())
			if label:
				frappe.db.set_value(
					"POS Monitoring Report Checkpoint", row.name, "label", label, update_modified=False
				)

	for snapshot, names in grouped.items():
		options = json.dumps(
			[
				{
					"label": label,
					"color": frappe.db.get_value("POS Monitoring Label", label, "color") or "Gray",
				}
				for label in snapshot
			]
		)
		for i in range(0, len(names), 500):
			chunk = names[i : i + 500]
			placeholders = ", ".join(["%s"] * len(chunk))
			frappe.db.sql(
				"update `tabPOS Monitoring Report Checkpoint` set label_options = %s "
				f"where name in ({placeholders}) and ifnull(label_options, '') = ''",
				(options, *chunk),
			)

	# 5. Template scopes: only NULL / empty columns. Company scope is always
	# Selected (§3.3 has no All for companies); profile / shift follow table fill.
	if frappe.db.table_exists("POS Monitoring Template"):
		empty_company = frappe.db.sql(
			"select name from `tabPOS Monitoring Template` where ifnull(company_scope, '') = ''",
			pluck=True,
		)
		for name in empty_company:
			frappe.db.set_value(
				"POS Monitoring Template", name, "company_scope", "Selected", update_modified=False
			)
		for field, child in _SCOPE_CHILDREN.items():
			empty = frappe.db.sql(
				f"select name from `tabPOS Monitoring Template` where ifnull({field}, '') = ''",
				pluck=True,
			)
			for name in empty:
				filled = frappe.db.exists(child, {"parent": name})
				frappe.db.set_value(
					"POS Monitoring Template",
					name,
					field,
					"Selected" if filled else "All",
					update_modified=False,
				)
