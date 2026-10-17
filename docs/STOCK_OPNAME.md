# Stock Opname

Stock Opname lets an outlet count its physical stock in the POS app and submit
the count as a **draft** ERPNext `Stock Reconciliation`. Head office (HQ)
reviews and approves it in Desk through a Frappe Workflow. Only approval moves
stock and posts the accounting entries — a submitted-but-unapproved count never
touches the books.

The count is **blind**: the outlet never sees the system quantity, the
valuation rate or the difference. It only enters how many units are on the
shelf.

- One opname = one warehouse = the outlet's POS Profile warehouse.
- Batch and serial items are supported.
- The count can be entered on screen or uploaded as an Excel sheet.
- Counting does **not** block sales; sales continue while a count is open.

## Setup

The workflow is installed by the app on install and on every `bench migrate`
(idempotent; it never overwrites a workflow of the same name). It governs **all**
Stock Reconciliations on the site, including ones created directly in Desk.

Approval needs roles that ERPNext does not grant by default. Configure the
approver users before the first count:

- **Stock Manager stage** — the approver needs the **Stock Manager** role. This
  role already has full Stock Reconciliation and Serial and Batch Bundle access,
  so nothing else is required.
- **Accounts Manager stage (final approval)** — the approver needs:
  - **Accounts Manager** — this is the only role that may submit the document
    (pos_next grants it read/write/submit on Stock Reconciliation). **Accounts
    Manager alone is not enough**: it has no permission on `Item`, so opening
    and approving the document fails.
  - **Accounts User** — grants the `Item` read that the form and submit need.
  - **Stock User** — **only required when the opname contains batch or serial
    rows.** On submit ERPNext builds a `Serial and Batch Bundle`, and Accounts
    Manager / Accounts User have no permission on that doctype; approval then
    fails with a `PermissionError`. Stock User (or Stock Manager) supplies it.

  In short, an Accounts Manager approver who handles batch/serial counts should
  hold **Accounts Manager + Accounts User + Stock User**.

A user who should only count — not approve — needs just **POSNext Cashier** (or
POSNext Manager). Outlet roles get read/write/create/delete on Stock
Reconciliation and read/write/create on Serial and Batch Bundle, but **no
submit**, so they cannot push a count past the approval stages.

Outlet-only users (no HQ role) can only see and edit Stock Reconciliations whose
warehouse is the warehouse of a POS Profile they are assigned to.

## Outlet flow (POS app)

1. **Stock Opname** in the menu (group *Stock & Supply*, online only).
2. **New count** — either:
   - enter items by hand: search an item, type the counted quantity; batch items
     pick from the item's enabled batches (one row per batch); serial items are
     ticked from the serials currently in the warehouse; or
   - **download the Excel template**, fill it in, and upload it (see below).
3. **Save** keeps a Draft. Only counted items become rows — uncounted items are
   left untouched (they are never zeroed).
4. **Submit** stamps the posting date/time as *now* and moves the document to
   *Approval Pending by Stock Manager*.

The list shows each opname's name, posting date, state and item count. Drafts and
Rejected opnames can be opened, edited and deleted. Pending and Approved opnames
are read-only. An Approved opname that is still posting (large counts, see
below) shows **Processing**.

One warehouse can have only one open opname at a time: starting a new count while
a Draft exists is refused until the previous one is submitted or deleted.

## HQ flow (Desk)

Approval happens in the standard Stock Reconciliation form (no custom page). The
workflow buttons drive the stages:

1. **Stock Manager** reviews the count and presses **Approve** (or **Reject**).
2. **Accounts Manager** gives the final **Approve** (or **Reject**).
3. On final approval the document is submitted: stock is adjusted, Stock Ledger
   Entries are written and the GL is posted against the Company's stock
   adjustment account.

Rejecting requires a reason. It is stored as a Comment on the document and the
outlet sees the latest reason on the Rejected opname.

While a count is still a Draft, HQ sees **blank current qty and difference**
columns in Desk — ERPNext fills them only on submit. This is deliberate: the
columns stay empty so nobody can infer the system quantity from a draft.

The **POSNext** workspace shows two shortcuts with pending counts, one per
approval stage.

## Workflow states and transitions

Workflow `POS Next Stock Opname`, on doctype `Stock Reconciliation`, driven by
the `workflow_state` field.

| State | docstatus | Who may edit |
|---|---|---|
| Draft | 0 | POSNext Cashier, POSNext Manager, Stock Manager |
| Approval Pending by Stock Manager | 0 | Stock Manager |
| Approval Pending by Accounts Manager | 0 | Accounts Manager |
| Rejected | 0 | POSNext Cashier, POSNext Manager, Stock Manager |
| Approved | 1 | Accounts Manager |
| Cancelled | 2 | Stock Manager |

| From | Action | To | Allowed | Self approval |
|---|---|---|---|---|
| Draft | Submit | Approval Pending by Stock Manager | POSNext Cashier, POSNext Manager, Stock Manager | yes |
| Approval Pending by Stock Manager | Approve | Approval Pending by Accounts Manager | Stock Manager | no |
| Approval Pending by Stock Manager | Reject | Rejected | Stock Manager | no |
| Approval Pending by Accounts Manager | Approve | Approved | Accounts Manager | no |
| Approval Pending by Accounts Manager | Reject | Rejected | Accounts Manager | no |
| Rejected | Revise | Draft | POSNext Cashier, POSNext Manager, Stock Manager | yes |
| Approved | Cancel | Cancelled | Stock Manager | yes |

"Self approval: no" means the person who created the document cannot approve or
reject it — a second person must act.

## Excel template

The template is generated per POS Profile (optionally filtered to an item group
and its descendants). It lists every enabled stock item with a Bin in the
warehouse; batch items appear once per batch currently holding stock there, and
serial items once per serial currently in that warehouse. Columns:

`Item Code | Item Name | UOM | Batch No | Serial No | Counted Qty`

There is **no system quantity** column — the sheet is blind like the screen.

Rules on upload:

- **Blank Counted Qty** = not counted; the row is ignored.
- **0** = counted, none present.
- For **serial** items the value is a presence flag: **1 = present, 0 = absent**.
- The upload **merges** into the open draft: a row in the file overwrites the
  matching draft row; draft rows not in the file are kept. This lets several
  people count different shelves into one opname.
- Bad rows are reported per row without aborting the upload.
- If **every serial** of a serial item is marked absent, the item is recorded as
  counted with quantity 0; on approval that item's serials are zeroed.

Uploads are merged server-side and **do not** run the stale-save check, so an
upload always applies to the current draft. (On-screen saving a draft that
changed since it was loaded is refused with a "reload" message.)

## When to count

The posting date/time is the moment the outlet presses **Submit**, and approval
later does not change it — ERPNext reposts any sales made after that time.
Because sales are not blocked during a count, counting while the outlet is
trading can make the count miss items sold between counting and submitting.

**Count outside selling hours** (before opening or after closing), and submit
the count straight away, so the counted quantity reflects the shelf at the
posting moment.

## Cancel and amend

A count cannot be cancelled from the POS app. Corrections are done in Desk:

1. A **Stock Manager** cancels the Approved opname via the workflow **Cancel**
   transition — stock and GL are restored to their pre-count values.
2. Amend the cancelled document, fix it, and resubmit it through the workflow.

## Large counts

An opname with more than 100 rows is submitted in the background by ERPNext. The
document moves to Approved immediately but stock and GL post shortly after; the
POS list shows **Processing** until the background submit finishes.

## Out of scope (v1)

Email notifications; printing from the POS app; blocking sales during a count;
creating batches or serials from the POS app; a POS approval screen; offline
counting.
