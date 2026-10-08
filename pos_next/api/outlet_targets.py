# Copyright (c) 2026, BrainWise and contributors
# For license information, please see license.txt

"""Excel (.xlsx) export / import for the Outlet Targets desk page.

Export writes the same sheet the import reads, so export -> edit -> import
round-trips. Read-only actual columns (MTD, Achievement, ...) are accepted on
import but ignored; only the editable target columns are applied.

Import goes through ``set_outlet_target`` per row, so its permission checks
and "blank keeps stored" rules apply unchanged. Per-row errors are collected,
not raised, so one bad row does not abort the whole upload.
"""

import frappe
from frappe import _
from frappe.utils import get_first_day, nowdate

from pos_next.api.hq_monitoring import _check_hq_access, set_outlet_target

EXPORT_HEADERS = (
	"Company",
	"Currency",
	"Month Start",
	"Monthly Target",
	"MTD Actual",
	"Achievement %",
	"Projection",
	"Balik Modal Target",
	"Balik Modal From",
	"Balik Modal Cumulative",
	"Balik Modal %",
)

# Headers that carry editable targets (indices into EXPORT_HEADERS).
EDITABLE = {"Company": 0, "Month Start": 2, "Monthly Target": 3, "Balik Modal Target": 7, "Balik Modal From": 8}


@frappe.whitelist()
def export_outlet_targets(month_start=None, company=None, search=None):
	"""Download the target sheet as .xlsx (GET /api/method/...)."""
	_check_hq_access()
	from frappe.utils.xlsxutils import build_xlsx_response

	from pos_next.api.hq_monitoring import get_outlet_targets

	out = get_outlet_targets(month_start=month_start, company=company, search=search)
	rows = [list(EXPORT_HEADERS)]
	for r in out["rows"]:
		m = r["monthly"] or {}
		o = r["overall"] or {}
		rows.append(
			[
				r["company"],
				r["currency"],
				out["month_start"],
				"" if m.get("missing") else r["target_value"],
				r["mtd_value"],
				r["achievement_sales_pct"] if not m.get("missing") else "",
				r["projected_value"] if not m.get("missing") else "",
				o.get("overall_target", ""),
				o.get("from_date", ""),
				o.get("cumulative_value", ""),
				o.get("achievement_pct", ""),
			]
		)
	return build_xlsx_response(rows, f"outlet-targets-{out['month_start']}")


@frappe.whitelist()
def import_outlet_targets(file_url=None, month_start=None):
	"""Apply target rows from an uploaded .xlsx (same layout as the export).

	Returns {updated, skipped, errors:[{row, company, message}]}; the summary
	dialog on the page renders it.
	"""
	_check_hq_access()
	if not file_url:
		frappe.throw(_("Please attach a file"))
	from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

	rows = read_xlsx_file_from_attached_file(file_url=file_url)
	if not rows:
		frappe.throw(_("The file is empty"))
	header = [str(c).strip() if c is not None else "" for c in rows[0]]
	missing = [h for h in ("Company", "Monthly Target", "Balik Modal Target") if h not in header]
	if missing:
		frappe.throw(_("Invalid template: missing column(s) {0}").format(", ".join(missing)))

	default_month = month_start or get_first_day(nowdate())
	updated, skipped, errors = 0, 0, []
	seen = set()
	for i, row in enumerate(rows[1:], start=2):  # data starts after the header row
		if not row or all(c is None or str(c).strip() == "" for c in row):
			skipped += 1
			continue
		cell = lambda idx: row[idx] if idx < len(row) else None  # noqa: E731
		company = str(cell(EDITABLE["Company"]) or "").strip()
		month = str(cell(EDITABLE["Month Start"]) or "").strip() or str(default_month)
		monthly = cell(EDITABLE["Monthly Target"])
		overall = cell(EDITABLE["Balik Modal Target"])
		overall_from = cell(EDITABLE["Balik Modal From"])
		overall_from = str(overall_from).strip() if overall_from not in (None, "") else ""

		if not company:
			skipped += 1
			continue
		key = (company, month)
		if key in seen:
			errors.append({"row": i, "company": company, "message": _("Duplicate row for {0}").format(company)})
			continue
		seen.add(key)

		# One value present per store; both blank = nothing to change -> skip.
		has_monthly = monthly is not None and str(monthly).strip() != ""
		has_overall = overall is not None and str(overall).strip() != ""
		if not has_monthly and not has_overall and not overall_from:
			skipped += 1
			continue

		try:
			set_outlet_target(
				company=company,
				month_start=month if has_monthly else None,
				target_sales=monthly if has_monthly else None,
				overall_target=overall if has_overall else None,
				overall_from=overall_from if has_overall or overall_from else None,
			)
			updated += 1
		except Exception as e:
			errors.append({"row": i, "company": company, "message": str(e)})

	return {"updated": updated, "skipped": skipped, "errors": errors}
