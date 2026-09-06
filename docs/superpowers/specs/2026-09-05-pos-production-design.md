# POS Production — Design

Date: 2026-09-05
Branch: `feat/pos-production` (worktree, based off `main` @ `52c0863`)
Status: Approved design, pending implementation plan

## Problem

Producing an item today requires a manual Stock Entry (purpose *Manufacture*) in the
Stock module. The form is powerful but complicated for POS operators: they must know
which items to consume, set source/target warehouses per row, and pick batches by hand.
The users of the POS cashier page are not trained ERPNext users.

We need a production feature on the cashier page that ends in a submitted
**Stock Entry Manufacture** but hides all of that behind a simple, guided flow:
pick a recipe → enter quantity → adjust materials if needed → done.

## Decisions (from brainstorming)

1. **Recipes are our own simple doctype** — no ERPNext BOM/Work Order dependency.
2. **Entry point**: existing icon sidebar (`ManagementSlider.vue`) on desktop, and the
   cashier profile dropdown (`UserMenu.vue`) on mobile.
3. **Recipe defaults, cashier can override** — auto-filled materials with editable
   quantities; rows can be removed or extra items added before submitting.
4. **Batch support** — required for items that are batch-tracked.
5. **V1 is recipe-only** — no ad-hoc production without a recipe.
6. **Approach: Direct + Log** — one call submits the Stock Entry, and a
   `POS Production Log` row is written for reporting.
7. **Recipes are multi-company** — one recipe can be enabled for several companies
   via a child table; it applies in every company where it is listed.

## UX flow

1. Cashier taps **Production** in the sidebar (desktop) or the profile dropdown
   (mobile). The control is hidden when the user lacks permission or is offline.
2. **Step 1 — pick recipe**: searchable card list of enabled recipes available for the
   outlet's company. Each card shows the finished item and a material availability
   hint computed against the POS warehouse stock.
3. **Step 2 — produce**: cashier enters the finished-goods quantity; material rows
   auto-scale from the recipe (`qty × target / output_qty`). The cashier may edit row
   quantities, delete rows, or add items via item search. Batch-tracked materials get
   a batch picker (FIFO suggestion, changeable). Non-batch materials show quantity
   input only — **no batch UI at all** for items whose Item master has
   `has_batch_no = 0`.
4. Tap **Process** → spinner → success toast `Production complete: <item> ×N`.
   Material stock decreases, finished-item stock increases, atomically.

## Data model

New doctypes (following existing naming and child-table patterns):

| Doctype | Key fields |
|---|---|
| `POS Production Recipe` | `recipe_name` (unique), `production_item` (Link Item), `output_qty` (FG units per run), `disabled`, child tables below |
| `POS Production Recipe Item` | `item_code` (Link Item), `qty` per run in the item's **stock UOM** |
| `POS Production Recipe Company` | `company` (Link Company), `enabled` — **multi-company: add one row per company where the recipe applies** (pattern of `POS Offer Company`) |
| `POS Production Log` | `recipe`, `production_item`, `qty`, `items_used` (JSON snapshot of actual materials), `stock_entry` (Link), `pos_profile`, `company`, `owner` — created and submitted by the system |

- The Stock Entry remains the source of truth for stock and valuation; the Log is the
  reporting record (per outlet/cashier/recipe) and is linked back to the Stock Entry.
- Stock Entry `remark` is set to `POS Production: <recipe_name>` so desk users can
  filter production entries easily.
- Multi-company behaviour: the recipe needs no duplication. When produced in company
  B, stock checks run against company B's POS-profile warehouse, and ERPNext resolves
  accounts/valuation in company B's context (Item master is global).

## API — `pos_next/api/production.py`

### `get_production_recipes(pos_profile)`

