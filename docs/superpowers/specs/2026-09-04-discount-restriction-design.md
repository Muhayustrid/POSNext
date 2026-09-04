# POS Discount Restriction — Design Spec

Date: 2026-09-04
Branch: `batasi-diskon`
Status: Approved-by-default (design decisions below used recommended defaults; reviewable async)

## Problem

Manual discounts in POS Next (item-level and cart-level) have no central control over:
validity period, transaction quota, which companies may use them, and HQ approval for
special discounts. Finance/HO needs to run discount campaigns across branches without
quota overuse or unauthorized usage.

## Design decisions (defaults chosen, user unavailable to answer)

1. **Scope**: all *manual* discounts — item-level (`discount_percentage` / `discount_amount`,
   and manual rate edits that reduce `rate` below `price_list_rate`) plus invoice-level
   additional discount (`discount_amount` on the invoice header). POS Offers/coupons are
   **not** gated (they already have their own `valid_from`/`max_use`).
2. **Targeting**: the rule is a gate per company. When a rule is active for the invoice's
   company and date, every discounted POS invoice in that company is validated.
3. **Confirmation codes**: one-time-use, generated in bulk by HO from the rule, consumed on
   submit. Required for discounts on listed items; if the item list is empty, required for
   *any* discount while the rule is active. Additional discount > 0 also requires a code
   (otherwise it would bypass item-level code requirements).
4. **Quota**: counted per submitted invoice that carries a governed discount. Released
   (ledger row deleted) when the invoice is cancelled. Global mode = shared counter across
   all companies; Per Company mode = independent counter per company. Limit `0` = unlimited.

## Architecture

### DocTypes (module: POS Next)

**POS Discount Restriction** (main rule, `autoname: field:title`, track_changes)
- `enabled` (Check, default 1)
- `valid_from` (Date, reqd, default Today), `valid_to` (Date, reqd)
- `companies` (Table → **POS Discount Restriction Company**):
  `company` (Link, reqd), `enabled` (Check, default 1), `max_usage` (Int, used in Per
  Company mode; 0 = unlimited). A company must be listed *and* enabled for the rule to
  apply there. Empty table = rule applies nowhere (validated: ≥1 row).
- `enforce_usage_quota` (Check, default 0), `quota_mode` (Select: `Global`/`Per Company`),
  `global_max_usage` (Int, 0 = unlimited)
- `require_confirmation_code` (Check, default 0), `code_items` (Table →
  **POS Discount Restriction Item**, column `item` Link Item; empty list = all items)
- `description` (Small Text)
- Controller validate: `valid_from <= valid_to`; quota mode required when quota enforced;
  ≥1 company row.
- Whitelisted doc method `generate_codes(count, company=None)` → creates N one-time codes
  (8 chars from confusion-safe alphabet `ABCDEFGHJKMNPQRSTUVWXYZ23456789`),
  permission: write on the doc.

**POS Discount Confirmation Code** (`autoname: hash`)
- `restriction` (Link, reqd, indexed), `code` (Data, reqd, unique), `company` (Link,
  optional — restrict code to one company), `status` (Select: Available/Used/Cancelled),
  `used_by`, `used_in_invoice`, `used_on` (read-only audit fields).
- Permissions: System Manager + Nexus POS Manager only (cashiers must not list codes).

**POS Discount Restriction Usage** (ledger, composite autoname `{restriction}::{sales_invoice}`)
- `restriction` (Link, reqd), `company` (Link, reqd), `sales_invoice` (Link, reqd),
  `used_by`, `used_on`.
- Composite name makes duplicate insert raise `DuplicateEntryError` → idempotent +
  race-safe (same pattern as `One Time Customer Offer Usage`).

### Core logic — `pos_next/overrides/discount_restriction.py`

- `get_applicable_restriction(company, posting_date)` → enabled rule where date is within
  window and company has an enabled row. More than one match → `ValidationError` naming
  the conflicting rules (ambiguous config must be fixed by HO).
- `invoice_has_manual_discount(doc)` → any item with `discount_percentage > 0`,
  `discount_amount > 0`, or manual rate edit below `price_list_rate`; or header
  `discount_amount > 0`.
