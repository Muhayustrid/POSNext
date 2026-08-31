## 1. Port foundation and schema reproducibility

- [x] 1.1 Copy the `selling_additional` DocTypes into `pos_next.pos_next.doctype` — `Price Group`, `Price Group Item`, `Price Group Outlet`, `Promotion`, `Promotion Component`, `Promotion Choice Group`, `Promotion Option`, `Pos Promotion Selection`, `Promotion Selection Fact` — and verify `bench --site erpnext16.localhost migrate` creates them with permissions mapped to `POSNext Cashier` / `Nexus POS Manager`.
- [x] 1.2 Copy the promotion pricing/expansion/returns/facts logic and `walk_in.py` into `pos_next` services, preserving the POST-only `@frappe.whitelist` methods and the `_check_access(pos_profile)` outlet gate, and verify the module imports cleanly with no dangling references to `selling_additional`.
- [x] 1.3 Port the corresponding `selling_additional` test files (`test_promotion_*`, `test_price_group_*`, `test_walk_in*`) to the new module paths and verify the full ported suite passes against `pos_next` before any new behaviour is added.
- [x] 1.4 Add the five POS Settings switches `enable_buyer_identity`, `require_buyer_name`, `enable_promotions`, `enable_price_groups`, `enable_direct_print`, all default off, and verify a newly created POS Settings row returns off for each and migrate succeeds.
- [x] 1.5 Implement `pos_next.install.setup_custom_fields()` upserting Custom Fields from a Python-side list (`Sales Invoice.buyer_name`, `Sales Invoice.queue_number`, `POS Opening Shift.current_queue_number`), wired into `after_install`/`after_migrate`; verify by deleting those field rows, re-migrating, and confirming recreation with matching `dt`/`fieldname`/`fieldtype`/`insert_after`.
- [x] 1.6 Make 1.5 idempotent and non-destructive: verify running migrate twice creates no duplicate `tabCustom Field` rows and does not overwrite a field an admin relabelled in the Desk.
- [x] 1.7 Correct the `pos_next/install.py` module docstring so it no longer claims fixtures apply custom fields/print formats, and verify the text matches what the module now does.
- [x] 1.8 Run the read-only pre-migration report of `Customer` rows that look like ad-hoc walk-ins (created via `update_invoice` auto-create, no address, no mobile, single-invoice history) and verify it lists rows without modifying data.

## 2. Buyer identity and queue numbering

- [x] 2.1 Add server-side buyer-name validation in `pos_next/api/invoices.py` — reject over 60 characters and control characters, treat whitespace-only as absent — and verify `test_invoices.py` asserts a `frappe.ValidationError` for each case.
- [x] 2.2 Port `validate_walk_in_customer_name` so the buyer-name field only applies when `customer` equals the POS Profile default, and verify a test rejects a buyer name attached to a non-default customer.
- [x] 2.3 Allocate `queue_number` in `submit_invoice` from `POS Opening Shift.current_queue_number` read under `frappe.qb.get_query(..., for_update=True)` and written with `db_set(update_modified=False)` in the same transaction; verify two sequential submissions yield 1 then 2.
- [x] 2.4 Verify the concurrency case with a test where two connections submit to the same shift and receive distinct, unskipped numbers.
- [x] 2.5 Verify the counter resets for a second POS Opening Shift so a new shift's first sale is number 1.
- [x] 2.6 Expose the current queue number per shift through a type-hinted whitelisted API and verify an open shift returns its highest number and an unknown shift raises `DataError`.
- [x] 2.7 Add `buyer_name` and `queue_number` to `get_invoices` search and returned fields, and verify tests for search-by-name, search-by-number, and an unmatched number returning nothing.
- [x] 2.8 Gate the fields on `enable_buyer_identity` in `api/bootstrap.py` and verify the payload omits them entirely when off.
- [x] 2.9 Add the buyer-name input and queue chip to `InvoiceCart.vue` and `PaymentDialog.vue` behind the switch, and verify a Vitest test asserts absence when disabled, presence when enabled, and blocked submit when `require_buyer_name` is on.
- [x] 2.10 Persist buyer name and queue number through the held-order/draft flow and verify saving then resuming a draft restores the name.
- [x] 2.11 Carry buyer name and a locally-estimated queue number through the offline IndexedDB queue and sync, and verify an offline-then-synced sale stores the server-allocated number while retaining the printed estimate.
- [x] 2.12 Bump the Dexie schema in `POS/src/utils/offline/db.js` additively and verify a test migrating a database that already holds queued unsynced invoices preserves them intact.
- [x] 2.13 Add buyer name and queue number to the `POS Next Receipt` print format and verify a name renders when present and nothing renders when absent.

## 3. Retiring implicit Customer provisioning (BREAKING)