Derives company and warehouse **server-side** from the POS Profile (the client never
supplies them). Returns enabled recipes whose company child table contains that
company (enabled), with:
- recipe name, finished item, `output_qty`
- material rows: item code/name, qty, `has_batch_no` flag
- per material: available qty in the caller's POS-profile warehouse, and for batch
  items the list of batches with remaining qty (FIFO order) — enough for the dialog to
  render and pre-suggest without extra round trips.

### `create_production(recipe, qty, items, batches, pos_profile)`

Whitelisted. Company and warehouse are derived from `pos_profile` on the server
(trust boundary — the client cannot choose an arbitrary warehouse). All work in one
request/DB transaction:

1. Validate: recipe enabled and caller's company is listed; items exist and are not
   disabled; quantities > 0; finished item valid.
2. Validate stock **server-side** per material against the POS warehouse: total qty
   for non-batch items; batch-wise qty for batch items (same batch stock helpers the
   sales flow uses). Reject with a clear message naming item, warehouse and shortfall
   *before* any document is created.
3. Build the Stock Entry: `purpose = Manufacture`, company from the POS profile;
   materials with `s_warehouse` = POS warehouse (+ serial/batch bundle for batch
   items); one finished row with `t_warehouse` = POS warehouse, `is_finished_item = 1`.
   For a batch-tracked finished item, a new batch is created automatically (verify the
   auto-batch behaviour on this ERPNext version during implementation; fallback is to
   create the `Batch` record in the API before insert).
4. Insert + submit the Stock Entry, then insert + submit the `POS Production Log`.
   Any failure rolls the whole request back — no half-applied production.

## UI & wiring

- New `ProductionDialog.vue` (two-step) under `POS/src/components/pos/`, following the
  dialog patterns of `WarehouseAvailabilityDialog` / `BatchSerialDialog` (batch
  picker reused conceptually, not imported from the sales dialog).
- `ManagementSlider.vue`: new icon button (`tool`), emits `menu-clicked('production')`;
  `POSSale.vue` opens the dialog.
- `UserMenu.vue` (mobile): a "Production" item delivered through the existing
  `#menu-items` slot passthrough (POSHeader → POSSale), rendered only on `lg:hidden`.
- Permission gating via the existing `usePermissions` composable; offline disables the
  control (existing pattern for other actions).

## Permissions

- Button + API gated by **create permission on `POS Production Log`** (the API's
  document inserts enforce it server-side too).
- Recipe management on the desk: roles with doctype access to `POS Production Recipe`
  (System Manager / Stock Manager by default). No new Role records required.

## Error handling

- Insufficient material stock → reject pre-flight with e.g.
  `Material Kopi Bubuk short by 2.5 Kg in Stores - Outlet A`.
- Recipe disabled/deleted while the dialog is open → clear error, dialog refreshes the
  recipe list.
- Double-submit → the Process button disables itself while the request is in flight.
- Offline → control disabled.
- Any server error rolls back the transaction; the toast shows the error message.

## Testing

Backend pytest (same style as existing `pos_next/api/test_*.py`):

- recipe listing scoped per company (multi-company rows honoured)
- quantity scaling and cashier overrides accepted
- pre-flight rejection on insufficient stock (batch and non-batch)
- successful run: Stock Entry submitted with correct rows/warehouses, FG valuation
  derived from materials, Log linked both ways
- batch consumption reduces the right batches; batch-tracked FG gets a batch

Frontend: `POS` build passes; a light unit test for the dialog (pattern of
`ShiftClosingDialog.test.js`).

## Non-goals (v1)

Ad-hoc production without recipe, by-products / multiple finished items per run,
ERPNext BOM import or Work Orders, approval step, per-outlet recipe overrides,
offline production queue.

## Isolation notes

All work happens in the git worktree `.worktrees/feat-pos-production` on branch
`feat/pos-production` (from `main`). The main checkout stays on `feat/pos-offer-2`
(another agent's active branch) and is never touched. No files shared with the
pos-offer/promotions/discount-restriction surface are modified.
