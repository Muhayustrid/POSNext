# POS Offer 2.0 — Simplified Campaign Manager

Date: 2026-09-05
Branch: `batasi-diskon` (new feature branch to be cut later)
Status: Draft for user review

## Problem

POS Next's offers engine runs on ERPNext Promotional Scheme + Pricing Rule. Those are
powerful but complicated for head office staff: single `company` per Pricing Rule, no
usage-quota concept, and the in-POS promotion dialog is store-scoped. Meanwhile the
`POS Offer` DocType (inherited from POS Awesome) is dead — nothing reads it — even
though its name and form are the friendliest place for HO to define a campaign.

## Goal

Revive `POS Offer` as a **simple form that is the source of truth**, which
creates and syncs **Promotional Scheme + Pricing Rules** under the hood, and adds
what the engine lacks: **multi-company** and **usage quotas** (global or per company,
daily or campaign-total).

## Decisions (user-confirmed)

1. Quota scope: **Global** and **Per Company** only. Outlet == Company (no separate
   POS-Profile scope).
2. Quota period: **Daily** and **Campaign Total**, configurable per offer; default Total.
3. The in-POS promotion dialog (`PromotionManagement`) becomes **read-only** — it stays
   as an informational list of active offers, but its create/edit/toggle functionality is
   hidden. Campaign management is centralized in POS Offer (Desk).
4. `POS Coupon` and `Referral Code` stay legacy/ignored.
5. **Percentage discount with nominal cap** (user-requested 2026-09-05): e.g.
   "50% off, max Rp 20.000". The cap applies **per unit of the discounted item**.
6. Legacy `POS Offer` test rows are deleted by patch before installing the new schema.

## Design

### 1. The form (POS Offer, rebuilt)

Keep: `title` (autoname), `enabled`, `valid_from`/`valid_to`, `description`.

Rebuilt fields:
- `apply_on` (Item Code / Item Group / Brand / Transaction) + matching child table
  (`POS Offer Detail`, reuse) for the targets.
- `offer_type`: **Discount Percentage**, **Discount Amount**, **Free Item**
  (covers the 90% cases; maps to Pricing Rule `rate_or_discount` / product discount).
- `discount_percentage` / `discount_amount` / `free_item` + `free_qty`.
- `max_discount_amount` (Currency, only shown for Discount Percentage): nominal cap —
  effective discount per unit = `min(unit_price × pct/100, cap)`. Covers the classic
  "50% max 20rb" promo that no existing field supports (Pricing Rule's Min/Max Amount
  is a cart qualifying window, not a cap).
- `min_qty` / `min_amt` (optional qualifying conditions).
- `companies` (Table → **POS Offer Company**): `company` (Link, reqd), `enabled` (Check,
  default 1), `max_usage` (Int — used in Per Company quota mode; 0 = unlimited).
- Quota section: `enforce_usage_quota` (Check), `quota_scope` (Select: Global / Per
  Company), `quota_period` (Select: Campaign Total / Daily, default Campaign Total),
  `global_max_usage` (Int, 0 = unlimited — used in Global mode).
- `min_max_pricing` stays OUT of the form (the pricing-rule engine's min/max price
  feature remains available to power users via Promotional Scheme directly).

Validation: `valid_from <= valid_to`; ≥1 company row; per-company quota limits ≥ 0;
duplicate company rows rejected; free item requires `free_item` + `free_qty` ≥ 1;
percentage 0–100.

### 2. Sync engine (POS Offer → Promotional Scheme + Pricing Rules)

POS Offer is the source of truth. Lifecycle:
- **Insert** → create one Promotional Scheme (named after the offer) + one Pricing Rule
  per enabled company (`rate_or_discount`, items, dates, min/max, `selling=1`,
  `promotional_scheme` link set). When the offer has a discount cap, the generated rules
  are stamped with a custom field `pos_offer_max_discount` so the offer engine can apply
  `min(pct_discount, cap)` per unit at application time (the cap cannot be expressed in
  native Pricing Rule fields).
