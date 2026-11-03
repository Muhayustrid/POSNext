# POS Spend Template Rule Ownership and Company Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the reverse Template-to-account-resolution dependency and add explicit Selected/All company scope without widening existing templates silently.

**Architecture:** The existing Template module owns company expansion, debit eligibility and allowed-item expansion. Account resolution depends on Template rules; existing readiness and transaction entry points remain. A Select field explicitly enables All leaf companies while the Companies table retains optional per-company overrides.

**Tech Stack:** Frappe/ERPNext v16, Python, DocType JSON, Desk JavaScript, node:test.

**Spec:** `docs/superpowers/specs/2026-11-03-spend-template-rule-ownership-design.md` (approved 2026-11-03).

## Global Constraints

- No new module, adapter or dependency. Preserve native documents, authorization, idempotency, GL, locking, attachments and SPA behavior.
- Preserve `allowed_items(template)` semantics, error behavior and legacy import binding; keep readiness validation and reference_company defaults.
- `company_scope` values: `Selected Companies` (default) and `All Companies`; empty legacy values mean Selected, invalid values are rejected.
- All means every Company with `is_group=0`, including future leaf companies; user access and readiness still gate use.
- Companies remains visible, retains rows on mode changes, and supports existing leaf overrides. Group rows cannot carry overrides.
- Preserve id.csv existing bytes/CRLF; append only missing translations.
- Backend tests run serially through `_pn_run_tests.py` in the verified fix container; never copy into the bind-mounted checkout.
- Before dev rollout: backup, sanctioned `docker/erp rebuild dev backend`, migrate and verify served behavior. No prod deployment.
- No commit or push without separate explicit approval of the exact commit message. Do not modify unrelated changes.

## Review Focus

1. Empty/null scope on an old template must not expand to all; pin in Task 2.
2. All must not bypass user company restrictions or hide account-readiness failures; pin in Task 2.
3. All still needs complete overrides when no reference debit account exists; pin in Task 2.
4. Switching modes must retain child rows and their overrides; pin in Tasks 2 and 3.
5. Direct item rows and Item Group expansion have different current filtering; preserve both in Task 1.

## Task 1: Give allowed-item rules their existing Template owner

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.py`
- Modify: `pos_next/services/spend_accounts.py`
- Modify: `pos_next/api/spend.py`
- Test: `pos_next/pos_next/doctype/pos_spend_template/test_pos_spend_template.py`
- Test: `pos_next/tests/test_spend_accounts.py`

**Interfaces:** Produces `allowed_items(template)` in the Template module, returning the same sorted unique item names. `spend_accounts.allowed_items` remains a direct imported binding, not a forwarding function. Existing company/debit functions and readiness outputs do not change.

- [ ] Inspect every `allowed_items` import and mock patch target with exact search; inspect fixture helpers and applicable instructions. Verify fix.localhost availability with `docker exec erpnext-fix-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site fix.localhost list-apps'`.
- [ ] Add characterization assertions for direct items, Item Group descendants, disabled/non-purchase expansion filtering, duplicate sorting, and readiness reference_company default. Snapshot and restore any Singles/config fixture changes in finally/addCleanup.
- [ ] Run characterization tests before refactor; they should pass on baseline. Add an ownership assertion that old/new imports refer to the same function, then run it red because the new owner does not yet export it.
- [ ] Move the existing implementation unchanged to the Template module; remove its local reverse import; import it directly from Template in account resolution and transaction code. Preserve the old imported binding and exception behavior.
- [ ] Run serial backend modules using `docker exec erpnext-fix-backend-1 bash -lc 'cd /home/frappe/frappe-bench && PN_SITE=fix.localhost FRAPPE_STREAM_LOGGING=1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>'`: `pos_next.pos_next.doctype.pos_spend_template.test_pos_spend_template`, `pos_next.tests.test_spend_accounts`, `pos_next.api.test_spend`. Record actual results, not expected passes.

