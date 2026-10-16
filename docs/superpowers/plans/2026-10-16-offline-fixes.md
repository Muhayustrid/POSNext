# Offline Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Offline Invoices sync without getting stuck (price mismatch accepted + flagged, sale time honoured, shift gate by sale time), offline pricing matches the server more closely, and offline receipts direct-print with the same look as "POS Next Receipt".

**Architecture:** Server trusts an Offline Invoice only through a request-scoped flag `frappe.flags.pos_next_offline_sync` that `submit_invoice` sets when the payload carries `offline_id` (clients cannot set `frappe.flags`). Price Replay, posting-date policy and the shift gate read that flag. Client stamps the sale time at the single offline save choke point (`saveOfflineInvoice`), moves transaction offers to the header slot, and caches print config per profile.

**Tech Stack:** Frappe v16 / ERPNext (Python, unittest via `_pn_run_tests.py`), Vue 3 + Pinia + Vitest, vite-plugin-pwa (workbox).

**Spec:** `.scratch/offline-fixes/spec.md` (ADR `docs/adr/0001-accept-offline-price-mismatch.md`, terms in `GLOSSARY.md`).

## Global Constraints

- No new dependencies; no client-side Jinja.
- Online invoices keep rejecting Price Replay mismatches; `price_replay_audit_only` behaviour unchanged.
- Fabricated / disabled / other-company rule names in an offline payload still reject.
- Custom fields go in `install.py` `CUSTOM_FIELDS` + a versioned patch `pos_next.patches.v2_22_0.*` (desk JSON mirrors do not apply).
- Error Log title exactly `POS Offline Price Mismatch`; field `pos_offline_price_mismatch` (Check, read-only, no_copy, in_standard_filter) on Sales Invoice and POS Invoice.
- Offline sale time accepted without backdate role only within [shift `period_start_date`, server now].
- `/api/` stays NetworkOnly in the SW.
- Code comments in English; new UI strings need translation CSV rows (`docs/LOCALIZATION.md`).
- No git state-changing commands by subagents; commits only after the user approves the message.
- Backend tests: `docker exec erpnext16_dev-frappe-1 bash -lc "cd /workspace/development/frappe-bench && FRAPPE_STREAM_LOGGING=1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>"` (serial). Frontend: same container, `cd apps/pos_next/POS && npx vitest run <file>`.

## Review Focus

1. Device clock ahead of server (sale time slightly in the future) → must not reject; clamp to server now (test in Task 1).
2. Device clock behind shift opening (wrong date on device) → clear rejection message naming the shift start, not a generic permission error (test in Task 1).
3. Offline invoice with a mismatch AND a coupon / header discount → accepted, flagged once, one Error Log (test in Task 2).
4. Transaction offer offline while a coupon is applied → coupon keeps the header slot, offer not applied (test in Task 5).
5. Print config never fetched on this device (first boot offline) → falls back to browser print without throwing (test in Task 6).

---

### Task 1: Offline sale time + shift gate (server)

**Files:**
- Modify: `pos_next/api/invoices.py` (`_enforce_posting_date_policy` ~1770, `submit_invoice` ~2637 and its `finally` ~3016)
- Modify: `pos_next/shift_schedule.py` (`validate_invoice` 288-318)
- Test: `pos_next/api/test_offline_replay_price_validation.py`, `pos_next/test_shift_schedule.py`

**Interfaces:**
- Produces: `frappe.flags.pos_next_offline_sync` (bool, set in `submit_invoice` right after the dedup block when `offline_id` is truthy, reset to `None` in its `finally`). Task 2 reads it.
- Produces: `_offline_sale_time_allowed(invoice_doc) -> bool` in `invoices.py`: True only when the flag is set, `set_posting_time` is on, and `get_datetime(posting_date + posting_time)` ≥ the shift's `period_start_date`. If the sale time is later than `now_datetime()`, overwrite `posting_date`/`posting_time` with now (clamp) and return True. If before shift start, `frappe.throw(_("Offline sale time {0} is before shift {1} opened at {2}."), frappe.ValidationError)`.
- `_enforce_posting_date_policy`: return early when `_offline_sale_time_allowed(invoice_doc)` is True (before the backdate checks). Update its docstring ("Everything else — including offline replays" → offline replays inside the shift window are allowed).
- `shift_schedule.validate_invoice`: when `frappe.flags.pos_next_offline_sync` and `cint(doc.get("set_posting_time"))`, use `get_datetime(f"{doc.posting_date} {doc.posting_time}")` as the admission time instead of `doc.creation`.

