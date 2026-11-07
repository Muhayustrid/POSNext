# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

import frappe

from pos_next.hq_scope import get_permitted_companies
from pos_next.outlet_monitoring_setup import REVIEWER_ROLE


def cashier_profiles(user=None) -> list[str]:
	"""Return POS Profile names where user is assigned in POS Profile User."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return []
	return frappe.get_all("POS Profile User", filters={"user": user}, pluck="parent")


def cashier_companies(user=None) -> list[str]:
	"""Return unique companies of the POS Profiles the cashier is assigned to."""
	profiles = cashier_profiles(user)
	if not profiles:
		return []
	companies = frappe.get_all("POS Profile", filters={"name": ["in", profiles]}, pluck="company")
	return sorted(set(filter(None, companies)))


def is_reviewer(user=None) -> bool:
	"""Check if user has reviewer role, is System Manager, or is Administrator."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if user == "Administrator":
		return True
	roles = frappe.get_roles(user)
	return bool({"System Manager", REVIEWER_ROLE} & set(roles))


def reviewer_companies(user=None) -> list[str] | None:
	"""Return permitted companies for reviewer based on User Permissions (None = unrestricted)."""
	return get_permitted_companies(user)


def get_permission_query_conditions(user=None) -> str:
	user = user or frappe.session.user
	if user == "Administrator":
		return ""

	if is_reviewer(user):
		permitted = reviewer_companies(user)
		if permitted is None:
			return ""
		if not permitted:
			return "1=0"
		escaped = ", ".join(frappe.db.escape(c) for c in permitted)
		return f"`tabPOS Monitoring Report`.company in ({escaped})"

	# Cashier / outlet user
	companies = cashier_companies(user)
	if not companies:
		return "1=0"
	escaped = ", ".join(frappe.db.escape(c) for c in companies)
	return f"`tabPOS Monitoring Report`.company in ({escaped})"


def has_permission(doc, ptype=None, user=None) -> bool:
	user = user or frappe.session.user
	if user == "Administrator":
		return True

	ptype = (ptype or "read").lower()
	if ptype == "cancel":
		return False

	if isinstance(doc, str):
		doc = frappe.get_doc("POS Monitoring Report", doc)

	company = doc.get("company")
	profile = doc.get("pos_profile")
	cadence = doc.get("cadence")
	docstatus = doc.get("docstatus", 0)
	status = doc.get("status")

	doc_name = doc.get("name")
	if doc_name and not getattr(doc, "__islocal", False):
		stored = frappe.db.get_value(
			"POS Monitoring Report",
			doc_name,
			["company", "pos_profile", "cadence", "docstatus", "status"],
			as_dict=True,
		)
		if stored:
			if ptype == "write" and (
				(doc.get("company") and doc.get("company") != stored.company)
				or (doc.get("pos_profile") and doc.get("pos_profile") != stored.pos_profile)
			):
				return False
			company = stored.company
			profile = stored.pos_profile
			cadence = stored.cadence
			docstatus = stored.docstatus
			status = stored.status

	if is_reviewer(user):
		permitted = reviewer_companies(user)
		if permitted is None:
			return True
		return bool(company and company in permitted)

	# Cashier / outlet user
	c_companies = cashier_companies(user)
	if not (company and company in c_companies):
		return False

	if ptype in ("read", "select"):
		return True

	if ptype == "write":
		if docstatus != 0 or status != "Draft":
			return False
		if cadence == "Per Shift":
			return bool(profile and profile in cashier_profiles(user))
		elif cadence == "Per Hari":
			return True
		elif cadence == "Manual":
			return bool(profile and profile in cashier_profiles(user))
		return False

	if ptype == "create":
		return True

	return False
