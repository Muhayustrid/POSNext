# POS Reject v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two decisions (Approved/Rejected) with multi-crew liability, HO-maintained purpose master, Desk sidebar + exportable report, and Production Loss status/"Ajukan" in the POS.

**Architecture:** Frappe DocTypes (new `POS Reject Purpose`, child `POS Reject Liability`) + the existing service `pos_next/services/reject_request.py` as the single business-logic layer; thin whitelisted API in `pos_next/api/reject_request.py`; a Script Report for liability reporting; Vue 3 POS dialogs consume the API.

**Tech Stack:** Frappe/ERPNext v16 (Python 3.14), Vue 3 + frappe-ui, vitest.

**Spec:** `docs/superpowers/specs/2026-10-12-pos-reject-v2-design.md` (rulings D1–D9 are authoritative).

## Global Constraints

- Branch `feat/pos-reject-v2` in the main checkout (container bench mounts it). Never a worktree.
- Bench runs in Docker `erpnext16_dev-frappe-1`, bench root `/workspace/development/frappe-bench`.
- BE tests: `docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && FRAPPE_STREAM_LOGGING=1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <dotted.module>"` (site `posnext.localhost`). **Serial only** — never two BE test/migrate processes at once.
- Migrate after DocType JSON changes: `bench --site posnext.localhost migrate` AND `bench --site production.localhost migrate` (inside container).
- FE: vitest/biome/build run inside the container under `apps/pos_next/POS` (`npx vitest run <file>`), never on the host.
- No new dependencies. Commits: conventional, scope required, Indonesian description (e.g. `feat(reject): ...`).
- Do not touch or commit the user's unrelated dirty files (`POSHeader.vue`, `POSSale.vue`, `POSSale.test.js`, untracked harness files). For `id.csv`/`ar.csv` stage only hunks this feature adds (`git add -p`).
- Decision literals exactly `"Approved"`, `"Rejected"`. System purpose name exactly `"Production Loss"`; default crew purpose `"Unsold"`.
- Server-owned fields (incl. new `liabilities`) are cleared in `validate_request` and written only by the service (C1 rule from v1).
- 3 pre-existing `test_production` failures (`_Test Company` lacks `stock_adjustment_account`) are known; don't fix, don't count as regressions.

## Review Focus

1. Liability amounts with rounding (e.g. 100000 split 3 ways) — sum must be accepted when within currency precision, not rejected for 0.01 drift. → Task 2 test `test_reject_split_rounding_accepted`.
2. A crew user forging `liabilities` rows via save/submit — must be wiped. → Task 1 test `test_forged_liabilities_cleared`.
3. HO disables a purpose that an existing Draft uses — submitting that Draft must still be blocked with a clear message (not silently accepted). → Task 2 test `test_submit_with_disabled_purpose_throws`.
4. Liable user not enabled / not existing — throws, not a 500. → Task 2 test `test_reject_liability_disabled_user_throws`.
5. Production history row whose Work Order has no reject request (loss = 0) — no badge, no crash. → Task 6 FE test `no badge when no reject_request`.

---

### Task 1: Data model — purpose master, liability child, request fields

**Files:**
- Create: `pos_next/pos_next/doctype/pos_reject_purpose/{__init__.py,pos_reject_purpose.json,pos_reject_purpose.py}`
- Create: `pos_next/pos_next/doctype/pos_reject_liability/{__init__.py,pos_reject_liability.json,pos_reject_liability.py}` (istable)
- Modify: `pos_next/pos_next/doctype/pos_reject_request/pos_reject_request.json` (purpose, decision, video_link, liabilities)
- Modify: `pos_next/services/reject_request.py` (`validate_request` clears `liabilities`; purpose checks keyed on `is_system`)
- Modify: `pos_next/install.py` (seed purposes in `after_install` and `after_migrate`)
- Modify: `pos_next/fixtures/custom_docperm.json` only if outlet-role read perms on the new DocType need the fixture pattern used by POS Reject Request; otherwise perms live in the DocType JSON.
- Test: `pos_next/api/test_reject_request.py`

**Interfaces:**
- Produces: DocType `POS Reject Purpose` (name = `purpose_name`; fields `enabled`, `is_system`); child `POS Reject Liability` (`user` Link User reqd, `amount` Currency reqd); request field `liabilities` (Table); helper `reject_request.is_system_purpose(purpose: str) -> bool` (cached `frappe.db.get_value(..., "is_system")`); `install.ensure_reject_purposes()` idempotent.
- Request JSON: `purpose` Link → `POS Reject Purpose`; `decision` options `\nApproved\nRejected`; `video_link` `options: "URL"`; `liabilities` placed in `section_decision`, `read_only: 1`, `no_copy: 1`.

