# Discount Stacking Rules + Multi-Company Coupon Scope — Design

Date: 2026-10-15. Branch: `review/serene-on-main` (on top of 0fd15f8 + offer-usage Dynamic Link fix).

## Goal

1. One explicit, server-enforced matrix for which discount classes may stack.
2. Fix the checkout error "At least one mode of payment is required" when a coupon is combined with a manual discount (client/server coupon amount drift).
3. Let one POS Coupon be valid across several companies, like POS Offer and the HQ discount codes.

## Discount classes

- **Offer**: automatic promotion (POS Offer → Promotional Scheme → Pricing Rule). Item-scope or transaction-scope.
- **Coupon**: coded promotion (POS Coupon), always a header (additional) discount, stamped server-side as `pos_coupon_code`.
- **Manual**: cashier discount (item row or header), gated by the HQ discount code when the Company switch requires it.

## Stacking matrix (server is the single judge; UI only pre-locks)

| Combination | Rule |
|---|---|
| Item offer + manual on the same row | Rejected (exists since 0fd15f8). |
| Transaction offer + manual (header or any item row) | Rejected. New: a verified Price-type transaction claim forbids any manual item discount. UI locks item discount while a transaction offer is applied; removing the offer unlocks. |
| Transaction offer + coupon | Always rejected (same header slot). Server-side check added; UI already refuses. |
| Item offer + coupon | Allowed only when BOTH flags are on: `POS Offer.allow_coupon_combination` and `POS Coupon.allow_offer_combination`. Default 0 on both. |
| Coupon + manual | Allowed; the manual part still goes through the HQ code gate. Amounts must reconcile (see Coupon recompute). |
| Offer + offer | Unchanged: ERPNext picks one rule per item. Out of scope. |

Pricing rules that do not belong to a POS Offer (raw Promotional Schemes / standalone rules) have no flag, so they never combine with a coupon.

**Conflict resolution (client):** the cashier's deliberate action wins. While a coupon is applied, auto offers that are not mutually combinable with it are not applied (and already-applied ones are removed with a notice). If such an offer is applied first, applying the coupon is refused with a clear message. The server rejects any invoice that still violates the matrix.

### Server changes (`pos_next/overrides/discount_code.py`)

Extend `reject_stacked_offer_discounts(doc)`:
- Transaction rule present (verified, Price-type, in `invoice_claims`) → any item row with a discount not explained by an item offer claim → throw "The invoice already has an offer discount; another discount cannot be added."
- Transaction rule present AND stamped coupon → throw "A coupon cannot be combined with an offer that discounts the invoice total."
- Stamped coupon AND any item row with a verified item offer claim → resolve the offer(s) via `pos_offer_usage.resolve_offers_from_rules`; throw "Coupon {0} cannot be combined with offer {1}." unless the coupon's `allow_offer_combination` and every such offer's `allow_coupon_combination` are 1.

No HQ code can authorize any of these (same as existing stacking rejects).

## Coupon recompute (fixes the mode-of-payment error)

Root cause: `useInvoice.applyDiscount` freezes the coupon amount at apply time; later cart changes (manual discounts, qty) are not reflected, the frontend clamp hides it, the client submits a 0 total with no payments, and the server's recompute (`_apply_stamped_coupon_discount`) produces a positive total.

Fix: the cart keeps the coupon definition (`discount_type`, `discount_percentage`, `discount_amount`, `max_amount`, `min_amount`, `apply_on`) and recomputes `additionalDiscount` on every cart change with the server formula (`pos_coupon.expected_coupon_discount`): basis = grand total before the header discount (or net total for "Net Total"), percent or amount, capped by `max_amount`, never above the basis. If the basis drops below `min_amount`, the coupon is removed with a warning (the server would reject it). One pure JS helper mirrors the Python function; both get the same table-driven test cases.

## Multi-company coupon scope

Schema (POS Coupon):
- `company` relabelled "Issuing Company" (still required; drives currency of Amount fields).
- New `company_scope` Select `All Outlets / Selected Outlets / All Outlets Except`, default `Selected Outlets`.
- New `companies` Table reusing child doctype `POS Discount Code Company` (same as discount/refund codes).
- New `allow_offer_combination` Check, default 0.

Rules:
- Usable at company X when the scope admits X (same semantics as `_code_usability_error`; `Selected Outlets` with an empty table means issuing company only).
- `Amount` coupons are rejected at a company whose default currency differs from the issuing company's ("Coupon {0} is in {1} and cannot be used at {2}.").
- Applied in all lookups: `pos_coupon.check_coupon_code`, `api/offers.validate_coupon`, `api/offers.get_active_coupons`, `api/promotions.get_coupons` (list coupons usable at the company, not only issued by it). One shared helper `coupon_usable_at(coupon, company)` in `pos_coupon.py`.
- Coupons created from the POS (`promotions.create_coupon`) keep current behaviour: scope `Selected Outlets` with the POS company. Scope is edited in Desk.

Migration: patch `v2_20_0.scope_existing_coupons` sets `company_scope = "Selected Outlets"` and adds a `companies` row for the issuing company on every existing coupon (44 local, all Percentage). Behaviour stays identical.

POS Offer: new `allow_coupon_combination` Check (default 0). Exposed on the Offer API object (`Offer` dataclass) via a custom field mirrored onto the Pricing Rule by `pos_offer_sync._rule_values` (`pos_offer_allow_coupon`, like `pos_offer_max_discount`), so the offers feed needs no extra query. Coupon API response adds `allow_offer_combination`.

## Behaviour change to communicate

Today an item offer and a coupon always stack. After this change they do not, until HQ ticks both flags. Running promos that rely on stacking must be re-flagged.

## Out of scope

Offer+offer stacking; per-company amounts for Amount coupons; offline coupon validation changes; Desk UI beyond standard form fields.

## Testing

- Backend: one test per matrix row (accept + reject), flag combinations (0/0, 1/0, 0/1, 1/1), non-POS-Offer rule + coupon rejected; scope modes × companies; Amount coupon currency mismatch; patch idempotency; existing suites (`test_discount_code`, `test_coupon_discount_enforcement`, `test_pos_invoice_submit`, `test_promotions`, `test_pos_offer_usage`) stay green.
- Frontend: coupon recompute helper parity cases; coupon 20% + manual 90% submits with consistent totals; min_amount drop removes coupon; item discount locked under a transaction offer; coupon removes non-combinable auto offers and refuses when such an offer was applied first.
- Manual: browser run of the three scenarios the user reported.
