# Reject Request Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `POS Reject Request` onto a native Frappe Workflow (submit → HO approve + Berita Acara → destruction proof → HO complete → Material Issue), dropping the reject-warehouse transfer.

**Architecture:** Workflow definition lives in a new `pos_next/reject_request_setup.py` (same shape as `stock_opname_setup.py`). All side effects run server-side in one service hook keyed on the `workflow_state` change (`services/reject_request.py::on_state_change`), so Desk, REST and the SPA behave the same. The SPA calls thin API wrappers around `apply_workflow`.

**Tech Stack:** Frappe/ERPNext v16 (Python, Jinja print format, Desk JS), Vue 3 SPA (`POS/`), Vitest.

**Spec:** `docs/superpowers/specs/2026-10-22-reject-request-workflow-design.md`

## Global Constraints

- Workflow name `POS Next Reject Request`; state field `workflow_state`; states/actions exactly as the spec tables (English, never translated in data).
- Approver role `POSNext Reject Approver` (constant `APPROVER_ROLE` already in `services/reject_request.py`). Crew roles `POSNext Cashier`, `POSNext Manager`.
- No stock movement before `Completed`. Material Issue source = POS Profile warehouse (`_resolve_profile(...)[1]`), expense = Company `stock_adjustment_account`. Legacy docs with `transfer_entry` issue from the transfer target (existing `_write_off_reject_stock`).
- Approval code: `confirmation_code.random_code()` alphabet/length, unique on `POS Reject Request.approval_code`.
- Print format name `Berita Acara Reject`, A4 portrait, PDF only.
- Tests run serially with `pos_next/_pn_run_tests.py` on a test-safe site (local bench container or an isolated stack like `erpnext-fix`). Never run tests against `dev.localhost` / `erp.localhost`: they hold production data.
- Frontend: `npm run test:run` and `npm run build` in `POS/` inside the container. Biome must not gain findings.
- New UI strings get `pos_next/translations/id.csv` rows.
- Subagents never run state-changing git commands; the controller commits per task with Indonesian descriptions (`feat(reject): ...`). No push.
- Do not commit the stray `CLAUDE.md` Skills lines or the unrelated stock-shortage files.

## Review Focus

1. Self approval via REST: owner who also holds the approver role saves `workflow_state` directly (bypassing `apply_workflow`) → must throw. Test in Task 2.
2. Double click on Complete: two concurrent `Complete` calls → exactly one Material Issue (`for_update` lock + `issue_entry` check). Test in Task 2.
3. Stock dropped between Submit and Complete → Complete throws a readable "Item X has only N in W" and the doc stays `Destruction Submitted`. Test in Task 2.
4. Crew edits items/amounts after approval via a REST save on a docstatus 1 doc → rejected (only `video_link` editable in Awaiting Destruction). Test in Task 2.
5. Legacy `Rejected` doc with transfer completes from the reject warehouse, not the profile warehouse. Test in Task 1 (patch) + Task 2 (issue source).

---

### Task 1: Workflow setup, schema, legacy patch

**Files:**
- Create: `pos_next/reject_request_setup.py`
- Create: `pos_next/patches/v2_25_0/__init__.py`, `pos_next/patches/v2_25_0/install_reject_request_workflow.py`
- Modify: `pos_next/patches.txt` (append under `[post_model_sync]`), `pos_next/install.py` (call `ensure_reject_request_workflow(quiet=True)` next to `ensure_stock_opname_workflow` in both after_install and after_migrate)
- Modify: `pos_next/pos_next/doctype/pos_reject_request/pos_reject_request.json`, `pos_next/pos_next/doctype/pos_reject_request_item/pos_reject_request_item.json`
- Test: `pos_next/tests/test_reject_request_setup.py`

