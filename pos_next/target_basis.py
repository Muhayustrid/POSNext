"""Global target-basis resolution for POS Next (see spec 2026-09-22)."""

import frappe

NET_SALES = "Net Sales"
GROSS_PROFIT = "Gross Profit"
NET_PROFIT = "Net Profit"
TARGET_BASES = (NET_SALES, GROSS_PROFIT, NET_PROFIT)

# Server-side label per basis (Indonesian UI); consumed by the HQ dashboard
# payload's target_basis labels.
TARGET_BASIS_LABELS = {
	NET_SALES: "Omzet",
	GROSS_PROFIT: "Laba Kotor",
	NET_PROFIT: "Laba Bersih",
}

_FIELDS = {"monthly": "monthly_target_basis", "overall": "overall_target_basis"}


def get_target_basis(which):
	"""The metric targets are measured against, for "monthly" or "overall".

	Stored on the POS Next Global Settings single - the site-wide choice.
	Invalid or empty values fall back to Net Sales (the behaviour before
	this switch existed).
	"""
	if which not in _FIELDS:
		raise ValueError(f"unknown target basis slot: {which!r}")
	cache = getattr(frappe.local, "_pos_next_target_bases", None)
	if cache is None:
		cache = frappe.local._pos_next_target_bases = {}
	cached = cache.get(which)
	if cached:
		return cached
	value = frappe.db.get_single_value("POS Next Global Settings", _FIELDS[which]) or NET_SALES
	if value not in TARGET_BASES:
		value = NET_SALES
	cache[which] = value
	return value


def validate_target_bases(doc):
	"""Validate hook (POS Next Global Settings): keep both basis values valid.

	Empty is normalised to Net Sales (the default); anything else invalid is
	rejected.
	"""
	from frappe import _

	labels = {
		"monthly_target_basis": "Monthly Target Basis",
		"overall_target_basis": "Overall (Payback) Target Basis",
	}
	for fieldname, label in labels.items():
		value = doc.get(fieldname)
		if not value:
			doc.set(fieldname, NET_SALES)
		elif value not in TARGET_BASES:
			frappe.throw(
				_("{0} must be Net Sales, Gross Profit, or Net Profit.").format(_(label))
			)
