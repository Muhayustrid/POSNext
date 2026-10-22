# Reject Request on a Frappe Workflow — Design

Date: 2026-10-22
Status: decisions approved in chat; written for review.

## Goal

Match the real outlet process and run approvals on a native Frappe Workflow,
the same pattern as Stock Opname (`pos_next/stock_opname_setup.py`):

1. Crew files a reject request (items, reason, evidence photos).
2. HO approves → a Berita Acara (PDF) with a unique approval code is issued.
3. Crew sends destruction proof (video link and/or photos).
4. HO approves the proof → stock leaves the outlet (Material Issue).

Either HO step can return the request to the crew with a reason.

## Decisions

| # | Decision |
|---|----------|
| D1 | Native Frappe Workflow `POS Next Reject Request` on `POS Reject Request`, state field `workflow_state`. Installed idempotently from after_install / after_migrate + a patch; never displaces an admin's own active workflow on the doctype (same rule as opname). |
| D2 | One approval tier: role `POSNext Reject Approver` (existing). No self approval (owner ≠ approver), enforced by the workflow AND by a server guard (REST saves bypass `apply_workflow`). |
| D3 | No Reject Warehouse, no transfer. Stock moves once: Material Issue from the POS Profile warehouse to the company `stock_adjustment_account` on **Completed**. System purposes (Production Loss) never move stock (unchanged). |
| D4 | Submit (docstatus 1) happens on HO's first approval and only locks the doc. It never touches stock. |
| D5 | Liability stays: on first approval HO sets `borne_by` = Company or Crew; Crew → `liabilities` rows (default: owner, full amount; must sum to total). Replaces the old `decision` Approved/Rejected. |
| D6 | Approval code: random 8-char code from `confirmation_code.CODE_ALPHABET`, unique on the doctype, generated on first approval, never changes. Printed with approver name + date. |
| D7 | Berita Acara: standard Jinja print format `Berita Acara Reject` (default for the doctype), letter head = company default Letter Head. Downloadable as PDF from Desk and the SPA once approved. |
| D8 | Item rows gain `production_date` (Date, optional, ≤ incident date) and `reject_reason` (Small Text, optional; print falls back to the doc `reason`). |
| D9 | Strict late mode (`POS Reject Settings.late_mode`): a late request still needs approval, but `borne_by` is forced to Crew (was: auto-Rejected by the system). |
| D10 | `workflow_state` is the single status. The `status` and `decision` fields are removed from the doctype (DB columns stay, unused); a patch maps old data. |
| D11 | Return needs a reason. Desk: a prompt before the action calls `record_return_reason`, which stores `return_reason` (shown to the crew) and writes a `Returned: <reason>` comment. The crew's next Submit / Send Proof clears it. Approve in Desk first prompts for `borne_by` (+ crew liabilities) and saves them via `set_reject_liability`. |
| D13 | `letter_head` field on the doctype, filled from the company's `default_letter_head` on validate, so Desk Print and the SPA PDF use the company letter head automatically. |
| D12 | Stock is re-checked at Completed (it is not reserved between steps). Insufficient stock → clear error; HO may Cancel instead. |

## States and transitions

| State | doc_status | Edit role |
|---|---|---|
| Draft | 0 | POSNext Cashier |
| Pending Approval | 0 | POSNext Reject Approver |
| Awaiting Destruction | 1 | POSNext Cashier |
| Destruction Submitted | 1 | POSNext Reject Approver |
| Completed | 1 | POSNext Reject Approver |
| Cancelled | 2 | POSNext Reject Approver |

| From | Action | To | Allowed | Self |
|---|---|---|---|---|
| Draft | Submit | Pending Approval | POSNext Cashier, POSNext Manager | yes |
| Pending Approval | Withdraw | Draft | POSNext Cashier, POSNext Manager | yes |
| Pending Approval | Approve | Awaiting Destruction | POSNext Reject Approver | no |
| Pending Approval | Return | Draft | POSNext Reject Approver | no |
| Awaiting Destruction | Send Proof | Destruction Submitted | POSNext Cashier, POSNext Manager | yes |
| Awaiting Destruction | Cancel | Cancelled | POSNext Reject Approver | no |
| Destruction Submitted | Complete | Completed | POSNext Reject Approver | no |
| Destruction Submitted | Return | Awaiting Destruction | POSNext Reject Approver | no |
| Destruction Submitted | Cancel | Cancelled | POSNext Reject Approver | no |

