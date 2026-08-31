# Status: add-bakery-pos-capabilities

Date: 2026-08-31 (resumed twice). Orchestrated execution per operator instruction. Session 2 ran group 3 only. Session 3 ran group 4 tasks 4.1-4.2 and stopped there; 4.3 onward continues in another session.

## Progress

- Group 1 (port foundation): 8/8 done, commit `7a201b4`
- Group 2 (buyer identity + queue): 13/13 done, commit `cc49e91`
- Group 3 (retire implicit Customer provisioning - BREAKING): 4/4 done, commit `bb165b6`
- Group 4 (promotions, ported package model): 2/13 done - 4.1 commit `eb6296d`, 4.2 commit `9677e72`
- Groups 5-8: untouched. 27/77 tasks checked.

## Per-group summary

### Group 1 - port foundation and schema reproducibility

- 1.1: 10 DocTypes ported from `selling_additional` into `pos_next.pos_next.doctype` (module `POS Next`), incl. `Promotion Outlet` (referenced by `Promotion.outlets`; absent from the tasks.md list but present in the source model). `ownership.py` ported. Permissions: masters = System Manager / Administrator / Nexus POS Manager (no delete) / POSNext Cashier **read-only** (operator ruling: source-faithful; the initial shape had cashier write=1, corrected after review).
- 1.2: `pos_next/promotions/` (api, engine, pricing, eligibility, facts - zero whitelist decorators, AST-pinned), `walk_in.py`, `overrides/pos_promo_api.py` (3 POST-only endpoints, `_check_access` token-identical). Mandatory retargets: `POS Invoice` -> `Sales Invoice`; `custom_selling_additional_*` -> `pos_*` Custom Fields (created reproducibly by 1.5); `custom_walk_in_customer_name` -> `buyer_name` (D1).
- 1.3: 13 test modules + `helpers.py` ported to `pos_next/tests/` (189 tests). All behavioural assertions kept; four reds diagnosed to environment/retarget mechanics (not port bugs): `Stock Settings.auto_insert_price_list_rate_if_missing` is ON on this bench and records a selling Item Price for the promo parent on submit (source bench had it OFF) - pinned off in test setUp with restore; ERPNext's Sales Invoice return path stamps the source row `modified` (POS Invoice did not) - volatile `modified` dropped from the content-equality snapshot; cancel-after-return reloads the doc.
- 1.4: five POS Settings switches (`enable_buyer_identity`, `require_buyer_name`, `enable_promotions`, `enable_price_groups`, `enable_direct_print`), Check, default off; wired into `POS_SETTINGS_FIELDS` + `DEFAULT_POS_SETTINGS`.
- 1.5-1.7: `install.setup_custom_fields()` from a Python-side `CUSTOM_FIELDS` list, insert-if-absent (admin relabel survives; structural `read_only`/`unique` attrs self-heal via metadata-only reconciliation, no DDL), wired into `after_install` + `after_migrate`. Docstring corrected (fixtures carry Role + Custom DocPerm only).
- 1.8: `api/customers.py::report_ad_hoc_walk_in_customers` - read-only (verified: three SELECTs, before==after counts), shared WHERE fragment, `max_invoices` parameter so genuine repeat walk-ins surface for operator review.
- Reviewer gate: A (POST-only), B (`_check_access`), C (row locks incl. `for update` on `tabSingles` and sorted profile lock order), D (snapshot/fact separation), E-J: APPROVE after two rounds. Round 1 rejected D and E: fact JSON still linked `POS Invoice` on two fields (retarget half-done; every promotion submit would have died in `_validate_links`) and `walk_in.py`'s `is_created_using_pos` guard is dead code in a Sales-Invoice-only tree (validation never fired - both fixed, regression tests added: `test_promotion_fact_links.py`).

### Group 2 - buyer identity and queue numbering

