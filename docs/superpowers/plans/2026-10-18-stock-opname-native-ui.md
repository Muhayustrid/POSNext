# Stock Opname Native Desk UI + Workflow-Agnostic Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the POS Stock Opname screen look and behave like a classic Frappe Desk form, and make the opname backend work with any admin-edited Stock Reconciliation workflow.

**Architecture:** Two parallel tracks. Backend (Tasks 1-2): `pos_next/api/stock_opname.py` derives initial/returned/editable/actions from the active workflow instead of state names, and `_payload` gains flags, owner/creation and an activity timeline; the backdate cap moves to POS Next Global Settings. Frontend (Tasks 3-5): a reusable `ConfirmDialog`, then `StockOpnameDialog.vue` rebuilt as Desk list + full document page with an editable child grid, consuming the payload contract below via mocks. Task 6 docs, translations and end-to-end verification.

**Tech Stack:** Frappe v16 / ERPNext (Python, `frappe.model.workflow`), Vue 3.5 + frappe-ui + Tailwind, Vitest + Vue Test Utils.

**Spec:** `.scratch/stock-opname/ui-native-spec.md` (N1-N15), parent `.scratch/stock-opname/spec.md`.

## Global Constraints

- Blind count is inviolable: outlet payloads never carry `current_qty`, `current_valuation_rate`, `quantity_difference`, `amount_difference`, `custom_qty_before`, `custom_qty_difference_per_uom`, `custom_valuation_rate_per_uom`, `custom_conversion_factor`, `difference_amount`, `expense_account`. `_payload` is built field-by-field, never `doc.as_dict()`.
- No workflow state or action name may be hardcoded in `pos_next/api/stock_opname.py`, `pos_next/public/js/stock_reconciliation.js` or `StockOpnameDialog.vue` (constants in `pos_next/stock_opname_setup.py` are for installation only).
- No `window.confirm` in `StockOpnameDialog.vue`.
- Backend tests: inside container `erpnext16_dev-frappe-1`, serially:
  `docker exec -w /workspace/development/frappe-bench -e FRAPPE_STREAM_LOGGING=1 erpnext16_dev-frappe-1 ./env/bin/python apps/pos_next/.claude/worktrees/stock-opname-hardening/pos_next/_pn_run_tests.py <module>`
- Frontend tests: `docker exec -w /workspace/development/frappe-bench/apps/pos_next/.claude/worktrees/stock-opname-hardening/POS erpnext16_dev-frappe-1 npx vitest run <file>`
- Subagents never run git state-changing commands (`add`, `commit`, `stash`, `checkout`, `reset`); the controller commits after review. Conventional commits with scope, Indonesian descriptions.
- New UI strings get rows in `pos_next/translations/id.csv` (Task 6 collects them).
- Desk palette for indicators: red `bg-red-500`, orange `bg-orange-400`, green `bg-green-500`, blue `bg-blue-500`, grey `bg-gray-400` dots, text `text-gray-700`.

## Payload contract (shared by both tracks)

`get_opname` / `save_opname` return `_payload(doc)`:
```
{ name, workflow_state, docstatus, modified, posting_date, posting_time, set_warehouse,
  set_posting_time: 0|1, owner, owner_fullname, creation, modified_by_fullname,
  editable: bool, returned: bool, processing: bool, actions: [str],  # actions [] unless editable+saved
  reject_reason: str|None,  # only when returned
  items: [...unchanged...], attachments: [...unchanged...],
  activity: [{type: "created"|"workflow"|"comment"|"attachment", state?: str, content?: str,
              user: str, user_fullname: str, at: "YYYY-MM-DD HH:MM:SS"}] }  # oldest first
```
`list_opnames` rows: existing fields + `editable`, `returned`, `processing` (replaces the state-name check).
`submit_opname(pos_profile, name, action=None)` → `{name, workflow_state}`.

## Review Focus

