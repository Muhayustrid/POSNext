# Backdate Entry as a native DocType — Design

Date: 2026-10-18
Status: approved in chat (sections 1-2); section 3 decided by agent per user "best practice, scalable".

## Goal

Replace the custom Desk page `backdate-entry` (vanilla JS, ~1600 lines) with a
native submittable DocType `POS Backdate Entry` so HO works in standard ERPNext
list/form UI, and extend the lane with:

1. Multiple invoices per entry (one shift, one re-close).
2. POS Package support (choice dialog, server-quoted, Price Group price).
3. Editable posting date **and posting time** per invoice.
4. Editable closing date/time for the re-close (default now).
5. Closing counts prefilled automatically (rule below).
6. Correction of a submitted invoice (cancel + amend): wrong mode of payment,
   add/remove items, change qty/rate.

Product Bundle already works (packed items expand server-side) — no change.

## Decisions (from the grilling)

| # | Decision |
|---|----------|
| D1 | New DocType (option A), not a page and not raw Sales Invoice forms. |
| D2 | One entry = one shift, many invoices; one cancel + one re-close per submit (A1). |
| D3 | Closing counted amounts: Cash mode = the old closing's physical count; non-cash modes = expected. Editable. |
| D4 | Correction = cancel original + create amended invoice (`amended_from`). Rejected when the original already has a return. |
| D5 | Package choice via `frappe.ui.Dialog` when a package parent item is entered; server `quote()` prices it. |
| D6 | Item input is the standard grid with a Link column — no separate search box + result table. |
| D7 | Shift invoices are listed immediately when the shift is picked (no "search first"). |
| D8 | Confirmation code follows the per-Company switches exactly like the SPA (`discount_code.get_status`); hidden and not required when the switch is off. Server re-checks. |
| D9 | Entry cannot be cancelled after submit (reversal is a new entry). |
| D10 | Invoice doctype follows `POS Next Global Settings.invoice_type` (`get_pos_invoice_doctype`), not hardcoded Sales Invoice. |

## Data model

### `POS Backdate Entry` (submittable, autoname `BDE-.YYYY.-.#####`, module POS Next)

| Field | Type | Notes |
|-------|------|-------|
| company | Link Company | reqd, fetched from shift |
| pos_opening_shift | Link POS Opening Shift | reqd; query: docstatus 1, status Closed, profile allows backdate |
| pos_profile | Link POS Profile | read-only, fetch_from shift |
| shift_period | Data | read-only display (start – end) |
| closing_date | Date | reqd, default Today |
| closing_time | Time | reqd, default Now |
| cancelled_closing_shift | Link POS Closing Shift | read-only, set on submit |
| new_closing_shift | Link POS Closing Shift | read-only, set on submit |
| shift_invoices_html | HTML | report-style table of the shift's submitted invoices with Return / Correct buttons |
| invoices | Table `POS Backdate Entry Invoice` | reqd |
| items | Table `POS Backdate Entry Item` | reqd |
| payments | Table `POS Backdate Entry Payment` | |
| closing_payments | Table `POS Backdate Entry Closing Payment` | |
| remarks | Small Text | |

### `POS Backdate Entry Invoice` (child)

invoice_no (Int, reqd), entry_type (Select Sale/Return/Correction, reqd),
against_doctype (Link DocType, hidden, R8), against_invoice (Dynamic Link → against_doctype; Return Against / Amend From),
customer (Link Customer, reqd), posting_date (Date, reqd), posting_time (Time, reqd),
confirmation_code (Data, depends on gate), created_invoice (Dynamic Link, read-only),
invoice_doctype (Data, hidden, read-only), grand_total (Currency, read-only, client-computed).

### `POS Backdate Entry Item` (child)

invoice_no (Int, reqd), item_code (Link Item, reqd), item_name (read-only),
uom (Link UOM), conversion_factor (Float, default 1), qty (Float), rate (Currency),
discount_percentage (Percent), amount (Currency, read-only),
against_item_row (Data, hidden — original row name for returns/corrections),
warehouse (Link Warehouse, hidden), remaining_qty (Float, read-only — returns only),
pos_package, pos_package_instance, pos_package_role, pos_package_snapshot (hidden; same
names/meaning as the invoice item custom fields).

Return rows are entered with positive qty in the grid; the server negates them.

### `POS Backdate Entry Payment` (child)