**Interfaces:**
- Produces: constants `WORKFLOW_NAME`, `DOCUMENT_TYPE = "POS Reject Request"`, `STATE_DRAFT`, `STATE_PENDING = "Pending Approval"`, `STATE_AWAITING = "Awaiting Destruction"`, `STATE_PROOF = "Destruction Submitted"`, `STATE_COMPLETED = "Completed"`, `STATE_CANCELLED = "Cancelled"`, `CREW_ROLES`, `STATES`, `TRANSITIONS`; `ensure_reject_request_workflow(quiet=False) -> bool`; `migrate_legacy_states() -> None`.
- Schema: parent fields `approval_code` (Data, unique, read_only, no_copy, allow_on_submit), `borne_by` (Select `\nCompany\nCrew`, allow_on_submit), `return_reason` (Small Text, read_only, no_copy, allow_on_submit), `letter_head` (Link Letter Head, hidden, print_hide); `video_link`, `liabilities`, `decided_by`, `decided_on`, `issue_entry`, `closed_on` get `allow_on_submit: 1`; remove `status`, `decision`, `decision_notes` from `fields` and `field_order` (columns stay in DB). Item fields `production_date` (Date), `reject_reason` (Small Text). Perms: `POSNext Reject Approver` gains write + submit + cancel; crew roles keep write/submit, no cancel.

- [ ] **Step 1: Write failing tests** in `test_reject_request_setup.py`:
  - `test_creates_workflow_with_spec_states`: after delete-if-exists + `ensure_reject_request_workflow()`, Workflow exists, `is_active`, state names/doc_status equal `STATES`, every `(from, action, to, role, self)` of `TRANSITIONS` present.
  - `test_idempotent_and_never_displaces_admin_workflow`: second call returns False; with another active workflow on the doctype, returns False and creates nothing.
  - `test_migrate_legacy_states`: seed rows via `db_set` (status Draft/0, Pending/1, Approved/1, Rejected/1, Closed/1, Cancelled/2) → after `migrate_legacy_states()`: states per spec legacy table; Approved→`borne_by` Company, Rejected→Crew; Approved/Rejected/Closed get an 8-char `approval_code`; rerun changes nothing.
- [ ] **Step 2: Run** `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.tests.test_reject_request_setup` → FAIL (ImportError).
- [ ] **Step 3: Implement** `reject_request_setup.py` copying the opname structure (`_ensure_masters`, guard clauses, insert); `migrate_legacy_states` reads old `status`/`decision` columns with `frappe.db.sql` (fields are gone from meta) and writes with `frappe.db.set_value(..., update_modified=False)`; approval codes via a `_unique_approval_code()` helper that loops `random_code()` against `approval_code`. Patch `execute()` = `ensure_reject_request_workflow()` + `migrate_legacy_states()`. Edit the two JSONs (bump `modified`).
- [ ] **Step 4: Run** `bench --site <test site> migrate`, then the test module → PASS.
- [ ] **Step 5: Commit** `feat(reject): workflow POS Next Reject Request + skema + patch data lama`.

### Task 2: Service: transitions, guards, stock

**Files:**
- Modify: `pos_next/services/reject_request.py`, `pos_next/pos_next/doctype/pos_reject_request/pos_reject_request.py`
- Test: `pos_next/tests/test_reject_request_workflow.py` (new; reuse fixtures/helpers from `pos_next/api/test_reject_request.py` by import, e.g. its profile/item/user setup)