- **Update** → diff & sync: push field changes to the scheme and all child rules;
  company rows added → create rules; company rows removed/disabled → rules disabled
  (not deleted — usage history stays readable); re-enabled → re-enabled.
- **Offer disabled** → all child rules disabled. **Offer deleted** → child rules deleted
  and the scheme disabled+renamed ` (DELETED <title>)`.
- **Ownership guard** (pattern: `price_group_ownership`): a `Pricing Rule` validate hook
  blocks direct edits to rules whose `promotional_scheme` belongs to a POS Offer
  ("managed by POS Offer <name> — edit the offer"). Keeps the two layers honest.

Implementation note to pin during planning: whether ERPNext's scheme→rule generator
supports per-company rules from one scheme, or POS Next must create the rules directly
(scheme as pure container). Either way the POS Offer API encapsulates it.

### 3. Quota engine

- New ledger doctype **POS Offer Usage** (pattern: `POS Discount Restriction Usage`):
  composite autoname `{pos_offer}::{sales_invoice}` → DuplicateEntryError = idempotent
  & race-safe. Fields: `pos_offer`, `company`, `sales_invoice`, `used_by`, `used_on`.
  One row per (offer, invoice) — an invoice using two offers writes two rows.
- **Capture point**: `update_invoice` already collects `applied_rule_names_seen` before
  clearing `item.pricing_rules`. Map rule → offer via `promotional_scheme`. This is the
  server-side truth; the client cannot fake it.
- **Enforcement** (draft save *and* submit, same posture as Discount Restriction):
  for each applied offer with quota enabled, count ledger rows:
  - Global scope: all rows of the offer
  - Per Company: rows of the offer + this invoice's company
  - Daily period: additionally filter `posting_date = today` (reset is implicit — no cron)
  - Campaign Total: no date filter
  Over the limit → `frappe.throw` with a clear cashier-facing message.
- **On submit**: insert usage rows (after re-check under the offer-row lock — same
  rule-row-lock serialization as Discount Restriction). **On cancel**: delete the
  invoice's rows (quota released).
- **Zero limit = unlimited** (consistent with Discount Restriction).

### 4. POS frontend (minimal)

- OffersDialog / apply_offers flow unchanged: offers keep appearing because they are
  real Pricing Rules.
- **PromotionManagement dialog → read-only**: hide the create/edit/toggle controls
  (server-side too — `create_promotion`, `update_promotion`, `toggle_promotion` gain a
  guard so the dialog cannot mutate campaigns anymore; HO manages via POS Offer). The
  list itself stays as an informational view of active offers.
- UX nicety: extend `get_offers` response with per-offer quota summary
  (`remaining`, `limit`, `period`) so the offer card can badge "sisa 12/50" and dim when
  exhausted. Server remains the authority.

### 5. Data / migration

- Existing `POS Offer` rows: only test data on this site ("Diskon Laptop") — a patch
  deletes all legacy rows before installing the new schema (the old fields do not map
  1:1 to the new semantics).
- `POS Coupon` / `Referral Code` doctypes stay as-is, untouched.

## Out of scope (this iteration)

- Per-POS-Profile (true outlet) quota scope.
- Cap on the *invoice-level* additional discount (cap here is per discounted item unit).
- Any change to POS Coupon or Referral Code.
- Interaction with Discount Restriction beyond what exists today (the "offers eat
  restriction quota" gray zone is now at least *detectable* — applied rules are known —
  and may be revisited as a follow-up).

## Testing

- Python unit tests (project style): sync engine (create/update/disable/delete,
  company add/remove), quota scopes (global vs per company), periods (daily reset via
  date filter, total), enforcement on save+submit, cancel release, ownership guard,
  validation errors.
- Vitest: offer quota badge normalization in the offers store.
- Smoke on `posnext.localhost`: create offer with 2 companies + daily quota → verify
  scheme/rules generated, apply in POS until exhausted, confirm ledger rows, cancel
  releases.
