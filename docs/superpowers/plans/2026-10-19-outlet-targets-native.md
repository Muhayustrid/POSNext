# Outlet Targets Native Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `/desk/outlet-targets` a native Frappe DataTable sheet with inline autosave editing, audit Balik Modal changes, add copy-from-previous-month, and fix the `POS Monthly Target` list/report presentation.

**Architecture:** Backend keeps the existing `set_outlet_target` / `get_outlet_targets` contract; it gains a Version row for Company field changes and one new endpoint `copy_outlet_targets`. The page swaps its hand-built HTML table for `frappe.DataTable` with a `getEditor` modelled on Frappe's Report View. DocType changes are JSON + a `_list.js`.

**Tech Stack:** Frappe v16 Desk (jQuery page, `frappe.DataTable`, `frappe.ui.form.make_control`), Python `IntegrationTestCase`.

**Spec:** `docs/superpowers/specs/2026-10-19-outlet-targets-native-design.md`

## Global Constraints

- Backend runs in Docker: `docker exec erpnext16_dev-frappe-1 bash -lc 'cd /workspace/development/frappe-bench && …'`. Host `bench`/`python` do not work.
- Backend tests: `FRAPPE_STREAM_LOGGING=1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <dotted.module>`, serial only, default site `posnext.localhost`; repeat with `PN_SITE=roti-posnext-test.localhost` before finishing.
- Baseline (measured 2026-10-19, before any change): on **roti** `test_hq_monitoring` + `test_outlet_targets` = 63/63 OK — roti is the green gate. On **posnext** 5 pre-existing data-dependent failures (real data on the dev site): `test_compare_mixed_currency_outlets_keep_own_currency`, `test_set_outlet_target_validation_and_permission`, `test_targets_available_and_achievement`, `test_targets_by_company_rows`, `test_outlet_targets.test_export_import_roundtrip`. On posnext the bar is "no NEW failures vs this list".
- Permission facts (verified): Sales Manager HAS write+create on POS Monthly Target (DocPerm; `test_hq_native_permissions` asserts it) and NO write on Company. POSNext Manager has Company write but no POS Monthly Target write. The existing assertion "Sales Manager cannot move targets" only passes where that user lacks Company read; do not copy that assumption.
- Verified frontend APIs (Frappe v16): `frappe.DataTable` (global, `frappe/public/js/frappe/ui/datatable.js`), options `getEditor`, `headerDropdown`, `serialNoColumn`, `checkboxColumn`, `inlineFilters`, `layout`; per-column `editable`/`sortable`/`dropdown`/`focusable`/`format`; `datatable.refreshRow(row, rowIndex)`; on edit DataTable skips `setValue` when `oldValue === value` and reverts the cell when the returned promise rejects (`cellmanager.js` `submitEditing`). `frappe.model.can_write/can_create`, `frappe.ui.form.make_control`.
- Tests must never touch real data: no global deletes by month for the current/next month; use a far-future month and delete only rows the test created.
- Code comments in English; commit descriptions in Indonesian with English type/scope, e.g. `feat(hq): …`. Subagents never run git state-changing commands other than the task's own `git add`/`git commit` of listed files; never `stash`, `checkout`, `reset`.
- No new dependencies. Match surrounding style (tabs in Python and JS).
- New UI strings get a row in `pos_next/translations/id.csv` (`"English","Indonesian",""`).
- `get_outlet_targets` / `set_outlet_target` signatures and return shapes must not change (Excel import/export and HQ monitoring call them).
- No in-table sort/filter/column dropdown on the DataTable (spec D5).

## Review Focus

- Saving a cell whose value did not change must not call the server (DataTable already skips when `oldValue === value`; keep it that way — do not coerce types so `"1000"` vs `1000` looks different).
- User without Company write but with POS Monthly Target write: monthly column editable, Balik Modal columns not focusable for editing; server PermissionError still reverts the cell if the client check is wrong.
- Clearing only the Balik Modal From date (target unchanged) must clear the date and write one Version with only that field (test in Task 1).
- Copy-from-previous-month run twice: second run copies 0, skips all (test in Task 1).
- Currency formatting per row: two outlets with different currencies on one page each show their own currency (Task 3 E2E check).

