# Stock Opname SR UOM Blind-count Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate the shared SR UOM engine while preventing outlet access to stock-system quantities, valuation, and revealing differences through Stock Reconciliation/Opname surfaces.

**Architecture:** warehouse_app owns UOM arithmetic and its public warehouse endpoints; pos_next owns blind-count behavior, the existing HQ policy, and native SR permission integration. Use native field permlevels for document serialization, explicit authorization before ledger-bearing endpoint calls, and the existing POS response allowlist. Internal document conversion remains ungated.

**Tech Stack:** Frappe/ERPNext v16, Python, native Property Setter/Custom DocPerm, Docker runtime, unittest/Frappe integration tests; existing Vue/Vitest only for regression checks.

**Spec:** `docs/superpowers/specs/2026-11-03-stock-opname-sr-uom-blind-count-design.md` (approved directly by user on 2026-11-03).

## Global Constraints

- No core edits, dependency changes, automatic commits/pushes, or production deployment.
- The plan is not execution approval. User must approve this plan before product changes or runtime fixture writes.
- Preserve native accounting values on submit; enforce confidentiality at read boundaries, not by destroying accounting data.
- Preserve stock-UOM behavior without warehouse_app, per-row import errors, serial/batch validation, workflow, and draft row retention.
- Both protections and permission metadata must be active before exposing the coordinated release.
- Keep user/untracked changes; never copy files into the fix container's bind-mounted pos_next checkout.
- Durable engineering instructions are English. User-facing summaries and any proposed Conventional Commit description are Indonesian.
- Each commit checkpoint means inspect the diff, propose an exact scoped message, and wait for user approval. Do not run commit commands automatically.

## Review Focus

1. Outlet+Stock User remains blind; adding an already-authorized HQ role follows existing HQ policy, not new role precedence (Task 2 matrix tests).
2. Submitted or HQ-edited documents and generic save responses must not bypass field restrictions (Task 3 HTTP tests).
3. Import equality/matched-row counts must not reveal ledger state even if response fields are scrubbed (Task 4 denial-before-read tests).
4. Repeated save/rounding and custom fields left behind without warehouse_app must not silently alter quantities (Task 5 regressions).
5. Reloaded background submit and a partially migrated image must not bypass conversion or expose data (Tasks 6–7 runtime/release checks).

---

## Evidence and fixed role policy

Verified source anchors:
- `pos_next/stock_opname_setup.py:30–31`: OUTLET_ROLES Cashier/Manager; HQ_ROLES Stock Manager, Accounts Manager, System Manager, Administrator.
- `pos_next/overrides/stock_reconciliation.py:139–167`: HQ membership and warehouse controller scope.
- `frappe/model/document.py:957–987`: field-level read filtering includes child fields; `1021–1044`: higher-level write reset, with special new-document behavior.
- `frappe/desk/form/save.py:87–88`: field filtering before save serialization. Inspect installed container version too; host source is not runtime proof.
- `erpnext/stock/doctype/stock_reconciliation/stock_reconciliation.py:1320–1413`: public get_stock_balance_for checks SR write and returns qty/rate.
- Frappe HTTP handler applies override_whitelisted_methods; internal Python calls do not use HTTP dispatch.

| Role combination | Existing HQ predicate | System-data endpoint policy | Native SR access |
|---|---|---|---|
| Cashier only; Manager only; Cashier+Manager | false | deny before ledger read | existing outlet warehouse scope, level0 blind fields |
| Any outlet role + Stock User | false | deny before ledger read | same blind scope; Stock User is not an HQ override |
| Stock User only | false | deny | no standard SR permission; do not grant it |
| Stock Manager | true | allow only with native SR and context permissions | preserve existing native permissions |
| Accounts Manager / System Manager / Administrator | true | allow only with native SR and context permissions | preserve existing HQ policy; Administrator uses native bypass |
| Outlet + Stock Manager / another existing HQ role | true | same authorized HQ path | preserve existing HQ precedence |
| Unrelated role / Guest | false | deny | no new access |