- Admin renames states/actions → outlet flow must still save, submit, get returned and resubmit: Task 1 fixture-workflow test.
- Admin adds an approval stage → list shows it orange and non-editable, Continue/New Count guard still blocks a second open count: Task 1 list test + Task 4 tone test.
- No active Stock Reconciliation workflow → save works, submit throws the clear message (not a 500/KeyError): Task 1 test.
- Device clock ahead of site TZ → a new count without "Edit Posting Date and Time" must not send posting fields: Task 4 payload test.
- Navigating back with unsaved grid edits → confirm modal, never silent loss: Task 4 test.

---

### Task 1: Workflow-agnostic backend + Desk JS

**Files:**
- Modify: `pos_next/api/stock_opname.py` (remove `EDITABLE_STATES`/`STATE_*` uses: lines ~33-37, 345, 392-393, 488, 501, 542, 552, 569, 583, 804, 992)
- Modify: `pos_next/stock_opname_setup.py` (`ensure_stock_opname_workflow` create guard)
- Modify: `pos_next/public/js/stock_reconciliation.js`
- Test: `pos_next/api/test_stock_opname.py` (new class `TestStockOpnameCustomWorkflow`; existing tests keep passing on the default workflow)
- Test: `pos_next/tests/test_stock_opname_setup.py`

**Interfaces:**
- Produces in `stock_opname.py`:
  - `_workflow()` → `frappe.model.workflow.get_workflow(DOCTYPE)` doc, or `None` when `get_workflow_name(DOCTYPE)` is empty.
  - `_initial_state(wf) -> str` = `wf.states[0].state`.
  - `_returned_states(wf) -> set[str]`: docstatus-0 states ≠ initial with a transition whose `next_state == initial`.
  - `_back_transition(wf, state, roles) -> str|None`: action of a transition `state → initial` allowed for one of `roles`.
  - `_state_flags(doc_or_row, wf, roles) -> dict(editable, returned, processing)`; with `wf is None`: editable = docstatus 0, returned/processing False.
  - `_submit_actions(doc, wf) -> list[str]`: actions from `get_transitions(doc)` when doc state is initial; [] otherwise.
  - `submit_opname(pos_profile, name, action=None)`.
- Consumes: nothing new.

- [ ] **Step 1: Write failing tests** in `TestStockOpnameCustomWorkflow` (setUpClass inserts an active workflow `SO Test Flow` on Stock Reconciliation: states `Open`(0, outlet edit) → `Area Check`(0) → `HQ Check`(0) → `Done`(1), `Returned`(0), `Void`(2); actions `Send` Open→Area Check (POSNext Cashier, self ok), `Pass` Area Check→HQ Check, `Pass` HQ Check→Done, `Return` Area Check/HQ Check→Returned (Stock Manager / Accounts Manager), `Fix` Returned→Open (POSNext Cashier). tearDownClass deletes it, re-activates `POS Next Stock Opname` with `frappe.db.set_value("Workflow", ..., "is_active", 1)` and `frappe.cache.hdel("workflow", DOCTYPE)`). Tests:
  - `test_custom_flow_full_cycle`: save → payload `editable True`, `actions == ["Send"]`; `submit_opname` → state `Area Check`, payload `editable False`, `actions []`; HQ `apply_workflow(doc, "Return")` + `Rejected: too low` comment → payload `returned True`, `editable True`, `reject_reason == "too low"`; `save_opname` → state `Open`; submit again; HQ Pass, Pass → docstatus 1, state `Done`.
  - `test_list_flags_for_extra_stage`: a doc in `HQ Check` → list row `editable False`, `returned False`, `processing False`.
  - `test_submit_with_unknown_action_rejected`: `submit_opname(..., action="Pass")` throws `ValidationError`.
  - `test_no_active_workflow`: deactivate all (`is_active 0`, clear cache) → `save_opname` works, payload `editable True`, `actions []`; `submit_opname` raises `ValidationError` with "No active workflow".
  - In `test_stock_opname_setup.py`: `test_existing_other_workflow_not_displaced`: with another active SR workflow and `POS Next Stock Opname` deleted, `ensure_stock_opname_workflow()` returns False and creates nothing.