**Interfaces:**
- Consumes: Task 1 constants.
- Produces:
  - `validate_request(doc)` — keeps profile access, pricing, deadline, system-purpose guards, video URL check; drops the clearing of decision/status fields; adds `_guard_fields(doc)` and `_guard_self_approval(doc)`; sets `letter_head` from Company `default_letter_head` when empty.
  - `on_state_change(doc)` — called from controller `on_update` and `on_update_after_submit` and `on_submit`; no-op when `doc.workflow_state == doc._doc_before_save.workflow_state`; dispatches per spec "Side effects".
  - `set_liability(doc, borne_by: str, liabilities: list[dict] | None) -> dict` — approver only, not owner, state must be Pending Approval; reuses `_resolve_liabilities` (rewritten to take `borne_by` Company/Crew) + `_write_liabilities`; db_set `borne_by`.
  - `record_return_reason(doc, reason: str) -> None` — approver only, state Pending Approval or Destruction Submitted, non-empty reason; db_set `return_reason` + comment `Returned: <reason>`.
  - `_check_stock(doc, warehouse) -> None` (extracted from `_transfer_to_reject_warehouse`), `_issue_stock(doc) -> Stock Entry` (profile warehouse, or legacy transfer target).
  - Removed: `_transfer_to_reject_warehouse`, `apply_decision`, `close_request`, `before_cancel_request` status check (cancel is allowed from Awaiting Destruction / Destruction Submitted only — enforce via workflow; keep `on_cancel_request` reversing legacy transfers).
- Guard rules (`_guard_fields`): compare to `_doc_before_save`; in any state but Draft, `items`, `purpose`, `pos_profile`, `incident_date`, `reason` must be unchanged; `video_link` changes only in Awaiting Destruction; `approval_code`, `decided_by`, `decided_on`, `issue_entry`, `closed_on`, `borne_by`, `liabilities` changes only via service `db_set` (any change in a save throws).
- `_guard_self_approval`: when the state moves out of Pending Approval or Destruction Submitted by an approver action (Approve/Return/Complete/Cancel targets) and `frappe.session.user == doc.owner` and user != Administrator → throw PermissionError.
- `_month_total`: `workflow_state not in ('Draft', 'Cancelled')` instead of `docstatus = 1`.

- [ ] **Step 1: Write failing tests** (each via `apply_workflow` as the right user):
  - `test_submit_moves_to_pending_without_stock_entry`: no Stock Entry with remark containing doc name; `workflow_state == "Pending Approval"`, docstatus 0.
  - `test_submit_blocks_when_stock_short`: qty > stock → ValidationError containing item code.
  - `test_approve_requires_borne_by` / `test_approve_sets_code_and_submits`: after `set_liability(doc, "Company", None)` + Approve: docstatus 1, `len(approval_code) == 8`, set ⊆ `CODE_ALPHABET`, `decided_by` = approver.
  - `test_crew_liability_defaults_to_owner_full_amount`.
  - `test_strict_late_forces_crew` (late_mode Strict, incident before deadline window): after Submit, `borne_by == "Crew"`, `set_liability(doc, "Company")` throws.
  - `test_return_from_pending_back_to_draft_with_reason` and `test_return_from_proof_back_to_awaiting`.
  - `test_send_proof_requires_video_or_file`.
  - `test_complete_issues_from_profile_warehouse`: Bin qty drops by row qty; `issue_entry` set; state Completed.
  - `test_complete_twice_issues_once` (Review Focus 2): second `on_state_change` call / second Complete throws, one Material Issue.
  - `test_complete_blocks_when_stock_dropped` (Review Focus 3).
  - `test_owner_with_approver_role_cannot_approve_via_save` (Review Focus 1): `doc.workflow_state = "Awaiting Destruction"; doc.submit()` as owner → PermissionError.
  - `test_crew_cannot_edit_items_after_approve` (Review Focus 4).
  - `test_legacy_transfer_completes_from_reject_warehouse` (Review Focus 5).
  - `test_production_loss_never_moves_stock`.
- [ ] **Step 2: Run** `... _pn_run_tests.py pos_next.tests.test_reject_request_workflow` → FAIL.
- [ ] **Step 3: Implement** the interfaces above. Lock with `frappe.db.get_value(..., "issue_entry", for_update=True)` before issuing.
- [ ] **Step 4: Run** the module → PASS; run `pos_next.api.test_reject_request` and note which old tests now fail (expected: decision/close/cancel/transfer tests — fixed in Task 3).
- [ ] **Step 5: Commit** `feat(reject): efek transisi workflow, kode persetujuan, material issue di akhir`.

