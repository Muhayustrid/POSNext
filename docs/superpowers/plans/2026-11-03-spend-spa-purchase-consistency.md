# Spend SPA Purchase Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present Spend as a PO/PR-style list and separate form with human-readable item/supplier selection, without weakening transaction recovery.

**Architecture:** Retain SpendDialog and the existing parent-owned attempt. Add display metadata to the existing Template response and a scoped Spend-specific supplier lookup. Reuse purchase controls and visual conventions rather than a new shared purchase framework.

**Tech Stack:** Frappe/ERPNext v16, Vue 3, frappe-ui, Tailwind, Vitest, existing Docker bench.

**Spec:** `docs/superpowers/specs/2026-11-03-spend-spa-purchase-consistency-design.md` (user approved 2026-11-03).

## Global Constraints

- Preserve all prior uncommitted Company Scope/refactor work. No commit/push authorization; previous proposed commit message remains unapproved.
- Inherit PO/PR Operate visual world and `docs/UI_GUIDELINES.md`. No dependencies, new palette, new global shell or speculative table framework.
- Preserve existing `items: list[str]` and submit payload, native PI/JE, account rules, atomic attachments, lock ordering, cancellation and idempotency.
- No stock/tax/UOM conversion, auto pricing, offline Spend, server draft or detail feature added.
- Parent attempt survives embedded unmount; unresolved attempt cannot be reset/discarded or silently replaced by New.
- Search only permitted Template item metadata; supplier lookup authorizes company/profile/Template and returns only ID/name.
- All new UI copy translated; append id.csv without changing old bytes/CRLF.
- Backend tests serial on verified fix site. Frontend toolchain in container; no installing/upgrading dependencies.
- No production writes/deployment. Backup before sanctioned dev rebuild; do not change QA permissions or passwords to bypass a failed browser check.

## Review Focus

1. Lost response followed by Back, New, reopen or company switch must never create a second transaction; Task 2 pins frozen payload and parent recovery.
2. Duplicate item names must remain distinguishable and submit their actual codes; Task 2 pins secondary codes and chosen payload.
3. Late supplier search for another company/Template must not overwrite current options; Task 2 pins stale responses and timer cleanup.
4. A fixed-supplier or foreign Template must not expose searchable suppliers; Task 1 pins rejection and minimal returned fields.
5. Mobile dropdowns and large tables must not clip or hide controls; Task 3 pins viewport/open-dropdown inspection and overflow assertions.

## Task 1: Human-readable authorized lookup data

**Files:** Modify `pos_next/api/spend.py`; test `pos_next/api/test_spend.py`.

**Interfaces:**
- `get_spend_templates(company, pos_profile=None)` retains current fields and `items`; adds `item_details: list[{item_code,item_name,stock_uom}]`, and `default_supplier_name`.
- New whitelisted `search_spend_suppliers(company, pos_profile, template, search_term=None) -> list[{name,supplier_name}]`; fixed maximum 20, ordered by supplier_name then name. Uses `_scope`, `_authorize`, PI-only and allow_supplier_change gates; disabled suppliers excluded, internal and external eligible. Validate search_term is text/null and query by ID or name.

- [ ] Inspect current fixture helpers, API tests and applicable instructions; record pre-edit diff so previous work remains identifiable. Verify fix container/site; run current Spend API baseline serially.
- [ ] Write tests for additive metadata: codes unchanged, true item names/UOM, default Supplier name, metadata only allowed items, missing labels safely omitted/fallback. Assert lookup returns active external supplier by human name/ID, max20 and only name/supplier_name.
- [ ] Write authorization tests for Guest, foreign user company/profile, disallowed role, disabled/out-of-scope Template, JE, fixed supplier, invalid search term and disabled supplier. Restore configuration/user context in cleanup.
- [ ] Run tests RED for absent metadata/lookup; distinguish fixture errors from behavioral failures.
- [ ] Implement metadata with one batch Item query per Template (skip empty list), mapping back to allowed code order. Add the lookup using existing Spend authorization, not PO internal-only search; keep all writes unchanged.
- [ ] Run API/Template/accounts tests serially with `docker exec erpnext-fix-backend-1 bash -lc 'cd /home/frappe/frappe-bench && PN_SITE=fix.localhost FRAPPE_STREAM_LOGGING=1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>'`; modules `pos_next.api.test_spend`, `pos_next.pos_next.doctype.pos_spend_template.test_pos_spend_template`, `pos_next.tests.test_spend_accounts`. Expected new tests and existing regression green.

## Task 2: Purchase-style list/form and readable inputs

**Files:** Modify `POS/src/components/spend/SpendDialog.vue`, `POS/src/components/spend/SpendDialog.test.js`; modify `POS/src/components/pos/POSMenuDialog.vue` and `.test.js` only if required by parent recovery wiring. Append `pos_next/translations/id.csv`.