- No change to `overrides/sales_invoice.py::validate_pos_opening_entry` (shift must be Open): closing is blocked while invoices are unsynced (`ShiftClosingDialog.vue:1218`), so the shift is still Open at late sync. Confirm by reading, no code.

- [ ] **Step 1: Write failing tests** in `test_offline_replay_price_validation.py` (reuse `_submit_offline`, `self.ctx`):
  - `test_offline_sale_time_kept`: payload `set_posting_time=1`, `posting_date` today, `posting_time` = now minus 2 minutes (and ≥ ctx shift `period_start_date`); submit offline; `doc.posting_time` equals the sent time (string compare `HH:MM:SS`).
  - `test_offline_sale_time_before_shift_rejected`: posting datetime = shift `period_start_date` − 1 day → `assertRaises(frappe.ValidationError)`, message contains `"before shift"`.
  - `test_offline_sale_time_future_clamped`: posting time = now + 10 minutes → submits; `get_datetime(doc.posting_date, doc.posting_time) <= now_datetime()`.
  - `test_online_set_posting_time_still_rejected`: same payload as the first test but via `update_invoice` (no offline_id) → `frappe.PermissionError` "Changing the invoice posting date is not allowed".
  In `test_shift_schedule.py` `TestInvoiceHook`:
  - `test_offline_sale_before_deadline_admitted`: `_doc` with `creation` after the deadline, `set_posting_time=1`, `posting_date/posting_time` before the deadline, `patch.object(frappe.flags, "pos_next_offline_sync", True, create=True)` → no throw.
  - `test_offline_sale_after_deadline_rejected`: same, sale time after deadline → throw called once.
  - `test_online_late_creation_still_rejected`: flag unset, sale time before deadline, creation after → throw.
- [ ] **Step 2: Run** both modules; expect the new tests to FAIL (PermissionError / throw called).
- [ ] **Step 3: Implement** the interfaces above. `_doc` helper in `test_shift_schedule.py` may need `set_posting_time/posting_date/posting_time` keys added to its `values` dict.
- [ ] **Step 4: Run** `pos_next.api.test_offline_replay_price_validation pos_next.test_shift_schedule pos_next.api.test_backdate_invoices` (if that module exists; else grep `test_*backdate*`) → new tests PASS, no new failures.
- [ ] **Step 5: Commit** (after user approval): `fix(offline): pakai waktu jual offline dan gerbang shift berdasarkan waktu jual`

### Task 2: Accept Offline Price Mismatch + HQ flag (server)

**Files:**
- Modify: `pos_next/api/invoices.py` (`_verify_offer_rate_magnitudes` violations tail ~810-822)
- Modify: `pos_next/install.py` (`CUSTOM_FIELDS["Sales Invoice"]` and `["POS Invoice"]`, insert after `pos_queue_date`)
- Create: `pos_next/patches/v2_22_0/__init__.py`, `pos_next/patches/v2_22_0/add_offline_price_mismatch_field.py` (`execute()` calls `pos_next.install.sync_custom_fields(quiet=True)`)
- Modify: `pos_next/patches.txt` (post_model_sync, after v2_21_0)
- Test: `pos_next/api/test_offline_replay_price_validation.py`

**Interfaces:**
- Consumes: `frappe.flags.pos_next_offline_sync` (Task 1).
- Produces: `_offline_claims_genuine(invoice_doc, claimed_rules: set[str]) -> bool` — every name exists in `Pricing Rule`, `disable=0`, `company == invoice_doc.company`; empty set → True (free/zero rows with no claim are still sales already paid).
- Tail logic order: if `violations` and flag set → if `_offline_claims_genuine(invoice_doc, claimed_all)`: `invoice_doc.pos_offline_price_mismatch = 1`, `frappe.log_error(title="POS Offline Price Mismatch", message=<invoice name or offline_id> + "\n" + violations joined, [:2000])`, return; else throw `violations[0]`. Audit-only branch stays for online.
- Custom field dict: `{"fieldname": "pos_offline_price_mismatch", "label": "Offline Price Mismatch", "fieldtype": "Check", "insert_after": "pos_queue_date", "read_only": 1, "no_copy": 1, "print_hide": 1, "in_standard_filter": 1, "description": "Offline Invoice accepted at the charged price although Price Replay disagreed; see Error Log 'POS Offline Price Mismatch'."}`. Check the next field's `insert_after` (`pos_coupon_code` currently after `pos_queue_date`) — leave it, Frappe orders by insert_after chain; if ordering matters, point `pos_offline_price_mismatch` after `pos_coupon_code` instead.