### Task 3: API for the SPA

**Files:**
- Modify: `pos_next/api/reject_request.py`, `pos_next/api/test_reject_request.py`

**Interfaces:**
- Consumes: Task 2 service functions.
- Produces (all `@frappe.whitelist()`):
  - `get_reject_requests(pos_profile, status=None, limit=30, offset=0)` — `status` now filters `workflow_state`; rows carry `workflow_state`, `approval_code`, `borne_by`.
  - `get_reject_request(name)` — payload adds `workflow_state`, `actions` (list of action names from `get_transitions(doc)` for the session user), `return_reason`, `approval_code`, `borne_by`, `letter_head`, item `production_date` / `reject_reason`.
  - `save_reject_request(data, submit=0)` — Draft only; accepts item `production_date` (≤ incident_date) and `reject_reason`; `submit=1` → `apply_workflow(doc, "Submit")`.
  - `apply_reject_action(name, action)` — allows only `Withdraw`, `Send Proof` (crew actions); returns `get_reject_request(name)`.
  - `save_reject_video(name, video_link)` — Awaiting Destruction only.
  - `set_reject_liability(name, borne_by, liabilities=None)`, `record_return_reason(name, reason)` — thin wrappers for Desk (Task 4).
  - `attach_reject_files`: evidence only in Draft; destruction only in Awaiting Destruction.
  - Removed: `decide_reject_request`, `close_reject_request`, `cancel_reject_request`.

- [ ] **Step 1: Update tests**: delete tests of the removed endpoints and of the transfer; add `test_detail_lists_actions_for_role` (cashier on Draft → `["Submit"]`; approver on Pending (not owner) → contains `Approve`, `Return`), `test_apply_action_rejects_approver_actions` (`apply_reject_action(name, "Approve")` → ValidationError), `test_destruction_attach_only_in_awaiting`, `test_production_date_after_incident_rejected`.
- [ ] **Step 2: Run** `pos_next.api.test_reject_request` → FAIL on the new tests.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `pos_next.api.test_reject_request pos_next.tests.test_reject_request_workflow pos_next.tests.test_reject_request_setup` → PASS.
- [ ] **Step 5: Commit** `feat(reject): API SPA pakai aksi workflow`.