- `validate_invoice_discounts(doc, method)` — doc_event `validate` (runs on draft save
  **and** submit; skip when `not doc.is_pos`, `doc.is_return`, or no manual discount):
  1. resolve rule for `doc.company` + `doc.posting_date`
  2. quota check: usage count vs limit (Global: `frappe.db.count` by restriction;
     Per Company: by restriction + company) → throw when exhausted
  3. code check (read-only): when `require_confirmation_code` and the invoice discounts a
     restricted item (or sets additional discount > 0), `doc.discount_confirmation_code`
     must reference an Available code of this rule (company match when set).
  Stamp `doc.pos_discount_restriction = rule.name` for audit.
- `record_usage_on_submit(doc, method)` — doc_event `on_submit`: re-resolve rule, lock the
  rule row (`frappe.db.get_value(..., for_update=True)` — serializes concurrent submits on
  one rule), re-check quota, insert usage row (catch `DuplicateEntryError`), then claim the
  code with a conditional update guarded on `status='Available'` (row-locked read via
  `for_update=True`; throw if it lost the race). All inside the submit transaction —
  any failure rolls the whole submit back.
- `release_usage_on_cancel(doc, method)` — doc_event `on_cancel`: delete usage rows for
  the invoice (quota released). Codes stay Used (one-time; a manager can reset manually).

### Whitelisted API — `pos_next/api/discount_restriction.py`

- `get_status(company)` → `{applicable, rule: {name,title}, quota: {mode, limit, used,
  remaining}, requires_code, code_items, message}` — UX only; client shows warnings /
  marks restricted items.
- `validate_confirmation_code(code, company, items=None, additional_discount=0)` →
  `{valid, message}` — live check while the cashier types a code.

### Custom fields (install.py `CUSTOM_FIELDS`, synced on every migrate)

Sales Invoice:
- `pos_discount_restriction` (Link → POS Discount Restriction, read_only, print_hide)
- `discount_confirmation_code` (Data, print_hide)

The POS payload carries `discount_confirmation_code` at invoice level (same transport as
`coupon_code`); `update_invoice` persists it onto the draft.

### hooks.py

Sales Invoice `validate` / `on_submit` / `on_cancel` lists gain the three handlers above.

### Frontend (Vue 3 + Pinia)

- New store `POS/src/stores/discountRestriction.js`: `fetchStatus(company)` on shift open
  and after each submission; exposes `status`, `requiresCodeForItem(itemCode)`,
  `code` (entered confirmation code), `setCode/clearCode`.
- `EditItemDialog.vue`: when the discount being confirmed hits a restricted item and no
  code is captured yet, render a "Kode Konfirmasi" input; validate via API before applying.
- `PaymentDialog.vue`: same input when additional discount > 0 and the rule requires codes.
- `useInvoice.js`: include `discount_confirmation_code` in both `saveDraft` and
  `submitInvoice` payloads; clear store code after successful submit.

### Known behavior / limits

- Offer/coupon-driven item discounts are indistinguishable from manual discounts
  server-side (`pricing_rules` is cleared before save), so they also consume quota while a
  rule is active. Accepted: HO campaigns and restriction rules should not overlap.
- Window check uses the invoice `posting_date` (date precision, no time-of-day).
- Offline invoices are enforced on sync because they travel through the same
  `update_invoice`/`submit_invoice` path and doc_events.
- Quota race: concurrent submits on the last quota slot are serialized by the rule row
  lock (MariaDB). On SQLite (tests) FOR UPDATE is a no-op; tests are serial.

## Testing

- Python: `pos_next/api/test_discount_restriction.py`, unit-style with mocked `frappe.db`
  (project convention, run via `pos_next/_pn_run_tests.py`): window edges, company
  scope/disabled row, global vs per-company quota, code required/rejected/used/conflict,
  return invoices skipped, no-discount invoices untouched, cancel release.
- JS: Vitest for the store (status normalization, item-restriction lookup).
- Manual gate: `bench --site posnext.localhost migrate`, `npm run test` in `POS/`.
