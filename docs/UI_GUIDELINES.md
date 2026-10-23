# POS SPA UI Guidelines

Rules for list screens, item grids and status display in the Vue SPA (`POS/`).
New screens follow them; existing screens are brought in line when touched.
Reference implementations: `RejectRequestDialog.vue`, `StockOpnameDialog.vue`,
`purchase/PurchaseOrderDialog.vue`, `purchase/PurchaseReceiptDialog.vue`.

## 1. List screens

### Toolbar (top of the list, in this order)
1. Status filter chips (left), when the doctype has a workflow/status filter.
2. Search box (if any), next to the chips.
3. Refresh button, then the primary "New ..." button (right, `variant="solid"`).

Status chips: rounded-full pills, `All` first, then workflow order. Active chip
`border-gray-900 bg-gray-900 text-white`, inactive `border-gray-200 bg-white text-gray-600`.
The chip row scrolls horizontally on small screens (`overflow-x-auto`), never wraps.

### Column order (desktop)
1. **ID** (document name, bold, may carry small badges like `Late` / paperclip).
2. **Main descriptor**: party or purpose (Supplier, Purpose, ...).
3. **Dates** (transaction/posting date, then due/required date).
4. **Numbers**, right-aligned, `tabular-nums` (Amount, Grand Total, Items).
5. **Status** pill.
6. **Actions** (icon buttons / Continue), right-aligned, last.

Status is always the column right before Actions (or the last column when a
list has no actions). Do not put it second.

### Column proportions
- Text columns use `minmax(0,Nfr)` so they shrink and truncate (`truncate`).
  ID `1.2fr`-`1.4fr`, descriptor `1fr`-`1.6fr`.
- Fixed columns use `rem`: date `7rem`-`8.5rem`, amount `8rem`, status `10rem`-`11rem`
  (wide enough for the longest state, e.g. "Destruction Submitted", on one line),
  icon action `2.5rem`.
- Header and rows share the exact same `md:grid-cols-[...]` template.

### Header row
`bg-gray-50 text-xs font-medium text-gray-500`, sentence case (no `uppercase`),
`border-b border-gray-200`. Numeric headers `text-end`.

### Mobile cards (below `md`)
Two-column card: ID top-left, Status pill top-right, descriptor full width,
date left / number right, actions full width at the bottom. Reorder cells with
`order-N` / `md:order-N` so one DOM serves both layouts.

### Paging
Long lists: "Previous / Page n / Next" (right-aligned) or "Load More" (centered).
Pick one per screen; prefer the pager for HQ-facing lists.

## 2. Status display

- Always a pill: `inline-flex shrink-0 items-center whitespace-nowrap rounded-full
  px-2 py-0.5 text-xs font-medium ring-1 ring-inset` + a tone.
- `whitespace-nowrap` is mandatory; size the column instead of wrapping the pill.
- Tones (bg / text / ring):
  - draft or returned: orange / red (needs the user's action)
  - pending approval: blue
  - in progress (awaiting, submitted for check): amber / purple
  - done (Completed, Approved, submitted): green
  - cancelled: gray
- Derive the tone from payload flags (docstatus, editable, returned, processing)
  when the workflow states are configurable; map state names only when the
  states are fixed in code.
- The label is the workflow state, translated with `__()`.

## 3. Editable item grids (child tables)

- Section: `rounded-xl border border-gray-200 bg-white`, title row, column
  header, rows, then the Add Row footer.
- **Never put `overflow-hidden` or `overflow-x-auto` on the section or on any
  wrapper around rows that hold a dropdown.** Both clip absolutely positioned
  menus (item search, UOM picker), which then look hidden behind the footer.
  Keep the rounded corners with `rounded-b-xl` on the footer instead.
- If a grid really needs horizontal scroll, render the dropdown outside the
  scroll container (teleport/fixed position) instead of clipping it.
- Dropdowns: `absolute inset-x-0 top-full z-20 mt-1 max-h-56 overflow-y-auto
  rounded-lg border bg-white shadow-lg`.
- Column order: select checkbox, No., Item, UOM/Batch, Qty, money, notes, remove.
- Footer: Add Row (left), bulk actions such as Delete Selected (next to it),
  totals (right).

## 4. Page shell

- Container `mx-auto w-full max-w-6xl flex flex-col gap-3` (lists) / `gap-4` (forms).
- Detail/form header: back arrow, title, status pill, primary action at the
  right end. No sticky footer with actions.
- Every new UI string goes through `__()` and gets a row in
  `pos_next/translations/id.csv` (see `docs/LOCALIZATION.md`).

## 5. Known deviations to fix later

- `InvoiceManagement.vue` lists: not yet audited against these rules.
- Reject Request uses "Load More", the others use a pager.
