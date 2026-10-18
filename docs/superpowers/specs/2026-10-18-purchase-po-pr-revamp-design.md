# POS Purchase Order / Purchase Receipt revamp (internal-only)

Date: 2026-10-18. Status: draft for user review.

## Intent

The outlet SPA handles only **internal** purchasing (outlet PO → factory/HQ Sales
Order → Delivery Note → outlet Purchase Receipt). External purchasing (outside
suppliers, negotiated prices, taxes, invoices) is done by the outlet supervisor
in native ERPNext Desk. Inside that internal scope the SPA must:

- open a PO or PR as a full-page detail view (Stock Opname style, ERPNext-native
  feel) instead of the inline expand-to-peek;
- let receipts be viewed again after receiving;
- have a clean Receive form;
- accept Excel/CSV attachments;
- hide prices from cashiers;
- keep working whether or not the `Purchase Receipt` Workflow is active.

## Decisions (settled with user)

| # | Decision |
|---|----------|
| D1 | SPA = internal suppliers only. External PO/PR are hidden from SPA lists entirely (option a); they live in Desk. |
| D2 | Prices (rate, amount, net/tax/grand total) are hidden from users without role `POSNext Manager`. Enforced in the API response, not only the UI. |
| D3 | No "PR without PO" in the SPA (external use case → Desk). Internal suppliers always receive via the Delivery Note. |
| D4 | PR submit works with or without an active Workflow on Purchase Receipt. Approve/Reject stays in Desk (HQ/factory). |
| D5 | Attachments additionally accept `.xlsx,.xls,.csv`; 10 MB limit unchanged. |
| D6 | Approach A: new views inside the existing dialogs (`view` state like `StockOpnameDialog.vue`), no new routes. |
| D7 | Actions live in the top header bar (ERPNext form toolbar style: primary button top-right, secondary actions beside it / in a "…" menu), for PO and PR, in detail, PO form and Receive views. No bottom action footer. |
| D8 | One primary button, ERPNext save/submit cycle: unsaved or dirty doc → **Save** (creates/updates Draft, then shows the saved Draft detail); saved, clean Draft → **Submit** (or the workflow action label, R6). No separate "Save Draft" button. Editing a saved Draft makes it dirty again → primary flips back to Save. |

Config (not code): outlet supervisors need Desk roles for buying/stock plus User
Permissions on their company/warehouse to do external purchasing natively.

## Requirements

### R1 Internal-only lists and pickers
- `get_purchase_orders` and `get_purchase_receipts` filter `is_internal_supplier = 1`.
- `get_purchase_order` / `get_purchase_receipt` refuse external documents
  ("Open this document in ERPNext").
- `search_suppliers` returns only `is_internal_supplier = 1` suppliers, so the
  SPA PO form can only create internal POs. `save_purchase_order` refuses an
  external supplier (trust boundary).
- The PO-sourced receive path (`get_purchase_receipt_draft`) is no longer
  reachable from the SPA; Receive always uses `get_intercompany_receipt_draft`.
  The endpoint itself stays (Desk/tests) — no deletion of shared API.

### R2 Price visibility
- One helper `_can_see_prices()` = user has `POSNext Manager` (or is
  Administrator/System Manager).
- When false, PO/PR summaries, list rows and receive drafts omit `rate`,
  `amount`, `net_total`, `total_taxes_and_charges`, `grand_total`,
  `price_list_rate`, `price_list`, `taxes_and_charges`.
- Saves never trust a client rate from a non-manager: PO rows get their rate
  from the price list (native `set_missing_values`); PR rows take the rate of
  the linked PO item / DN item from the server-side mapper output.
- UI shows price columns/totals only when the fields are present in the
  response.

### R3 PO detail view
Clicking a PO row opens `view = "doc"`:
- Header: back button, PO name, status pill, `Factory SO` badge when linked,
  action buttons by state — Draft: Edit, Submit (no Delete: no endpoint exists,
  not requested); To Receive: Receive
  (gated as today: `receive_requires_delivery_note` + `delivery_ready`),
  Cancel (blocked when factory-linked, as today); always: Open in ERPNext.
