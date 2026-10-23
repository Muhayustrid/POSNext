# Shift Group: one POS Profile per outlet, shifts as time slots

Date: 2026-10-23. Branch: `feat/shift-group`.

## Problem

Today a shift is modelled as a POS Profile: one outlet with a morning and an
afternoon shift needs two profiles (`SHIFT 1 - RS PKU ...` 05:00-15:00 and
`SHIFT 2- RS PKU ...` 13:00-21:00, same company, same warehouse). Sales,
returns and unpaid lists are scoped per profile, so each shift only sees its
own transactions. Opening asks a balance for every payment mode, closing asks a
counted amount for every mode.

## Goal

1. One POS Profile per outlet (company). Shifts are time slots defined once on
   a Shift Group and shared by any number of profiles.
2. Exactly one open shift per outlet; a shift past its end is force-closed.
3. Sales lists show every profile of the company, with a shift badge and a
   shift filter; returns across those profiles are allowed.
4. Opening asks only the cash float ("Modal Awal"); closing asks nothing and
   always uses the expected amounts.

## Decisions

| # | Decision |
|---|---|
| D1 | The existing `POS Profile Group` doctype becomes the Shift Group. It gains a child table `shifts` (new doctype `POS Shift Slot`): `shift_name` (Data, required), `start_time`, `end_time` (Time, required), `warning_minutes` (Int), `enforce_closing` (Check, default 1). |
| D2 | A shift may be opened only while the current time is inside its window (`start <= now < end`, site timezone). No early opening. Windows may overlap; overlap is the hand-over window. `end <= start` means the window crosses midnight. |
| D3 | Opening offers only the slots whose window contains now. If several match, the cashier picks; the default is the slot that started last. Outside every window opening is refused. |
| D4 | A slot may be opened again the same day after a closing, as long as it is still inside its window. |
| D5 | One open shift per POS Profile (any user). Opening while another shift is open is refused ("{slot} is still open by {user}; close it first"). Exception: if the open shift is past its own deadline, it is closed on the spot (`_submit_closing(auto_balance=True, auto_closed=True)`) and the new shift opens. The 30-minute server cron stays as the fallback. |
| D6 | The chosen slot is snapshotted onto `POS Opening Shift`: new field `shift_slot` (Data, the slot name) plus the existing `pos_schedule_*` fields (start, end, warning, enforce, deadline). Deadline, warning, tablet auto-close and server auto-close keep reading the snapshot, unchanged. |
| D7 | A profile whose group has no `shifts` rows keeps today's behaviour (single schedule from the profile fields, or no schedule). The profile-level `pos_schedule_*` fields stay for that fallback; the group no longer pushes them down when it has slots. |
| D8 | Sales lists (Invoice History in InvoiceManagement and InvoiceHistoryDialog, returnable invoices + invoice-number search in ReturnInvoiceDialog, Unpaid and Partial Payments lists + summaries) query every enabled-or-disabled POS Profile of the current profile's company. Access: member of the current profile, or management. Rows carry `pos_profile` and `shift_slot` (from the invoice's opening shift; empty for legacy rows). |
| D9 | Each row shows a badge: `shift_slot`, else the `pos_profile` name. A filter (All / one badge value) sits on each list; default All. Filtering is client-side over the fetched page. |
| D10 | Returns of any invoice of the same company are allowed (`_return_access_denied` widens from "member of the invoice's profile" to "same company as the open shift"). The return keeps today's mechanics: stock back into the current profile's warehouse, refund through the current shift, foreign payment modes remapped. |
| D11 | Offline caches stay keyed by the current profile; their content becomes the company-wide result. |
| D12 | Opening dialog: the title "Opening Balance" becomes "Modal Awal"; only cash modes are shown and editable. Non-cash modes are sent with `opening_amount = 0` (the rows are still needed for expected-per-mode at closing). |
| D13 | Closing never asks a counted amount: the SPA closing dialog drops every counted input and the server always sets `closing_amount = expected_amount` for SPA closings. The `auto_balance_closing` setting (POS Settings + Global Settings, store, tests) is removed. Server-side callers that pass explicit `counted` (POS Backdate Entry re-close) keep that path. |
| D14 | Migration patch (generic, no hard-coded names): for each company with more than one enabled POS Profile sharing the same warehouse, create one Shift Group holding one slot per profile (from that profile's schedule; slot name = the profile's current group name, else the profile name), keep the profile with the most opening shifts, move the other profiles' `POS Profile User` rows to it, and disable the others. A company with one enabled profile that has a schedule gets a group with one slot. A company with any open shift is skipped and logged; the patch is idempotent so it can run again. Old invoices stay on their old profile name. |

## Out of scope

- HQ reports / outlet targets per slot.
- Renaming the kept profile (`SHIFT 1 - RS PKU ...`); rename by hand in Desk if wanted (renaming rewrites links on every old transaction).
- Drafts (IndexedDB, owner-scoped), Shift History, Reject Request.

## Data on dev (copy of production, 2026-10-23)

| Profile | Company | Group | Window | Shifts |
|---|---|---|---|---|
| SHIFT 1 - RS PKU MUHAMMADIYAH DELANGGU | RS PKU MUHAMMADIYAH DELANGGU | SHIFT 1 | 05:00-15:00 | 20 |
| SHIFT 2- RS PKU MUHAMMADIYAH DELANGGU | same | SHIFT 2 | 13:00-21:00 | 11 |
| TRAINING | TRAINING | ALL TIME | 00:00-23:59:59 | 15 |
| Outlet Pameran | Outlet Pameran | - | none | 1 |

Expected patch result: one group for RS PKU with slots `SHIFT 1` 05:00-15:00
and `SHIFT 2` 13:00-21:00, `SHIFT 1 - RS PKU ...` kept, `SHIFT 2- RS PKU ...`
disabled; TRAINING gets a one-slot group; Outlet Pameran untouched.

## Risks

- Closing at expected means cash shortages are never recorded at closing.
- Existing groups `SHIFT 1` / `SHIFT 2` / `ALL TIME` belong to company
  `PT. JUARA ROTI INDONESIA` and are shared; the patch creates new groups and
  leaves the old ones in place (unused after the move).
- A cross-company return stays refused; a same-company return of a legacy
  profile's invoice puts stock into the kept profile's warehouse (same
  warehouse today for RS PKU).

## Tests

- Backend: slot resolution (inside / outside / overlap / midnight), one-open
  rule incl. on-the-spot close of an expired shift, snapshot fields, company
  scope on each list endpoint + foreign company denied, same-company return of
  another profile's invoice, closing always equals expected, patch on fixture
  data (merge, single profile, skip on open shift, idempotent).
- Frontend: opening shows only cash + "Modal Awal" and slot picker; closing has
  no inputs; badges + filter on the lists.
