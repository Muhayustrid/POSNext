# Discount Stacking Matrix + Multi-Company Coupon Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One server-enforced stacking matrix (offer / coupon / manual), a coupon amount that follows the cart, and POS Coupons usable across several companies.

**Architecture:** The server stays the single judge: `reject_stacked_offer_discounts` in `pos_next/overrides/discount_code.py` gains the transaction-offer and coupon rows of the matrix, reading the offer flag from a Pricing Rule mirror field (no extra query). POS Coupon gets the Discount Code scope pattern (`company_scope` + `companies` child) behind one helper `coupon_usable_at`. The frontend keeps the coupon definition and recomputes the header discount with a JS mirror of `pos_coupon.expected_coupon_discount`; UI locks only pre-empt what the server rejects.

**Tech Stack:** Frappe 16 / ERPNext (Python, unittest via `_pn_run_tests.py`), Vue 3 + Pinia + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-15-discount-stacking-and-coupon-scope-design.md`

## Global Constraints

- Work on branch `review/serene-on-main` in place. Subagents NEVER run git state-changing commands (`add`, `commit`, `stash`, `checkout`, `reset`, `restore`, `merge`). Commits happen only after the user approves, run by the main session.
- Everything runs inside Docker container `erpnext16_dev-frappe-1`, bench at `/workspace/development/frappe-bench`. Host `bench`/`python`/`npm` do not work.
- Backend test command: `docker cp /Users/rotiropi/.claude/jobs/f005c766/tmp/run.sh erpnext16_dev-frappe-1:/tmp/pn_run.sh && docker exec -e FRAPPE_STREAM_LOGGING=1 erpnext16_dev-frappe-1 bash /tmp/pn_run.sh <dotted.module> [...]` (serial only; full log in container `/tmp/pn_<module>.txt`).
- Frontend test command: `docker exec erpnext16_dev-frappe-1 bash -c "cd /workspace/development/frappe-bench/apps/pos_next/POS && npx vitest run <paths>"`.
- Schema changes need `bench --site posnext.localhost migrate` AND `bench --site roti-posnext-test.localhost migrate`.
- No new dependencies. Code comments, docstrings and messages in English; every new user-facing string gets an `id.csv` row (Task 6).
- Defaults: `POS Offer.allow_coupon_combination` = 0, `POS Coupon.allow_offer_combination` = 0, `POS Coupon.company_scope` = `Selected Outlets`.
- Exact messages:
  - `The invoice already has an offer discount; another discount cannot be added.` (existing string, reused)
  - `A coupon cannot be combined with an offer that discounts the invoice total.`
  - `Coupon {0} cannot be combined with offer {1}.`
  - `Coupon {0} is in {1} and cannot be used at {2}.`
  - `Sorry, this coupon is not valid for this company` (existing string, reused for scope miss)
- No HQ code may authorize any stacking reject (same as the existing ones). Returns stay outside the matrix (`validate_invoice_discounts` returns before it).
- Tests must not leak rows into the site DB (lesson from ZER8D8AA: clean up in a way that commits, or use mocks).

## Review Focus

1. **Cart shrinks below the coupon's minimum after the coupon was applied** → the coupon is removed with a warning, the invoice submits without it (Task 4 test `removes coupon when basis drops below min_amount`).
2. **Coupon applied, then every row discounted to 0 / cart emptied** → header discount recomputes to 0, no negative total, no "mode of payment" error (Task 4 test `coupon follows cart down to zero`).
3. **`Selected Outlets` with an empty companies table** → usable only at the issuing company, never "everywhere" (Task 2 test `test_selected_outlets_empty_table_means_issuing_company`).
4. **Amount coupon at another company with the SAME currency** → allowed; only a currency difference rejects (Task 2 test `test_amount_coupon_same_currency_other_company_allowed`).
5. **Offer flag turned off by HQ while a cart already holds offer + coupon** → server rejects at submit with `Coupon {0} cannot be combined with offer {1}.`; nothing is silently dropped (Task 3 test `test_coupon_with_item_offer_flag_off_rejected`).

---

### Task 1: POS Offer `allow_coupon_combination` flag + Pricing Rule mirror

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_offer/pos_offer.json` (new Check after `min_amt` section's fields, before `companies_section`; bump `modified`)
- Modify: `pos_next/install.py:~437` (Pricing Rule custom field list, next to `pos_offer_max_discount`)
- Modify: `pos_next/overrides/pos_offer_sync.py:268` (`_rule_values`)
- Modify: `pos_next/api/offers.py` (`Offer` dataclass ~line 102; every place that selects/maps `pos_offer_max_discount`: lines ~375 and ~572)
- Test: `pos_next/overrides/test_pos_offer_sync.py` if it exists, else the module that already tests `_rule_values` (find with `git grep -n "_rule_values" -- '*test*'`); `pos_next/api/test_offers*.py` for the feed mapping

**Interfaces:**
- Produces: POS Offer field `allow_coupon_combination` (Check, default 0, label "Allow Coupon Combination", description "Let a coupon that also allows offer combination stack on this offer.").
- Produces: Pricing Rule custom field `pos_offer_allow_coupon` (Check, read_only, no_copy, print_hide, insert_after `pos_offer_max_discount`).
- Produces: `_rule_values(...)["pos_offer_allow_coupon"] == cint(offer.allow_coupon_combination)`.
- Produces: `Offer.allow_coupon_combination: int = 0`, filled from `rule.pos_offer_allow_coupon`; serialized in the offers feed the frontend reads (`offer.allow_coupon_combination`).

- [ ] **Step 1: Failing tests** — `test_rule_values_mirror_allow_coupon` (offer with flag 1 → `pos_offer_allow_coupon == 1`; flag 0/None → 0) and `test_offer_feed_carries_allow_coupon_combination` (rule dict with `pos_offer_allow_coupon=1` → mapped Offer has `allow_coupon_combination == 1`).
- [ ] **Step 2: Run, expect FAIL** (KeyError / attribute missing).
- [ ] **Step 3: Implement** the field JSON, install.py custom field, `_rule_values` key, SQL select + dataclass mapping.
- [ ] **Step 4: Migrate both sites**, then `bench --site posnext.localhost mariadb -e "SHOW COLUMNS FROM \`tabPricing Rule\` LIKE 'pos_offer_allow_coupon'"` → 1 row; rerun tests → PASS; rerun `pos_next.api.test_pos_offer_usage` → still 22/22.

### Task 2: POS Coupon multi-company scope + `allow_offer_combination`

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_coupon/pos_coupon.json` (relabel `company` → "Issuing Company"; add after it `company_scope` Select `All Outlets\nSelected Outlets\nAll Outlets Except` default `Selected Outlets`, `companies` Table options `POS Discount Code Company` with `depends_on: eval:doc.company_scope!='All Outlets'`; add `allow_offer_combination` Check default 0 in the discount section; bump `modified`)
- Modify: `pos_next/pos_next/doctype/pos_coupon/pos_coupon.py` (new `coupon_usable_at`; `check_coupon_code` line ~92 uses it)
- Modify: `pos_next/api/offers.py` (`validate_coupon` ~714: drop `"company"` from the get_value filter, then call `coupon_usable_at`; `get_active_coupons` ~672: drop company filter, keep rows where `coupon_usable_at` is None; `_COUPON_RESPONSE_FIELDS` += `"allow_offer_combination"`)
- Modify: `pos_next/api/promotions.py` (`get_coupons` ~757: list coupons usable at `company`, not only issued by it; `create_coupon` ~901: set `company_scope="Selected Outlets"` and one `companies` row = the company)
- Create: `pos_next/patches/v2_20_0/scope_existing_coupons.py`; append `pos_next.patches.v2_20_0.scope_existing_coupons` under `[post_model_sync]` in `pos_next/patches.txt`
- Test: `pos_next/pos_next/doctype/pos_coupon/test_pos_coupon.py` (mock style already used there)

**Interfaces:**
- Produces: `coupon_usable_at(coupon, company) -> str | None` in `pos_coupon.py`. `coupon` is a dict/doc with `name`, `coupon_code`, `company`, `company_scope`, `discount_type`. Returns None when usable, else the user-facing message. Rules: falsy `company` → None; scope `All Outlets` → usable everywhere; `Selected Outlets` → company in table, or (table empty and company == issuing company); `All Outlets Except` → company not in table. Then, if usable and `discount_type == "Amount"` and the two companies' `default_currency` (via `frappe.get_cached_value("Company", c, "default_currency")`) differ → `Coupon {0} is in {1} and cannot be used at {2}.` (code, issuing currency, company). Scope miss → `Sorry, this coupon is not valid for this company`. Child rows read with `frappe.get_all("POS Discount Code Company", {"parenttype": "POS Coupon", "parent": coupon name}, pluck="company")`.
- Produces: coupon API objects carry `allow_offer_combination` (0/1).

- [ ] **Step 1: Failing tests** (mock `frappe.get_all` / `get_cached_value`):
  - `test_all_outlets_usable_anywhere`
  - `test_selected_outlets_listed_company_usable` / `..._unlisted_company_rejected`
  - `test_selected_outlets_empty_table_means_issuing_company` (issuing → None; other → message)
  - `test_all_outlets_except_excludes_listed`
  - `test_amount_coupon_currency_mismatch_rejected` (message contains code, both values)
  - `test_amount_coupon_same_currency_other_company_allowed`
  - `test_percentage_coupon_ignores_currency`
  - `test_patch_scopes_existing_coupons_idempotent` (real DB is acceptable here only if the test creates nothing; otherwise test the patch function with mocks: a coupon with no rows gets one row + scope, a second run adds nothing)
- [ ] **Step 2: Run `pos_next.pos_next.doctype.pos_coupon.test_pos_coupon`, expect FAIL** (ImportError on `coupon_usable_at`).
- [ ] **Step 3: Implement** helper, lookups, create_coupon, patch (`execute()`: for every POS Coupon, set `company_scope` to `Selected Outlets` where empty; insert a `POS Discount Code Company` row for the issuing company when the coupon has no rows; skip coupons with no `company`).
- [ ] **Step 4: Run tests → PASS**; also `pos_next.tests.test_coupon_discount_enforcement` and `pos_next.tests.test_coupon_one_use_resubmit` stay green.
- [ ] **Step 5: Migrate both sites**; verify `bench --site posnext.localhost mariadb -e "SELECT COUNT(*) FROM \`tabPOS Coupon\` c WHERE NOT EXISTS (SELECT 1 FROM \`tabPOS Discount Code Company\` r WHERE r.parenttype='POS Coupon' AND r.parent=c.name)"` → 0 (and same on roti site).

### Task 3: Server stacking matrix

**Files:**
- Modify: `pos_next/overrides/discount_code.py` (`_RULE_FIELDS` line 62: add `"pos_offer_allow_coupon"` and `"title"` if missing; `_stamped_coupon` line 415: add `"allow_offer_combination"` to the fetched fields; `reject_stacked_offer_discounts` line 543)
- Test: `pos_next/api/test_discount_code.py` class `TestOfferStacking` (reuse its `_run`, `_row`, `_doc`, `_rule`, `_txn` helpers; patch `_stamped_coupon` for coupon cases)

**Interfaces:**
- Consumes: Pricing Rule `pos_offer_allow_coupon` (Task 1), POS Coupon `allow_offer_combination` (Task 2).
- Produces: `reject_stacked_offer_discounts(doc)` throws `frappe.ValidationError` for the new rows below. Deviation from spec (simpler, same result): the offer flag is read from the verified rule's mirror field instead of `resolve_offers_from_rules`; rules not owned by a POS Offer carry 0, so they never combine with a coupon. Offer name in the message = rule `title` or `name`.

New checks, in this order, after the existing per-row check:
1. Verified Price-type transaction claim present (same `txn_rules` selection as today, computed before the header early-return) → any item row with `_item_has_discount(item)` that is not free and not `_item_offer_matches(...)` → throw `The invoice already has an offer discount; another discount cannot be added.`
2. `txn_rules` present and `_stamped_coupon(doc)` → throw `A coupon cannot be combined with an offer that discounts the invoice total.`
3. Stamped coupon and any item row whose applicable verified rules (`_item_applicable_rules`) are non-empty → unless `coupon.allow_offer_combination` and every such rule's `pos_offer_allow_coupon` are truthy, throw `Coupon {0} cannot be combined with offer {1}.` (coupon code, first offending rule title/name).

- [ ] **Step 1: Failing tests** in `TestOfferStacking`:
  - `test_transaction_offer_with_manual_item_discount_rejected_even_with_code`
  - `test_transaction_offer_with_undiscounted_items_passes` (existing exact-header case still passes)
  - `test_transaction_offer_with_coupon_rejected`
  - `test_coupon_with_item_offer_both_flags_on_passes`
  - `test_coupon_with_item_offer_flag_off_rejected` — parametrize via `subTest` over (coupon, offer) = (0,0), (1,0), (0,1); message contains the coupon code
  - `test_coupon_with_non_pos_offer_rule_rejected` (rule dict without `pos_offer_allow_coupon`)
  - `test_coupon_with_manual_item_discount_no_offer_unchanged` (stays in manual lane; valid code passes)
- [ ] **Step 2: Run `pos_next.api.test_discount_code`, expect the new tests to FAIL** (no throw).
- [ ] **Step 3: Implement** the three checks.
- [ ] **Step 4: Run** `pos_next.api.test_discount_code pos_next.api.test_discount_gate_hardening pos_next.tests.test_coupon_discount_enforcement pos_next.api.test_pos_invoice_submit pos_next.test_promotions pos_next.api.test_invoices_offer_relay` → all green except the known `test_promotions.test_free_same_item`.

### Task 4: Frontend coupon recompute (fixes the mode-of-payment error)

**Files:**
- Create: `POS/src/utils/couponDiscount.js`, `POS/src/utils/couponDiscount.test.js`
- Modify: `POS/src/composables/useInvoice.js` (`applyDiscount` ~618, `removeDiscount`, new computed bases near `appliedAdditionalDiscount` ~207)
- Modify: `POS/src/stores/posCart.js` (`applyDiscountToCart` 419: store definition; watcher for min_amount)
- Modify: `POS/src/components/sale/CouponDialog.vue` (~392-431: emit the coupon definition; keep its min_amount pre-check as UX)
- Test: `POS/src/composables/useInvoice.test.js`, `POS/src/stores/posCart.test.js`

**Interfaces:**
- Produces: `expectedCouponDiscount(coupon, bases) -> number` and `couponMinAmountUnmet(coupon, bases) -> boolean` in `utils/couponDiscount.js`. `coupon` = `{discount_type, discount_percentage, discount_amount, max_amount, min_amount, apply_on}`; `bases` = `{grand, net}` = totals BEFORE the header discount. Same formula as `pos_coupon.expected_coupon_discount`: basis = `net` when `apply_on === "Net Total"` else `grand`; Percentage → basis × pct / 100; Amount → amount; unknown type → 0; cap by `max_amount` when > 0; never above basis; never below 0. Rounding stays with the caller (`roundCurrency`).
- Produces: `useInvoice` exposes `couponBases` computed: `grand` = the base `appliedAdditionalDiscount` already uses; `net` = `grand - totalTax` when tax-inclusive, else `subtotal - itemDiscount` (pre-tax).
- Produces: `useInvoice.applyDiscount(discount)` stores `discount.coupon` as the coupon definition; while one is stored, `additionalDiscount` is recomputed on every change of `couponBases` via `expectedCouponDiscount`. `removeDiscount` clears it.
- Produces: posCart watcher — when `appliedCoupon` is set and `couponMinAmountUnmet(def, couponBases)` → clear the coupon (same path as `removeDiscountFromCart`, but without touching applied offers) and `showWarning(__("Coupon {0} was removed: the cart is below its minimum of {1}", [name, formatted min]))`.

- [ ] **Step 1: Failing tests**
  - `couponDiscount.test.js`, table-driven (same table as Python `expected_coupon_discount`): 20% of 100000 → 20000; 20% capped by max 5000 → 5000; Amount 30000 on basis 10000 → 10000; Net Total uses `net`; unknown type → 0; min 50000 with basis 40000 → unmet true.
  - `useInvoice.test.js`: `coupon follows cart down to zero` — apply 20% coupon on 100000, then 100% manual item discount → `additionalDiscount` 0, grand total 0 not negative; `coupon 20% + manual 90%` — after the manual discount the header discount equals 20% of the remaining basis (server value), not the frozen apply-time amount.
  - `posCart.test.js`: `removes coupon when basis drops below min_amount` (appliedCoupon null, warning shown).
- [ ] **Step 2: Run, expect FAIL** (module missing / frozen amount).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the three test files → PASS, then the full suite `npx vitest run` → no new failures.

### Task 5: Frontend conflict locks

**Files:**
- Modify: `POS/src/stores/posCart.js` (`applyDiscountToCart` 419; `applyOffer` 734; auto-apply gates ~1137 offline and ~1832 online)
- Modify: `POS/src/components/sale/EditItemDialog.vue` (`discountLockedByOffer` ~598)
- Test: `POS/src/stores/posCart.test.js`, `POS/src/components/sale/EditItemDialog.test.js`

**Interfaces:**
- Consumes: `offer.allow_coupon_combination` (Task 1 feed), `coupon.allow_offer_combination` (Task 2 API), `headerDiscountFromOffer` (existing).
- Produces: `canCombine(offer, coupon) -> boolean` local to posCart (`Boolean(offer.allow_coupon_combination && coupon?.allow_offer_combination)`); a transaction offer is never combinable.

Behaviour:
- `applyDiscountToCart(coupon)` — the cashier's coupon wins over automatic offers: non-combinable applied offers with `source` `auto`/`offline` are removed through `removeOffer` (which also adds them to `dismissedOfferCodes`) and `showWarning(__("Offer {0} was removed: it can't be combined with coupon {1}", ...))`. A non-combinable offer the cashier applied (`source: "manual"`) → refuse the coupon, `showWarning(__("Coupon {0} can't be combined with offer {1}. Remove the offer first.", ...))`.
- Auto-apply (both gates): while `appliedCoupon` is set, skip offers not combinable with it; skip transaction offers while any row carries a manual discount.
- `applyOffer` (cashier-chosen) while a coupon is applied and not combinable → refuse with `__("Offer {0} can't be combined with coupon {1}. Remove the coupon first.", ...)`.
- EditItemDialog: discount fields also locked while `headerDiscountFromOffer` is true, with hint `__("A cart offer is applied; item discounts are locked. Remove the offer to edit.")`.

- [ ] **Step 1: Failing tests**: `coupon removes non-combinable auto offer with notice` (offer dismissed, not re-applied by the next auto pass); `refuses coupon over non-combinable cashier-applied offer`; `allows coupon when both flags on`; `auto-apply skips non-combinable offers while coupon applied`; `auto-apply skips transaction offer when a row has a manual discount`; `refuses cashier offer over non-combinable coupon`; EditItemDialog `locks item discount under a transaction offer` and `unlocks after the offer is removed`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the two files → PASS; full `npx vitest run` → no new failures.

### Task 6: Translations, build, full verification (main session)

**Files:**
- Modify: `pos_next/translations/id.csv` (one row per new string from Tasks 2–5)

- [ ] **Step 1:** Add `id.csv` rows; `npm run lint` in `POS/` → no new findings vs. baseline (4 pre-existing).
- [ ] **Step 2:** Backend: Task 3 Step 4 module list + `pos_next.pos_next.doctype.pos_coupon.test_pos_coupon pos_next.api.test_pos_offer_usage` → green except known `test_free_same_item`.
- [ ] **Step 3:** Frontend: full `npx vitest run` green; `npm run build` OK; `bench --site posnext.localhost clear-cache`.
- [ ] **Step 4:** Leak check: no new rows in `tabPOS Coupon`, `tabPOS Discount Code`, `tabPricing Rule` from tests (compare counts before/after).
- [ ] **Step 5:** Browser (Playwright, `posnext.localhost:8001/pos`): (a) txn offer → item discount locked; (b) coupon 20% + manual 90% → submits, paid matches grand total; (c) item offer sale submits (offer-usage fix); (d) coupon over item offer refused when flags off, accepted when both on.
- [ ] **Step 6:** Update `PROJECT_STATE.md`; propose commit message(s) to the user; commit only after approval.

## Execution order (fan-out)

- Wave 1 (parallel, Sonnet): Task 1, Task 2, Task 4 — disjoint regions (Task 1 and 2 both touch `api/offers.py` but different functions).
- Wave 2 (parallel, Sonnet): Task 3 (needs 1+2 field names), Task 5 (needs 1+2 field names, after Task 4 because both edit `posCart.js`).
- Wave 3 (main session): Task 6.
- Each task gets a reviewer pass before the next wave; migrations run by the main session after Wave 1.