- [ ] **Step 1: Write failing tests:**
  - Replace the expectation in `test_forged_offline_replay_rejected` → rename `test_offline_mismatch_accepted_and_flagged`: same forged payload (rate 20 vs 42.5, real rule) submits; `doc.docstatus == 1`, `doc.pos_offline_price_mismatch == 1`, `flt(doc.items[0].rate) == 20`; an Error Log with `method="POS Offline Price Mismatch"` created after test start contains the invoice name.
  - `test_offline_mismatch_fabricated_rule_rejected`: same payload but `pricing_rules` = `"_PNXT_SEC23_DOES_NOT_EXIST"` → `assertRaises(frappe.ValidationError)`.
  - `test_offline_mismatch_disabled_rule_rejected`: real rule, then `frappe.db.set_value("Pricing Rule", rule, "disable", 1)` before submit → raises.
  - `test_offline_honest_not_flagged`: existing `test_honest_offline_replay_passes` additionally asserts `doc.pos_offline_price_mismatch == 0`.
  - `test_forged_magnitude_rejected` (online) stays unchanged and must still pass.
  - Review Focus 3: `test_offline_mismatch_with_header_discount_flagged_once`: forged row + header `discount_amount` with `apply_discount_on="Grand Total"` and a transaction rule relayed → accepted, exactly one new Error Log for this invoice.
- [ ] **Step 2: Run** `migrate` is not needed for the failing run; tests FAIL (ValidationError raised / AttributeError on field).
- [ ] **Step 3: Implement**; run `bench --site posnext.localhost migrate` and `--site roti-posnext-test.localhost migrate` in the container.
- [ ] **Step 4: Run** `pos_next.api.test_offline_replay_price_validation pos_next.api.test_draft_submit_rate_gate pos_next.test_promotions` → new tests PASS; only pre-existing failures (`test_free_same_item`).
- [ ] **Step 5: Commit**: `feat(offline): terima selisih harga invoice offline dan tandai untuk HQ`

### Task 3: Expose offer conditions to the offline engine (server)

**Files:**
- Modify: `pos_next/api/offers.py` (`Offer` dataclass 65-113, both builders 316-420, both SQLs 569 + 626, `EligibilityFetcher._fetch_items` ~154)
- Test: existing offers test module (grep `get_offers` under `pos_next/**/test_*.py`; add there)

**Interfaces:**
- Produces new `Offer` fields (all defaulted, appended after `quota_remaining`): `applicable_for: str | None = None`, `applicable_value: str | None = None` (the Pricing Rule field named by `applicable_for`, lower-snake: customer / customer_group / territory / sales_partner / campaign), `warehouse: str | None = None`, `for_price_list: str | None = None`, `item_uoms: dict[str, str] = field(default_factory=dict)` (item_code → uom from `Pricing Rule Item Code.uom`, only rows with uom set; for schemes read the same child of the scheme).
- Both SQLs select `applicable_for, customer, customer_group, territory, sales_partner, campaign, warehouse, for_price_list` from `tabPricing Rule`.

- [ ] **Step 1: Failing test** `test_get_offers_exposes_conditions`: create standalone rule with `applicable_for="Customer Group"`, `customer_group="Commercial"` (any existing group), `warehouse=ctx.warehouse`, `for_price_list=profile price list`, item row with `uom="Nos"`; `get_offers(profile)` entry has `applicable_for == "Customer Group"`, `applicable_value == "Commercial"`, `warehouse`, `for_price_list`, `item_uoms == {ITEM_A: "Nos"}`.
- [ ] **Step 2: Run** → FAIL (KeyError).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the module → PASS.
- [ ] **Step 5: Commit**: `feat(offers): kirim syarat customer/gudang/price list/uom ke engine offline`

### Task 4: Stamp the sale time on Offline Invoices (client)

**Files:**
- Modify: `POS/src/utils/offline/sync.js` (`saveOfflineInvoice` ~89)
- Test: `POS/src/utils/offline/sync.test.js` (create if absent, mock `db`)