This is the existing opname policy, not a claim that every Stock User lacks stock visibility elsewhere. Existing sales/product availability, Bin/SLE access, and unrelated stock APIs are outside this SR integration. Do not claim global blindness across the ERP. If the user requires that broader restriction, stop and seek a separate scope/role decision rather than removing Stock User permissions opportunistically.

## File and ownership map

**pos_next session:**
- Modify `pos_next/overrides/stock_reconciliation.py`: shared authorization predicate, native endpoint wrappers, post-validation blanking, remove duplicate valuation method.
- Modify `pos_next/hooks.py`: native public endpoint override bindings.
- Modify `pos_next/stock_opname_setup.py`: idempotent sensitive-field permission setup and level1 HQ grants.
- Modify `pos_next/install.py`: run permission setup on install/migrate after standard-permission mirroring, independently of workflow creation.
- Modify `pos_next/fixtures/custom_docperm.json`: remove SR print/export/report flags from outlet entries; keep unrelated entries unchanged.
- Modify `pos_next/api/stock_opname.py`: shared resolver validation and delegated conversion.
- Extend `pos_next/tests/test_stock_opname_setup.py`, `pos_next/tests/test_stock_opname_override.py`, `pos_next/api/test_stock_opname.py`.
- Create `pos_next/tests/test_stock_opname_data_access.py`: matrix, native wrappers, HTTP serialization and read-surface regressions.

**warehouse-app-58 session only:**
- Modify `warehouse_app/warehouse_app/stock_reconciliation.py`, `warehouse_app/warehouse_app/sr_import.py`: guarded public read paths.
- Extend `warehouse_app/tests/test_sr_endpoints.py`; create `warehouse_app/tests/test_sr_access.py` for public boundary and import preflight tests.
- Metadata custom fields remain warehouse-owned; agree the coordination rule in Task 3 before editing metadata. No direct edits by the pos_next worker in that repository.

**Local operational artifacts:** isolated test compose override under gitignored root `docker/`; task state in untracked PROJECT_STATE.md; proof `docs/proofs/2026-11-03-stock-opname-sr-uom-blind-count.md`.

### Task 1: Establish a safe co-installed runtime and baseline

**Files:** inspect root `docker/compose.fix.yaml`, runtime mounts, `_pn_run_tests.py`, warehouse test files; create only a local isolated-test compose override after approval.

**Interfaces:** Produces a verified test container/site manifest with both exact checkouts and installed Frappe/ERPNext versions. Do not replace the shared fix runtime or use dev/prod for automated fixtures.

- [ ] Inspect Docker mounts, installed apps and runtime source paths read-only. Current fix backend only mounts pos_next; its existing warehouse source is not evidence of feature co-installation. Record both app HEAD/diffs and resolve any stale worktree base before using a worker.
- [ ] Prepare a distinct Compose project `erpnext-sr-uom-test`, distinct ports/network/volumes, using the existing pinned image and explicit mounts of the approved pos_next checkout and warehouse feature checkout. Mounts are read-only where practical; no source copying into either checkout. No dev/prod volume reuse or credentials. If initialization requires extra infrastructure/dependencies, ask rather than expanding the environment silently.
- [ ] Create a synthetic-only site `sr-uom-test.localhost`, install both apps through native bench setup, migrate, and provision the smallest Company/Warehouse/Item/UOM/POS Profile/users needed by tests. Reuse existing fixture setup, not restored production data. Record site marker and volume provenance before any test writes.
- [ ] Validate runtime MRO includes StockReconciliationUOM → CustomStockReconciliation → native StockReconciliation. Record method source paths; do not monkeypatch MRO to make tests pass.
- [ ] Run baseline serially with `FRAPPE_STREAM_LOGGING=1`, `PN_SITE=sr-uom-test.localhost`, `SITES_PATH=/home/frappe/frappe-bench/sites`, bench venv Python and `_pn_run_tests.py` modules `pos_next.tests.test_stock_opname_setup`, `pos_next.tests.test_stock_opname_override`, `pos_next.api.test_stock_opname`. Record failures individually; no blanket baseline exclusions.
- [ ] Record the exact docker exec command using the created backend name in the proof; confirm nonzero test count. Infrastructure failures are blockers, not test passes. No automatic commit.

