# Stock Opname Hardening Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the POS Stock Opname blind count support guarded backdate posting, self-converted UOM input, a blind search endpoint, and a complete read-only detail view — closing the blind-count leaks Fable found.

**Architecture:** Backend-first: `pos_next/api/stock_opname.py` gains posting-date guards, UOM self-conversion, and a new blind search endpoint; the Vue dialog consumes them task by task. Spec `.scratch/stock-opname/spec.md` D6 is amended to guarded backdate.

**Tech Stack:** Frappe/ERPNext Python (`pos_next/api/stock_opname.py`, tests `pos_next/api/test_stock_opname.py`), Vue 3 + frappe-ui (`POS/src/components/pos/StockOpnameDialog.vue`, Vitest).

**Spec:** `.scratch/stock-opname/spec.md` (D6 amended by Task 1); Fable review findings + user decisions (backdate guarded, UOM hardening yes, blind-search endpoint yes) from the 2026-10-17 session.

## Global Constraints

- Blind count is inviolable: outlet users must never receive system qty (`current_qty`, `current_valuation_rate`, `quantity_difference`, `amount_difference`, `custom_qty_before`, `custom_qty_difference_per_uom`, `custom_valuation_rate_per_uom`, `custom_conversion_factor`, `difference_amount`, `expense_account`).
- `_payload` is built field-by-field; never `doc.as_dict()`.
- Backend tests run serially via `pos_next/_pn_run_tests.py` inside container `erpnext16_dev-frappe-1` (`FRAPPE_STREAM_LOGGING` set); frontend via `npx vitest run <file>` in `POS/` inside the same container.
- Conventional commits with scope; descriptions in Indonesian.
- New UI strings need `pos_next/translations/id.csv` entries (existing "Add Item"/"Search item..." reused where possible).

## Review Focus

- A backdated opname reposts later stock entries: the max-backdate cap and frozen-stock check must actually block, not just warn — test with a backdate beyond the cap and a frozen date inside the window.
- If `warehouse_app` is absent its custom fields do not exist: saving with a non-stock UOM must throw a clear per-row error, never post system qty as the count.
- Serial-row option counts reveal system qty (accepted v1 limitation): document in spec, do not fix.
- Excel import with a non-stock UOM in the sheet must error per row, not silently import as stock UOM.
- Timezone trap: site TZ behind wall clock — compare posting datetime against server `now_datetime()`, never browser time.

---

### Task 1: Posting-date decision + spec amendment

**Files:**
- Modify: `.scratch/stock-opname/spec.md` (D6 line)
- Test: none (docs only)

**Interfaces:**
- Consumes: nothing
- Produces: amended D6 text consumed by Task 5 (`MAX_BACKDATE_DAYS = 2` default, `set_posting_time = 1`, submit preserves outlet-set date, stock-frozen check against outlet user).

- [ ] **Step 1: Amend D6 in `.scratch/stock-opname/spec.md`**

Replace the D6 line with: posting date/time defaults to save-moment now, the outlet may backdate it up to a POS Settings cap (default 2 days); future datetimes are rejected server-side using the site timezone; `set_posting_time = 1`; Submit preserves an outlet-set date and only stamps now when unset; `save_opname` and `submit_opname` enforce Stock Settings `stock_frozen_upto`/`stock_frozen_upto_days` against the outlet user.

- [ ] **Step 2: Commit**

```bash
git add .scratch/stock-opname/spec.md
git commit -m "docs(stock): amandemen D6 backdate posting ber-guard"
```

---

### Task 2: UOM self-conversion in `_validated_rows` + tests

**Files:**
- Modify: `pos_next/api/stock_opname.py:80-183` (`_item_flags`, `_validated_rows`)
- Test: `pos_next/api/test_stock_opname.py` (new `TestStockOpnameUom` class reusing `_StockOpnameFixture`)