Completed is final (stock already issued). State names are canonical English
and never translated in data; UI translates them via `id.csv`.

## Side effects per transition (server, one place)

A single service hook compares the state before/after save and runs:

- → Pending Approval: block if any item (non-system purpose) exceeds stock in
  the profile warehouse; require 1–5 evidence photos; recompute `is_late`,
  `over_limit`; Strict + late → `borne_by` = Crew; notify approvers.
- → Awaiting Destruction: require `borne_by`; default/validate liabilities;
  set `approval_code`, `decided_by`, `decided_on`; notify owner.
- → Draft / → Awaiting Destruction via Return: notify owner.
- → Destruction Submitted: require the existing `video_link` (now
  `allow_on_submit`, editable only in Awaiting Destruction) or ≥1
  `destruction` attachment; notify approvers.
- → Completed: non-system → Material Issue (`issue_entry`), stock re-check
  (D12); `closed_on`; notify owner.
- → Cancelled: nothing to reverse for new docs; legacy docs cancel their
  transfer (existing `on_cancel` logic).

Field locks (server guard, not only Desk): items/purpose/incident/reason
editable only in Draft; `borne_by`/`liabilities` writable only in Pending
Approval by an approver; `approval_code`, `decided_*`, `issue_entry`,
`closed_on` server-only.

`_month_total` (over-limit) counts every non-Draft, non-Cancelled request in
the month (was docstatus 1 only, which would now miss Pending Approval).

## Legacy data (patch)

Dev site today: 1 request (Rejected, docstatus 1, with a transfer). The
mapping must still cover every old status for the cloud site.

| Old status | docstatus | → workflow_state | Notes |
|---|---|---|---|
| Draft | 0 | Draft | |
| Pending | 1 | Pending Approval | stays docstatus 1; can be Approved (save path) or cancelled via Desk Cancel (reverses transfer). Return/Withdraw throw Frappe's illegal-docstatus error — acceptable for the few legacy rows. |
| Approved / Rejected | 1 | Awaiting Destruction | `borne_by` = Company / Crew; approval code generated |
| Closed | 1 | Completed | |
| Cancelled | 2 | Cancelled | |

Legacy Completed issue: if `transfer_entry` is set, issue from that transfer's
target (reject warehouse, existing `_write_off_reject_stock`), else from the
profile warehouse.

## Surfaces

- API (`pos_next/api/reject_request.py`): `save_reject_request(data, submit)`
  submits via workflow action `Submit`; new `apply_reject_action(name, action)`
  for crew actions (Withdraw, Send Proof); `save_reject_video`;
  `set_reject_liability` + `record_return_reason` (Desk approver prompts);
  payload adds `workflow_state`,
  `actions`, `return_reason`, `approval_code`, `borne_by`. Removed:
  `decide_reject_request`, `close_reject_request`, `cancel_reject_request`.
- Desk: decide buttons removed; workflow buttons + return prompt; list by
  workflow state.
- SPA `RejectRequestDialog.vue`: state pills/tabs by workflow state, per-row
  production date + reason, return-reason banner, destruction proof +
  Send Proof, Berita Acara PDF button. Item entry uses the Stock Opname row
  model (Add Row → per-row item search, UOM, qty) instead of one search box;
  the dialog gets the Stock Opname look plus a workflow step indicator.
- Report `POS Reject Report`: filter `borne_by` instead of `decision`;
  status column = workflow state.
- `POS Settings.reject_warehouse`: no longer used; left in place (no schema
  churn), description marks it legacy.

## Out of scope

Multi-tier approval (editable later in the Workflow, no code), stock
reservation between Submit and Completed, cancelling a Completed request.