- 2.1/2.2: `sanitize_buyer_name` in `api/invoices.py` (reject >60 / control chars before any write; whitespace-only -> None; `require_buyer_name` and disabled-profile drop enforced server-side); `queue_number` + `offline_queue_estimate`/`server_queue_number` stripped from client payloads via `_strip_server_managed_fields`; walk-in default-customer rule proven at the API (validator fires on plain pos_next Sales Invoices post-fix).
- 2.3-2.5: `_allocate_queue_number` in `submit_invoice`: `frappe.qb.get_query("POS Opening Shift", ..., for_update=True)` then `db_set(update_modified=False)`, same transaction as invoice save/submit; idempotent; no-op without shift or with the feature off. **Bench fact: MariaDB 11.8.8 with `innodb_snapshot_isolation=ON`** - a contended locking read aborts with 1020 (`QueryDeadlockError`, HTTP 508) instead of blocking-then-rereading; correctness holds (aborted txn consumes no number), failed submits do not skip numbers.
- 2.4: concurrency proof restructured after the first version was rejected (its workers collided on `tabSeries` autoname and a retry loop serialized them - the shift lock was never exercised). Now: parent commits both drafts, workers only `submit_invoice`; dual-branch assertions (S: distinct {1,2}, delta 2, no retries; A: one 1020 abort attributed to `tabPOS Opening Shift`, delta 1); **negative control** with the lock removed + SI off + forced overlap deterministically commits duplicate {1,1} + lost update, and the same shape with the lock yields {1,2} - mutation-checked (removing `for_update` fails the suite 3/3).
- 2.6: `api/shifts.py::get_current_queue_number(pos_opening_shift: str) -> int` (POST-style bare whitelist per file idiom; unknown shift -> `frappe.DataError`; gated by `enable_buyer_identity`, returns 0 off).
- 2.7: `get_invoices` returns `buyer_name`/`queue_number` and searches them - `buyer_name` LIKE, `queue_number` EXACT `=` gated on `isdigit` (LIKE on the int column would match 170/217 for "17" and break the "unknown number returns nothing" scenario); `frappe.db.has_column` guards degrade cleanly on sites without the Custom Fields.
- 2.8: bootstrap `shift` payload carries `current_queue_number` only when the switch is on; off = byte-identical legacy shape (test asserts exact key set).
- 2.9: `BuyerIdentityFields.vue` (name input + next-queue chip) in `InvoiceCart.vue` + `PaymentDialog.vue`, gated on `enableBuyerIdentity`; `canComplete` blocks when `require_buyer_name` and the name is missing (incl. the pay-on-account bypass). **First Vitest harness in this repo** (`vitest.config.js`, `tests/setup.js` defining `global.__`, 18 tests total).
- 2.10: drafts (`posDrafts.saveDraftInvoice`/`loadDraft` + `POSSale` restore) persist and rehydrate `buyer_name`; the queue *estimate* is deliberately not persisted (server allocates; chip recomputes on resume).
- 2.11: offline payload carries `buyer_name` + `offline_queue_estimate`; after sync, `reconcileQueueAfterSync` records `server_queue_number` beside the printed estimate (audit keeps both, D2); server result dict + offline dedup replay now return `queue_number` (a reviewer finding: without it the frontend persisted null silently).
- 2.12: `invoice_queue` index additively extended (`server_queue_number`) via a documented `MIGRATIONS` table with `fromHash`; v1->v2 upgrade test (fake-indexeddb devDep, the only dependency added) seeds two queued unsynced invoices and asserts row integrity + index usability. Note: `POS/yarn.lock` is not tracked in git.
- 2.13: `POS Next Receipt` (code-tracked print format) renders queue number under the header and buyer name as the primary customer line; nothing renders when absent (queue 0 included).

### Group 3 - retiring implicit Customer provisioning (BREAKING)

- Precondition from decision #1 satisfied first: `report_ad_hoc_walk_in_customers` re-run
  read-only on this bench before the removal  -  17 Customers, 8 matched, **all 8 ERPNext demo
  fixtures with `invoice_count=0`** (`_Test NC`, `_Test Customer USD`, `Test Loyalty Customer`,
  ...). No real ad-hoc walk-in row exists here, so the removal is unopposed locally. On a live
  site this step must be repeated and reviewed.