### Task 2: Define and test the shared access boundary

**Files:** modify `pos_next/overrides/stock_reconciliation.py`, `pos_next/hooks.py`; test `pos_next/tests/test_stock_opname_data_access.py`.

**Interfaces:**
- Produces internal `assert_system_stock_access(warehouse=None, company=None, user=None) -> None` in `pos_next.overrides.stock_reconciliation`. Not whitelisted. Two explicit stages: with warehouse=None and company=None it checks only authenticated existing HQ membership and SR read permission; this is a role-only precheck and NEVER authorizes a subsequent stock read. With a nonempty warehouse it additionally derives Company, verifies native Warehouse/Company read access and rejects supplied-company mismatch. A company without warehouse is invalid. No ledger reads, mutation, or permission grants. Every public stock-bearing route must reject empty warehouse and complete the contextual call before reading stock; upload first performs role-only precheck, then parses and context-checks every warehouse before validation.
- Existing `is_hq_user(user=None)` remains the sole role policy. Public endpoints always use frappe.session.user and never accept a request user as authority; Guest/unknown users fail closed. The helper's optional user is internal/test-only. All stock-bearing calls require a nonempty warehouse; derive company from Warehouse and cross-check any supplied company. Missing warehouse cannot skip scope. Public callers retain their native action-specific write/create checks; SR read is additionally required intentionally, so write-only/create-only roles are not authorized to read stock.
- Native wrapper `get_stock_balance_for` retains the exact native argument signature and delegates only after authorization. Native get_items fallback wrapper retains its exact native signature. These wrap public HTTP dispatch only, not native internal validation calls.

- [ ] Add `test_system_access_role_matrix`, covering every table row above, native permission denied despite HQ role, unauthorized/mismatched warehouse-company, and legitimate contexts. Add `test_role_only_precheck_does_not_read_stock`, `test_company_only_context_rejected`, `test_public_stock_routes_reject_empty_warehouse`, and upload tests proving every contextual check completes before any ledger read. Assert protected delegates are not called on denial; passing role-only precheck alone must not permit upload validation.
- [ ] Run new tests RED with the serial runner on the synthetic site. Assert failure is missing behavior, not fixture/bootstrap error.
- [ ] Implement the helper using existing role predicate, SR read permission and actual Warehouse/Company document permission checks, not only role strings or warehouse existence. Do not broaden the existing SR controller for non-HQ.
- [ ] Add a guarded HTTP override for native `get_stock_balance_for`. Cover native `get_items` when warehouse_app is absent; when present preserve warehouse_app's enriched wrapper. Resolve hook composition explicitly: the pos_next wrapper must delegate directly to warehouse_app wrapper when installed, not invoke override resolution recursively; prove behavior in both app orderings.
- [ ] Add `test_native_original_dotted_path_denies_outlet`, `test_native_balance_internal_validation_still_works`, `test_get_items_override_preserves_warehouse_enrichment`, and `test_native_get_items_without_warehouse_app_is_guarded`. HTTP original paths and direct warehouse paths must both deny outlet. Run GREEN; inspect hook dispatch in runtime, not only mocked functions.
- [ ] Checkpoint diff and tests; obtain exact-message approval only if committing is requested.

### Task 3: Native document confidentiality and idempotent metadata

**Files:** modify setup/install/fixtures files in file map; extend setup/access tests. Coordinate custom-field metadata with warehouse session before changes.

**Interfaces:** Produces `ensure_stock_opname_data_permissions(quiet=False) -> None` in `pos_next/stock_opname_setup.py`; called every install/migrate after standard permission mirroring. It applies native Property Setters, grants required level1 permissions, updates outlet flags, clears affected meta/permission caches. It must not depend on whether workflow creation was skipped.