- [x] 3.1 Replace the auto-create-Customer block at `pos_next/api/invoices.py:766` with validation rejecting an unknown `customer` value whose message names `buyer_name` as the replacement, and verify a test asserts no `Customer` row is created and the count is unchanged.
- [x] 3.2 Verify a known `customer` value still loads and books exactly as before, scoping the change to unknown values only.
- [x] 3.3 Run the full existing backend suite and verify no previously passing test regressed, fixing any that depended on the old provisioning rather than deleting them.
- [x] 3.4 Document the behaviour change in `CHANGELOG.md` under a breaking-changes heading, naming the migration path.

## 4. Promotions (ported package model)

- [x] 4.1 Port and extend `Promotion Choice Group` with an explicit `allow_repeats` Check, and verify `test_promotion_master.py` covers the three states: pick-count 1, pick-many-distinct, and pick-many-with-repeats.
- [x] 4.2 Keep the promotion validation core as the single source, called both by `quote_promotion` and by `update_invoice`/`submit_invoice`, and verify a directly-posted invalid selection (over pick_count, over `max_per_option`, foreign option, `max_instances_per_invoice` breach) is rejected server-side regardless of the client.
- [x] 4.3 Verify expansion sets the parent line to `base_price + sum(price_adjustment)` and deducts stock per component with no stock entry for the parent item, covering the paperbag + flavour scenario.
- [x] 4.4 Verify quantity scaling: setting a promotion line to quantity 2 doubles every component quantity and stock deduction.
- [x] 4.5 Verify fixed-component shortage is rejected and names the component item code in the error.
- [x] 4.6 Verify the `Pos Promotion Selection` snapshot and per-item `Promotion Selection Fact` are written on submit and reproduce the exact selection when the invoice is re-read.
- [x] 4.7 Verify editing a selection in place replaces the components while leaving parent price and quantity unchanged.
- [x] 4.8 Verify promotion returns at proportional component quantities, reusing the ported `test_promotion_returns.py` expectations.
- [ ] 4.9 Cache promotion definitions in the offline store and verify an offline sale expands from cache and syncs with the stored snapshot, deducting the components as sold.
- [ ] 4.10 Build the selection UI — port/replace with `PromotionSelectionDialog.vue` driven by group constraints and `quote_promotion` — and verify a Vitest test asserts confirm stays disabled below/above pick count and that `allow_repeats`/`max_per_option` gate double-tapping an option.
- [ ] 4.11 Wire `posCart.js:addItem` to open the dialog before a promotion enters the cart while a plain item still adds with no dialog, and show promotion tiles in the catalogue with barcode entry.
- [ ] 4.12 Render a promotion as one priced cart/receipt line with expandable component detail, and gate all promotion UI on `enable_promotions`.
- [ ] 4.13 Run `yarn lint` in `POS/` and verify zero new Biome errors.

## 5. Price groups (ported)

- [ ] 5.1 Wire ported price-group resolution into `pos_next.api.items.get_item_details` keyed off the sale's outlet, with fallback to the standard Price List when no group covers the item, and verify a group item at 8,000 overrides a 10,000 price-list rate and an unlisted item keeps 10,000.
- [ ] 5.2 Verify outlet scoping: one group listing two companies applies in both; an outlet the group does not list falls back to the price list; two enabled groups claiming the same outlet+item fail resolution with both group names in the error.
- [ ] 5.3 Verify UOM handling: a per-piece group rate converted to a per-box sale follows the item's UOM settings.
- [ ] 5.4 Run the ported `test_price_group_concurrency.py` against `pos_next` and verify two operators editing different items on one group both persist with nothing lost.
- [ ] 5.5 Cache the applicable group for the outlet in the offline store and record which price group produced each line's rate, and verify an offline-priced sale recomputes the same rate on sync and the line stores its price-group source.
- [ ] 5.6 Gate price-group consultation on `enable_price_groups` and verify a disabled profile prices from the price list exactly as today.

## 6. Coupon scope (new capability)

