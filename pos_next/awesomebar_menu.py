import frappe
from frappe.desk.doctype.sidebar.sidebar import doctype_landing_route, resolve_sidebar
from frappe.utils import slug


def search(txt):
	term = (txt or "").strip().casefold()
	if not term:
		return []
	try:
		sidebar = resolve_sidebar("POSNext", frappe.session.user)
	except KeyError:
		return []
	if not sidebar:
		return []

	results = []
	for item in sidebar.items:
		label = item.get("label") or ""
		if item.get("type") != "Link" or term not in label.casefold():
			continue
		route = _route(item)
		if route:
			results.append({"label": label, "route": route, "index": 90})
	return results


def _route(item):
	kind, name = item.get("link_type"), item.get("link_to")
	if kind == "URL":
		return item.get("url")
	if not name:
		return None
	if kind == "DocType":
		return doctype_landing_route(item)
	if kind == "Page":
		return f"/desk/{name}"
	if kind == "Workspace":
		return f"/desk/{slug(name)}"
	if kind == "Report":
		report = item.get("report") or {}
		if report.get("report_type") in ("Query Report", "Script Report"):
			return f"/desk/query-report/{name}"
		if report.get("ref_doctype"):
			return f"/desk/{slug(report['ref_doctype'])}/view/report/{name}"
	return None