## Task 2: Explicit company scope, schema and backend behavior

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.json`
- Modify: `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.py`
- Test: `pos_next/pos_next/doctype/pos_spend_template/test_pos_spend_template.py`
- Test: `pos_next/tests/test_spend_accounts.py`
- Test: `pos_next/api/test_spend.py`
- Conditional: a versioned patch and `pos_next/patches.txt` only if migration verification shows explicit legacy backfill is needed; select the next unused version using current registry, not a guessed version.

**Interfaces:** `template_companies(template)` retains document/name input and sorted unique output. Missing/empty scope resolves as Selected; All queries leaf company names. Validation normalizes blank to Selected and rejects other strings. New required Select `company_scope`, label `Company Scope`, precedes Companies and defaults to Selected.

- [ ] Add failing tests: Selected empty returns []; legacy null/empty behaves as Selected; group expansion unchanged; All equals sorted live leaf-company names excluding groups; another leaf created after saving is included; invalid scope rejected.
- [ ] Add failing tests through existing readiness/submit interfaces: All respects leaf account override; incomplete overrides without a reference are rejected; out-of-user-scope company remains forbidden; unready target remains unavailable/rejected; saved company rows survive mode switches. Use existing authorization fixture patterns and restore every mutated setting.
- [ ] Run the focused modules above to capture failures for absent behavior.
- [ ] Implement the Select and shared scope resolution/validation. Reuse the resolved company set inside the existing no-reference override completeness check instead of resolving twice. Do not change reference matching or add fallback accounts.
- [ ] Migrate fix only: `docker exec erpnext-fix-backend-1 bash -lc 'cd /home/frappe/frappe-bench && bench --site fix.localhost migrate'`. Verify a template created before the schema change still resolves its original scope; verify persisted blank/default handling. Add an idempotent backfill patch only if needed; never convert legacy templates to All.
- [ ] Rerun the three backend modules serially. Check native cancellation/shift regression modules `pos_next.api.test_spend_cancel` and `pos_next.tests.test_spend_shift` without changing their protocol.

## Task 3: Desk explanation, translations and regression proof

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.js`
- Modify: `pos_next/tests/js/spend_template.test.cjs`
- Modify: `pos_next/translations/id.csv`
- Create: `docs/proofs/2026-11-03-spend-template-company-scope.md`
- Update: local gitignored `PROJECT_STATE.md` (compress current state to <=80 lines and <=30000 bytes; retain open decisions/cautions, not history).

**Interfaces:** Desk refresh and `company_scope` change update the Companies description without clearing/hiding child rows. Suggested copy: Selected — “Select companies or a group company to include its descendants.” All — “Applies to all non-group companies, including new ones. Company rows are optional overrides.” New labels/options/descriptions use translation lookup.

- [ ] Extend existing node:test form stubs and add failing assertions: Selected/legacy and All explanations, Companies remains available, child rows unchanged after mode changes, existing readiness escaping/dirty-document guard unchanged.
- [ ] Run `node --test pos_next/tests/js/spend_template.test.cjs`; observe new assertions fail.
- [ ] Implement description update on refresh and scope change using existing Frappe form conventions; append only absent translations for Company Scope, options and descriptions. Do not alter readiness button behavior.
- [ ] Rerun Desk JS tests; run relevant Python Ruff checks in the installed container toolchain and `git diff --check`. Recheck exact import callers and absence of reverse dependency. Separate old lint findings from new ones.
- [ ] Before dev operations read root operational instructions and sanctioned command behavior. Backup with `/Users/rotiropi/ERPNext-Project/docker/erp backup dev`, then `/Users/rotiropi/ERPNext-Project/docker/erp rebuild dev backend`. Check actual exits; migrate via sanctioned bench command if bootstrap has not applied the change. Do not proceed past failed rollout.
- [ ] Verify deployed field/default/rules and browser form descriptions with no unapproved production-data writes; browser switch checks can use an unsaved form. Verify old saved templates retain Selected semantics. Never enable All on a real saved template merely as a smoke test.
- [ ] Write proof with actual tests, migration compatibility, browser/rollout results and limitations; update local state. No commit/push. Request exact-message approval only if the user asks to commit.

## Handoff and acceptance

The user reviews this plan and selects Native or Subagent-driven execution before implementation. Recommended: Native, because three tasks modify one tightly coupled rule flow; one independent Opus review after implementation checks the financial authorization and migration behavior. Implementation must not start on approval of the spec alone.