- [ ] 6.1 Add `discount_scope` (Transaction/Item Code/Item Group/Brand) plus scope member rows to `POS Coupon`, defaulting to Transaction, and verify migrate succeeds and an existing coupon loads as unrestricted.
- [ ] 6.2 Compute the discount base from in-scope lines only in `api/offers.py` / `api/promotions.py`, and verify a percentage coupon scoped to one of two priced items discounts only that item.
- [ ] 6.3 Support Item Code, Item Group, and Brand scopes reusing `POS Offer`'s `apply_on`/`apply_type` vocabulary, and verify one test per scope type.
- [ ] 6.4 Evaluate `min_amount` against the in-scope base and apply `max_amount` after scoping, and verify a coupon below its scoped minimum is rejected with the shortfall, and a 30 percent coupon with a 5,000 cap yields exactly 5,000.
- [ ] 6.5 Apply free-item coupons only when a qualifying in-scope line exists, and verify the granted free item does not count toward the qualifying amount.
- [ ] 6.6 Make the customer guard in `validate_coupon` (`pos_next/api/offers.py:610`) conditional so an unbound coupon validates on a walk-in sale while a customer-bound coupon still rejects a mismatch, and verify both branches given the recent `PN-77` change.
- [ ] 6.7 Verify scoping leaves combination rules untouched: a test asserting the set of promotions that may coexist with a coupon is identical with and without a scope, and only the amount differs.
- [ ] 6.8 Recompute the scoped discount on cart change and drop it when nothing qualifies, and verify removing the last matching line removes the discount and notifies the cashier.
- [ ] 6.9 Record the discounted amount and affected lines on `POS Coupon Detail` and verify a redemption test reads back which lines the coupon touched.
- [ ] 6.10 Show scope feedback in `CouponDialog.vue` and verify a test asserts an inapplicable scoped coupon explains which items it applies to.
- [ ] 6.11 Run the existing `test_offers.py` suite and verify every legacy no-scope coupon test passes unmodified.

## 7. Direct print integration

- [ ] 7.1 Add an availability probe for `pos_direct_print` used by bootstrap and verify POS Next loads with the app present and absent.
- [ ] 7.2 Resolve the terminal through `pos_direct_print.core.print_api.resolve_terminal_for_profile` for the sale's company and profile, and verify a test binds two profiles to two terminals and each sale resolves its own.
- [ ] 7.3 Reject an ambiguous binding (one profile, several enabled terminals) and verify the error names the conflicting terminal IDs.
- [ ] 7.4 Implement `POS/src/utils/directPrint.js` driving reserve → `bind_receipt_snapshot` → `start_attempt` → `complete_attempt`/fail against the existing API, and verify a happy-path test drives the job to completion.
- [ ] 7.5 Wire `printInvoice.js` to try direct print and fall back to QZ Tray / browser when no terminal is bound, and verify no job record is created on the fallback path.
- [ ] 7.6 Send the rendered `POS Next Receipt` content as the receipt snapshot and record its hash, and verify reprinting reproduces stored content rather than re-rendering live data.
- [ ] 7.7 Handle concurrent claims: verify a test where two windows print the same receipt lets one through and refuses the other.
- [ ] 7.8 Map driver failures to retry-vs-fallback, and verify a device-absent failure offers browser print and a device-condition failure offers retry without creating a duplicate job.
- [ ] 7.9 Verify submission never waits on printing: with the device unreachable the sale still submits and printing is offered afterwards.
- [ ] 7.10 Require a reason for reprint and restrict reprint to the originating profile, verifying both refusals.
- [ ] 7.11 Defer printing while offline and record the job on reconnect marked printed-while-offline, carrying the locally-recorded terminal, reference, and hash.
- [ ] 7.12 Gate direct print on `enable_direct_print` and verify a disabled profile behaves exactly as today.
- [ ] 7.13 Verify permission boundaries: a cashier cannot edit `POS Print Terminal`/`POS Print Job`, and a job listing withholds device diagnostics and reservation internals.
- [ ] 7.14 Print one real receipt end-to-end on an iMin device at 58 mm, confirming buyer name, queue number, promotion components, and totals render inside the width without truncation. Record the device model, plugin version, and captured receipt.

## 8. Rollout and consolidation

- [ ] 8.1 Enable buyer identity on one dev profile and walk a full sale: name entry, queue numbers 1-3 in order, receipt print, invoice-list search. Capture the printed receipt.
- [ ] 8.2 Build "Paket Hemat 1" (paperbag fixed + single-choice flavour) with the ported Promotion, sell one, and verify stock moved per component, the parent moved none, and the receipt shows one priced line with component detail.
- [ ] 8.3 Reconfigure the group to `pick_count` 3 with repeats allowed, sell it, and verify the selection and deductions match.
- [ ] 8.4 Set one price group across two company outlets and verify the same rates apply at both, an unlisted outlet falls back, and editing many items happened without touching `Item Price`.
- [ ] 8.5 Redeem an item-scoped and a transaction-scoped coupon on partially-matching carts, and verify the discounts differ as specified and the legacy coupon is unchanged.
- [ ] 8.6 Retire `selling_additional` as a dependency once all ported tests pass in `pos_next`, and verify `pos_next` is the sole authority (no duplicate DocTypes installed on the site).
- [ ] 8.7 Verify every switch off returns the app to pre-change behaviour, and run the complete backend suite plus `yarn test:run` in `POS/` and confirm green.
- [ ] 8.8 Sync `openspec/specs/` and confirm the five capability specs are archived as main specs.