**Interfaces:**
- Consumes: `UOM Conversion Detail` rows; `frappe.get_meta("Stock Reconciliation Item").has_field("custom_uom")`.
- Produces: `_validated_rows` accepts entry keys `custom_uom`, `custom_qty_after`; row dicts always carry native `qty = after * factor`, plus `custom_uom`/`custom_qty_after`/`custom_conversion_factor` only when the meta has the fields. Rejects: unknown UOM per row, `custom_uom` on serial rows.

- [ ] **Step 1: Write failing tests in `pos_next/api/test_stock_opname.py`**

```python
def test_uom_converts_to_native_qty(self):
    # item with UOM conversion Pack x 12: entry {custom_uom: "Pack", custom_qty_after: 2}
    # -> row qty == 24, custom_uom == "Pack", custom_qty_after == 2
def test_unknown_uom_rejected_per_row(self):
    # entry {custom_uom: "Dus"} with no conversion row -> ValidationError naming the item
def test_serial_row_rejects_custom_uom(self):
    # serial entry + custom_uom -> ValidationError
def test_missing_custom_fields_still_posts_stock_qty(self):
    # simulate meta without custom_uom (mock has_field False): non-stock UOM throws,
    # stock-UOM entry posts native qty and no custom_* keys
```

- [ ] **Step 2: Run to verify they fail**

Run: `docker exec erpnext16_dev-frappe-1 bash -c "cd /workspace/development/frappe-bench && FRAPPE_STREAM_LOGGING=1 python pos_next/_pn_run_tests.py pos_next.api.test_stock_opname.TestStockOpnameUom"` (adjust runner invocation to repo convention)
Expected: FAIL (methods missing / conversion absent)

- [ ] **Step 3: Implement in `pos_next/api/stock_opname.py`**

Extend `_item_flags` with `stock_uom`. In `_validated_rows`, for non-serial rows: resolve `custom_uom` (default `meta.stock_uom`); factor = 1 for stock UOM else from `UOM Conversion Detail` for (item, uom), throw per-row otherwise; serial rows with `custom_uom` throw; native `qty = custom_qty_after * factor`; write `custom_uom`/`custom_qty_after`/`custom_conversion_factor` only if `has_field("custom_uom")`. Batch rows follow the same path (hook W31 converts them too). Keep the formula identical to `warehouse_app` hook (`row.precision("qty")` semantics) so the hook recomputes the same value.

- [ ] **Step 4: Run tests to verify they pass**

Run: same runner, `TestStockOpnameUom` + full `test_stock_opname.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pos_next/api/stock_opname.py pos_next/api/test_stock_opname.py
git commit -m "fix(stock): konversi UOM mandiri di _validated_rows"
```

---

### Task 3: Payload roundtrip + draft leak clearing + tests

**Files:**
- Modify: `pos_next/api/stock_opname.py:186-214` (`_payload`), `pos_next/overrides/stock_reconciliation.py` (`CustomStockReconciliation.validate`)
- Test: `pos_next/api/test_stock_opname.py` (`TestStockOpnameUom` additions)

**Interfaces:**
- Consumes: Task 2 row shape (custom_uom/custom_qty_after stored on rows).
- Produces: `_payload` item dicts include `custom_uom`, `custom_qty_after`; drafts never persist `custom_qty_before`/`custom_qty_difference_per_uom` (and rate-per-UOM difference) while `docstatus == 0`.

- [ ] **Step 1: Write failing tests**

```python
def test_payload_roundtrips_uom(self):
    # save entry with Pack x2 -> get_opname item has custom_uom "Pack", custom_qty_after 2
def test_draft_clears_hook_system_qty_fields(self):
    # after save_opname, the stored row has empty custom_qty_before and custom_qty_difference_per_uom
```

- [ ] **Step 2: Run to verify they fail**

Run: same runner, new test names
Expected: FAIL

- [ ] **Step 3: Implement**

`_payload`: add `custom_uom` (fallback `stock_uom`) and `custom_qty_after` (fallback native `qty`) per item — never add the forbidden fields from Global Constraints. `CustomStockReconciliation.validate`: while `docstatus == 0` and not submitting, blank `custom_qty_before` and `custom_qty_difference_per_uom` (hook recomputes them on submit; HQ keeps `get_system_qty`).