**Interfaces:**
- Produces on every queued payload: `posting_date` (`toISODate(new Date())` from `@/utils/salesRecap`), `posting_time` (`new Date().toLocaleTimeString("en-GB", { hour12: false })`, same as POSSale's print doc), `set_posting_time: 1`. Only set when `cleanData.posting_date` is absent (an HO-backdated payload keeps its own).

- [ ] **Step 1: Failing test** `stamps posting date/time at save`: `vi.setSystemTime(new Date(2026, 9, 16, 14, 5, 9))`; saved `data` has `posting_date "2026-10-16"`, `posting_time "14:05:09"`, `set_posting_time 1`. Second test: payload with `posting_date "2026-10-01"` keeps it.
- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run src/utils/offline` → PASS.
- [ ] **Step 5: Commit**: `fix(offline): simpan tanggal dan jam jual di antrean offline`

### Task 5: Offline pricing parity (client)

**Files:**
- Modify: `POS/src/stores/posOffers.js` (`checkOfflineEligibility` new helper, called from `checkOfferEligibility` 234-351 only when `offlineState.isOffline`)
- Modify: `POS/src/stores/posCart.js` (`applyOffersOffline` 1225+, `buildCartSnapshot` 1548)
- Test: `POS/src/stores/posCart.test.js`, `POS/src/stores/posOffersQuota.test.js` (or new `posOffers.offline.test.js`)

**Interfaces:**
- Consumes: Task 3 offer fields; Task 4 not needed (sale date = today at evaluation time).
- `buildCartSnapshot` adds `customer` (name string or null), `warehouse` (profile warehouse), `priceList` (profile `selling_price_list`), `uomsByItem` ({item_code: uom}).
- Offline eligibility rules, each failing → `{eligible:false, reason}`:
  - `valid_from`/`valid_upto` vs `toISODate(new Date())` (string compare `YYYY-MM-DD`).
  - `applicable_for`: `"Customer"` → `applicable_value === snapshot.customer`; any other non-empty value (Customer Group, Territory, Sales Partner, Campaign) → ineligible ("cannot be checked offline").
  - `warehouse` set → must equal `snapshot.warehouse`; `for_price_list` set → must equal `snapshot.priceList`.
  - `item_uoms` non-empty → every eligible cart item with an entry must have that uom.
  - Missing snapshot value for a set condition → ineligible.
- `applyOffersOffline`: an offer with `apply_on === "Transaction"` and `offer === "Item Price"` no longer touches rows; it calls `applyHeaderDiscountFromServer({ discountAmount })` where `discountAmount = discount_type === "Discount Percentage" ? round(subtotalAfterItemDiscounts * discount_percentage / 100) : discount_amount`, capped by `max_discount_amount` when > 0; still pushed into `appliedOffers` (`rules:[offer.name]`) so `pos_relayed_offer_rules` carries it. Skipped (not pushed) when `appliedCoupon` is set.

- [ ] **Step 1: Failing tests:**
  - `offline transaction offer becomes header discount`: offline, cached offer `{apply_on:"Transaction", offer:"Item Price", discount_type:"Discount Percentage", discount_percentage:10, auto:1}`, cart subtotal 50000 → `additionalDiscount === 5000`, every row `discount_percentage === 0` and empty `pricing_rules`, `appliedOffers[0].code` = offer name.
  - `offline transaction offer skipped with coupon` (Review Focus 4): coupon applied first → `additionalDiscount` stays the coupon amount, offer not in `appliedOffers`.
  - `expired offer not applied offline`: `valid_upto` yesterday → not applied.
  - `customer-group offer skipped offline`: `applicable_for:"Customer Group"` → not applied.
  - `customer offer applies only to that customer`: `applicable_for:"Customer"`, value `CUST-A`; cart customer `CUST-B` → not applied; `CUST-A` → applied.
  - `warehouse / price list / uom mismatch skip`: one case each.
- [ ] **Step 2: Run** `npx vitest run src/stores` → new tests FAIL.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `npx vitest run src/stores` → PASS (whole folder, no regressions).
- [ ] **Step 5: Commit**: `fix(offline): samakan perhitungan offer offline dengan server`

### Task 6: Offline direct print (client)

**Files:**
- Modify: `POS/src/utils/print/transport.js` (`initTransportFromServer` 234-270)
- Modify: `POS/vite.config.js` (workbox `runtimeCaching`, after the `/api/` route)
- Test: `POS/src/utils/print/transport.test.js`

**Interfaces:**
- localStorage key `pos_next_print_config:<posProfile>` holds the raw `cfg` JSON. Success → write it; call throws → read it, apply via the same `setConfig` mapping, return it; nothing cached → rethrow the original error (callers already fall back to browser print — verify `ensureTransportInitialized` in `printInvoice.js:594` swallows it; Review Focus 5).
- Workbox route: `{ urlPattern: /\/assets\/pos_next\/js\/lib\/imin\/.*\.js$/i, handler: "CacheFirst", options: { cacheName: "imin-sdk-cache", expiration: { maxEntries: 5 }, cacheableResponse: { statuses: [0, 200] } } }`. The SDK path is versioned (`1.4.0`), so CacheFirst cannot pin a stale copy.

- [ ] **Step 1: Failing tests:** `caches config on success` (localStorage has key after call); `falls back to cached config when the call fails` (mock `call` rejects; `getTransport().getConfig?.()` / driver equals cached `"imin"` — use whatever getter `createTransport` exposes, check the file); `rethrows when nothing cached`.
- [ ] **Step 2: Run** `npx vitest run src/utils/print/transport.test.js` → FAIL.
- [ ] **Step 3: Implement** both changes.
- [ ] **Step 4: Run** the test → PASS; `npm run build` → succeeds and `grep -c imin-sdk-cache ../pos_next/public/pos/sw.js` (or wherever the SW is emitted) ≥ 1.
- [ ] **Step 5: Commit**: `fix(print): direct print tetap jalan saat offline`

### Task 7: One Receipt Template look (client)

**Files:**
- Modify: `POS/src/utils/printInvoice.js` (`buildReceiptHTML` 191-417 markup + its CSS in `buildReceiptDocumentHTML` 419)
- Create: `POS/src/utils/printInvoice.parity.test.js`
- Modify: `pos_next/translations/*.csv` only if new `__()` strings appear

**Interfaces:**
- Target layout = Jinja in `pos_next/pos_next/print_format/pos_next_receipt/pos_next_receipt.json`: queue number block (`NO. ANTRIAN`, `%03d`), logo, company name + address line (offline: `invoiceData.company_address_line` if present), divider, info rows `Invoice` / `Cashier` / `Date` (`posting_date posting_time`) / `Buyer` or `Customer` / `Status`, items (FREE line, Discount line, serials), `Total Qty`, Subtotal/Tax, Additional Discount, `total-group` with `TOTAL`, each payment, `Paid`, `Change`, `Due` (label + `info-colon` + value), footer `Thank you` / `Powered by POS Next`. Keep the `OFFLINE — PENDING SYNC` badge (offline-only, intentional difference). Copy the Jinja `<style>` classes (`info-row`, `info-label`, `info-colon`, `info-value`, `total-group`, `divider`, `item-section`, `discount-line`, `footer`) into the JS document CSS.
- Remove from JS: `TAX INVOICE` header, `Invoice #:`, `toLocaleString` date, `Payments:` header, `Total Paid:`, `BALANCE DUE:`, BrainWise footer.

- [ ] **Step 1: Failing parity test:** read the Jinja via `fs.readFileSync(resolve(__dirname, "../../../pos_next/pos_next/print_format/pos_next_receipt/pos_next_receipt.json"))` → `JSON.parse(...).html`; render `buildReceiptHTML(sampleDoc)` where `sampleDoc` has queue number 7, buyer, 2 items (one free, one discounted), tax, header discount, 2 payments, change, outstanding 0, partial status off. `MARKERS = ["NO. ANTRIAN", "ropi-logo.png", "info-label\">Invoice", "info-label\">Cashier", "info-label\">Date", "info-label\">Buyer", "Total Qty", "Additional Discount", "total-group", "info-label\">TOTAL", "info-label\">Paid", "info-label\">Change", "Thank you", "Powered by POS Next"]`; assert each marker is in both strings. Also assert `!html.includes("TAX INVOICE")` and `!html.includes("BrainWise")`.
- [ ] **Step 2: Run** `npx vitest run src/utils/printInvoice.parity.test.js` → FAIL.
- [ ] **Step 3: Implement** the markup change; update assertions in `printInvoice.test.js` / `printInvoice.dateonly.test.js` that pinned the old labels (they encode the old layout, which the spec replaces).
- [ ] **Step 4: Run** `npx vitest run src/utils` → PASS.
- [ ] **Step 5: Commit**: `fix(print): samakan struk offline dengan template POS Next Receipt`

### Task 8: Full verification + state

- [ ] Backend: run Task 1-3 modules plus `pos_next.test_promotions pos_next.api.test_draft_submit_rate_gate` on posnext; only known pre-existing failures.
- [ ] Frontend: `npm run test:run` (expect ≥ 854 tests pass), `npm run lint` (no new findings beyond the 4 known), `npm run build`.
- [ ] Migrate both sites; confirm `pos_offline_price_mismatch` exists on Sales Invoice + POS Invoice (`tabCustom Field`) and `tabPatch Log` has v2_22_0.
- [ ] Update `PROJECT_STATE.md` (≤ 80 lines): outcome + manual device check still to do (offline reload → sale with offer → direct print → sync → flag in Desk; re-sync the 2 training invoices).