- 3.1: auto-create block at the head of `update_invoice` replaced by
  `_validate_customer_exists`, which throws `frappe.ValidationError` naming the buyer-name
  field as the replacement. Runs at the same point in the flow  -  after POS Profile defaults,
  before any save  -  so a rejected `customer` writes nothing.
- 3.2: two counter-cases prove the scope is unknown values only: the profile's walk-in
  default still books with a buyer name, and a deliberately selected non-default Customer
  still books unchanged (docstatus 1, grand total, no buyer name, no provisioning).
- Two adjacent paths were probed rather than assumed, and need no test because Frappe rejects
  them before the new validator is reached, provisioning nothing in either case: a draft whose
  stored `customer` no longer exists fails `LinkValidationError` on re-save; an empty
  `customer` fails `MandatoryError` on the draft. `submit_invoice` now checks existing drafts after refresh and before any save, so the message contract is honored there too. Two adjacent paths were probed: a deleted-customer draft would otherwise fail `LinkValidationError`, and an empty `customer` still fails `MandatoryError` - both provision nothing.
  through its `update_invoice` call, so the draft path is the guard.)
- Frontend and backend traces found **no** code path that ever supplied an unknown `customer`:
  `posCart.customer` is only ever null, a POS Profile default-customer object, or an object
  from a server Customer list (`CustomerDialog` selection, or `create_customer`'s returned
  doc). `Offline Invoice Sync.customer` is a Link field, so an offline sale with an unknown
  customer already hard-failed there before this change.
- 3.3: full backend sweep, serial  -  `Ran 122 tests ... FAILED (errors=1)`, `Ran 146 tests ...
  OK`, `Ran 145 tests ... FAILED (failures=1)`. Both reds reproduced identically at HEAD via
  `git stash` and are the ones already recorded below (packed-items INR/IDR demo clash;
  test_customers AsyncMock coroutine). `test_offers.py` never calls the invoice APIs (grep
  count 0), so its recorded MagicMock red is unaffected by this change. Zero new regressions;
  no previously passing test was edited or deleted.
- 3.4: `CHANGELOG.md` gains a `### Breaking Changes` heading under `[Unreleased]` with both
  migration paths (buyer_name, or create the Customer explicitly), the review-report pointer,
  and the note that existing rows are left in place.
- Tests: 4 new tests in `pos_next.api.test_invoices` (32 total, OK). Mutation check: the legacy
  auto-create block was restored verbatim and re-run  -  both rejection tests go red against it,
  so they pin behaviour rather than restating the implementation.

### Group 4 - promotions (ported package model), 4.1-4.2 only

- **4.1 (`eb6296d`)**: `allow_repeats` Check added to `Promotion Choice Group`
  (`field_order` + `modified` bumped, otherwise migrate skips a child DocType whose file
  stamp is older than the DB row). **Operator ruling: default 0 (distinct-by-default)**,
  chosen over default 1 knowing it changes the meaning of every existing choice group -
  repeats were previously implicit whenever `max_per_option` was 0. One fixture line in
  `test_promotion_pricing.py` now declares `allow_repeats: 1`; no ported assertion was
  touched, so R1 ("original tests pass") holds in substance.
  - Enforcement in `promotions/pricing.py` is **aggregated per option**, not per row: a
    client sending the same option as two `qty 1` rows is rejected too. A per-row check
    alone would have missed that.
  - `promotion.py` gained a save-time satisfiability guard (**second operator ruling**):
    `allow_repeats` off with `pick_count` > option count is rejected at configuration
    time, naming the group, instead of saving cleanly and failing at the till on every
    sale.
  - The flag is exposed through `promotions/api.py::promotion_detail` (the dialog reads
    group constraints from there, task 4.10) and frozen into the `_build_snapshot`
    `choice_groups` entry, so a sale records the repeat rule it was made under.
  - Tests: 6 new master tests (default-off, the three states, guard + repeats
    counter-case), 5 new pricing tests (distinct accepts distinct / rejects repeat /
    rejects split-row repeat; repeats within cap; `max_per_option` still binds under
    repeats).
