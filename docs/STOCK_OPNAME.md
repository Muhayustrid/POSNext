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

The app installs a workflow named `POS Next Stock Opname` on install and on
every `bench migrate`, but only when the site has no **active** Stock
Reconciliation workflow (and none already named `POS Next Stock Opname`). A site
running its own active Stock Reconciliation workflow keeps it — POS Next adapts
to whatever workflow is active (see *Changing the workflow*). The active
workflow governs **all** Stock Reconciliations on the site, including ones
created directly in Desk.

Outlets may backdate a count up to **Stock Opname Max Backdate Days** days in the
past. The setting lives on **POS Next Global Settings** (default 2). A blank or
`0` value falls back to the default of 2, so the cap cannot be narrowed to
same-day-only from this setting.

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

  An Accounts Manager who only *reviews* a batch/serial opname at the AM stage
  also needs **Stock User** just to open it: the form reads the Serial and Batch
  Bundle, so without it Desk shows a `PermissionError` before Approve is even
  reached.

A user who should only count — not approve — needs just **POSNext Cashier** (or
POSNext Manager). Outlet roles get read/write/create/delete on Stock
Reconciliation and read/write/create on Serial and Batch Bundle, but **no
submit**, so they cannot push a count past the approval stages.

Outlet-only users (no HQ role) can only see and edit Stock Reconciliations whose
warehouse is the warehouse of a POS Profile they are assigned to.

## Outlet flow (POS app)

The screen is a Desk-style list → document: a list of the warehouse's counts,
then a full document page.

1. **Stock Opname** in the menu (group *Stock & Supply*, online only). Click a
   list row (or **New Count** / **Continue**) to open the document page.
2. The page shows **Details**, **Items**, **Attachments** and an **Activity**
   timeline. Add counted items with **Add Row** in the item grid — type or scan
   an item code and press Enter on an exact code/barcode match. Each batch is one
   row (add the same item again for another batch); serial items tick the serials
   currently in the warehouse. You can also **download the Excel template**, fill
   it in and upload it (see below).
3. **Save** keeps a Draft. Only counted items become rows — uncounted items are
   left untouched (they are never zeroed).
4. The posting date/time defaults to the moment New Count was pressed. Tick
   **Edit Posting Date and Time** to change it (within the backdate cap, see
   *Setup*).
5. The page has **one primary button**: **Save** while the count is new or has
   unsaved edits, then the workflow action (e.g. **Submit**) once it is saved.
   Submitting moves the document to the first approval stage, keeping an
   outlet-set posting date or stamping *now* when it was left at the default.
   **Delete** lives in the **⋯** menu of a saved, editable count; every
   destructive step asks for confirmation.

The list shows each opname's ID, status, posting date and item count, with the
reject reason under returned rows. A count is editable from POS only while it is
in the workflow's **initial** state, or in a **returned** state if your role may
send it back to the initial state; approval stages are read-only. An Approved
opname that is still posting (large counts, see below) shows **Processing**.

One warehouse can have only one open opname at a time: starting a new count while
a Draft exists is refused until the previous one is submitted or deleted.

## HQ flow (Desk)

Approval happens in the standard Stock Reconciliation form (no custom page). The
workflow buttons drive the stages:

1. **Stock Manager** reviews the count and presses **Approve** (or **Reject**).
2. **Accounts Manager** gives the final **Approve** (or **Reject**).

A **Show System Qty** button on the form shows the live system qty and
difference per row while the count is still open, so approval is not blind
(see below). It is display only.
3. On final approval the document is submitted: stock is adjusted, Stock Ledger
   Entries are written and the GL is posted against the Company's stock
   adjustment account.

Rejecting requires a reason. It is stored as a Comment on the document and the
outlet sees the latest reason on the Rejected opname.

While a count is still a Draft, the Desk **current qty** column reads **0** —
the app's override skips ERPNext's no-change filter, the only place a draft
would otherwise be filled with the system quantity — so a draft never exposes
the real on-hand quantity. The **valuation rate** is filled, and the
**difference** column shows the counted value (counted qty × rate, because the
current side is 0), not the true system difference. The real system qty and
difference appear only on submit.

Because of that, HQ approves blind unless it asks. The **Show System Qty**
button on the form (visible only to Stock Manager, Accounts Manager or System
Manager, and only while the count is a Draft, Rejected or pending) opens a
read-only table of counted qty, live system qty, the difference and the value
difference per row. The numbers are computed with the same helper submit uses,
so they match what approval will post; nothing is saved, and outlet users never
see them.

The **POSNext** workspace shows two shortcuts with pending counts, one per
approval stage.

## Workflow states and transitions

Workflow `POS Next Stock Opname` (the one the app installs), on doctype `Stock
Reconciliation`, driven by the `workflow_state` field.

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

## Changing the workflow

POS Next does not hardcode state or action names. The initial state, which
states are *returned*, which are editable, the submit actions and the
**Processing** flag are all derived from the **active** Stock Reconciliation
workflow. Admins may rename, add or remove states and actions, as long as the
shape stays recognisable:

- The **first** state listed is the *initial* state — where a new count starts,
  and the state outlets edit and submit from.
- A **returned** state is a docstatus-0 state, other than the initial one, that
  has a transition back to the initial state. The outlet may edit a returned
  count only if their role is allowed on that back-to-initial transition; saving
  it applies that transition first (a generic "revise").
- Outlet roles must be allowed on the submit transitions out of the initial
  state, otherwise the outlet is told "you are not allowed to submit this count,
  ask HQ".
- **Approval stages** (docstatus-0 states with no transition back to initial) are
  read-only from POS — not editable, deletable or importable.
- Approval must end in a **docstatus-1** state. A count shows **Processing**
  while it is docstatus 0 in a state whose workflow doc status is 1 (the
  background submit of large counts).
- The Desk reject-reason prompt appears on **any** transition into a returned
  state (not only one named "Reject"); the outlet sees the latest reason while
  the count is in that state, and HQ free-text comments are shown to the outlet
  in the **Activity** timeline.

With **no active workflow**, listing, saving and deleting still work; submitting
fails with *No active workflow for Stock Reconciliation; ask HQ to activate one*.

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