- [ ] **Step 4: Run tests to verify they pass**

Run: full `test_stock_opname.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pos_next/api/stock_opname.py pos_next/overrides/stock_reconciliation.py pos_next/api/test_stock_opname.py
git commit -m "fix(stock): payload UOM roundtrip dan bersihkan bocoran draft"
```

---

### Task 4: Blind search endpoint + dialog switch

**Files:**
- Create: `search_opname_items(pos_profile, search_term=None, start=0, limit=10)` in `pos_next/api/stock_opname.py`
- Modify: `POS/src/components/pos/StockOpnameDialog.vue` (`onItemSearch`, `onItemPick`), `POS/src/components/pos/StockOpnameDialog.test.js`
- Test: `pos_next/api/test_stock_opname.py` (endpoint tests), frontend test file

**Interfaces:**
- Consumes: `_profile_warehouse` gate; `Bin`-free item lookup.
- Produces: `search_opname_items(...) -> [{item_code, item_name, stock_uom, has_batch_no, has_serial_no, uoms: [{uom, conversion_factor}]}]` — no `actual_qty`, no `hide_unavailable` filter, barcode match kept.

- [ ] **Step 1: Write failing backend tests**

```python
def test_blind_search_has_no_system_qty(self):
    # response rows have exactly the allowed keys; no actual_qty/current_qty
def test_blind_search_finds_zero_stock_item(self):
    # item with no Bin row is still returned
def test_blind_search_barcode_match(self):
    # exact barcode returns the item
```

- [ ] **Step 2: Run to verify they fail**

Run: same runner, new test names
Expected: FAIL (endpoint missing)

- [ ] **Step 3: Implement `search_opname_items` in `pos_next/api/stock_opname.py`**

Profile gate via `_profile_warehouse`; word-AND LIKE over name/item_name/item_group/description (reuse `build_item_search_condition` from `pos_next.api.items` with `fulltext_available` check); exact-barcode OR; UOM conversions from `UOM Conversion Detail`; limit/offset. No stock joins at all.

- [ ] **Step 4: Run backend tests to verify they pass**

Run: full `test_stock_opname.py`
Expected: PASS

- [ ] **Step 5: Switch the dialog (failing frontend test first)**

Update `StockOpnameDialog.test.js` helper mocks: `ITEMS_URL` mock replaced by `SEARCH_URL = "pos_next.api.stock_opname.search_opname_items"` returning the blind shape; add test "pick reuses the cached search row" (assert `call` count for the pick path issues no second search request). Run: `npx vitest run src/components/pos/StockOpnameDialog.test.js` — FAIL. Then change `onItemSearch`/`onItemPick` to the new endpoint and reuse the cached row; `get_items` no longer called from this dialog. Re-run — PASS.

- [ ] **Step 6: Commit**

```bash
git add pos_next/api/stock_opname.py pos_next/api/test_stock_opname.py POS/src/components/pos/StockOpnameDialog.vue POS/src/components/pos/StockOpnameDialog.test.js
git commit -m "feat(stock): endpoint blind-search khusus opname"
```

---

### Task 5: Guarded posting datetime (backend) + inputs (frontend)

**Files:**
- Modify: `pos_next/api/stock_opname.py` (`save_opname`, `submit_opname`), `POS/src/components/pos/StockOpnameDialog.vue` (inputs above items table, save/submit payload), `pos_next/api/test_stock_opname.py`, `POS/src/components/pos/StockOpnameDialog.test.js`
- Test: backend `TestStockOpnamePosting` class; frontend input/payload test

**Interfaces:**
- Consumes: Task 1 D6 (cap default 2 days; setting key `opname_max_backdate_days` on POS Settings or fallback constant `MAX_BACKDATE_DAYS = 2` — Task 5 implementer picks one and documents it; later tasks use it).
- Produces: `save_opname` accepts `posting_date`/`posting_time`; `submit_opname` preserves outlet-set values.

- [ ] **Step 1: Write failing backend tests**