- [ ] **Step 1: Write failing tests** in `test_reject_request.py`:
  - `test_seed_purposes`: after `ensure_reject_purposes()`, `Unsold` exists with `is_system=0`, `Production Loss` with `is_system=1`; calling twice creates no duplicates.
  - `test_system_purpose_not_deletable`: `frappe.delete_doc("POS Reject Purpose", "Production Loss")` raises `frappe.ValidationError`.
  - `test_system_flag_cannot_be_cleared`: setting `is_system=0` on Production Loss and saving raises.
  - `test_forged_liabilities_cleared`: crew user saves a draft with `liabilities=[{"user": crew, "amount": 1}]` → reloaded doc has 0 liabilities; same via `doc.submit()` → 0 liabilities.
- [ ] **Step 2: Run** the module → new tests FAIL (DocType missing).
- [ ] **Step 3: Implement** DocTypes + JSON edits + `ensure_reject_purposes()` (called from both hooks) + controller `on_trash`/`validate` guards + `doc.liabilities = []` in the server-field reset block of `validate_request` + replace every `doc.purpose == "Production Loss"` / `== "Unsold"` check in the service with `is_system_purpose(doc.purpose)` / `not is_system_purpose(...)`. Perms: System Manager full; POSNext Reject Approver read/write/create; POSNext Manager + POSNext Cashier read.
- [ ] **Step 4: Migrate both sites**, run module → PASS (old decision tests may now fail — expected, Task 2 rewrites them; record which).
- [ ] **Step 5: Commit** `feat(reject): master purpose, tabel liability, field request v2`.

### Task 2: Service + API — two decisions, liability, close, limit, purposes endpoint

**Files:**
- Modify: `pos_next/services/reject_request.py` (`DECISIONS`, `apply_decision`, `close_request`, delete `_return_to_outlet`, Strict path, `_month_total`, notification text)
- Modify: `pos_next/api/reject_request.py` (`save_reject_request` purpose, `decide_reject_request` liabilities, attach gate, new `get_reject_purposes`)
- Test: `pos_next/api/test_reject_request.py` (rewrite all Write Off / Charge Crew / old-Rejected tests)

**Interfaces:**
- Consumes: Task 1 `is_system_purpose`, `liabilities` child table.
- Produces:
  - `apply_decision(doc, decision: str, notes: str|None=None, liabilities: list[dict]|None=None, system: bool=False) -> dict` (payload as today, now including `liabilities: [{user, amount}]`).
  - `decide_reject_request(name, decision, notes=None, liabilities=None)` — `liabilities` JSON string or list.
  - `get_reject_purposes() -> list[str]` — enabled, `is_system=0`, ordered by name.
  - `save_reject_request(data, submit=0)` — `data.purpose` required for new rows, validated: exists, enabled, not system.
  - `_payload`/`get_reject_request` include `liabilities`.