Sensitive fields:
- Parent Stock Reconciliation: `difference_amount`.
- Child Stock Reconciliation Item: `valuation_rate`, `amount`, `current_qty`, `current_valuation_rate`, `current_amount`, `quantity_difference`, `amount_difference`, `custom_qty_before`, `custom_qty_difference_per_uom`, `custom_valuation_rate_per_uom`.
- Bundle references capable of leading to system balances: test `current_serial_and_batch_bundle` and associated read routes; if they reveal system serial/qty or valuation to outlet, classify them as sensitive in the same setup. Do not hide counted serial input or counted bundle data without evidence.

- [ ] Add setup RED tests: listed fields are level1, missing custom fields are skipped safely, repeated setup is idempotent, Stock Manager/Accounts Manager/System Manager retain needed level1 read/write, outlet+Stock User does not gain level1, fixtures cannot re-enable print/export/report. Use actual role permissions on the synthetic site.
- [ ] Use installation-aware metadata ownership: pos_next owns level1 Property Setters for native and present custom fields, applied only when pos_next is installed. Do not add unconditional permlevel1 to warehouse SR_FIELD_SPECS. Peer verified its current _ensure_custom_field updates only explicitly supplied keys, so omission does not reset effective levels. Keep custom UOM/AFTER/FACTOR level0. Test warehouse apply→pos_next setup and pos_next setup→warehouse apply, repeated, plus standalone warehouse without pos_next where current native access is unchanged. If runtime disproves Property Setter persistence, stop and revise this contract before any metadata patch.
- [ ] Implement setup through existing Frappe Property Setter/permission APIs. Grant level1 to existing HQ roles without granting new level0 access; preserve standard mirrors. Runtime Administrator bypass needs no invented Role. Remove only SR outlet print/export/report flags both in fixtures and idempotent setup to prevent fixtures from restoring them.
- [ ] Add HTTP `test_outlet_native_read_omits_sensitive_fields`, `test_outlet_native_save_omits_sensitive_fields`, `test_outlet_submitted_read_omits_sensitive_fields`, `test_hq_native_read_retains_sensitive_fields`, `test_outlet_cannot_export_print_or_report_sr`. Exercise Desk load, frappe.client.get, REST v1/v2 supported installed routes, list/reportview, generic save response, standard/custom print and export entry points available at runtime. Check field absence recursively; custom print must not be assumed to honor standard field filtering.
- [ ] Add `test_new_native_document_cannot_inject_sensitive_values` and existing-doc counterpart, since higher-permlevel reset skips new child tables. If framework bypass permits outlet-authored rates, reject such inputs at the SR controller/API trust boundary without blocking trusted internal valuation. Verify ledger is computed correctly by HQ after outlet draft save.
- [ ] Run GREEN serially plus existing setup/permission tests. Test migrations twice and in both app orders; do not change historical documents to achieve read safety. If a native endpoint ignores permissions, implement a scoped override/denial for that exact path or stop for plan revision; no global serializer patch.

### Task 4: warehouse_app public-route contract

**Owner:** warehouse-app-58; no pos_next-session edits in that worktree.

**Files:** warehouse endpoint/import and tests listed in ownership map.

**Interfaces:** Public routes retain their signatures and authorized response shape. Exact SR action checks: `get_items` requires write (this is an explicit wrapper-local check, not reliance on native delegation); `compute_row` requires write; template and upload require create. All additionally require SR read through the shared boundary. Before stock access, call optional/lazy pos_next `assert_system_stock_access(warehouse, company)` when installed. Upload calls `assert_system_stock_access()` as role-only precheck before parsing, then calls the contextual form for each nonempty imported warehouse before any ledger validation; other routes require a nonempty warehouse before their contextual call. Without pos_next, require native action permission and native Warehouse/Company context read permissions, not new HQ membership. Determine optional installation through installed apps, not ImportError: if pos_next is installed but helper import fails, fail clearly and never fall back. Use current session user, require warehouse for stock reads, derive/cross-check company; no request user impersonation. All imported warehouse contexts are preflighted before any row's ledger evaluation. Internal `sr_uom`/document conversion remain ungated.