- [ ] **Step 2: Run to verify they fail** — runner `pos_next.api.test_stock_opname` `pos_next.tests.test_stock_opname_setup`; expected: new tests FAIL (state-name logic, no `action` param).
- [ ] **Step 3: Implement.** Replace every `EDITABLE_STATES`/`STATE_*` comparison with `_state_flags`; Save on a returned doc applies `_back_transition(...)` (throw `ValidationError` "You cannot revise this count" when None); `submit_opname` requires the doc state to be initial, picks/validates `action` against `_submit_actions` (none → "You are not allowed to submit this count, ask HQ"; several and no action → message listing them), keeps posting stamping as is; `_reject_reason` called when `returned`. `ensure_stock_opname_workflow`: return False when any `Workflow` with `document_type = Stock Reconciliation` exists. Desk JS: `Show System Qty` condition = `docstatus === 0` + roles; `before_workflow_action` prompts when the selected transition's `next_state` is a returned state, computed from `frappe.workflow.workflows[frm.doctype]` (states[0] = initial; returned = transition target that has a transition back to initial).
- [ ] **Step 4: Run** full `pos_next.api.test_stock_opname`, `pos_next.tests.test_stock_opname_setup`, `pos_next.tests.test_stock_opname_override` — all PASS.
- [ ] **Step 5: Report** changed files to controller (controller commits `feat(stock): workflow opname mengikuti definisi workflow aktif`).

### Task 2: Payload metadata, activity timeline, backdate setting

**Files:**
- Modify: `pos_next/api/stock_opname.py` (`_payload`, `_max_backdate_days`)
- Modify: `pos_next/pos_next/doctype/pos_next_global_settings/pos_next_global_settings.json` (Int `opname_max_backdate_days`, label "Stock Opname Max Backdate Days", default `2`, `non_negative: 1`, placed in the section holding `allow_negative_stock`)
- Modify: `pos_next/api/settings_resolver.py` (`GLOBAL_FIELDS` += `"opname_max_backdate_days"`)
- Test: `pos_next/api/test_stock_opname.py`

**Interfaces:**
- Consumes: Task 1 `_state_flags`, `_submit_actions` (payload keys `editable`, `returned`, `processing`, `actions`).
- Produces: payload contract above (`set_posting_time`, `owner`, `owner_fullname`, `creation`, `modified_by_fullname`, `activity`).

- [ ] **Step 1: Write failing tests**
  - `test_payload_activity_after_reject`: save, submit, HQ reject with comment `Rejected: count again`, attach a link → `activity[0]["type"] == "created"` and `user == cashier`; contains `{"type": "workflow", "state": <pending state>}` and `{"type": "comment", "content": "count again"}`; attachment entry `content` has no `<`; list is sorted by `at` ascending.
  - `test_payload_owner_fields`: `owner == cashier`, `owner_fullname == "SO API Test"`, `creation` set, `set_posting_time` in (0, 1).
  - `test_backdate_cap_from_global_setting`: set `POS Next Global Settings.opname_max_backdate_days = 5` → save with posting date today-4 succeeds; today-6 raises; restore in `addCleanup`. `test_backdate_cap_default`: setting blank → today-3 raises (default 2).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.** Activity from `frappe.get_all("Comment", filters reference_doctype/name, comment_type in ("Workflow","Comment","Attachment"), fields content/comment_type/owner/creation, order_by creation asc)` plus the synthetic `created` entry; `Comment` content strips `REJECT_PREFIX`; attachment content via `frappe.utils.strip_html`; full names via `frappe.utils.get_fullname`. `_max_backdate_days` reads `frappe.db.get_single_value("POS Next Global Settings", "opname_max_backdate_days")`, blank/None → `MAX_BACKDATE_DAYS`; update its docstring.