---

### Task 1: Backend — Version audit for Balik Modal + `copy_outlet_targets`

**Files:**
- Modify: `pos_next/api/hq_monitoring.py` (`set_outlet_target`, ~line 437–511; add `copy_outlet_targets` right after it)
- Test: `pos_next/tests/test_hq_monitoring.py` (class `TestHQMonitoring`, next to `test_set_outlet_target_updates_and_creates` ~line 1098; it has fixtures `self.company_a`, `self.user_all` (Sales Manager), `self.user_no_role`, helper `self._make_target(company)`)

**Interfaces:**
- Produces: `copy_outlet_targets(month_start: str) -> {"copied": int, "skipped": int}` (whitelisted, path `pos_next.api.hq_monitoring.copy_outlet_targets`). `set_outlet_target` unchanged signature/return.

- [ ] **Step 1: Write the failing tests** in `TestHQMonitoring`:

```python
def _company_versions(self):
	return frappe.get_all(
		"Version",
		filters={"ref_doctype": "Company", "docname": self.company_a},
		fields=["name", "data"],
		order_by="creation desc",
	)

def test_set_outlet_target_writes_company_version(self):
	frappe.set_user(ADMIN)
	set_outlet_target(company=self.company_a, overall_target=0, overall_from="")
	before = len(self._company_versions())
	set_outlet_target(company=self.company_a, overall_target=750000, overall_from="2026-01-01")
	versions = self._company_versions()
	self.assertEqual(len(versions), before + 1)
	changed = {c[0]: c for c in json.loads(versions[0].data)["changed"]}
	self.assertEqual(set(changed), {"pos_overall_sales_target", "pos_overall_target_from"})
	self.assertEqual(flt(changed["pos_overall_sales_target"][2]), 750000.0)
	# no-op save writes no Version
	set_outlet_target(company=self.company_a, overall_target=750000, overall_from="2026-01-01")
	self.assertEqual(len(self._company_versions()), before + 1)
	# clearing only the date: one Version, only that field
	set_outlet_target(company=self.company_a, overall_target=750000, overall_from="")
	versions = self._company_versions()
	self.assertEqual(len(versions), before + 2)
	self.assertEqual([c[0] for c in json.loads(versions[0].data)["changed"]], ["pos_overall_target_from"])
	self.assertIsNone(frappe.db.get_value("Company", self.company_a, "pos_overall_target_from"))
	set_outlet_target(company=self.company_a, overall_target=0, overall_from="")

def test_copy_outlet_targets(self):
	frappe.set_user(ADMIN)
	# far-future months: no real data lives there, cleanup touches only them
	prev = add_months(get_first_day(nowdate()), 36)
	month = add_months(prev, 1)
	cleanup = lambda: frappe.db.delete("POS Monthly Target", {"month_start": ["in", [prev, month]]})  # noqa: E731
	cleanup()
	self.addCleanup(cleanup)
	for co, amount in ((self.company_a, 123000), (self.company_b, 456000)):
		frappe.get_doc(
			{"doctype": "POS Monthly Target", "company": co, "month_start": prev, "target_sales": amount}
		).insert(ignore_permissions=True)
	# company_b already has a target for the destination month: never overwritten
	frappe.get_doc(
		{"doctype": "POS Monthly Target", "company": self.company_b, "month_start": month, "target_sales": 1}
	).insert(ignore_permissions=True)

	out = copy_outlet_targets(month_start=str(month))
	self.assertEqual(out, {"copied": 1, "skipped": 1})
	get = lambda co: flt(frappe.db.get_value("POS Monthly Target", {"company": co, "month_start": month}, "target_sales"))  # noqa: E731
	self.assertEqual(get(self.company_a), 123000.0)
	self.assertEqual(get(self.company_b), 1.0)
	# second run copies nothing
	self.assertEqual(copy_outlet_targets(month_start=str(month)), {"copied": 0, "skipped": 2})
	with self.assertRaises(frappe.ValidationError):
		copy_outlet_targets(month_start="2026-08-15")
	frappe.set_user(self.user_no_role)  # fails the HQ role gate
	with self.assertRaises(frappe.PermissionError):
		copy_outlet_targets(month_start=str(month))
```

