# Shift Schedule (POS Profile / Shift Group)

Optional shift hours for POS outlets, configured by HQ, enforced by the
server. Shift hours are normally managed once per **Shift Group** and pushed
to its member POS Profiles; per-profile settings still work. Existing
profiles keep whatever was configured before — nothing is mass-enabled.

## HQ Setup

### Shift Group (Desk → POSNext → Shift Group, doctype `POS Profile Group`)

One group per outlet shift pattern: a name, the company/outlet (e.g. a PT
Juri child outlet), the shift hours, and the member POS Profiles.

| Field | Meaning |
|---|---|
| Shift Group | Group name (also the document name). |
| Company | Every member profile must belong to this company — cross-company members are rejected on save. |
| Enable Shift Schedule | Master switch pushed to members. On by default. |
| Shift Start Time / Shift End Time | Shift hours in the **site timezone**. An end earlier than the start runs past midnight (e.g. 05:00 → 12:00, or 21:00 → 05:00). Required while enabled — a group (or profile) saved enabled without valid times is rejected. |
| Schedule Warning Minutes | Members' cashiers get a toast this many minutes before the end. 0 = no warning. |
| Enforce Closing After Schedule | When on, the schedule deadline is **hard** for members (see below). When off, advisory only. |
| POS Profiles (Members) | The profiles that follow these hours, filtered to the group's company in the picker. A profile can only be in one group — adding a member of another group is rejected (remove it there first). |

**Saving the group syncs every member**: each member profile gets the group's
enabled/time/warning/enforce values (a profile can still be edited
afterwards, until the next group save). **Removing** a profile from the list
only unlinks it — it **keeps its last synced hours** (no silent loss); re-add
it or edit its profile to change them. Groups are saved in one transaction:
any failure (e.g. another group still holding a profile) aborts the whole
save.

**Open shifts are never moved**: the schedule is snapshotted onto the POS
Opening Shift at open time, so group edits only apply from the next session.

### Per profile (Desk → POS Next → POS Profile)

| Field | Meaning |
|---|---|
| Shift Group | Membership — **read-only mirror**: membership lives in the group's Members table, and a profile whose link has no matching member row is rejected on save (it could never receive the group's hours). |
| Enable Shift Schedule | Master switch. **On by default for new profiles** — give them a Shift Group or set times, otherwise saving fails with a clear validation error. Existing profiles keep their current value. |
| Shift Start Time / Shift End Time | Shift hours in the **site timezone**. An end time earlier than the start time runs past midnight (e.g. 21:00 → 05:00). Required when the schedule is enabled — enabling it with blank/invalid times or a negative warning value is rejected on save/open. |
| Schedule Warning Minutes | Cashier gets a toast this many minutes before the end. 0 = no warning; negatives are rejected. |
| Enforce Closing After Schedule | When on, the schedule deadline is **hard**: see below. When off, the schedule is advisory (warning only). |

## What is enforced (server-side, site timezone, server clock)

- **Opening** — with Enforce Closing on, a shift cannot be opened outside the
  schedule window, and an expired scheduled shift cannot be reopened.
- **Snapshot lifecycle** — the schedule (and computed deadline) is snapshotted
  onto the POS Opening Shift at insert and re-resolved authoritatively at
  submit (a draft held past the enforced hours cannot be submitted). Once the
  shift exists, any save discards client edits to the snapshot fields; the
  deadline can only be moved by a System Manager through the audited,
  forward-only API (works from Desk console, `bench --site <site> execute`, or
  an authenticated HTTP call):

  ```
  POST /api/method/pos_next.shift_schedule.extend_deadline
       {"opening_shift": "POSA-OS-0001", "new_deadline": "2026-09-08 01:00:00"}
  ```

  It validates that the shift actually has a mandatory deadline, refuses any
  value that is not after the current deadline, writes the new deadline and a
  Comment audit entry, and is the **only supported way** to extend a running
  shift (profile/group edits never move an existing shift's deadline). This is
  an administrator/technical recovery action — there is no dedicated UI button
  or screen for it.
- **After the deadline (Enforce Closing on)** — new sales, payments (incl.
  partial payments / credit redemption) and refunds are rejected by the
  server on every submit path, online or offline replay. Refreshing or
  logging in again cannot bypass this. An invoice submitted with an emptied
  opening-shift field is gated against the profile's latest open shift, so
  the field cannot be used to dodge the deadline.
- **Closing is always allowed** — the cashier closes the shift through the
  normal POS Closing Shift flow and counts actual cash. Nothing is
  auto-submitted; no financial documents are created automatically.
- **Already-submitted transactions** are never touched — only new submits at
  or past the deadline (server clock) are refused.

## Exact acceptance boundary

Acceptance is decided by the **server clock at the moment the submit is
validated**. The POS never tears down a dialog or cancels a request that is
already in flight: if the deadline passes while a checkout/return/partial
payment is submitting, the request settles normally — the server accepts it
if validation ran before the deadline, otherwise it rejects it with the
shift-schedule message and the invoice simply remains a draft (nothing is
lost; the cashier closes the shift and records the cash). A physically
accepted card/cash payment whose submit has **not yet started** when the
deadline passes is refused — that is the boundary.

## POS UI behavior

- Warning toast N minutes before the end.
- When the deadline passes with Enforce Closing on, the Shift Closing dialog
  is forced open on top of anything else (online). Sales/returns can no
  longer be started, but open dialogs are left alone until the cashier
  dismisses them.
- Offline, sales are still refused (fail closed) and the closing dialog
  appears once connectivity returns — closing is a server call. While a
  mandatory schedule is active, offline checkout is disabled proactively so
  no new invoice can enter the queue and be rejected at sync time; the
  existing queue is never discarded.
- Offline invoices queued **before** the deadline are preserved in the queue;
  the server rejects their replay past the deadline and they stay queued.
  Recovery: a System Manager extends the shift deadline via
  `pos_next.shift_schedule.extend_deadline` (see above), then the cashier
  syncs again from Management → Offline Invoices.

## Tests

- Backend: `pos_next/test_shift_schedule.py` and
  `pos_next/test_pos_profile_group.py` — run with
  `./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py pos_next.test_shift_schedule pos_next.test_pos_profile_group`
- Frontend: `POS/src/utils/shiftSchedule.test.js` — run with
  `cd POS && npx vitest run src/utils/shiftSchedule.test.js`

## Panduan HQ (Bahasa Indonesia)

Lihat [SHIFT_SCHEDULE_ID.md](SHIFT_SCHEDULE_ID.md) untuk panduan singkat
dalam Bahasa Indonesia.