- [ ] **Step 4: Run** `bench --site posnext.localhost migrate` and `--site roti-posnext-test.localhost migrate` (container, bench root), then full `pos_next.api.test_stock_opname` + `pos_next.tests.test_naming_series_settings` (GLOBAL_FIELDS neighbour) — PASS.
- [ ] **Step 5: Report** (controller commits `feat(stock): activity, metadata dan setting backdate opname`).

### Task 3: ConfirmDialog component

**Files:**
- Create: `POS/src/components/common/ConfirmDialog.vue`
- Test: `POS/src/components/common/ConfirmDialog.test.js`

**Interfaces:**
- Produces: `<ConfirmDialog v-model="open" :title :message :confirm-label :destructive @confirm @cancel />` using frappe-ui `Dialog` (`size: "xs"`), Cancel (`variant="subtle"`) + confirm (`variant="solid"`, `theme="red"` when `destructive`). Closing by overlay/Esc emits `cancel`. The host component drives it with a local helper `confirmAsk({title, message, confirmLabel, destructive}) -> Promise<boolean>` (Task 4 writes the helper inside the dialog).

- [ ] **Step 1: Failing tests:** renders title/message; clicking confirm emits `confirm` and `update:modelValue(false)`; clicking Cancel emits `cancel`; `destructive` passes `theme="red"` to the confirm Button.
- [ ] **Step 2: Run** — FAIL (file missing).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** — PASS.
- [ ] **Step 5: Report** (controller commits with Task 4).

### Task 4: Desk-style list + document page shell

**Files:**
- Modify: `POS/src/components/pos/StockOpnameDialog.vue`
- Test: `POS/src/components/pos/StockOpnameDialog.test.js`

**Interfaces:**
- Consumes: payload contract; Task 3 `ConfirmDialog`.
- Produces for Task 5: `view` ∈ `"list" | "doc"`; `editor` keeps current row shape (`blankRow`, `normalizeItem`, `buildItems` unchanged); `dirty` computed from a JSON snapshot of `{items: buildItems(), set_posting_time, posting_date, posting_time}` taken in `applyEditor`/after save.

- [ ] **Step 1: Failing tests** (update existing ones to the new structure; keep existing `data-testid`s where the element survives):
  - list renders a bordered table header `ID | Status | Posting Date | Items`; a row with `editable` flag and no docstatus shows **Continue**; a row with state `Area Check` (`editable false`) blocks New Count; indicator dot classes: returned → `bg-red-500`, other docstatus-0 non-editable → `bg-orange-400`, `processing` → `bg-blue-500`, docstatus 1 → `bg-green-500`, docstatus 2 → `bg-gray-400`.
  - clicking a row opens the doc page: breadcrumb text `Stock Opname`, document name, Details section shows warehouse, owner full name, posting date.
  - primary button: new doc → `Save`; after save response with `actions: ["Submit"]` → button `Submit`; editing a qty → back to `Save`; with `actions: ["Send", "Escalate"]` → an `Actions` dropdown listing both.
  - Submit: opens ConfirmDialog (assert `window.confirm` spy not called); confirming calls `submit_opname` with `action: "Submit"`.
  - Delete lives in the `⋯` menu, only for saved editable docs, and asks ConfirmDialog.
  - posting inputs `disabled` until `Edit Posting Date and Time` is ticked; save payload omits `posting_date`/`posting_time` when unticked and includes them + `set_posting_time: 1` when ticked; a loaded doc with `set_posting_time: 1` and a non-today date starts ticked.
  - back with dirty edits asks "Discard unsaved changes?"; cancelling keeps the page.
  - activity renders one entry per payload item, newest last, with user full name.
  - non-editable doc renders values as text (no inputs, no Add Row).
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** list (Desk list toolbar + table), doc page (header with back arrow, breadcrumb, indicator, `⋯` menu, primary action; sections Details → Items (existing grid kept for now) → Attachments → Activity), `confirmAsk` replacing all 4 `window.confirm` calls, posting checkbox, dirty guard. Editability/tones read payload flags only. Actions slot no longer used for the doc page (actions live in the header).
- [ ] **Step 4: Run** `StockOpnameDialog.test.js`, `ConfirmDialog.test.js`, `POSMenuDialog.test.js` — PASS.
- [ ] **Step 5: Report** (controller commits `feat(pos): tampilan stock opname gaya Desk`).

