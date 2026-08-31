# Status: add-bakery-pos-capabilities

Date: 2026-08-31 (resumed three times). Orchestrated execution per operator instruction. Session 2 ran group 3 only. Session 3 ran group 4 tasks 4.1-4.2. Session 4 ran group 4 tasks 4.3-4.6 (commit `b5183cd`) and stopped there with 4.7 half-done (engine implemented, no test file) and 4.8 untouched; 4.7 onward continues in another session.

## Progress

- Group 1 (port foundation): 8/8 done, commit `7a201b4`
- Group 2 (buyer identity + queue): 13/13 done, commit `cc49e91`
- Group 3 (retire implicit Customer provisioning - BREAKING): 4/4 done, commit `bb165b6`
- Group 4 (promotions, ported package model): 6/13 done - 4.1 commit `eb6296d`, 4.2 commit `9677e72`, 4.3-4.6 commit `b5183cd`
- Groups 5-8: untouched. 31/77 tasks checked.

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

### Group 4 - promotions, 4.3-4.6 (session 4, commit `b5183cd`)

Three of these four were listed as "verification only" in the previous session's
exploration. Two were not: 4.4 and 4.5 needed real implementation, and 4.3's test found a
live defect that needed a third.

- **4.4 quantity scaling - IMPLEMENTED.** `pricing.quote` gained `quantity=1`. It scales
  every component row `qty` and the parent `amount`; **`rate` and `total_price` stay
  PER-UNIT** and that is load-bearing - the engine stores the per-unit price as the
  selection's `total_amount` and `_reassert_promotion_invariants` re-asserts the parent
  row's rate from it (`row.rate = flt(selection.total_amount)`), so making `rate` a line
  total would silently multiply the price on every re-save. New public helper
  `pricing.validate_instance_quantity` rejects bool, non-numeric string, fraction, zero
  and negative through one named message so every invalid vector shares a killer. The
  instance cap (I16/D19) now **sums quantities** instead of counting instances.
  - Tests: `pos_next/tests/test_promotion_quantity_scaling.py`, 13 tests, all green.