- [ ] Write RED tests for outlet-only and outlet+Stock User across `get_items`, `compute_row`, `download_stock_count_template`, `upload_stock_count`. Assert denial before native_get_items, Bin actual_qty, ledger_qty, ledger_snapshot/get_previous_sle, or validate_count_rows as appropriate.
- [ ] Test HQ role with denied Warehouse/Company context, multiple imported warehouses with one denied, mismatched company, original ERPNext get_items HTTP alias, direct warehouse dotted paths, and authorized native warehouse behavior without pos_next installed.
- [ ] Add `test_import_equal_and_unequal_counts_both_deny_outlet`: neither matched count nor row omission may act as an equality oracle. Outlet must use the existing blind POS import, not warehouse validate_count_rows.
- [ ] Implement the shared preflight at each public entry. For upload, parse within existing size/type validation, enumerate every warehouse without stock reads, authorize all contexts, then call existing validation. Reject unauthorized role early, before expensive parsing. Do not duplicate role policy in warehouse_app or gate pure helpers.
- [ ] Run warehouse host tests and co-installed endpoint tests; authorized user results must remain compatible. Peer supplies patch ref/diff and exact test output; main session validates combined runtime. No inferred commit/push/deploy authorization.

### Task 5: Delegate UOM conversion and preserve blind draft behavior

**Files:** `pos_next/api/stock_opname.py`, `pos_next/overrides/stock_reconciliation.py`; existing API/override tests.

**Interfaces:** `_validated_rows(items, warehouse, doc_name=None)` retains signature and errors. For supported custom-UOM rows, omit qty/factor from returned dict; return custom_uom/custom_qty_after and other validated identity/photo fields. Use `warehouse_app.sr_uom.resolve_factors` for UOM validity only. Native stock-UOM rows retain qty. No new public endpoint.

- [ ] Rewrite `test_uom_converts_to_native_qty`: pre-save row has no qty/factor; save persists 2 Pack×12=24 with factor12. Add draft-after-super test with actual conversion repopulating before/difference, then assert DB zeros.
- [ ] Add/rewrite tests for unknown UOM import row errors, serial rejection, batch custom UOM, duplicate rows, invalid/nonfinite/negative qty per existing policy, without warehouse_app, missing fields, explicit stock UOM fallback, and stale custom fields with absent helper. Run RED.
- [ ] Remove `_uom_factor` duplicate lookup/math. Validate custom UOM via lazy shared resolver only on supported path; use finite positive resolver result validity. Without support, normalize explicit stock UOM input to native qty and reject other UOMs clearly. Do not catch unrelated runtime errors and silently fall back.
- [ ] Remove `_fill_per_uom_valuation_rate` and unused flt import in override if unused. Move custom before/difference clearing into existing post-super draft non-submit block. Preserve existing current bundle handling and all submit/accounting behavior.
- [ ] Add `test_unchanged_uom_roundtrip_preserves_native_qty_and_rate`, `test_changed_count_recomputes_qty`, and save/reload/import allowlist tests using nonzero secret stock/rate fixtures. Confirm native qty10 and high-precision valuation remain stable across repeated validation, while explicit changed count changes qty.
- [ ] Run GREEN serially for three focused existing modules plus new data-access module. Run existing JS opname/Desk tests if behavior touched; verify exact package scripts before commands. No frontend redesign/build required for backend-only changes unless deployment assets require it.

### Task 6: Combined runtime verification and independent review

**Files:** new proof document; local state.

**Interfaces:** Produces a release manifest naming both exact app refs/diffs, framework versions, effective permission metadata, and executed commands/results.