invoice_no (Int, reqd), mode_of_payment (Link, query: the profile's modes), amount (Currency).
Several rows per invoice allowed (split payment).

### `POS Backdate Entry Closing Payment` (child)

mode_of_payment, opening_amount, expected_amount (read-only), closing_amount (editable), difference (read-only).

## Form behaviour (client script `pos_backdate_entry.js`)

- Pick shift → fetch company/profile; call `get_shift_context(pos_opening_shift)`:
  shift invoices, profile payment modes, default customer, code gate status,
  old closing's counts. Render `shift_invoices_html` immediately (D7).
- **Return** button on an invoice row → append an Invoice row (type Return,
  against = that invoice, customer, posting = shift start date/now clamped) and
  its items with `remaining_qty` (qty 0, HO fills in); payment row prefilled from
  the original's first mode.
- **Correct** button → append a Correction row and copy every item + payment of
  the original (full qty) for editing.
- **Add Sale** button → new Invoice row (next invoice_no, default customer).
- Items grid: `item_code` change → `get_item_rate(pos_profile, item_code, uom, posting_date)`
  (profile selling price list incl. Price Group). If the item is a POS Package
  parent → open the choice dialog (groups/options/min/max from `get_packages`),
  call `quote_package`, replace the row with parent + component rows sharing a new
  `pos_package_instance`; component rows read-only; "Edit Package" re-opens the dialog.
  Packages without choice groups skip the dialog.
- Payments: when an invoice has exactly one payment row and its amount was not
  touched, keep it equal to the invoice total (same as the page today).
- Closing Payments recompute client-side whenever items/payments change:
  expected(mode) = old closing expected(mode) + this entry's Sale/Correction payments
  − Return refunds − payments of the invoices being corrected. Counted follows D3
  (Cash = old physical count, non-cash = new expected); a touched `closing_amount`
  is kept. The server recomputes expected on submit (authoritative); only
  `closing_amount` is taken from the grid.
- `confirmation_code` column shown/required per D8.
- List view: standard; indicator Draft / Submitted; filters company, pos_profile, pos_opening_shift.

## Server (`pos_backdate_entry.py` + `api/backdate_invoices.py`)

### validate
- Role gate (`has_backdate_role`) + `allow_change_posting_date(pos_profile)`.
- Shift: docstatus 1, Closed, has a submitted closing (on submit it is re-checked under lock).
- Every invoice_no has ≥1 item; payments/items reference an existing invoice_no.
- `posting_date + posting_time` within [shift period_start, now]; closing datetime ≥ max posting datetime and ≤ now.
- Return/Correction: `against_invoice` submitted, same shift, not a return itself.
  Correction additionally: no submitted return against it; not already amended.
- Return qty ≤ remaining qty (reuses `validate_return_items`).

### on_submit (single DB transaction)
1. Lock: `SELECT ... FOR UPDATE` on the POS Opening Shift row.
2. Cancel the submitted closing (extract `reopen_shift` core into `_reopen(shift)` without the
   whitelist wrapper); store `cancelled_closing_shift`.
3. For each invoice_no in ascending order, build the payload and call a shared core
   `_submit_backdated(payload, shift)` (extracted from `submit_backdate_invoice`, keeps the
   marker flag, window check and server authority over profile/posting fields; now also
   honours `posting_time`):
   - Sale → new invoice.
   - Return → `is_return`, `return_against`, negative qty, `refund_confirmation_code`.
   - Correction → `frappe.get_doc(dt, against).cancel()` then new invoice with
     `amended_from = against` and `discount_confirmation_code`.
   Store `created_invoice`. Package rows are sent with their `pos_package_*` fields so
   `validate_invoice_packages` re-quotes and validates them.
4. Re-close: `make_closing_shift_from_opening` → apply `closing_payments` counts →
   set `period_end_date = closing_date + closing_time`, `posting_date = closing_date` →
   `submit_closing_shift`. Store `new_closing_shift`.
5. Any exception propagates → Frappe rolls the whole request back; error message is
   prefixed with "Invoice No. {n}: ".
- `before_cancel`: throw (D9).

### Whitelisted helpers
- `get_shift_context(pos_opening_shift)` — gated; shift invoices (name, customer, total,
  posting, is_return, has_return, payments), payment modes, default customer, gate status, old counts.
- `get_item_rate(pos_profile, item_code, uom=None, posting_date=None)` — reuses items.py price lookup.
- `get_return_items(invoice)` — reuses `prepare_return_invoice` remaining-qty logic.

- `get_backdate_packages` / `quote_backdate_package` — see R5.

Backend tests live in `pos_next/pos_next/doctype/pos_backdate_entry/test_pos_backdate_entry.py`.

## Research findings (2026-10-18) and resulting requirements

R1. **Closing gates exclude Sales/Accounts Manager.** `make_closing_shift_from_opening`,
`get_pos_invoices` and `submit_closing_shift` (pos_closing_shift.py) allow only the
shift owner or `is_management_user()` (= System Manager, POSNext Manager). The
backdate roles also include Sales Manager / Accounts Manager → re-close would raise
PermissionError for them (same latent bug in the old page). Requirement: these three
gates also pass when `frappe.flags.pos_next_backdate_entry` is set (the marker is only
set by backdate code after `_check_backdate_access`). `reopen_shift`'s `.cancel()` of
the closing runs with `ignore_permissions` for the same reason.

R2. **`amended_from` cannot ride the payload.** It is in `_DRAFT_UNSETTABLE_FIELDS` and
`update_invoice` strips it. Requirement: after `submit_invoice` returns, set it
server-side with `frappe.db.set_value(dt, new_name, "amended_from", original)`
(the new invoice keeps its normal series name, not `-1`). Also append a Comment on
the original: "Corrected by {new} via {entry}".

R3. **Closing period end is not settable.** `make_closing_shift_from_opening` stamps
`period_end_date = now()` and `submit_closing_shift` rebuilds from it. Requirement:
extract the body of `submit_closing_shift` into
`_submit_closing(opening_shift_name, counted: dict, period_end_date=None, posting_date=None)`;
the whitelisted wrapper keeps current behaviour (None → now/today); the backdate
entry passes its closing datetime. The FOR UPDATE lock stays the first statement.

R4. **Posting time not validated.** `submit_backdate_invoice` checks the date only.
Requirement: the shared core checks `posting_date + posting_time` ∈
[shift `period_start_date`, now] and forwards `posting_time` (default `nowtime()` when
missing for back-compat with the old endpoint).

R5. **Package endpoints reject Sales Manager.** `packages._assert_profile_access`
passes on POS Profile User membership or POS Profile write (ERPNext grants write to
Accounts Manager only). Requirement: backdate exposes its own whitelisted wrappers
`get_backdate_packages(pos_opening_shift, posting_date)` and
`quote_backdate_package(pos_opening_shift, package, choices)` that run
`_check_backdate_access` and then call the internal `get_packages`/`quote` logic
(no access assert). `get_item_rate` likewise gates on backdate access, not profile
membership (items.`get_item_details` uses the same assert).

R6. **Return row link field differs per doctype.** Return items must carry
`sales_invoice_item` (Sales Invoice) or `pos_invoice_item` (POS Invoice) = the
original row name; `against_item_row` maps to the right one by the original's doctype.
Return payloads also carry `update_outstanding_for_self: 0` and the original's
`sales_team` (same as the old page).

R7. **Repost on rollback is safe.** Cancelling an invoice creates a `Repost Item
Valuation` row inside the transaction; it is only picked up by the scheduler
(`execute_repost_item_valuation`), so a rollback discards it. No extra handling;
the rollback test asserts no RIV row survives. No `frappe.db.commit()` exists in
the submit/closing path (only `queue.acquire_queue_number`, not called here).

R8. **Shift may hold both invoice doctypes.** `get_pos_invoices` reads POS Invoice and
Sales Invoice. The Shift Invoices table and `against_invoice` therefore store the
original's own doctype (`against_doctype`, Link DocType, hidden) — Dynamic Link
options point at it; new invoices use `get_pos_invoice_doctype()`.
Implementation deviation: `against_invoice`, `created_invoice` and
`cancelled_closing_shift` are Data fields, not (Dynamic) Link — a Link to a cancelled
closing or an amended original would block cancel/amend of those documents via
link checks. Doctype is kept in `against_doctype`/`invoice_doctype` and the form
renders them as links.

R9. **Correction and confirmation codes.** A corrected invoice is a new invoice, so
the discount gate runs again: if it carries a discount and the company's discount
switch is on, a (new) code is required. Rate below price list counts as a discount
(`_validate_item_rates`), same as the SPA.

R10. **Corrections blocked when unsafe.** Reject a Correction when the original:
has a submitted return; is itself a return; is a credit sale with a Payment Entry
or wallet redemption against it (cancel would cascade outside the shift). Message
tells HO to use Return + new Sale instead.

R11. **Patch numbering.** Latest patch is `v2_23_0.install_stock_opname_workflow`;
the page-removal patch is `v2_24_0.remove_backdate_entry_page`.

R12. **Translations.** `pos_next/translations/{id,ar,pt-br}.csv`; add every new
label/message at least to `id.csv` (ar/pt-br may reuse English per LOCALIZATION.md).

R13. **Invoice numbers and payment totals validation.** `_validate_structure` rejects
duplicate `invoice_no` in the Invoices table and verifies each invoice's sum of
payments equals its items grand total (`-total` for Return) within currency precision.

R14. **Correction rate copy without double-discounting.** `handle_correct` copies `rate`
from original row's `price_list_rate` (fallback `rate`) when keeping
`discount_percentage`, so server re-discount calculation produces the correct net rate.

R15. **Shift Company and Profile enforcement.** Entry `company` and `pos_profile`
must strictly match the opening shift's company and pos_profile.

R16. **Access and date precision on backdate helpers.** `pos_opening_shift_query` requires
`has_backdate_role()`; `get_return_items` checks backdate access on the invoice's profile
and exempts backdate callers from SEC-05 POS Profile User membership checks;
`quote_backdate_package` and `get_item_rate` accept and honor `posting_date`.

R17. **Precise reference check on linked documents.** Payment Entry Reference and
Wallet Transaction checks filter `reference_doctype = inv.against_doctype` and require
parent `docstatus == 1`.

R18. **Shift context payload.** `get_shift_context` returns the profile's warehouse
and company currency to eliminate synchronous DB calls and missing fields in Desk JS.

R19. **Preserve outer backdate flag.** `get_shift_context` preserves and restores
`prev_flag` in finally to prevent clobbering outer backdate requests.

## Removal / migration

- Delete page `pos_next/pos_next/page/backdate_entry/` (js, css, json).
- Sidebar `workspace_sidebar/posnext.json`: link → DocType `POS Backdate Entry`.
- Patch `v2_24_0`: delete the `Page` record `backdate-entry`; keep `rename_nexus_pos_manager_role` patch untouched.
- Permissions on the DocType: System Manager, POSNext Manager, Sales Manager, Accounts Manager (create/read/write/submit; no cancel/delete for submitted).
- `submit_backdate_invoice` / `reopen_shift` whitelisted endpoints stay (back-compat, tests) but delegate to the extracted cores.
- Translations: new labels/messages into `pos_next/translations/*.csv` (id at least).

## Error handling

- Gate failures → `PermissionError`.
- Domain failures (stock, price, code, window) → `ValidationError` with Invoice No. prefix; whole submit rolled back; entry stays Draft.
- Concurrent submit on the same shift → second one waits on the row lock, then fails "shift is not closed".
- Repost on rollback: resolved by R7 (test asserts no surviving RIV row).

## Testing

Backend (`pos_next/pos_next/doctype/pos_backdate_entry/test_pos_backdate_entry.py`, run via `_pn_run_tests.py`):
1. Gates: role, profile flag, non-closed shift; a Sales Manager-only user can submit end-to-end incl. re-close (R1) and package quote (R5).
2. Multi-invoice submit: 2 sales + 1 return → 3 invoices, old closing cancelled, one new closing submitted, posting date/time as entered, closing period_end as entered.
3. Closing counts: cash = old count, non-cash = expected; difference preserved.
4. Correction: wrong MOP fixed → original cancelled, amended invoice with `amended_from`, closing payments reflect new MOP; rejected when a return exists.
5. Package: parent + components created, Price Group price used.
6. Rollback: failing invoice #2 → no invoices, old closing still submitted, entry Draft.
7. Code gate: switch on requires code; switch off accepts without.
8. Both invoice doctypes (POS Invoice and Sales Invoice global setting).
9. Posting time outside the shift window (before start / future) rejected (R4).
10. Correction blocked cases (R10); `amended_from` + Comment set (R2).
11. `submit_closing_shift` whitelisted wrapper unchanged (existing closing tests green).
Existing `test_backdate_invoices.py` must stay green.

Frontend: form script is Desk JS; covered by an E2E pass in the browser on posnext.localhost (pick shift → return + correction + package sale → submit → check closing).

## Out of scope

Editing invoices outside a closed shift; cancelling an entry; bulk import; SPA (Vue) changes.