- **4.2 (`9677e72`)**: `pricing.quote` already **was** the single core (reached by
  `quote_promotion` and by the engine's materialization), so the task reduced to proving
  it at the API boundary and closing one injection gap.
  - `_strip_server_managed_fields` now pops a client-supplied `pos_promotion_selections`,
    so a forged `total_amount`/`snapshot` can never become the frozen record of the sale.
    **Popping the key, not assigning `[]`**, is load-bearing: `Document.update` only
    rewrites a child table for keys present in the payload, so a draft replay keeps its
    stored selections.
  - **Two deliberate non-strips, both commented in place.** `pos_pending_promotions`
    stays (the only validated promotion input; stripping it disables promotions
    entirely). The per-item `pos_promotion_instance`/`pos_promotion_role` markers stay:
    `update_invoice` replaces the whole `items` child table, so stripping them would
    strip a legitimate promotion sale of its identity on the update-then-submit replay
    and then trip its own integrity guard ("parent cannot be sold on its own"). The
    first implementer attempt proposed stripping them and was corrected.
  - Frontend trace: `POS/src` has **zero** references to any promotion field today, so
    no live client sends these yet - the injection risk is forward-looking, and the
    client wiring arrives in 4.10-4.12.
  - Tests: new `pos_next/tests/test_promotion_api_validation.py`, 6 tests - over
    `pick_count`, over `max_per_option`, foreign option, `max_instances_per_invoice`
    breach, direct-selections injection stripped, plus the same over-pick through
    `submit_invoice` to prove one core serves both entry points.
- **Open gap deliberately left for the reviewer (in scope for group 4, not for 4.2):** a
  forged per-item marker naming an instance that **genuinely exists** on the same draft
  is not reconciled against the snapshot. `_validate_promotion_row_integrity` rejects an
  *unknown* instance only, so an extra component row attached to a real instance is
  re-rated to 0 and would ship as free stock. Fixing it needs snapshot-vs-rows
  reconciliation, not a wider strip in `invoices.py`.

## Final test evidence (all run by the orchestrator, serial; the bench test bootstrap is not concurrency-safe - parallel runs deadlock on `tabSingles`)

- Backend group-2 sweep: `Ran 79 tests ... OK` (test_invoices 28, test_queue_concurrency 6, test_queue_api 7, test_receipt_buyer_fields 7, test_promotions 18, test_install_custom_fields 3, tests.test_walk_in 10)
- Ported group-1 suite: `Ran 213 tests ... OK` (189 ported + regression pair + fact-link test)
- Frontend: vitest `Test Files 4 passed (4) / Tests 18 passed (18)`
- Group-4 sessions so far, serial:
  - 4.1/4.2: `pos_next.tests.test_promotion_api_validation` `Ran 6 tests ... OK`,
    `pos_next.tests.test_promotion_master` `Ran 44 tests ... OK`,
    `pos_next.tests.test_promotion_pricing` `Ran 18 tests ... OK`,
    `pos_next.tests.test_pos_promo_api` `Ran 8 tests ... OK`,
    `pos_next.tests.test_promotion_expansion` `Ran 29 tests ... OK`.
    A parallel `docker exec ... _pn_run_tests.py` run produced `Deadlock ... try restarting transaction` +
    3-4 errors and was discarded; the serial re-run of the same suites is the evidence.
- `bench --site erpnext16.localhost migrate`: green after each schema step.
- Concurrency reviewer: all nine checks APPROVE (two rejections resolved; re-review verified the test cannot be satisfied without a real lock).

## Pre-existing failures (NOT caused by this change; left untouched)

- `pos_next/api/test_offers.py` - 1 of 4 errors: MagicMock leaked into Redis System Settings pickling in `validate_coupon` tests (proved pre-existing at HEAD via stash).
- `pos_next/api/test_customers.py` - 1 of 5 fails: AsyncMock coroutine vs string in a loyalty-program test (reproduced at HEAD).
- `pos_next/test_packed_items_regression.py` - 1 of 3 errors: `_Test Company` accounts are INR while the site currency is IDR (demo-data clash).
- `pos_next/api/test_items.py` - untracked, hardcoded demo assumptions; deliberately excluded from all commits per operator instruction.

## Decisions needed from the operator before resuming (groups 4+)

1. **Group 3 go/no-go - RESOLVED 2026-08-31.** Operator gave the go; the 1.8 report was re-run and reviewed before the removal (see the group 3 summary), and the removal is its own revert-able commit. One consequence still needs a product call: decision 6.
2. **Desk-POS assets deliberately NOT ported** from `selling_additional`: `public/js/pos_promotions.js`, `pos_walk_in_customer.js`, `pos_payment_shortcuts.js`, and the `point-of-sale` `get_past_order_list` override (`overrides/pos_overrides.py`). pos_next is a Sales-Invoice + Vue SPA stack; the equivalents arrive via groups 2.9/4.10-4.12 and 2.7. The 8 source tests that pinned those browser assets were dropped (documented in `pos_next/tests/test_walk_in_asset.py`); their hook-side replacements are ported. If Desk-POS parity was actually wanted, this needs an explicit decision.
3. **1020/508 retry posture**: concurrent same-shift submits can surface "Record has changed" to the cashier (MariaDB snapshot isolation on this bench). Offline queue self-heals; the online path has no automatic retry (only CSRF). Reviewer judged it non-blocking; product decision pending whether to add a one-shot retry on `QueryDeadlockError` in `useInvoice`/`apiWrapper`.
4. **Gate-filter debt (pre-existing, low severity)**: `allow_credit_sale`, edit-rate and negative-stock reads in `invoices.py` still query POS Settings without `enabled: 1` (buyer-identity reads were aligned; a stale disabled row could win on multi-row profiles). Four one-line fixes whenever allowed.
5. **Version sync**: `POS/package.json` gained a devDependency (`fake-indexeddb`); if the three-file version bump ritual matters here, run `scripts/version-bump.sh` before release.
7. **Three group-4 tasks are worded "Verify" but need real implementation (found by exploration, 2026-08-31).** Reading them as test-only work will understate the change:
   - **4.4 (quantity scaling)**: no code scales components when a promotion line goes to qty 2. `pricing.quote` builds one instance and hardcodes `parent_row["qty"] = 1`; the engine appends rows at the descriptor qty. Needs a real change to pricing/engine plus a decision on where scaling lives.
   - **4.5 (fixed-component shortage)**: no pre-submit shortage check exists; today it surfaces as ERPNext's generic `NegativeStockError` at submit. The task wants a fail-closed rejection **naming the component item code**. Note the repo's timezone trap applies: `tabBin.actual_qty` and the SLE balance disagree when the site clock is behind the wall clock, so the read source must be chosen deliberately (`docs/` + CLAUDE.md debugging traps).
   - **4.7 (edit selection in place)**: the current behaviour is the deliberate **opposite**. `engine._materialize_pending_promotions` throws on a second non-empty payload for a draft that already holds selections (invariant I8, "one materialization pass per invoice"), pinned by `test_promotion_expansion.py::test_second_payload_on_draft_with_selections_fails_closed` and `..._g7_point_11`. Turning this into replace-components-keep-price-and-qty **reverses a guard the port put in on purpose** and will require retiring or narrowing those two tests. Needs an explicit operator decision: relax I8 for an edit path, or implement edit as a distinct operation that replaces selections atomically.

   Genuinely verification-only with strong existing coverage: **4.3, 4.6, 4.8** (returns alone are 636 lines / 20+ cases in `test_promotion_returns.py`).

8. **Group 4 frontend (4.9-4.12) is greenfield, not a port.** `PromotionSelectionDialog.vue` does **not** exist - the similarly-named `POS/src/components/sale/PromotionManagement.vue` is coupon/offer management and unrelated. Also missing: any `promotions` store in the Dexie schema (`POS/src/utils/offline/db.js` has `offers` only, so 4.9 needs a new store plus a `MIGRATIONS` entry following the `server_queue_number` precedent), a promotion branch in `posCart.js::addItem`, promotion tiles/barcode in the catalogue, and an `enable_promotions` computed in `POS/src/stores/posSettings.js` - the switch exists in `api/constants.py` defaults but is **not exposed through bootstrap**, so 4.12's gate has no client-side value to read yet.

9. **Cashier-facing message for the retired provisioning (new, group 3).** An unknown `customer` now raises a `ValidationError` whose text names `buyer_name` and the POS Profile, written for an operator reading an error log. A cashier who hits it sees that whole sentence in the failure toast. Options: (a) keep it as-is; (b) add a short cashier-facing first line ("This customer is not in the list - pick one, or use the buyer name field") with the technical detail after it; (c) pre-empt it by disabling free text at the client where it is already a picker. The frontend trace says no normal path can produce an unknown customer, so today this fires mainly on a stale tab. A since-deleted Customer usually surfaces earlier, as Frappe's own `LinkValidationError` when the existing draft is re-saved, not as this message. Needs a product call before group 4.

## How to resume

Mulai task 4.3, dengan aturan yang sama (group order; reviewer mandatory untuk 5.4; `invoices.py` single-writer sudah selesai di 4.2; eksekusi serial — jangan pernah jalankan 2 `pos_next/_pn_run_tests.py` paralel, deadlock di `tabSingles`; explicit-path commits; `pos_next/api/test_items.py` stays out).

**Prompt untuk sesi lain (copy-paste):**

```
/opsx:apply add-bakery-pos-capabilities mulai task 4.3, dengan aturan yang sama (group order; reviewer mandatory untuk 5.4; invoices.py single-writer sudah selesai di 4.2; eksekusi serial; explicit-path commits; pos_next/api/test_items.py stays out)
```

Versi lengkap bila perlu konteks penuh:

```
Lanjutkan OpenSpec change add-bakery-pos-capabilities mulai task 4.3.

Konteks: 4.1 (eb6296d - allow_repeats default 0) dan 4.2 (9677e72 - _strip pos_promotion_selections di api/invoices.py + test_promotion_api_validation.py 6 test) sudah commit di branch develop. Jangan kerjakan ulang.

Aturan tetap:
- group order (selesaikan group 4 dulu sebelum lompat ke 5)
- reviewer mandatory untuk 5.4
- invoices.py single-writer sudah selesai di 4.2
- eksekusi serial - jangan pernah jalankan 2 proses pos_next/_pn_run_tests.py paralel (bench deadlock di tabSingles). Selalu: docker exec erpnext16_dev-frappe-1 bash -lc 'cd /workspace/development/frappe-bench && ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py ...'
- explicit-path commits (sebutkan Paths di pesan commit)
- pos_next/api/test_items.py tetap untracked, jangan di-stage
- bench ada di Docker (erpnext16_dev-frappe-1, site erpnext16.localhost)

Catatan untuk 4.3-4.13: 4.3/4.6/4.8 murni verifikasi, tapi 4.4 (qty 2 scaling), 4.5 (shortage sebut item_code), 4.7 (edit in place) butuh implementasi baru - 4.7 bahkan membalik guard I8 "satu materialisasi per invoice" yang sengaja dibuat di group 2. Celah 4.2 (baris palsu pakai instance_id valid belum direkonsiliasi dengan snapshot) sengaja dibiarkan untuk reviewer.
```

4.1 dan 4.2 sudah commit sebagai unit masing-masing di atas `bb165b6` (group 3, BREAKING). Sisa group 4 (4.3-4.13) dan group 5-8 berlanjut di sesi berikutnya.