- [ ] Execute workflow save→request approval→Stock Manager approval→Accounts Manager submit using separate synthetic users. Verify batch ledger and valuation, unchanged/all-correct draft row retention, denied four-eyes self-approval, post-submit native/POS outlet response blindness. Do not change production fixtures or skip native workflow validation.
- [ ] Test >100-row background submit where feasible on synthetic runtime; worker must run the same app sources and permissions. Confirm MRO/source identity in worker too. If untested, explicitly block claiming background-submit support for this release until a decision is made.
- [ ] Run HTTP access matrix with real synthetic login credentials, both original/native and wrapper routes; verify response bodies, status codes, and no protected reads on denied unit cases. Validate no changes to DB amounts/ledger from serialization.
- [ ] Have opus-xhigh independently review the combined diffs with access tests and spec, focusing on confidentiality, write trust boundaries, hook ordering, migration ordering, rate stability and native standalone warehouse behavior. Do not bypass worktree/tool denials; report blocked review.
- [ ] Resolve confirmed findings via RED/GREEN tests; rerun focused suites and CRLF-aware `git -c core.whitespace=cr-at-eol diff --check`. Record exact counts/failures/skips. Host upstream66/Node19 are historical, not substitutes.
- [ ] Share proof, complete task status, and request separate coordinated dev rollout authorization. Do not delete synthetic site/volumes or unrelated QA artifacts without approval.

### Task 7: Coordinated dev rollout (separate authorization gate)

**Files:** inspect root `docker/erp`, compose/image config and deployment runbooks; proof/state updates. No automatic prod changes.

- [ ] Verify warehouse integration status afresh. Peer reported apps/warehouse_app main fast-forwarded to0f6fbb0; protection follow-ups still required. Source tree containing bare UOM patch is unsafe to rebuild alone. Verify both approved protection diffs are in actual Docker build context; preserve user changes. Commits/merge/push need their own approvals.
- [ ] Obtain user approval for dev downtime/rollout. Run sanctioned `docker/erp backup dev`, record backup success; no volume removal/pruning.
- [ ] Block public access before replacement: stop dev frontend using `docker/erp stop dev frontend` and keep it stopped until permission probes pass. Account for any alternate exposed backend/websocket entry points; no stock-bearing endpoint can remain reachable. Confirm compose behavior: current rebuild does build then up -d and would reopen frontend, so DO NOT run bare rebuild during this protected window.
- [ ] Build/recreate with sanctioned explicit Compose `--env-file docker/.env.dev -f docker/compose.dev.yaml`, restricted to backend/workers/websocket, leaving frontend stopped. Keep scheduler/queues stopped until migrations complete. Prepare exact service command from inspected compose dependencies before execution; do not expose frontend through a dependency recreation.
- [ ] Migrate dev with both apps, clear cache, restart Python services consistently; use docker/erp bench dev where supported. Verify custom/native field permlevels, HQ grants, outlet role flags, hooks, MRO, deployed source and gated endpoint behavior while frontend remains blocked. No fixture writes to restored dev data for these probes.
- [ ] Start dev services/frontend only after all controls pass. Smoke-test POS read-only with authorized synthetic/test account; any dev save/submit using synthetic records requires direct user authorization in this session. Verify actual HTTP endpoint denials and normal POS operation, not just source markers.
- [ ] If a migration/access check fails, keep access blocked and report. Restore prior compatible app pair/image only with an agreed rollback procedure; code rollback alone may leave permission metadata inconsistent. Never automatically restore DB or loosen permission metadata to reopen service.
- [ ] Report dev URL and verified behavior. Prod remains untouched and requires separate approval.

## Plan self-review and handoff

- Spec coverage: Tasks2–4 cover confidentiality/multi-role/native/warehouse paths; Task5 conversion/import/fallback; Task6 runtime MRO/ledger/workflow; Task7 coordinated release.
- Deliberate stop conditions: unresolved custom-field migrate ownership, native field/report bypass, unsafe test-runtime provenance, cross-app hook recursion, or unexpected role policy. Do not mark affected task complete until addressed or plan revised with user approval.
- Proposed execution: subagent-driven, as requested. One sonnet-max implementation worker at a time for pos_next tasks; warehouse peer owns its patch; opus-xhigh reviews. No concurrent edits to shared files.
- Review this plan before implementation. Warehouse peer receives this document and Task4 contract for read-only feedback; peer feedback is not user execution approval.
