# Outlet Targets: native Desk revamp — design

Date: 2026-10-19. Status: approved in brainstorming, awaiting spec review.

## Goal

HQ both **enters** targets for many outlets and **monitors** achievement on
`/desk/outlet-targets`. Make that page native-ERPNext (Frappe DataTable with
inline cell editing, like Report View) and fix the presentation of the
`POS Monthly Target` List/Report views. Must stay fast with hundreds of outlets.

## Decisions (settled)

- D1. Storage unchanged: monthly targets stay in `POS Monthly Target` (one row
  per company per month, history + own permissions); payback ("Balik Modal")
  target + counted-from date stay as custom fields on `Company`.
- D2. Nothing is mirrored to `Company.monthly_sales_target` and no
  "Monthly POS Target / Total Monthly POS" fields are added. ERPNext's
  `total_monthly_sales` counts submitted Sales Invoices only (POS Invoices
  count only after closing consolidation), so it is not the POS figure.
- D3. `/desk/outlet-targets` stays a custom Page: Report View can only edit
  existing records, but the sheet must list every outlet for a month, including
  outlets without a target record yet.
- D4. Concurrent edits: last write wins, no locking. Every change is recorded
  in Version (R6), so it is auditable.
- D5. No in-table sort/filter: DataTable sorts/filters only the loaded page,
  which misleads under server pagination. Cross-outlet ranking lives in
  `POS Outlet Performance Report`.

## Requirements

### Outlet Targets page (`pos_next/pos_next/page/outlet_targets/`)

- R1. Replace the hand-built HTML table with `frappe.DataTable`
  (same component as Report View). Columns:
  Outlet · Target (monthly, basis label) ✏️ · MTD (basis) · Achievement % ·
  Projection · Balik Modal Target ✏️ · Balik Modal From ✏️ · Balik Modal %.
  Money columns use `format_currency` with the row currency; percentages use
  native indicator colours (red < 50, orange < 100, green ≥ 100). Empty values
  render empty (drop "not set yet", the custom bars, the "0 TC" sub-line).
  `inlineFilters`, sorting and the column dropdown are disabled (D5).
- R2. Inline editing via DataTable `getEditor` with native Frappe controls
  (Currency for targets, Date for Balik Modal From), the same pattern as
  `frappe/public/js/frappe/views/reports/report_view.js` `get_editing_object`.
- R3. Save on cell commit (autosave). On success: green `show_alert`
  ("Saved") and refresh **only that row** by calling `get_outlet_targets` with
  `company=<outlet>` (no full-page recompute). On failure: the server message
  is shown and the cell reverts to its previous value.
- R4. Blank handling: clearing the monthly target saves 0. Balik Modal cells
  always send the pair from the row: `overall_target` (blank → 0, which clears
  the target, existing rule) and `overall_from` ("" clears the date).
- R5. Editability is per column, from client-side permission checks; the
  server stays authoritative (existing checks in `set_outlet_target`):
  monthly target editable iff `frappe.model.can_write("POS Monthly Target")`
  or `can_create`; Balik Modal columns editable iff
  `frappe.model.can_write("Company")`.
- R7. Header: Month / Year / Outlet filters unchanged. Refresh becomes a
  normal button (not primary). Menu keeps Export Excel, Import Excel, Download
  Template and adds **Copy Targets from Previous Month** (R8) and
  **Target History** (opens `/desk/pos-monthly-target`).
- R9. Outlet name links to the `POS Monthly Target` list filtered by that
  company (per-month history).
- R10. Server pagination (20/100/500, Prev/Next, count) and the month info line
  stay. The "Set" button and Set Target dialog are removed.

### Backend (`pos_next/api/hq_monitoring.py`)

- R6. Audit for Balik Modal: `set_outlet_target` keeps writing the Company
  fields with `frappe.db.set_value` (no `company.save()`: that would run every
  Company validation), and additionally inserts a `Version` for `Company`
  with `{"changed": [[field, old, new], ...]}` for fields that actually
  changed, so the change shows on the Company timeline. No Version when
  nothing changed.
- R8. New whitelisted `copy_outlet_targets(month_start)`: copies
  `target_sales` from the previous month's `POS Monthly Target` rows into
  `month_start` for companies in the user's scope that have **no** record for
  `month_start` yet; never overwrites. Same access + permission rules as
  `set_outlet_target` (HQ access, create permission, company read
  permission). Returns `{copied, skipped}`. The page confirms first
  ("Copy N targets from <prev month>?" via `frappe.confirm`), then shows the
  summary and refreshes.
- No other API change. `get_outlet_targets` already supports `company=` for
  the single-row refresh.

### POS Monthly Target list/report (`pos_next/pos_next/doctype/pos_monthly_target/`)

- R11. DocType JSON: `title_field: company`, `show_title_field_in_link: 1`,
  `target_sales` `in_list_view: 1`, `sort_field: month_start`, `sort_order: DESC`.
- R12. New `pos_monthly_target_list.js`: `month_start` formatted as
  "October 2026" (translated month name) and an "Outlet Targets" button in the
  list header. Report View totals use the built-in "Show Totals" (no code).

## Out of scope

Mirroring to Company goal fields; achievement columns in the DocType Report
View; paste-from-spreadsheet bulk edit (Import Excel covers it); locking.

## Testing

- Backend (`pos_next/tests/test_hq_monitoring.py`, `test_outlet_targets.py`):
  Version row written on Balik Modal change and not on no-op; clearing only the
  date via the pair rule; `copy_outlet_targets` copies missing, skips existing,
  respects scope/permission.
- Browser E2E on posnext: edit monthly / Balik Modal / date cells → DB and
  Company timeline updated; failed save reverts; user without Company write
  sees read-only Balik Modal cells; copy-from-previous-month; List/Report view
  of POS Monthly Target shows outlet title, target and formatted month.
- New UI strings get `id.csv` rows. `bench migrate` on both sites (DocType JSON
  change).
