## Context

POS Next transacts against `Sales Invoice` (ERPNext's `POS Invoice` doctype is not used). A sale is built client-side in `POS/src/composables/useInvoice.js` and `POS/src/stores/posCart.js`, sent as a JSON payload to `pos_next.api.invoices.update_invoice` for the draft and `submit_invoice` for confirmation, and printed through `POS/src/utils/printInvoice.js`, which renders a Frappe Print Format (`POS Next Receipt`) and hands HTML to QZ Tray or the browser.

The pricing/promotion features are not greenfield. A working `selling_additional` app already exists at `/Users/rotiropi/DockerERPNext/.../apps/selling_additional`, on a different bench, and the user has confirmed it runs correctly. It is **not installed** on this bench, and its `main` on GitHub is a scaffold only — the live source is the Docker path. Its proven model:

- **Walk-in**: `walk_in.py::validate_walk_in_customer_name` puts the free-text name on a Sales Invoice Custom Field `custom_walk_in_customer_name`, keeps `customer` as the POS Profile default, and enforces that the name only applies to that default customer. It never creates a Customer — this is exactly the ERPNext-aligned direction the user wants.
- **Price Group**: `Price Group` (autoname by name, currency, price_list) + child `Price Group Item` (`item_code`, `uom`, `rate`) + child `Price Group Outlet` (`company`, `warehouse`, `pos_profile`, status). One master applies to many company/warehouse outlets; a bulk table replaces per-`Item Price` editing. Has concurrency and lifecycle tests.
- **Promotion (paket)**: `Promotion` (`parent_item`, `base_price`, `valid_from/to`, `max_instances_per_invoice`) + child `Promotion Component` (fixed `item_code`,`qty`) + `Promotion Choice Group` (`label`,`group_key`,`pick_count`) + `Promotion Option` (`choice_group_key`,`item_code`,`price_adjustment`,`max_per_option`) + `Promotion Outlet`. Per-sale it writes `Pos Promotion Selection` (JSON `snapshot`, `total_amount`, `instance_id`) and a queryable `Promotion Selection Fact` (one row per chosen item, `kind` in {Option, Fixed Component}) linked to the invoice. API: `get_available_promotions`, `get_promotion_detail`, `quote_promotion(promotion, choices, pos_profile)`; the whitelist layer is POST-only with a `_check_access(pos_profile)` gate.

Constraints that shape this approach:

- The user has decided **everything lands in `pos_next`** — price group, promotion, and buyer identity are ported/adopted here; `selling_additional` is not a runtime dependency. This change therefore includes a **migration of proven code and its test suite** from a sibling app into `pos_next`, not only new code.
- Offline-first is a hard product requirement. Every new field and payload must survive the IndexedDB queue and background sync in `POS/src/utils/offline/` and `pos_next.offline_invoice_sync`.
- Server-side pricing is deliberately suppressed — `ignore_pricing_rule = 1` — and discounts run through POS Next's own `api/offers.py` / `api/promotions.py`. Price Group and coupon scoping must integrate there, not through ERPNext `Pricing Rule`.
- `pos_direct_print` is installed and its server lifecycle is complete (`reserve_print_job`, `bind_receipt_snapshot`, `start_attempt`, `complete_attempt`, `reprint_invoice`, `fallback_to_browser`, `resolve_terminal_for_profile`); the user confirms printing reaches an iMin device. Physical printing happens in the iMin webview via the `IminPrintInstance` JS SDK, not a Python driver, so there is no blocking driver milestone for POS Next to wait on.
- Custom Fields in this app are today created by hand in the Desk; the JSON under `pos_next/pos_next/custom/` mirrors that state but is not an apply mechanism (`hooks.py` fixtures export only `Role` and `Custom DocPerm`). New fields must be made reproducible. See D8.

## Goals / Non-Goals

**Goals:**

- Five capabilities that each degrade to today's behaviour when switched off, so rollout is per-profile.
- Adopt the `selling_additional` data model where it is already proven, rather than re-deriving a parallel one — the port must carry its tests.
- Server-side authority for every client-entered structure (selections, coupon scope, buyer name), because the POS client is a mutable browser.
- One printing seam from POS Next's point of view whether the backend is QZ Tray, an iMin device, or the browser.

**Non-Goals:**

- No ERPNext/Frappe core modification.
- No customer-display / kitchen screen for queue calling; only the current-number API and its receipt/list surfacing.
- No new loyalty or credit semantics for name-only sales.
- No iMin model-capability matrix (D1/D1w/D4/M2/S1, USB vs SPI, cutter) — that lives in `pos_direct_print`.
- No attempt to keep `selling_additional` installable in parallel; this change folds its features into `pos_next`.

## Decisions

### D1 — Buyer identity as Custom Fields on Sales Invoice, not as Customer

`buyer_name` (Data) and `queue_number` (Int) become Custom Fields on `Sales Invoice`, created by the mechanism in D8. `customer` keeps its semantics and defaults to the POS Profile walk-in customer; a name-only sale never provisions a Customer.

This adopts `selling_additional/walk_in.py`'s validated pattern — including its rule that a buyer name only applies when `customer` equals the profile default — instead of inventing new validation. Port that function into `pos_next` (D9). The one deliberate rename: `custom_walk_in_customer_name` → `buyer_name`, so the field carries no stale `custom_` prefix and reads as the queue-facing name it is.

Alternatives considered: auto-provisioning an `Individual` Customer per name — what `update_invoice` (`pos_next/api/invoices.py:766`) does today. Rejected, and consistent with the user's "follow ERPNext default" preference: a bakery at ~300 walk-ins/day adds ~78,000 Customer rows/year/outlet, each polluting customer pickers, reports, and lead scoring, and it collides with `allow_duplicate_customer_names`. Because that path is load-bearing for some deployment, retiring it is the change's one BREAKING item; it becomes an explicit validation error naming `buyer_name` as the replacement.

### D2 — Queue numbers allocated server-side per shift

`queue_number` is allocated in `submit_invoice` against the transaction's `posa_pos_opening_shift`. The shift row is locked with `frappe.qb.get_query("POS Opening Shift", filters={"name": shift}, for_update=True)` and `current_queue_number` is incremented in that same database transaction as the invoice write, so concurrent submissions serialise on the shift row. `frappe.qb` is used rather than raw SQL, matching project convention and `selling_additional`'s own price-group concurrency tests.

Alternatives considered: a `Counter` DocType per shift (extra document, no benefit), client-side allocation (duplicates across terminals), a DB sequence (unavailable on the bench's MariaDB).

Uniqueness cannot be a DB constraint on the invoice — a Custom Field cannot carry a unique index — so it comes from construction: the shift-row lock makes a duplicate assignment impossible rather than merely detectable. `queue_number` on `Sales Invoice` is the published copy for reporting and search. `POS Opening Shift` is submittable, so the counter is written with `db_set(update_modified=False)`; it is an operational counter, not financial data, and is kept out of the shift's audit trail.

Offline sales take a locally-estimated number for the physical receipt and are renumbered at sync; the offline payload carries both values so the audit shows what was printed.

### D3 — Promotions: port the `selling_additional` model, do not redesign it

Adopt `Promotion` + `Promotion Component` + `Promotion Choice Group` + `Promotion Option` + `Pos Promotion Selection` + `Promotion Selection Fact` as the package model. It already satisfies the stated needs — `pick_count > 1` gives "choose more than one", `max_per_option > 1` gives "same flavour twice", `Promotion Component` gives the mandatory paperbag, `base_price + price_adjustment` gives combo pricing — and it separates an immutable `snapshot` (what was sold) from a queryable `Promotion Selection Fact` (what drove what), which a naive design would collapse.

Alternatives considered and rejected:
- Designing a fresh `POS Package`/`POS Package Component`/`POS Package Selection` triple as an earlier draft of this change assumed. Rejected — it duplicates a model that is already in production and tested, and its "expand into zero-value invoice rows" approach is weaker than the `Selection Fact` table, which keeps components off the accounting lines entirely while staying queryable.
- ERPNext `Product Bundle`: no constrained choice, no min/max or repeats, derived price. Rejected.
- One Item per flavour combination: zero logic but explodes the catalogue and cannot express "up to 3, repeats allowed". Rejected.

**Enhancement proposed over the existing model** (welcome, per user; additive, not a rewrite): the current `Promotion` has no `allow_repeats` toggle on a choice group — repeats are implied by `max_per_option`. Add an explicit `allow_repeats` Check on `Promotion Choice Group` so "this group is pick-2-distinct" versus "pick-2-any" is a named, readable constraint rather than an inference from per-option caps. Everything else ports as-is.

### D4 — Selection validation lives in one server function, reused at write time

Keep `selling_additional`'s shape: a `quote_promotion(promotion, choices, pos_profile)` whitelisted endpoint that returns the expanded lines and total, plus a validation core. Expose it to the client to drive the dialog's live enable/disable state, and call the same core from `update_invoice`/`submit_invoice` on every write. The client cannot be the authority — it is a browser with IndexedDB. Preserve the POST-only whitelist and `_check_access(pos_profile)` outlet gate during the port.

### D5 — Price Group: port as-is and resolve through POS Next's pricing path

Adopt `Price Group` + `Price Group Item` + `Price Group Outlet` unchanged; the outlet model (one master → many company/warehouse/pos_profile rows) already delivers the user's "apply across companies at once, edit fast, not one-by-one in Item Price". Carry its concurrency and lifecycle tests.

Resolution point differs between the two apps and must be pinned: `selling_additional` resolved price group inside ERPNext's item-details flow, but POS Next sets `ignore_pricing_rule = 1` and owns pricing. So the ported price-group lookup is wired into **POS Next's** `pos_next.api.items.get_item_details` (and its offline IndexedDB price mirror), keyed off the sale's outlet. A walk-in sale with no price-group assignment keeps the standard Price List rate — price group is an override, never a requirement.

### D6 — Coupon scope: new capability, evaluated in POS Next's offer engine

`POS Coupon` gains `discount_scope` in {Transaction, Item Code, Item Group, Brand} plus member rows, reusing the vocabulary `POS Offer` already uses for `apply_on`/`apply_type`. Discount math in `api/offers.py` / `api/promotions.py`: the base becomes the sum of matching line amounts instead of `grand_total`, then the existing `max_amount` cap applies. This one is genuinely new — `selling_additional`'s Promotion covers bundles, not coupon-to-item scoping — so spec D6-style compatibility matters: an unset scope must take today's exact code path.

`validate_coupon` currently returns `{"valid": False}` when `customer` is falsy (`pos_next/api/offers.py:610`). That guard becomes conditional: a customer-bound coupon still requires a match; an unbound coupon validates on a walk-in sale. This sits on the endpoint the recent `PN-77` fix touched, so it needs test coverage for both branches.

### D7 — pos_next drives pos_direct_print through the existing API; browser print stays fallback

Printing moves behind one client seam, `POS/src/utils/directPrint.js`: resolve a terminal via `pos_direct_print`'s `resolve_terminal_for_profile`, then drive reserve → bind snapshot → start attempt → complete/fail. When no terminal is bound, `printInvoice.js` continues down its existing QZ Tray / browser path unchanged.

Alternative considered: a self-contained iMin adapter inside `pos_next`. Rejected — the job/attempt/reservation/hash lifecycle already exists in `pos_direct_print`; forking it would leave two sources of truth for whether a receipt actually printed. The iMin SDK bridge stays in `pos_direct_print` where the user has already proven it works.

### D8 — Custom Fields become reproducible, not Desk state

All Custom Fields introduced here (`Sales Invoice.buyer_name`, `Sales Invoice.queue_number`, `POS Opening Shift.current_queue_number`, plus coupon/price-group fields) are created by a `pos_next.install` setup function, called from `after_install` and `after_migrate`, that upserts each definition from a Python-side list and keeps `pos_next/pos_next/custom/*.json` as a review mirror. `selling_additional` already solved this correctly in its own `install.py`/`tests/test_install_paths.py`; port that approach rather than re-litigating it.

`install.py`'s docstring currently claims fixtures apply custom fields and print formats; it does not. Correcting it is part of this work.

### D9 — One module, and `selling_additional` is retired as a runtime dependency

All five capabilities land in the existing `pos_next.pos_next` module and `pos_next/api/` package. Porting means copying DocType JSON, controllers, `walk_in.py`, the price-group and promotion logic, and their tests out of `selling_additional`, then rewiring DocType module paths and role permissions to POS Next's existing `POSNext Cashier` / `Nexus POS Manager` roles.

## Risks / Trade-offs

- **[R1] Porting mature code can quietly drop the safety the original earned** — access gates, POST-only whitelists, concurrency locks, snapshot/fact separation, its test suite. → Every ported unit carries its `selling_additional` tests into `pos_next`; a capability is not "ported" until its original tests pass unchanged against the new module path. Diff the two install/hook/override files as a checklist, not a vibe.
- **[R2] Retiring `update_invoice`'s auto-create-Customer breaks deployments that type a name into the picker** → Land `buyer_name` + its UI first (which already works via the walk-in pattern), then remove the auto-create in its own revert-able commit. Ship a read-only pre-migration report of `Customer` rows that look like ad-hoc walk-ins (no address, no mobile, single-invoice history) so the operator reviews before upgrade.
- **[R3] Two source trees for pricing after the port risks divergence** → Do not leave `selling_additional` installed alongside. Retire it as a dependency in the same release the port lands, so `pos_next` is the single authority.
- **[R4] Queue-number races across concurrent terminals on one shift** → Row-lock the shift counter with `for_update=True` before assigning, invoice write in the same transaction, and a concurrency test asserting distinct sequential numbers.
- **[R5] Offline IndexedDB schema migration for several new payload shapes risks losing queued unsynced sales during a client update** → Version the upgrade path and write it additively; test migrating a database that already holds queued invoices — losing them means losing revenue records.
- **[R6] Coupon applied twice if the scoped base is computed separately from the applied-discount path** → Compute scope and discount in one pass; assert the applied amount never exceeds the in-scope base; test a coupon covering no line, one line, and all lines.
- **[R7] Five capabilities in one change makes partial rollback hard** → Each is switch-gated in POS Settings behind its own commit series; rollback is switch-off first, revert second.
- **[Trade-off] Server-authoritative validation adds a round trip per package add** → Cache promotion definitions client-side for offline; for a bakery catalogue they are small enough to ship in the bootstrap payload.

## Migration Plan

1. Port `walk_in.py`, `Price Group`/`Promotion` DocTypes, controllers, and their tests into `pos_next.pos_next`; make all five POS Settings switches (`enable_buyer_identity`, `require_buyer_name`, `enable_promotions`, `enable_price_groups`, `enable_direct_print`) default off. Verify with `bench --site erpnext16.localhost migrate` that all ported tests pass at the new module path and behaviour is unchanged with switches off.
2. Implement `pos_next.install.setup_custom_fields()` (D8) for buyer/queue/coupon fields, idempotent, with the docstring correction; verify by deleting the field rows, re-migrating, and confirming recreation with matching `dt`/`fieldname`/`fieldtype`/`insert_after`.
3. Wire ported price-group resolution into `pos_next.api.items.get_item_details` + its offline mirror; verify a priced walk-in sale and a price-group sale differ correctly and an unassigned outlet falls back to Price List.
4. Wire coupon scope into the offer engine (D6) with the conditional customer guard; verify every legacy no-scope coupon test still passes unmodified.
5. Land `buyer_name` UI + queue allocation; run the pre-migration walk-in Customer report and review with the operator.
6. Remove auto-create-Customer from `update_invoice` as its own revert-able commit.
7. Land the `POS/src/utils/directPrint.js` seam behind `enable_direct_print`; enable on one pilot terminal, print a real 58 mm receipt, then roll out.
8. Retire `selling_additional` as a dependency once all ported tests pass in `pos_next`.

Rollback at any step is switch-off in POS Settings. Steps 1-5 are additive and revert-able; step 6 needs a data decision — existing auto-created `Customer` rows are left in place, not deleted.

## Open Questions

- Whether `buyer_name` keeps a `custom_`-free name (`buyer_name`) as decided, or preserves the original `custom_walk_in_customer_name` to avoid a data mapping during the port. Resolved toward `buyer_name`; revisit only if live sites already depend on the old fieldname and a rename patch is judged too costly.
- Whether the queue number should be human-formatted (`017`) for calling/printing or a bare integer with client-side padding. Cosmetic; no schema impact.
- Whether a partially-returned promotion reuses the existing return path per component or records a promotion-level return line — `selling_additional` already has `test_promotion_returns.py`; match its behaviour rather than decide fresh.