### Task 5: Desk child grid with Add Row

**Files:**
- Modify: `POS/src/components/pos/StockOpnameDialog.vue`
- Test: `POS/src/components/pos/StockOpnameDialog.test.js`

**Interfaces:**
- Consumes: Task 4 doc page, `dirty`, existing `search_opname_items`, `get_item_batches`, `get_item_serials`.

- [ ] **Step 1: Failing tests:**
  - `Add Row` appends a blank row with an Item link input (`data-testid="row-item-input"`); typing `ROTI` calls `search_opname_items` (debounced, use fake timers) and shows a dropdown; clicking a result fills item name, UOM select and qty `1`.
  - Enter with an exact `item_code` match fills the row (barcode).
  - batch item: row shows a batch `select` (`data-testid="row-batch-select"`) with `get_item_batches` options, qty `0`; Save with a batch row without batch shows an error toast and does not call `save_opname`.
  - duplicate: picking an item already in the grid (same batch for batch items) shows an error toast and clears the row.
  - blank rows are dropped from the `save_opname` payload.
  - serial item: tick list + scan input render in the sub-row; qty = ticked count (existing behaviour).
  - Excel toolbar (Item Group, Download Template, Upload) renders above the grid; Upload over unsaved rows asks ConfirmDialog.
  - the old batch picker (`data-testid="batch-picker"`) and bottom search box are gone.
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement** the grid (bordered table, grey header, `No.` column, row card layout below `md`), per-row search state (one shared debounce timer + sequence guard like `doSearchItems`), Add Row footer. Remove `batchPicker`, `addBatchRows`, `itemSearch`/`itemResults` globals.
- [ ] **Step 4: Run** full FE suite `npx vitest run` — PASS; `npm run build` — OK; `npx biome lint src/components/pos/StockOpnameDialog.vue src/components/common/ConfirmDialog.vue` — no new findings.
- [ ] **Step 5: Report** (controller commits `feat(pos): grid item opname dengan Add Row`).

### Task 6: Docs, translations, end-to-end check

**Files:**
- Modify: `docs/STOCK_OPNAME.md` (new section "Changing the workflow" per spec N15; outlet flow section updated for the new screen; setup section names the backdate setting)
- Modify: `.scratch/stock-opname/spec.md` (D6 setting location; "Known limitations": serial option count reveals system qty)
- Modify: `pos_next/translations/id.csv` (+ other CSVs per `docs/LOCALIZATION.md`) for every new string from Tasks 1-5

- [ ] **Step 1:** grep new `__("...")` / `_("...")` strings in the diff since 961563e; add missing rows.
- [ ] **Step 2:** write docs.
- [ ] **Step 3:** build (`npm run build` in POS, container), `bench --site posnext.localhost clear-cache`, Playwright on `http://localhost:8001/pos`: screenshots of list, new count with Add Row, saved draft (Submit button), pending read-only with activity. Run Desk reject on a test count and confirm the reason shows in POS.
- [ ] **Step 4:** full backend `pos_next.api.test_stock_opname`, `pos_next.tests.test_stock_opname_setup`, `pos_next.tests.test_stock_opname_override` + FE suite — PASS.
- [ ] **Step 5:** controller commits `docs(stock): panduan workflow opname dan terjemahan`.