(Assumes only the test-seeded companies have rows in `prev`; that far-future month is empty on both dev sites.)

Add imports as needed (`json`, `add_months`, `flt`, `copy_outlet_targets`).

- [ ] **Step 2: Run to verify failure**

Run: `… _pn_run_tests.py pos_next.tests.test_hq_monitoring`
Expected: the 2 new tests FAIL/ERROR (`copy_outlet_targets` import error / no Version rows).

- [ ] **Step 3: Implement**
  - In `set_outlet_target`, before `frappe.db.set_value("Company", …)`, read the current `pos_overall_sales_target`, `pos_overall_target_from`; after writing, build `changed = [[field, old, new], …]` for fields whose value actually differs (compare `flt` for the amount, `getdate`-or-None for the date) and, if non-empty, insert `frappe.get_doc({"doctype": "Version", "ref_doctype": "Company", "docname": company, "data": frappe.as_json({"changed": changed, "added": [], "removed": [], "row_changed": []})}).insert(ignore_permissions=True)`. Keep `frappe.db.set_value` (spec R6: no `company.save()`).
  - `copy_outlet_targets(month_start=None)`: `_check_hq_access()`; parse/validate first-of-month exactly like `set_outlet_target`; require `frappe.has_permission("POS Monthly Target", "create")` else `PermissionError`; companies = `_resolve_scope(None, 0)["company_options"]`; previous month = `add_months(month, -1)`; read previous rows (`company`, `target_sales`) for those companies and the set of companies already having a row for `month`; insert a `POS Monthly Target` doc (normal `insert()`, so controller validation runs) for each previous row whose company is not in that set and for which `frappe.has_permission("Company", "read", doc=company)` holds (otherwise count it as skipped — the controller would throw mid-loop); return counts. `skipped` = previous rows not copied (already had a target, or no Company read).

- [ ] **Step 4: Run to verify pass**

Run `pos_next.tests.test_hq_monitoring pos_next.tests.test_outlet_targets` with `PN_SITE=roti-posnext-test.localhost` → all OK (65 tests). On posnext → only the 5 baseline failures from Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add pos_next/api/hq_monitoring.py pos_next/tests/test_hq_monitoring.py
git commit -m "feat(hq): audit Version untuk target balik modal dan salin target bulan lalu"
```

---

### Task 2: POS Monthly Target list/report presentation

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_monthly_target/pos_monthly_target.json`
- Create: `pos_next/pos_next/doctype/pos_monthly_target/pos_monthly_target_list.js`
- Modify: `pos_next/translations/id.csv`

**Interfaces:** none consumed/produced.

- [ ] **Step 1: DocType JSON** — set `"title_field": "company"`, `"show_title_field_in_link": 1`, `target_sales` `"in_list_view": 1`, `"sort_field": "month_start"`, `"sort_order": "DESC"`, bump `"modified"` to `"2026-10-19 00:00:00.000000"` (migrate only syncs when `modified` changes).

- [ ] **Step 2: `pos_monthly_target_list.js`** — `frappe.listview_settings["POS Monthly Target"]` with:
  - `formatters.month_start(value)` → translated month name + year, e.g. `"October 2026"` (use `moment(value).format("MMMM YYYY")` under `frappe.boot.lang`-aware moment, which Desk already configures).
  - `onload(listview)` → `listview.page.add_inner_button(__("Outlet Targets"), () => frappe.set_route("outlet-targets"))`.