- **4.5 fixed-component shortage - IMPLEMENTED.** `engine._validate_promotion_stock`, run
  at `before_submit`, refuses a submission whose **fixed** components are short at the
  outlet and names item code, warehouse, required and available.
  - Scoped to fixed components on purpose: chosen options are lines the cashier picked and
    already fail with ERPNext's own per-row message, so duplicating that check would only
    reword someone else's error. Fixed components are implicit - nothing on the UI names
    them - so the generic `NegativeStockError` points at a row the cashier never added.
  - Skipped when `update_stock` is off, when `Stock Settings.allow_negative_stock` is on
    (the site's explicit decision to tolerate shortages), and per item when the Item allows
    negative stock.
  - Balance reads `erpnext.stock.utils.get_stock_balance`, **not** `tabBin.actual_qty`: the
    two disagree when the site clock trails the wall clock (the `NegativeStockError` trap
    in CLAUDE.md), so a bin-based pre-check could wave through a submit that then fails.
  - Required quantity multiplies through the instance quantity, so 4.4 and 4.5 compose.
  - Tests: `pos_next/tests/test_promotion_component_shortage.py`, 8 tests, all green.
- **4.3 expansion + no-parent-stock - REAL DEFECT FOUND AND FIXED.** The verification test
  `test_stock_ledger_excludes_parent_item_even_when_parent_is_a_stock_item` failed with a
  measured SLE of **-1 per unit for the parent item**. Root cause:
  `SellingController.update_stock_ledger` (`erpnext/controllers/selling_controller.py:653`)
  writes an SLE for any row with `is_stock_item == 1` plus a warehouse, and the promotion
  parent row legitimately carries the outlet warehouse (I13 re-asserts it and
  `test_promotion_expansion.py` pins it). `Promotion._validate_parent_item` (D12/I11)
  rejects a stock parent only at **master-save** time, so an Item flipped to
  `is_stock_item = 1` after the Promotion was saved left an already-valid Promotion selling
  a stock parent with nothing downstream noticing.
  - Fix: `engine._validate_parent_rows_move_no_stock` at `before_submit` refuses the
    submission, naming the row and the item. **Refusing was chosen over blanking the parent
    row's warehouse**, which would silently contradict I13 and let a misconfigured master
    keep selling; a named refusal points at the Item that has to be corrected. A draft
    carrying the same rows is still fixable by correcting the Item.
- **4.6 snapshot + facts - verification only, green as read.** Snapshot assertions parse the
  stored JSON and reconstruct the sold selection from it rather than comparing raw strings
  (which is what `test_promotion_master.py` does today), and a post-submit master pricing
  edit is proved not to change the sold record (I14: facts derived, never authority).
  - One coverage finding recorded as a passing test, not a fix: the shipped POS Next Receipt
    print format has **no promotion rendering at all** - that is task 4.12's job.
  - Tests: `pos_next/tests/test_promotion_stock_and_snapshot.py`, 9 tests (4.3 + 4.6), green.

**Reachable-state constraints discovered while writing these fixtures** (they cost real time,
so they are recorded here):
- A Promotion with a **non-stock component** cannot be created - D13/I12 rejects it at save
  (`Row {0}: Component item {1} must be a stock item`). To test a non-stock component you
  must save the Promotion with a stock item and then flip `Item.is_stock_item` to 0 with
  `frappe.db.set_value(..., update_modified=False)` plus `frappe.clear_cache(doctype="Item")`.
- Likewise a **stock parent** is unreachable through `insert`; same post-save flip.
- A second **enabled** Promotion cannot share a parent item (`Parent item {0} is already used
  by enabled Promotion {1}`), so a fixture that needs a different component must mutate the
  existing Promotion in place rather than create a sibling.
- `doc.update_stock = 1` set before save is **not** enough: `SalesInvoice.set_pos_fields`
  (`sales_invoice.py:1037`) overwrites it from the POS Profile on **every** save, and
  `pos_profile.json:334` ships `'default': '1'`. Set it before save *and* before submit; to
  turn it **off**, set it on the POS Profile (`frappe.db.set_value("POS Profile", ...,
  "update_stock", 0)` + `clear_cache`), not on the document.
- An item that never received stock has **no valuation rate**, so a negative-stock test dies
  with `Valuation Rate for the Item ... is required to do accounting entries` for an
  unrelated reason. Receipt 1 unit and sell 3 - the shortage is still real, the accounting
  entry succeeds.

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
- Session 4 (4.3-4.6), serial, `pos_next/_pn_run_tests.py`:
  - `pos_next.tests.test_promotion_quantity_scaling` `Ran 13 tests ... OK`
  - `pos_next.tests.test_promotion_component_shortage` `Ran 8 tests in 82.377s ... OK`
  - `pos_next.tests.test_promotion_stock_and_snapshot` `Ran 9 tests in 62.685s ... OK`
  - Regression check for the 4.7 I8 relaxation: `pos_next.tests.test_promotion_expansion`
    `Ran 29 tests in 171.245s ... OK` - the two tests that pin the old refusal
    (`test_second_payload_on_draft_with_selections_fails_closed:514` and
    `test_second_payload_after_selections_fails_closed_g7_point_11:813`) still pass because
    both send payloads **without** `replace_instance`, which is exactly the branch the
    relaxation left untouched.
  - `pos_next.tests.test_promotion_returns` re-run for the same reason:
    `Ran 19 tests in 151.084s ... OK`.

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

### First thing to do in the next session

Write 4.7's missing test file (below). Nothing else is outstanding: both regression suites
run for the 4.7 I8 relaxation came back green (`test_promotion_expansion` 29/29,
`test_promotion_returns` 19/19).

### Task 4.7 - engine DONE, test file MISSING (this is the exact stopping point)

The engine side of 4.7 is **already committed** in `b5183cd` inside
`pos_next/promotions/engine.py`. It is NOT ticked in `tasks.md` because it has no dedicated
test file. Do not re-implement it; write the tests for what is there.

**Operator ruling that shaped it: "atomic replace per instance."** The payload edit carries
an explicit target `instance_id` under the key `replace_instance`; the engine replaces only
that instance's selections and rows; the parent price and quantity are preserved. Invariant
I8 still applies in full to any payload instance **without** the key.

What was implemented (all in `engine.py`):
- `_validate_replacements(instances, existing)` - returns the ordered list of instance ids
  being replaced. An instance with no `replace_instance` on a doc that already has
  selections hits the original I8 message; unknown and doubly-targeted replacements get
  their own named errors.
- `_validate_replacement_promotions(...)` - a replacement must target the same Promotion the
  instance was sold under.
- `_resolve_instance_quantities(...)` + `_stored_instance_quantity(...)` - a replacement is
  **pinned** to the quantity in the original snapshot. Editing a selection may change which
  options fill the units, never how many units were ordered. Pre-4.4 selection rows with no
  `quantity` in the snapshot read as 1.
- `_drop_instance(doc, instance_id)` - removes one instance's selection row and every item
  row it backs, as a unit.
- In `_materialize_pending_promotions`: **every validation runs before anything is dropped**,
  so a rejection anywhere in the payload leaves the draft's existing rows and selections
  untouched. That ordering is the atomicity guarantee and needs a test of its own.
- A replacement **keeps the old instance identity**: the regenerated selection row and every
  regenerated item row carry the same `instance_id` as before the edit.

The five error strings a test must pin (all `frappe.ValidationError`):
- `"Cannot apply new promotion payload to an invoice with existing promotion selections"`
  (unchanged I8, for an instance lacking `replace_instance`)
- `"Promotion instance {0} does not exist on this invoice"`
- `"Promotion instance {0} is replaced more than once in one payload"`
- `"Promotion instance {0} belongs to Promotion {1} and cannot be re-selected under Promotion {2}"`
- `"Promotion instance {0} was sold at quantity {1}; editing its selection cannot change the quantity"`

Suggested file: `pos_next/tests/test_promotion_edit_selection.py`, modelled on
`pos_next/tests/test_promotion_quantity_scaling.py` - copy its fixture scaffolding
(`_make_company`, `_make_warehouse`, `_setup_companies_and_warehouses`, `_setup_items`,
`_setup_pos_profile`, `_setup_promotion`, `_pending`, `_instance`, `_new_invoice`,
`_submit_paid`) verbatim and change the prefix. Cases to cover:
- (a) a payload carrying `replace_instance` swaps that instance's **component** rows while
  the parent row's `rate`, `qty` and `pos_promotion_instance` are unchanged - this is the
  literal task wording and the one that must not be missed;
- (b) a second, untouched instance on the same draft is left completely alone;
- (c) an instance lacking `replace_instance` on a draft with selections still hits the I8
  message (this is also pinned from the other side by the two existing
  `test_promotion_expansion.py` tests, which stayed green);
- (d) each of the other four error strings above;
- (e) **atomicity** - a payload where one instance is valid and another is rejected must
  leave the draft's rows and selections exactly as they were; re-read the doc from the DB
  and count.

### Task 4.8 - NOT STARTED

"Verify promotion returns at proportional component quantities, reusing the ported
`test_promotion_returns.py` expectations."

Read the current state before deciding scope: `engine._validate_return_completeness`
(`engine.py:647`) today enforces **whole-instance** returns (I6/D11 - Model C assigns all
revenue to the parent, so a partial instance return has no defensible refund amount) and
`test_promotion_returns.py` has 19 tests, most of which assert exactly that a partial return
throws. Task 4.8's word "proportional" therefore reads as **proportional to the instance
quantity** - a quantity-2 instance returns 2 of each component - not as "a partial instance
may be returned." Under that reading 4.8 is the 4.4 counterpart on the return side and the
existing whole-instance tests stay valid. **Confirm this reading with the operator before
touching `_validate_return_completeness`** - the other reading would retire a guard the port
put in deliberately, the same trap 4.7 turned out to be.

### Standing rules (unchanged, carry these into the next session)

- group order - finish group 4 before group 5
- reviewer mandatory for task 5.4
- `invoices.py` single-writer work is DONE in 4.2 - do not redo
- **eksekusi serial** - never run two `pos_next/_pn_run_tests.py` processes at once. The
  bench test bootstrap deadlocks on `tabSingles`/`tabSeries` under MariaDB
  `innodb_snapshot_isolation=ON` and surfaces as
  `QueryDeadlockError (1020, "Record has changed since last read...")`. Always:
  ```
  docker exec erpnext16_dev-frappe-1 bash -lc 'cd /workspace/development/frappe-bench && \
    ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>'
  ```
- explicit-path commits - name the Paths in the commit message
- `pos_next/api/test_items.py` stays **untracked**, never staged in any commit
- bench is in Docker (`erpnext16_dev-frappe-1`, site `erpnext16.localhost`)

### Prompt for the next session (copy-paste)

```
Lanjutkan OpenSpec change add-bakery-pos-capabilities dari task 4.7.

Konteks: 4.1 (eb6296d), 4.2 (9677e72), dan 4.3-4.6 (b5183cd) sudah commit di develop.
Jangan kerjakan ulang. Engine 4.7 SUDAH diimplementasi di b5183cd (replace_instance,
atomic replace per instance) tapi BELUM ada file test - itu pekerjaan pertama.

Aturan tetap: group order; reviewer mandatory untuk 5.4; invoices.py single-writer sudah
selesai di 4.2; eksekusi serial (jangan pernah 2 proses _pn_run_tests.py paralel);
explicit-path commits; pos_next/api/test_items.py tetap untracked.

Baca openspec/changes/add-bakery-pos-capabilities/STATUS.md bagian "How to resume" untuk
detail 4.7 dan peringatan soal 4.8.
```

4.1, 4.2 dan 4.3-4.6 sudah commit sebagai unit masing-masing di atas `bb165b6` (group 3,
BREAKING). Sisa group 4 (4.7-4.13) dan group 5-8 berlanjut di sesi berikutnya.
