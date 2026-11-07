# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""Cadence Manual helpers (§4.8): scope matching, reference validation, shipment key."""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, getdate, now_datetime, today

from pos_next.services.outlet_monitoring import profile_in_scope, template_companies


def _open_shift_slot(pos_profile: str):
	"""Return the shift_slot of the open shift for this profile, or None."""
	shift_name = frappe.db.get_value(
		"POS Opening Shift", {"pos_profile": pos_profile, "status": "Open"}, "name"
	)
	if not shift_name:
		return None
	return frappe.db.get_value("POS Opening Shift", shift_name, "shift_slot")


def manual_template_matches(t_doc, pos_profile: str) -> bool:
	"""Check Manual template scope: company, profile, shift slot (§4.8; fail closed)."""
	company = frappe.db.get_value("POS Profile", pos_profile, "company")
	if not company or company not in template_companies(t_doc):
		return False
	if not profile_in_scope(t_doc, pos_profile):
		return False
	if getattr(t_doc, "shift_type_scope", None) != "Selected":
		return True
	slot = _open_shift_slot(pos_profile)
	if not slot:
		return False
	child = getattr(t_doc, "shift_types", None) or []
	return slot in {r.shift_type for r in child}


def _dn_rules_ok(dn_name: str, company: str, *, check_age: bool = True):
	"""Return the DN doc if it passes the inter-company shipment rules, else None."""
	dn = frappe.db.get_value(
		"Delivery Note",
		dn_name,
		[
			"name",
			"docstatus",
			"is_return",
			"is_internal_customer",
			"represents_company",
			"company",
			"posting_date",
		],
		as_dict=True,
	)
	if not dn:
		return None
	if (
		cint(dn.docstatus) != 1
		or cint(dn.is_return)
		or not cint(dn.is_internal_customer)
		or dn.represents_company != company
		or dn.company == company
	):
		return None
	if check_age and not _within_window(dn.posting_date):
		return None
	return dn


def _within_window(posting_date) -> bool:
	"""posting_date must satisfy today - 7 <= date <= today."""
	if not posting_date:
		return False
	day = getdate(posting_date)
	today_date = getdate(today())
	return getdate(add_to_date(today_date, days=-7)) <= day <= today_date


def _pr_rules_ok(pr_name: str, company: str):
	"""Return (doctype, name) normalized key target if the PR passes the rules, else None."""
	pr = frappe.db.get_value(
		"Purchase Receipt",
		pr_name,
		[
			"name",
			"docstatus",
			"is_return",
			"is_internal_supplier",
			"company",
			"inter_company_reference",
			"posting_date",
		],
		as_dict=True,
	)
	if not pr:
		return None
	if cint(pr.docstatus) != 1 or cint(pr.is_return) or pr.company != company:
		return None
	if not _within_window(pr.posting_date):
		return None
	if not cint(pr.is_internal_supplier):
		return ("Purchase Receipt", pr.name)
	# Internal PR: must point at one qualifying DN through every item.
	dn_name = pr.inter_company_reference
	if not dn_name:
		return None
	dn = _dn_rules_ok(dn_name, company, check_age=False)
	if not dn:
		return None
	items = frappe.get_all(
		"Purchase Receipt Item",
		filters={"parent": pr.name},
		fields=["delivery_note_item"],
	)
	if not items or not all(r.delivery_note_item for r in items):
		return None
	parents = {frappe.db.get_value("Delivery Note Item", r.delivery_note_item, "parent") for r in items}
	if parents != {dn.name}:
		return None
	return ("Delivery Note", dn.name)


def shipment_key(doctype: str, name: str, company: str, *, check_age: bool = True):
	"""Validate the reference and return the normalized (doctype, name) key (§4.8)."""
	if doctype == "Delivery Note":
		dn = _dn_rules_ok(name, company, check_age=check_age)
		if not dn:
			frappe.throw(_("Delivery Note {0} is not a valid reference").format(name), frappe.ValidationError)
		return ("Delivery Note", dn.name)
	if doctype == "Purchase Receipt":
		key = _pr_rules_ok(name, company)
		if not key:
			frappe.throw(
				_("Purchase Receipt {0} is not a valid reference").format(name), frappe.ValidationError
			)
		return key
	frappe.throw(_("Reference must be a Purchase Receipt or Delivery Note"), frappe.ValidationError)