- [ ] **Step 3: Migrate + verify**

Run: `bench --site posnext.localhost migrate && bench --site roti-posnext-test.localhost migrate` (in container), then `bench --site posnext.localhost clear-cache`.
Verify (curl or browser as Administrator): `/api/method/frappe.desk.form.load.getdoctype?doctype=POS%20Monthly%20Target` → `title_field == "company"`, `target_sales.in_list_view == 1`. Run `pos_next.tests.test_hq_monitoring` class `TestPOSMonthlyTarget` → pass.

- [ ] **Step 4: Commit** (no new UI strings: `"Outlet Targets"` already has an `id.csv` row)

```bash
git add pos_next/pos_next/doctype/pos_monthly_target/
git commit -m "feat(hq): rapikan list dan report view POS Monthly Target"
```

---

### Task 3: Outlet Targets page — DataTable with inline autosave

**Files:**
- Modify: `pos_next/pos_next/page/outlet_targets/outlet_targets.js` (rewrite `_render`, `_row`, `_bar`, `_set_dialog`, `_setup_actions`, `_bind_pager`; keep `_setup_filters`, `_month_start`, `_search`, `_basis*`, `refresh`, `_render_error`, `_pager`, Excel export/import/summary)
- Modify: `pos_next/pos_next/page/outlet_targets/outlet_targets.css` (delete rules for removed `.ot-table`, `.ot-bar*`, `.ot-dialog-head`, etc.; keep pager/sub styles still used)
- Modify: `pos_next/translations/id.csv`

**Interfaces:**
- Consumes: `pos_next.api.hq_monitoring.get_outlet_targets(month_start, company, search, start, page_length)` (row shape: `company, currency, monthly{target_sales,missing}, mtd_value, achievement_sales_pct, projected_value, overall{overall_target, from_date, cumulative_value, achievement_pct} | None`), `set_outlet_target(company, month_start, target_sales, overall_target, overall_from)`, `copy_outlet_targets(month_start) -> {copied, skipped}` (Task 1).