- [ ] **Step 1: Write failing tests:**
  - `test_approve_no_notes_ok` → status `Approved`, decision `Approved`, 0 liabilities.
  - `test_approve_with_liabilities_throws`.
  - `test_reject_requires_notes`.
  - `test_reject_default_liability_owner` → 1 row `{user: owner, amount: total_amount}`.
  - `test_reject_split_two_users` → rows saved as given.
  - `test_reject_split_rounding_accepted` → total 100000, three rows 33333.33/33333.33/33333.34 accepted.
  - `test_reject_split_sum_mismatch_throws` (off by 1000).
  - `test_reject_duplicate_user_throws`; `test_reject_liability_disabled_user_throws`; `test_reject_liability_nonpositive_throws`.
  - `test_close_from_rejected_issues_stock` → `issue_entry` set, Material Issue from reject wh; same for Approved.
  - `test_month_total_counts_rejected`.
  - `test_strict_auto_rejected_owner_liability`.
  - `test_production_loss_can_be_rejected` (system purpose, liability on chosen user, close issues nothing).
  - `test_save_with_system_purpose_throws`; `test_save_with_disabled_purpose_throws`; `test_submit_with_disabled_purpose_throws`; `test_save_custom_purpose_moves_stock` (create purpose "Rusak", submit → transfer_entry set).
  - `test_get_reject_purposes_excludes_system_and_disabled`.
  - `test_destruction_attach_allowed_when_rejected`.
  - Keep: segregation-of-duties, `test_decide_endpoint_keeps_permission_error`, `test_submit_clears_forged_server_fields`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.** Tolerance: `abs(sum - total) <= 0.5 * 10 ** -precision` with precision from `doc.precision("total_amount")`. Liabilities written by `doc.append` + `db_update_all`/child insert under `ignore_permissions` after the status `db_set` (same transaction, same lock as today — reuse the for_update row's own result, I1). Strict path passes no liabilities (defaults to owner).
- [ ] **Step 4: Run** `pos_next.api.test_reject_request`, `pos_next.api.test_production`, `pos_next.api.test_docperm_fixture`, `pos_next.tests.test_uninstall_coverage` serially → all PASS except the 3 known.
- [ ] **Step 5: Commit** `feat(reject): keputusan Approved/Rejected dengan liability kru`.

### Task 3: Desk — decide dialog, sidebar, workspace

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_reject_request/pos_reject_request.js`
- Modify: `pos_next/pos_next/sidebar/posnext/posnext.json`
- Modify: `pos_next/pos_next/workspace/posnext/posnext.json`

**Interfaces:**
- Consumes: Task 2 `decide_reject_request(name, decision, notes, liabilities)`; Task 4 report name `POS Reject Report` (link may be added before the report exists; verify after Task 4).

- [ ] **Step 1: Implement** buttons: `Approved` (confirm + optional notes); `Rejected` → `frappe.ui.Dialog` with `notes` (Small Text, reqd) and `liabilities` (Table: `user` Link User reqd, `amount` Currency reqd) prefilled `[{user: frm.doc.owner, amount: frm.doc.total_amount}]`; on adding a row redistribute evenly (last row absorbs rounding) unless the user edited an amount; show "Total X / Y" HTML field updated on change. Buttons hidden from owner (unchanged rule). Show `liabilities` grid read-only on the form.
- [ ] **Step 2: Sidebar** entries (match neighbor entry shape exactly, `child: 1`): "Reject Request" (DocType `POS Reject Request`) under Head Office; "Reject Report" (Report `POS Reject Report`) under Report; "Reject Purpose" (DocType `POS Reject Purpose`) under Configuration if that section exists in the sidebar, else Head Office. Workspace: add the same links to the matching cards.
- [ ] **Step 3: Verify** in container: `bench --site production.localhost migrate` then `curl -s` is not enough — load `http://production.localhost:8001/desk/posnext` with Playwright, assert the three labels appear in the sidebar; open a Pending request as `hq@posnext.local`, open the Rejected dialog, screenshot. Check `video_link` renders as a link on a submitted doc; if not, add a `formatter`/`refresh` HTML render in `pos_reject_request.js`.
- [ ] **Step 4: Commit** `feat(reject): dialog keputusan Desk + sidebar POSNext`.

### Task 4: Script Report — POS Reject Report

**Files:**
- Create: `pos_next/pos_next/report/pos_reject_report/{__init__.py,pos_reject_report.json,pos_reject_report.py,pos_reject_report.js}`
- Test: `pos_next/pos_next/report/pos_reject_report/test_pos_reject_report.py`

**Interfaces:**
- Consumes: Task 1/2 fields.
- Produces: `execute(filters) -> (columns, data, None, chart, report_summary)`. Filters: `from_date`, `to_date` (reqd, default month start/today), `pos_profile`, `purpose`, `decision`, `user`, `view` (Select `Per Request\nPer Liability`, default `Per Request`). Per Liability columns: request, incident_date, pos_profile, purpose, decision, bearer (Data: user id or literal `"Company"`), amount. Roles: System Manager, POSNext Reject Approver. Only `docstatus = 1` and `decision` set.

- [ ] **Step 1: Write failing test** `test_liability_rows_sum_to_totals`: one Approved (100) + one Rejected split 60/40 → Per Liability rows: Company 100, u1 60, u2 40; summary company=100, crew=100, count=2. `test_user_filter_per_liability` → only that user's rows. `test_per_request_liable_crew_column` → "u1, u2".
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** with two queries max (requests + liabilities `in` names). Chart: bar, labels = outlets, datasets Company/Crew.
- [ ] **Step 4: Run** → PASS; open the report in Desk via Playwright on production.localhost, confirm Export menu present.
- [ ] **Step 5: Commit** `feat(reject): laporan POS Reject Report per request/per penanggung`.

### Task 5: POS RejectRequestDialog — purpose dropdown, liability view, video link

**Files:**
- Modify: `POS/src/components/pos/RejectRequestDialog.vue`, `POS/src/components/pos/RejectRequestDialog.test.js`
- Modify: `pos_next/translations/id.csv`, `pos_next/translations/ar.csv` (new strings only)

**Interfaces:**
- Consumes: `get_reject_purposes`, `save_reject_request` with `purpose`, detail `liabilities`.
- Produces: new prop `initialRequest: { type: String, default: null }` — when set on mount/open, calls existing `openDetail(name)` (consumed by Task 6).

- [ ] **Step 1: Write failing tests:** purpose select lists API result and is sent in `save_reject_request` payload; detail with decision `Rejected` renders each liability user + amount; detail with `Approved` shows no liability block; draft video link input shows an anchor with `target="_blank"` and the URL when valid, none when invalid; destruction/close step enabled for status `Rejected`; `initialRequest` triggers `get_reject_request` with that name.
- [ ] **Step 2: Run** vitest → FAIL.
- [ ] **Step 3: Implement**; remove return-to-outlet wording; decision label shows `__("Approved")` / `__("Rejected")` with subtitle "Ditanggung perusahaan" / "Ditanggung kru" via `__()` English source strings ("Borne by company", "Borne by crew").
- [ ] **Step 4: Run** the file + full vitest → PASS. Add id/ar rows for new strings.
- [ ] **Step 5: Commit** `feat(reject): dialog POS — pilih purpose, tampil liability, link video`.

### Task 6: Production Loss status in POS

**Files:**
- Modify: `pos_next/services/production.py` (`get_production_history` rows)
- Modify: `POS/src/components/pos/ProductionDialog.vue`, `ProductionDialog.test.js`, `POS/src/components/pos/POSMenuDialog.vue`
- Test: `pos_next/api/test_production.py`

**Interfaces:**
- Consumes: Task 5 `initialRequest` prop.
- Produces: each history row gains `reject_request: str|None`, `reject_status: str|None` (`status` of the system request for that WO; `Draft` when docstatus 0). ProductionDialog emits `open-reject-request` with the name; POSMenuDialog handles it by setting `activeView = 'reject-request'` and passing `:initial-request`.

- [ ] **Step 1: Write failing tests:** BE `test_history_includes_reject_status` (finish a WO with loss → row has draft name + `"Draft"`; WO without loss → `None`). FE: badge text per status (Draft → "Not submitted" + "Submit report" button, Pending → "Waiting for HO", Approved, Rejected, Closed); clicking button emits `open-reject-request` with the name; `no badge when no reject_request`. POSMenuDialog test: event switches to reject view with `initialRequest`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement** — one `frappe.get_all("POS Reject Request", filters={"work_order": ["in", names], "is_system_generated": 1}, fields=["name","work_order","status"])`. Badge colors follow existing status-chip classes in ProductionDialog. i18n id/ar rows.
- [ ] **Step 4: Run** `pos_next.api.test_production` (3 known failures only) + vitest → PASS.
- [ ] **Step 5: Commit** `feat(production): badge status reject + tombol ajukan`.

### Task 7: Verification, build, UAT data reset

- [ ] **Step 1:** Serial BE run of all four modules from Task 2 Step 4 + report test; full vitest; `bench build --app pos_next` in container. Record counts in the ledger.
- [ ] **Step 2:** Ask the user before deleting UAT data (D9). On approval, per site (`production.localhost`, `posnext.localhost`): cancel submitted POS Reject Requests (cancels their transfer SE via existing cancel path where possible), then delete requests and their File attachments; report counts.
- [ ] **Step 3:** Manual Playwright smoke on production.localhost: cashier creates request with purpose "Unsold" → HO Rejects with 2 crew → outlet closes with destruction photo → report Per Liability shows both rows.
- [ ] **Step 4: Commit** any fixups as `fix(reject): ...`.

## Execution notes for fan-out
- Order: T1 → T2 (sequential, same files). Then T3, T4, T5 in parallel (disjoint files; only one may run BE tests/migrate at a time — T4 owns the BE run slot, T3 does Playwright after T4's migrate). T6 after T5 (consumes `initialRequest`). T7 last.
- Parallel implementers share one checkout: commit only their own files with explicit paths; on `index.lock` retry after a few seconds.
