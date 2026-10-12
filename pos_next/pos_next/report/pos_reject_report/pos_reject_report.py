# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

"""POS Reject Report — HO verdict ledger for decided reject requests.

Per Request: one row per decided request with the liable crew comma-joined.
Per Liability: one row per POS Reject Liability row, plus one "Company" row
per Approved request (the company bears it). Two queries max: the request
window, then the liability rows of those requests."""

import frappe
from frappe import _
from frappe.utils import flt, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	requests = get_requests(filters)
	liabilities = get_liabilities([r.name for r in requests])
	if filters.get("user"):
		# liable-crew filter: keep only requests where that user carries a row
		requests = [r for r in requests if any(l.parent == r.name and l.user == filters.user for l in liabilities)]
	liab_rows = liability_rows(requests, liabilities)
	per_liability = filters.view == "Per Liability"
	if per_liability and filters.get("user"):
		liab_rows = [r for r in liab_rows if r.bearer == filters.user]
	data = liab_rows if per_liability else request_rows(requests, liabilities)
	return get_columns(per_liability), data, None, get_chart(liab_rows), get_summary(liab_rows)


def get_requests(filters):
	conds = ["docstatus = 1", "ifnull(decision, '') != ''"]
	params = {
		"from_date": getdate(filters.from_date or nowdate()),
		"to_date": getdate(filters.to_date or nowdate()),
	}
	conds.append("incident_date between %(from_date)s and %(to_date)s")
	for key in ("pos_profile", "purpose", "decision"):
		if filters.get(key):
			conds.append(f"{key} = %({key})s")
			params[key] = filters[key]
	rows = frappe.db.sql(
		f"""
		select name, incident_date, pos_profile, purpose, status, decision,
			total_amount, owner, decided_by, closed_on
		from `tabPOS Reject Request`
		where {" and ".join(conds)}
		order by incident_date desc, name desc
		""",
		params,
		as_dict=True,
	)
	return rows


def get_liabilities(names):
	if not names:
		return []
	return frappe.get_all(
		"POS Reject Liability",
		filters={"parent": ["in", names], "parentfield": "liabilities"},
		fields=["parent", "user", "amount", "idx"],
		order_by="parent asc, idx asc",
	)


def liability_rows(requests, liabilities):
	by_parent = {}
	for l in liabilities:
		by_parent.setdefault(l.parent, []).append(l)
	rows = []
	for r in requests:
		if r.decision == "Approved":
			rows.append(_row(r, "Company", r.total_amount))
		for l in by_parent.get(r.name, []):
			rows.append(_row(r, l.user, l.amount))
	return rows


def _row(r, bearer, amount):
	return frappe._dict(
		request=r.name,
		incident_date=r.incident_date,
		pos_profile=r.pos_profile,
		purpose=r.purpose,
		decision=r.decision,
		bearer=bearer,
		amount=flt(amount),
	)


def request_rows(requests, liabilities):
	by_parent = {}
	for l in liabilities:
		by_parent.setdefault(l.parent, []).append(l.user)
	rows = []
	for r in requests:
		r = frappe._dict(r)
		r.request = r.name
		r.liable_crew = ", ".join(by_parent.get(r.name, []))
		r.total_amount = flt(r.total_amount)
		rows.append(r)
	return rows


def get_columns(per_liability):
	if per_liability:
		return [
			{"fieldname": "request", "label": _("Request"), "fieldtype": "Link", "options": "POS Reject Request", "width": 180},
			{"fieldname": "incident_date", "label": _("Incident Date"), "fieldtype": "Date", "width": 100},
			{"fieldname": "pos_profile", "label": _("Outlet"), "fieldtype": "Link", "options": "POS Profile", "width": 160},
			{"fieldname": "purpose", "label": _("Purpose"), "fieldtype": "Link", "options": "POS Reject Purpose", "width": 130},
			{"fieldname": "decision", "label": _("Decision"), "fieldtype": "Data", "width": 90},
			{"fieldname": "bearer", "label": _("Bearer"), "fieldtype": "Data", "width": 200},
			{"fieldname": "amount", "label": _("Amount"), "fieldtype": "Currency", "width": 130},
		]
	return [
		{"fieldname": "request", "label": _("Request"), "fieldtype": "Link", "options": "POS Reject Request", "width": 180},
		{"fieldname": "incident_date", "label": _("Incident Date"), "fieldtype": "Date", "width": 100},
		{"fieldname": "pos_profile", "label": _("Outlet"), "fieldtype": "Link", "options": "POS Profile", "width": 160},
		{"fieldname": "purpose", "label": _("Purpose"), "fieldtype": "Link", "options": "POS Reject Purpose", "width": 130},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 90},
		{"fieldname": "decision", "label": _("Decision"), "fieldtype": "Data", "width": 90},
		{"fieldname": "total_amount", "label": _("Total Amount"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "liable_crew", "label": _("Liable Crew"), "fieldtype": "Data", "width": 200},
		{"fieldname": "decided_by", "label": _("Decided By"), "fieldtype": "Link", "options": "User", "width": 180},
		{"fieldname": "closed_on", "label": _("Closed On"), "fieldtype": "Datetime", "width": 140},
	]


def get_summary(liab_rows):
	company = sum(r.amount for r in liab_rows if r.bearer == "Company")
	crew = sum(r.amount for r in liab_rows if r.bearer != "Company")
	requests = len({r.request for r in liab_rows})
	return [
		{"value": company, "label": _("Borne by Company"), "datatype": "Currency", "indicator": "Blue"},
		{"value": crew, "label": _("Borne by Crew"), "datatype": "Currency", "indicator": "Red"},
		{"value": requests, "label": _("Requests"), "datatype": "Int", "indicator": "Purple"},
	]


def get_chart(liab_rows):
	if not liab_rows:
		return None
	by_outlet = {}
	for r in liab_rows:
		b = by_outlet.setdefault(r.pos_profile, {"Company": 0.0, "Crew": 0.0})
		b["Company" if r.bearer == "Company" else "Crew"] += r.amount
	outlets = sorted(by_outlet)
	return {
		"data": {
			"labels": outlets,
			"datasets": [
				{"name": _("Company"), "values": [round(by_outlet[o]["Company"], 2) for o in outlets]},
				{"name": _("Crew"), "values": [round(by_outlet[o]["Crew"], 2) for o in outlets]},
			],
		},
		"type": "bar",
		"colors": ["#3b82f6", "#ef4444"],
		"fieldtype": "Currency",
		"height": 260,
	}