- [ ] **Step 1: Columns + DataTable.** Build `new frappe.DataTable(wrapper, {...})` with `serialNoColumn: false`, `checkboxColumn: false`, `inlineFilters: false`, `headerDropdown: []`, `layout: "fluid"`, `cellHeight: 35`, `language: frappe.boot.lang`, `translations: frappe.utils.datatable.get_translations()`, `direction` as Report View, `getEditor: this._get_editor.bind(this)`. Every column sets `sortable: false, dropdown: false`. Column ids (used by editor and refresh): `company`, `target_sales`, `mtd_value`, `achievement`, `projection`, `overall_target`, `overall_from`, `overall_pct`. Editable columns carry a `docfield` (`{fieldname, fieldtype: "Currency"|"Date", options: <row currency set per-edit>}`); non-editable columns set `editable: false`. Labels follow spec R1 (`Target` + basis label + `(monthly)`, basis label + `MTD`, `Achievement`, `Projection`, `Balik Modal Target`, `Balik Modal From`, `Balik Modal %`). Cell values are raw numbers/dates; column `format(value, row, column, data)` (DataTable calls it with those 4 args; `data` is the row object passed in `data:`, so pass row objects keyed by column id plus `currency`) renders: money → `format_currency(value, row.currency)`; pct → indicator pill `<span class="indicator-pill {red|orange|green}">` with thresholds <50 red, <100 orange, ≥100 green, via `HQ_UTILS.fmtPct`; empty → `""`. Company cell → link `/desk/pos-monthly-target?company=<encoded>` (spec R9).
- [ ] **Step 2: Permissions per column (R5).** `target_sales.editable = frappe.model.can_write("POS Monthly Target") || frappe.model.can_create("POS Monthly Target")`; `overall_target.editable = overall_from.editable = frappe.model.can_write("Company")`.
- [ ] **Step 3: `_get_editor(colIndex, rowIndex, value, parent)`** — mirror `report_view.js` `get_editing_object` + `render_editing_input`: `frappe.ui.form.make_control({df, parent, render_input: true})`, hide label/description, return `{initValue, setValue, getValue}`. `setValue(value)` returns the save promise (DataTable reverts the cell on rejection): call `set_outlet_target` via `frappe.call` (no `freeze`) with args per spec R4 — monthly: `{company, month_start: this._month_start(), target_sales: value || 0}`; Balik Modal: `{company, overall_target: <edited or row value> || 0, overall_from: <edited or row value> || ""}`. On success: `frappe.show_alert({message: __("Saved"), indicator: "green"})` then `_refresh_row(rowIndex, company)`. On error: reject (Frappe already shows the server message).
- [ ] **Step 4: `_refresh_row(rowIndex, company)`** — `get_outlet_targets({month_start, company, start: 0, page_length: 1})`, replace `this.state.rows[rowIndex]`, `this.datatable.refreshRow(this._row_values(row), rowIndex)`. No full `refresh()` (spec R3).
- [ ] **Step 5: Header/menu (R7, R8).** Remove the primary "Refresh"; use `this.page.add_button(__("Refresh"), …)`. Menu: keep Export Excel / Import Excel / Download Template; add `__("Copy Targets from Previous Month")` → `frappe.confirm(__("Copy last month's targets into {0} for outlets without a target?", [month label]), …)` → `copy_outlet_targets` → `frappe.msgprint(__("Copied: {0} · Skipped: {1}", [copied, skipped]))` → `refresh()`; add `__("Target History")` → `frappe.set_route("List", "POS Monthly Target")`.
- [ ] **Step 6: Remove dialog/bar code.** Delete `_set_dialog`, `_bar`, `_row` HTML builder and the `[data-ot-set]` binding; keep pager below the table and the month info line (R10). Remove CSS that only those used.
- [ ] **Step 7: Translations.** Add `id.csv` rows for every new string: `Saved`, `Balik Modal Target`, `Balik Modal From`, `Balik Modal %`, `Copy Targets from Previous Month`, `Copy last month's targets into {0} for outlets without a target?`, `Copied: {0} · Skipped: {1}`, `Target History` (skip any that already exist — grep first).
- [ ] **Step 8: Verify in browser** (Playwright as Administrator, `http://localhost:8001/desk/outlet-targets`, after `clear-cache` + hard reload): table renders with native look; edit monthly target on an outlet → alert "Saved", row recomputes, DB `tabPOS Monthly Target` row updated; edit Balik Modal target and clear its date → DB updated and Company timeline shows the Version; typing an invalid value (negative) → error shown and cell reverts; Copy Targets on next month → summary, second run copies 0; two outlets with different currencies show their own currency. Screenshot before/after into `/tmp/e2e-outlet-targets/`.
- [ ] **Step 9: Commit**

```bash
git add pos_next/pos_next/page/outlet_targets/ pos_next/translations/id.csv
git commit -m "feat(hq): halaman Outlet Targets pakai DataTable native dengan edit langsung"
```

---

### Task 4: Permission E2E + both-site check

**Files:** none (verification only; fixes go back to the owning task's files).

- [ ] **Step 1:** On posnext create user `ot.salesmgr@posnext.test` with only role `Sales Manager` (verified: passes `HQ_ROLES`, POS Monthly Target write+create, no Company write). In the browser as that user: monthly column editable and saves; Balik Modal columns not editable. Disable the user afterwards. In the browser as that user: monthly column editable, Balik Modal columns not editable. Disable the user afterwards.
- [ ] **Step 2:** Run `pos_next.tests.test_hq_monitoring pos_next.tests.test_outlet_targets pos_next.api.test_hq_native_permissions` on both sites. Expected: roti all OK; posnext only the 5 baseline failures.
- [ ] **Step 3:** Update `PROJECT_STATE.md` (current work line for this feature) — no commit of that file (gitignored).