- Info card: supplier, transaction date, required-by date, warehouse, linked
  factory Sales Order, remarks.
- Items table: item (name + code), ordered qty, received qty, UOM
  (+ rate/amount when R2 allows), totals block when R2 allows.
- Attachments (view + add while Draft or To Receive).
- Linked Purchase Receipts list (name, date, status/workflow state); clicking
  one renders the shared PR detail in place inside the PO dialog (Back returns
  to the PO).

### R4 PR detail view
Clicking a PR row opens `view = "doc"`:
- Header: back, PR name, pill = `workflow_state` when a workflow is active,
  else status. Actions: Draft & editable → Submit; Open in ERPNext.
- Info card: supplier, posting date, warehouse, source PO(s), Delivery Note
  (`inter_company_reference`).
- Items table: item, received qty, UOM, source PO (+ rate/amount per R2).
- Attachments (view; add while Draft).

### R5 Receive form redesign
- Header card: supplier (shown once), PO, Delivery Note, warehouse, posting date.
- Table: item (name + code), Ordered, Received, Remaining, Qty to receive
  (large right-aligned input) + UOM.
- Row warning when qty > remaining; "Fill remaining" and "Clear" actions.
- Attachment drop zone under the table, hint lists PDF/image/Excel/CSV.
- No footer: actions in the header per D7/D8 (Save → Draft PR detail →
  Submit). Same for the PO create/edit form.
- Submit asks for a short confirmation (stock moves / cannot be edited after).

### R6 Workflow-safe PR submit
- New `submit_purchase_receipt(name)` (the SPA's Submit after Save, D8);
  `save_purchase_receipt(submit=1)` stays for compatibility. Both use a helper that
  mirrors `stock_opname.py`: `get_workflow_name("Purchase Receipt")`; if active,
  `apply_workflow(doc, action)` with the single action available from the
  initial state (error listing actions if several); else `doc.submit()`.
- Summaries/list rows include `workflow_state` (null when no workflow).
- Editable = docstatus 0 and (no workflow, or state is the initial state).

### R7 Attachments
- `ATTACHMENT_ACCEPT` in `POS/src/utils/attachments.js` adds `.xlsx,.xls,.csv`;
  hint text updated; server already validates size only.

## Components

- `POS/src/components/purchase/PurchaseDocView.vue` (new): presentational
  header/info/items/totals/attachments block shared by PO and PR detail; props
  only, emits actions. Keeps `PurchaseOrderDialog.vue` from growing further.
- `PurchaseOrderDialog.vue`: list row click → doc view; receive form redesign;
  internal-only receive call.
- `PurchaseReceiptDialog.vue`: list row click → doc view; Submit action.
- `pos_next/api/purchase_orders.py`, `purchase_receipts.py`: R1, R2, R6.
- Translations: new strings into `pos_next/translations/*.csv` (id at minimum).

## Testing

Backend (`pos_next/api/test_purchase_orders.py`, `test_purchase_receipts.py`):
- lists exclude external suppliers; get on an external doc is refused;
  `search_suppliers` internal-only; save PO with external supplier refused.
- price fields absent for a cashier, present for POSNext Manager; a tampered
  client rate from a cashier is ignored.
- PR submit with no workflow → docstatus 1; with a temporary active workflow →
  `workflow_state` moves to the next state, docstatus stays 0. Restore the
  workflow's `is_active` in cleanup.

Frontend (Vitest): PO row click opens doc view with items; PR row click opens
doc view; price columns hidden when fields absent; attachment input accepts
`.csv`; receive form "Fill remaining" and over-remaining warning; primary
header button reads Save for new/dirty docs and Submit for a clean Draft
(PO form and Receive); no Save Draft button rendered.

## Out of scope

External purchasing in the SPA, PR without PO, Approve/Reject in the SPA,
editing submitted receipts, URL routes for documents, supplier invoices.

## Risks

- Existing external PO/PR rows disappear from the SPA (intended, D1) — tell
  outlet staff before deploy.
- Activating the PR workflow changes who can finish a receipt (Stock Manager);
  outlet stock only moves after HQ approval.