### Task 4: Desk form, list, Berita Acara print format

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_reject_request/pos_reject_request.js`, `pos_reject_request_list.js`
- Create: `pos_next/pos_next/print_format/berita_acara_reject/{__init__.py,berita_acara_reject.json}`
- Test: `pos_next/tests/test_berita_acara_print.py`

**Interfaces:**
- Consumes: `set_reject_liability`, `record_return_reason` (Task 3).
- Desk JS: remove `decide_buttons` and its dialogs; `before_workflow_action(frm)`: for `Approve` from Pending Approval → dialog (`borne_by` Select; when Crew, a Table of user/amount defaulting to owner/total) → xcall `set_reject_liability`; for `Return` → required reason prompt → xcall `record_return_reason`. Use the freeze/unfreeze pattern of `public/js/stock_reconciliation.js`. Add "Berita Acara" button (opens `/printview?doctype=POS%20Reject%20Request&name=...&format=Berita%20Acara%20Reject`) when docstatus 1. List indicator by `workflow_state`.
- Print format: `doc_type` POS Reject Request, `custom_format` 1, `print_format_type` Jinja, `standard` "Yes", module POS Next, set as `default_print_format` on the doctype JSON. Content per spec D7 and the PDF template: title "BERITA ACARA ROTI / BAHAN / BARANG RUSAK (REJECT)"; "Pada hari ini, {{ frappe.utils.format_date(doc.incident_date, 'EEEE, d MMMM yyyy') }}"; Nama = owner full name, Jabatan = Outlet, Outlet = pos_profile; statement text verbatim from the template; table NO | Nama Produk | Jumlah (qty + uom) | Tanggal Produksi | Alasan Reject (row `reject_reason` or doc `reason`); signature row Dibuat Oleh (Karyawan / Crew) | Diketahui Oleh (PIC / PL Outlet) | Kode Persetujuan (Operasional Outlet) showing `approval_code`, approver full name, `decided_on` date. Body text stays Indonesian (it is the legal document).

- [ ] **Step 1: Write failing test** `test_berita_acara_renders_code_and_items`: approved doc → `frappe.get_print("POS Reject Request", name, "Berita Acara Reject")` contains `approval_code`, each `item_name`, and "BERITA ACARA"; `test_letter_head_defaults_from_company`: after save, `doc.letter_head == Company.default_letter_head`.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**; `bench migrate` to sync the print format.
- [ ] **Step 4: Run** → PASS. Manual Desk check on the test site: Approve dialog, Return prompt, Print → PDF with letter head.
- [ ] **Step 5: Commit** `feat(reject): tombol workflow Desk dan print format Berita Acara`.

### Task 5: Report and overdue task

**Files:**
- Modify: `pos_next/pos_next/report/pos_reject_report/pos_reject_report.{py,js}`, `pos_next/tasks/reject_overdue.py`
- Test: `pos_next/pos_next/report/pos_reject_report/test_pos_reject_report.py`, overdue cases in `pos_next/api/test_reject_request.py`

**Interfaces:**
- Report: base condition `workflow_state in ('Awaiting Destruction','Destruction Submitted','Completed')`; filter `borne_by` replaces `decision`; columns `workflow_state` (label Status) and `borne_by` (label Borne By) replace status/decision; company-borne rows are those with `borne_by = 'Company'` (was `decision == "Approved"`).
- Overdue task: overdue = `workflow_state = 'Draft'` and deadline passed (was docstatus 0, which now also matches Pending Approval). Work Order backfill unchanged.

- [ ] **Step 1: Update tests** to seed `workflow_state`/`borne_by`; add `test_pending_approval_not_flagged_overdue`.
- [ ] **Step 2: Run** → FAIL. **Step 3: Implement.** **Step 4: Run** → PASS.
- [ ] **Step 5: Commit** `fix(reject): laporan dan tugas overdue ikut state workflow`.

### Task 6: SPA RejectRequestDialog

**Files:**
- Modify: `POS/src/components/pos/RejectRequestDialog.vue`, `POS/src/components/pos/RejectRequestDialog.test.js`, `pos_next/translations/id.csv`

**Interfaces:**
- Consumes: Task 3 endpoints and payload.
- Behaviour:
  - Status tabs/pills from `workflow_state` (Draft, Pending Approval, Awaiting Destruction, Destruction Submitted, Completed, Cancelled) with `STATUS_TONES` keyed by those names.
  - Item rows gain Production Date (date input, max = incident date) and Reason (text).
  - Draft with `return_reason` → amber banner "Returned by Head Office: …".
  - Pending Approval: read-only; `Withdraw` button when `actions` includes it.
  - Awaiting Destruction: approval code + "Download Berita Acara" (opens `/api/method/frappe.utils.print_format.download_pdf?doctype=POS%20Reject%20Request&name=<name>&format=Berita%20Acara%20Reject&letterhead=<letter_head>` in a new tab), video link input (`save_reject_video`), destruction photo upload, `Send Proof` button enabled when a valid link or ≥1 photo.
  - Destruction Submitted / Completed: read-only summary incl. `borne_by`.
  - Remove cancel/close/decision UI.
  - **Item entry = Stock Opname row model (user request 2026-10-22).** Replace the single search box above the grid with the row-based grid of `POS/src/components/pos/StockOpnameDialog.vue`: an "Add Row" footer button (`data-testid="add-row"`) appends a blank row; each row has its own inline item search (typeahead dropdown in the row, Enter on an exact `item_code`/barcode picks it), UOM select, Qty, Production Date, Reason, and a remove button. Reuse the opname row helpers/pattern (`blankRow`, `setRow`, `clearRow`, per-row `search`/`results`, sequence guard against stale searches) — copy the pattern, do not import from the opname component. Duplicate item in two rows is allowed only if production dates differ; otherwise show the row error inline. Empty trailing rows are dropped on save.
  - **Visual revamp (user request: current look is unpleasant).** Match the look of `StockOpnameDialog.vue` (same Tailwind tokens, header, list cards, detail layout, mobile card layout for rows under `sm`, sticky action footer). List view: cards with outlet, incident date, total, state pill, item count; Detail view: a clear step indicator Draft → Pending Approval → Awaiting Destruction → Destruction Submitted → Completed with the current state highlighted; evidence/destruction photos as a thumbnail grid. Keep all existing `data-testid`s that tests still use; keep accessibility (labels, aria on row inputs).
  - **Native ERPNext feel (user request).** The item grid must look and behave like an ERPNext child table (Frappe grid): header row with column labels, row number column (`No.`), thin borders, compact rows, the clicked cell becomes an inline input (Link-style item field with dropdown showing item name + code, like Frappe's awesomplete), "Add Row" small button bottom-left, row delete via a row checkbox + "Delete" button like Frappe, empty state "No Data" box. Tab/Enter move to the next cell; Enter on the last cell of the last row adds a row. Form fields above the grid use Frappe form styling (label above control, muted `bg-gray-50`-style control, 2-column section layout on desktop).
  - **Responsive.** ≥ `md`: the grid layout above. < `md`: each row becomes a stacked card (item, UOM + qty side by side, production date, reason, delete) with full-width tap targets ≥ 40px; form sections collapse to one column; the action footer stays sticky. Verify at 375px, 768px and 1280px widths (Playwright screenshots in the ledger, `/tmp/e2e-reject`).
  - Tests additionally: `add row appends a blank row and picking an item fills it`, `empty trailing rows are not sent on save`, `step indicator highlights the current workflow state`.

- [ ] **Step 1: Update tests**: replace status/decision/close/cancel cases with `renders return reason banner on returned draft`, `shows Send Proof only in Awaiting Destruction and disables it without proof`, `Berita Acara link targets download_pdf with format and letterhead`, `submit sends production_date and reject_reason per row`, `Withdraw calls apply_reject_action`.
- [ ] **Step 2: Run** `npm run test:run -- RejectRequestDialog` (in container, `POS/`) → FAIL.
- [ ] **Step 3: Implement**; add `id.csv` rows for every new string.
- [ ] **Step 4: Run** full `npm run test:run`, `npm run build`, `npx biome lint` on the changed files → PASS, no new findings.
- [ ] **Step 5: Commit** `feat(reject): SPA ikut alur workflow dan unduh Berita Acara`.

### Task 7: End-to-end proof

**Files:**
- Create: `.superpowers/sdd/2026-10-22-reject-request-workflow/progress.md` (results; screenshots under `/tmp/e2e-reject`)

- [ ] **Step 1:** On the test site (never dev/prod): crew submits (2 items, production dates, 2 photos) → approver Returns with reason → crew sees banner, resubmits → approver Approves as Crew with 2 liabilities → crew downloads Berita Acara PDF (letter head, code, items) → crew sends video link → approver Returns → crew adds photo, Send Proof → approver Completes → Stock Ledger shows one Material Issue from the profile warehouse, Bin reduced.
- [ ] **Step 2:** Run all backend reject modules + full frontend suite once more; record counts.
- [ ] **Step 3:** Update `PROJECT_STATE.md`; ask the user before any push/deploy (deploy = migrate v2_25_0 + frontend rebuild).
