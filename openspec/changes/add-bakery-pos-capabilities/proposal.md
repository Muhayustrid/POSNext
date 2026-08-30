## Why

POS Next today models a sale as a customer plus a flat list of item lines. A retail bakery and coffee-bun operation (`Roti O`-style) breaks that model in five ways: cashiers need to call an order by buyer name without creating thousands of junk `Customer` records; combo deals ("Paket Hemat 1") need a mandatory component plus a flavour choice the cashier can satisfy in more than one way; discounts must be restrictable to specific items instead of only whole-cart thresholds; physical receipts must go to an iMin thermal printer rather than only a browser print dialog; and a single price level must be applied to many companies at once without editing `Item Price` one by one.

## What Changes

- Add a buyer identifier on the sales document: `buyer_name` (free text, used for queue calling) and `queue_number` (auto per POS Opening Shift), stored as Custom Fields on `Sales Invoice`, the doctype POS Next actually transacts against (ERPNext's `POS Invoice` is not used here). `customer` keeps its existing semantics and defaults to the POS Profile walk-in customer; a name-only sale never creates a `Customer` record. This follows the validated walk-in pattern already proven in `selling_additional`.
- Retire the implicit "create a `Customer` from an unknown name" behaviour in `pos_next.api.invoices.update_invoice`, replacing it with validation that points at `buyer_name`. This is the change's one **BREAKING** item.
- Promote the existing **Promotion (paket)** model into `pos_next` as the package capability: a parent item with a base price, fixed mandatory components (paperbag), and choice groups (ropi coklat/keju/butter) governed by `pick_count` and per-option `max_per_option`, so pick-one, pick-many, and repeated-flavour are all representable. Sales record an immutable snapshot plus a queryable selection fact for reprint and returns. One additive enhancement: an explicit `allow_repeats` flag on a choice group.
- Promote the existing **Price Group** model into `pos_next`: one master of item/UOM rates applied to many outlets (Company + Warehouse + POS Profile) at once, resolved through POS Next's own pricing path instead of editing `Item Price` per item.
- Extend `POS Coupon` with item-scoped applicability: a coupon can apply to the whole cart (current behaviour), or only to selected item codes / item groups / brands, with a per-coupon cap on discount. This capability is new; `selling_additional` did not scope coupons.
- Route receipt printing through the already-installed `pos_direct_print` app, driving its completed print-job lifecycle and reaching the iMin `IminPrintInstance` SDK, keeping browser / QZ Tray printing as the fallback.

## Capabilities

### New Capabilities

- `queue-buyer-identity`: Buyer name and queue number captured on a sale, their validation rules, per-shift queue numbering, display and search surfaces, and privacy/retention behaviour — without creating Customer master records.
- `pos-promotions`: Package (combo) definition model, mandatory and choice components, selection constraints and cart-expansion behaviour, pricing and stock effects, and return/reprint handling.
- `price-groups`: Outlet-scoped price overrides applied to many companies at once, resolved through POS Next pricing, with fallback to the standard price list.
- `coupon-applicability`: Scope rules for coupons — whole-cart versus specific items, groups, or brands — with cap and eligibility behaviour.
- `imin-receipt-printing`: Printing a submitted sale receipt to an iMin thermal printer through the direct-print job lifecycle, including terminal resolution, retry/reprint, fallback to browser printing, and offline behaviour.

### Modified Capabilities

_None. `openspec/specs/` has no existing capabilities yet, so all five are new._

## Impact

**Porting from `selling_additional`** (source at `/Users/rotiropi/DockerERPNext/.../apps/selling_additional`, a different bench; GitHub `main` is a scaffold). This change copies the following into `pos_next.pos_next` **together with their tests**, then retires `selling_additional` as a runtime dependency:
- `walk_in.py` and its Sales Invoice Custom Field pattern.
- DocTypes `Price Group`, `Price Group Item`, `Price Group Outlet`.
- DocTypes `Promotion`, `Promotion Component`, `Promotion Choice Group`, `Promotion Option`, `Pos Promotion Selection`, `Promotion Selection Fact`, plus the promotion pricing/expansion/returns/facts logic and its API (`get_available_promotions`, `get_promotion_detail`, `quote_promotion`).

**New in pos_next**
- New DocTypes: none for promotions or price groups (ported); coupon scope fields are added to `POS Coupon`.
- New Custom Fields on `Sales Invoice` (`buyer_name`, `queue_number`), `POS Opening Shift` (`current_queue_number`), and `Sales Invoice Item` (promotion link + selection), applied reproducibly via `pos_next.install` (the desk-mirror JSON under `pos_next/pos_next/custom/` is not an apply mechanism today).
- Modified: `POS Coupon` (applicability fields), `POS Settings` (feature toggles), `pos_next/api/invoices.py` (customer provisioning, promotion expansion, buyer_name, queue), `pos_next/api/items.py` (price-group resolution), `pos_next/api/offers.py` and `pos_next/api/promotions.py` (coupon scoping).
- A patch to backfill `queue_number` and normalise existing walk-in rows; no data migration for promotions or price groups (they arrive with their own install/migration path from `selling_additional`).

**Frontend (POS/ Vue 3)**
- `InvoiceCart.vue`, `PaymentDialog.vue`, `CustomerDialog.vue`: buyer name + queue input.
- Port and rewire the promotion selection UI + `posCart.js` expansion; `itemSearch.js` promotion tiles.
- `CouponDialog.vue`: scoped-coupon feedback.
- `utils/printInvoice.js` + new `utils/directPrint.js`: hand off to the `pos_direct_print` lifecycle; `useQzTray.js` stays as one fallback path.
- IndexedDB schema bump for offline queue of buyer name, queue number, promotion selections, and price-group prices (`utils/offline/db.js`, `posSync.js`).

**External app**
- `pos_direct_print` (already installed) remains the print authority; POS Next only calls it. No new driver code in `pos_next`.

**Not in scope**: kitchen/display screen for queue calling, loyalty accrual on name-only sales, iMin models beyond the class the SDK covers, coupon printing on the receipt face, keeping `selling_additional` installable in parallel.