```python
def test_future_posting_rejected(self):
    # posting tomorrow -> ValidationError
def test_backdate_beyond_cap_rejected(self):
    # posting 5 days ago with cap 2 -> ValidationError
def test_frozen_stock_blocks_outlet(self):
    # stock_frozen_upto = today, outlet user saves yesterday -> ValidationError even though approver could
def test_submit_preserves_outlet_date(self):
    # save with yesterday (in cap), submit -> posting_date still yesterday, set_posting_time 1
def test_submit_stamps_now_when_unset(self):
    # save without date, submit -> posting_date == today
```

- [ ] **Step 2: Run to verify they fail**

Run: same runner, `TestStockOpnamePosting`
Expected: FAIL

- [ ] **Step 3: Implement backend**

`save_opname`: parse `posting_date`/`posting_time` with `getdate`/`get_time`, reject bad input; reject `get_datetime(d t) > now_datetime()` (site TZ); reject older than cap; enforce frozen (`stock_frozen_upto`/`stock_frozen_upto_days` vs outlet user) in both `save_opname` and `submit_opname`; set `doc.posting_date/posting_time` + `set_posting_time = 1`. `submit_opname`: only stamp now when the doc has no outlet-set date (track via explicit `data` flag from save, or compare against save-moment — implementer picks the simpler: preserve whenever posting_date differs from today). Draft/Rejected-only via existing `EDITABLE_STATES` order (apply date after the check, before Revise).

- [ ] **Step 4: Run backend tests to verify they pass**

Run: full `test_stock_opname.py`
Expected: PASS

- [ ] **Step 5: Frontend inputs (failing test first)**

Add test: editor shows Posting Date/Time inputs defaulting to today+now; save payload carries them. Run vitest — FAIL. Implement two inputs above the items table + include in save payload; re-run — PASS.

- [ ] **Step 6: Commit**

```bash
git add pos_next/api/stock_opname.py pos_next/api/test_stock_opname.py POS/src/components/pos/StockOpnameDialog.vue POS/src/components/pos/StockOpnameDialog.test.js
git commit -m "feat(stock): input posting date/time dengan guard backdate"
```

---

### Task 6: UOM dropdown + stable keys + column layout

**Files:**
- Modify: `POS/src/components/pos/StockOpnameDialog.vue`, `POS/src/components/pos/StockOpnameDialog.test.js`, `pos_next/translations/id.csv` (only if a new string is unavoidable; reuse "UOM" if present)
- Test: frontend tests only

**Interfaces:**
- Consumes: Task 3 payload (`custom_uom`, `custom_qty_after`); Task 4 search (`uoms` list).
- Produces: per-row UOM `<select>` bound to `row.uom`; `row.qty` entered in that UOM; `buildItems` sends `custom_uom` + `custom_qty_after`; stable `:key` per row.

- [ ] **Step 1: Write failing tests**

```js
it("uom select changes the sent custom fields", ...)  // pick Pack, qty 2 -> save payload custom_uom Pack, custom_qty_after 2
it("serial rows show uom read-only", ...)  // no select, stock UOM text
it("removing a middle row keeps per-row state", ...)  // regression for :key=idx bug
```

- [ ] **Step 2: Run to verify they fail**

Run: `npx vitest run src/components/pos/StockOpnameDialog.test.js`
Expected: FAIL

- [ ] **Step 3: Implement**

`blankRow()`: add stable `key: ++rowSeq` (module counter like PurchaseOrderDialog `itemRow`), `:key="row.key"`; UOM column: `<select data-testid="uom-select" v-model="row.uom" :aria-label="...UOM">` with options stock UOM + `row.uoms`; serial rows render stock UOM text only; on pick, seed `row.uom = stock_uom`, `row.uoms`; `buildItems` sends `custom_uom: row.uom`, `custom_qty_after: Number(row.qty)` for non-serial rows (serial: no custom keys); reopening a draft restores `row.uom`/`row.qty` from `custom_uom`/`custom_qty_after`. Column widths: No. narrow | Item flexible | Batch/Serial medium | UOM small | Qty small | delete small; mobile per-cell labels per PO pattern.