def manual_obligation_key(template: str, doctype: str, name: str) -> str:
	"""Obligation key for a Manual report: `{template}|manual|{doctype}|{name}`."""
	return f"{template}|manual|{doctype}|{name}"


CANDIDATE_LIMIT = 20


def _reported_keys(template: str) -> set[str]:
	"""Obligation keys already reported for this template (revision 1)."""
	return set(
		frappe.get_all(
			"POS Monitoring Report",
			filters={"obligation_key": ["like", f"{template}|manual|%"], "revision": 1},
			pluck="obligation_key",
		)
	)


def _count_items(child: str, names: list[str]) -> dict:
	if not names:
		return {}
	rows = frappe.db.sql(
		f"select parent, count(name) as cnt from `tab{child}` where parent in %(names)s group by parent",
		{"names": tuple(names)},
		as_dict=True,
	)
	return {r.parent: r.cnt for r in rows}


def reference_candidates(company: str, template: str, txt: str | None = None) -> list[dict]:
	"""List unreported PR/DN references for a Manual template (§4.8, allowlist fields).

	The 7-day window is part of the query and the cap of CANDIDATE_LIMIT applies
	to eligible rows, so already reported / rule-rejected documents never use up
	a slot. Search matches the document name OR the sender (§4.8).
	"""
	search = f"%{txt}%" if txt else None
	reported = _reported_keys(template)
	window_start = getdate(add_to_date(getdate(today()), days=-7))

	def _append(out, doctype, name, posting_date, sender, key_target):
		key = manual_obligation_key(template, *key_target)
		if key in reported:
			return False
		out.append(
			{
				"doctype": doctype,
				"name": name,
				"posting_date": str(posting_date),
				"sender": sender,
			}
		)
		return True

	out = []
	pr_filters: dict = {
		"docstatus": 1,
		"is_return": 0,
		"company": company,
		"posting_date": [">=", window_start],
	}
	pr_rows = frappe.get_all(
		"Purchase Receipt",
		filters=pr_filters,
		or_filters=[["name", "like", search], ["supplier_name", "like", search]] if search else None,
		fields=["name", "posting_date", "supplier_name", "is_internal_supplier"],
		order_by="posting_date desc",
	)
	pr_picked = []
	for hdr in pr_rows:
		pr_key = _pr_rules_ok(hdr.name, company)
		if not pr_key:
			continue
		if _append(pr_picked, "Purchase Receipt", hdr.name, hdr.posting_date, hdr.supplier_name, pr_key):
			if len(pr_picked) >= CANDIDATE_LIMIT:
				break
	pr_counts = _count_items("Purchase Receipt Item", [c["name"] for c in pr_picked])
	for cand in pr_picked:
		cand["item_count"] = pr_counts.get(cand["name"], 0)
	out.extend(pr_picked)

	dn_filters: dict = {
		"docstatus": 1,
		"is_return": 0,
		"is_internal_customer": 1,
		"represents_company": company,
		"company": ["!=", company],
		"posting_date": [">=", window_start],
	}
	dn_rows = frappe.get_all(
		"Delivery Note",
		filters=dn_filters,
		or_filters=[["name", "like", search], ["company", "like", search]] if search else None,
		fields=["name", "posting_date", "company"],
		order_by="posting_date desc",
	)
	dn_picked = []
	for hdr in dn_rows:
		if not _dn_rules_ok(hdr.name, company):
			continue
		if _append(
			dn_picked, "Delivery Note", hdr.name, hdr.posting_date, hdr.company, ("Delivery Note", hdr.name)
		):
			if len(dn_picked) >= CANDIDATE_LIMIT:
				break
	dn_counts = _count_items("Delivery Note Item", [c["name"] for c in dn_picked])
	for cand in dn_picked:
		cand["item_count"] = dn_counts.get(cand["name"], 0)
	out.extend(dn_picked)

	return out