**Interfaces:**
- Keep props company/posProfile/isOffline and v-model:attempt unchanged.
- Internal `view` values `list` / `form`: initialize list unless an unresolved attempt requires recovery form. Keep existing submit method and payload shape.
- Reuse `AutocompleteSelect` option `{value,label,subtitle}` (code as value, human name as label), `PurchaseAttachments`, `RefreshButton`, and `ConfirmDialog` following their actual installed props/events. No modification of shared controls without a demonstrated defect necessary for this task.
- Supplier lookup consumes Task1 and is debounced 300ms; context/term generation invalidates late responses, timers clear on unmount. Prime default supplier option from Template metadata.

- [ ] Read current Spend tests, PO form/list markup and shared control interfaces. Load Impeccable craft-floor immediately before first UI edit; persist approved direction in surface brief using the shipped command. No repeat concept selection.
- [ ] Add failing tests for list-first, loading/empty/error/reload/local history search and New->form; absent context shows guidance. Update existing helpers to enter New explicitly rather than weakening old assertions.
- [ ] Add failing PI tests with names unlike codes and duplicate human names: filter by name/code, show secondary identifier/UOM, submit correct code/qty/rate, correct row subtotal/total after edits/removal. Add JE assertions for description/amount columns only.
- [ ] Add failing supplier tests for default label, fixed-supplier read-only display, debounce results, late-context/term rejection, search error retry and unmount cleanup. Use real controls for at least one name-selection test instead of testing a stub's internal behavior.
- [ ] Add navigation tests: dirty Back confirm cancel retains data, confirm accepts clears only unsubmitted form; successful submit returns list and refreshes it. Keep attachment and cancel tests.
- [ ] Pin recovery: rejected/lost response preserves exact request ID/payload; Back/Reset/New cannot clear unresolved attempt; menu unmount/reopen shows recovery; company context stays pinned to attempt; offline cannot submit/search. Preserve current limitations across full page reload.
- [ ] Run focused Vitest RED inside verified container POS directory, then implement minimum list/form markup and state. PI columns No/Item/Qty/Rate/Subtotal/Remove; JE No/Description/Amount/Remove. Header actions match PO; avoid overflow clipping ancestor around dropdown. Translate human labels/placeholders and append only absent CSV rows.
- [ ] Run SpendDialog, POSMenuDialog, PurchaseOrderDialog and PurchaseReceiptDialog tests GREEN in container. Check new label/input accessible association and touch targets. Verify unchanged backend submit and cancellation contracts by existing tests.

## Task 3: Render, review and dev delivery

**Files:** Existing Spend targets; optional existing POS harness fixtures only as needed (local/untracked); create `docs/proofs/2026-11-03-spend-spa-purchase-consistency.md`; update local ignored `PROJECT_STATE.md` and this plan's ledger. Surface brief remains development-only, never copied into shipped markup.

**Interfaces:** Real backend smoke is read-only unless user separately authorizes test transactions. Harness synthetic data proves rendering/interactions, not ledger behavior. Reviewers receive main checkout artifacts directly without worktree Git access; do not bypass a denied tool action.

- [ ] Run full frontend test command and production build using installed container Node toolchain. Run backend Spend API/cancel/shift/setup/Template/accounts serially. Run relevant lint and CRLF-aware diff check; record all actual failures by name and compare proven baselines only.
- [ ] Capture one batched round at1280,820,390,2000 widths: empty/populated list, pre-Template form, PI/JE, open dropdown, overlong names, error, attachments/cancel and recovery state. Inspect captures for clipping/overflow/label clarity and batch necessary corrections; confirm corrected states in one final round.
- [ ] Run Impeccable detector once on changed web targets after UI finish; fix mechanical findings and retain remaining report for reviewer.
- [ ] Dispatch shipped Impeccable finish reviewer with request, confirmed direction, surface contract, valid screenshots and craft-floor; do not choose worktree isolation for a review of uncommitted main-checkout files. Also obtain code review on financial/recovery/lookup changes using pinned opus-xhigh. If access is denied, stop that path and disclose; do not transfer denied artifacts as a workaround.
- [ ] Address material review findings in a single focused batch with failing regression tests, recapture required sizes and obtain verdict on listed visual fixes. Document incumbent-system consistency through Impeccable documenter; no new global DESIGN.md/world for this narrow extension.
- [ ] After passing checks, backup dev using `/Users/rotiropi/ERPNext-Project/docker/erp backup dev`, rebuild via `/Users/rotiropi/ERPNext-Project/docker/erp rebuild dev backend`, verify exit0, served asset markers and read-only API metadata/lookup. Migrate/cache per existing deployment requirements, not assumed schema work for UI.
- [ ] Browser smoke dev list and form without submitting financial documents. If user role lacks access, report rather than modify role/password; use clearly labelled harness evidence separately.
- [ ] Record proof: exact test/build/review/rollout results, screenshot paths, unresolved limitations and no-commit/prod status. Update local state <=80 lines/30000 bytes. Present result; no commit/push until exact-message approval.

## Handoff

Recommend Native execution: one tightly coupled Spend view plus additive lookup data, followed by fresh code and visual reviews. User must review this plan and choose execution method before product edits. Previous Native choice applied to Company Scope work, not automatically this UI task.