- [ ] **Step 4: Run tests to verify they pass**

Run: vitest file + `npx biome check` on both files (new lines must be clean)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add POS/src/components/pos/StockOpnameDialog.vue POS/src/components/pos/StockOpnameDialog.test.js pos_next/translations/id.csv
git commit -m "feat(stock): dropdown UOM dan key baris stabil"
```

---

### Task 7: Complete read-only detail view

**Files:**
- Modify: `pos_next/api/stock_opname.py` (`_payload`: add `posting_date`, `posting_time`, `set_warehouse`), `POS/src/components/pos/StockOpnameDialog.vue` (extend read-only section), `pos_next/api/test_stock_opname.py`, `POS/src/components/pos/StockOpnameDialog.test.js`
- Test: backend payload test + frontend render test

**Interfaces:**
- Consumes: Task 5 fields, Task 3 UOM roundtrip, existing `_attachments`.
- Produces: read-only view showing number, status, posting date/time, warehouse, items (item + batch/serial + UOM + qty), attachments, reject reason.

- [ ] **Step 1: Write failing tests**

```python
def test_payload_has_header_fields(self):
    # get_opname returns posting_date, posting_time, set_warehouse; none of the forbidden fields
```

```js
it("read-only view shows header info and attachments", ...)  // doc no, status, posting, warehouse, uom per row, file/link, reject reason
```

- [ ] **Step 2: Run to verify they fail**

Run: backend runner + vitest
Expected: FAIL

- [ ] **Step 3: Implement**

`_payload`: add the three header fields (explicit keys). Extend the `!editable` section: header card (doc number, status pill, posting date/time, warehouse), items table with UOM column, `PurchaseAttachments` read-only (already exists for attachments — keep), reject-reason banner stays. After approval show counted qty only, never the difference.

- [ ] **Step 4: Run tests to verify they pass**

Run: full backend module + vitest file
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pos_next/api/stock_opname.py pos_next/api/test_stock_opname.py POS/src/components/pos/StockOpnameDialog.vue POS/src/components/pos/StockOpnameDialog.test.js
git commit -m "feat(stock): detail read-only lengkap setelah submit"
```

---

### Task 8: Excel UOM column (support or reject) + batch verify

**Files:**
- Modify: `pos_next/api/stock_opname.py` (`_parse_import`, `_merge_rows`), `pos_next/api/test_stock_opname.py`
- Test: backend import tests

**Interfaces:**
- Consumes: Task 2 validation (UOM-aware `_validated_rows`).
- Produces: `_parse_import` reads the template UOM column: stock UOM or empty → as today; non-stock UOM with valid conversion → op with `custom_uom`/`custom_qty_after`; unknown UOM → per-row error. `_merge_rows` preserves UOM fields through merge.

- [ ] **Step 1: Write failing tests**

```python
def test_import_supports_uom_column(self):
    # sheet row with UOM Pack qty 2 -> merged row custom_uom Pack, native qty converted
def test_import_rejects_unknown_uom_per_row(self):
    # sheet row with UOM "Dus" -> errors entry naming row+item, op skipped
```

- [ ] **Step 2: Run to verify they fail**

Run: same runner, new test names
Expected: FAIL

- [ ] **Step 3: Implement**

`_parse_import`: read UOM cell (header "UOM" already in `TEMPLATE_HEADER`); pass `custom_uom`/`custom_qty_after` into `_validated_rows` entries; per-row errors on unknown UOM. `_merge_rows`: carry `custom_uom`/`custom_qty_after` through `current`/`entries` so imports do not silently convert UOM rows to stock UOM.

- [ ] **Step 4: Run full verification**

Run: full `test_stock_opname.py`, vitest file, `npm run build` in `POS/`, `bench --site posnext.localhost migrate` + `clear-cache` if any patch/fixture changed (none expected).
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add pos_next/api/stock_opname.py pos_next/api/test_stock_opname.py
git commit -m "feat(stock): dukung kolom UOM pada import Excel"
```
