# POS Reject Request v2 — Approved/Rejected, configurable purpose, liability, report

Date: 2026-10-12. Builds on `2026-10-11-pos-reject-request-design.md` (merged to local main at 569bc33, not pushed).
Scope: wave 1 only. Deferred to separate specs: "request more evidence" decision (wave 2), crew dispute/appeal (wave 3), automatic employee-receivable Journal Entry (option B).

## Goal

UAT feedback: three decisions (Write Off / Charge Crew / Rejected) confuse operators, the purpose is hardcoded, the Desk sidebar has no entry, there is no way to see who bears which loss, and production operators cannot see the state of their loss reports.

Success: HO decides with exactly two buttons; who bears every loss (company or named crew, with amounts) is visible and exportable; HO maintains the purpose list without code; production operators see and act on loss-report status from the POS.

## Decisions (ruled with user 2026-10-12)

| # | Ruling |
|---|---|
| D1 | Only two decisions: **Approved** = company bears the loss; **Rejected** = crew bears the loss. Old "Rejected" (return stock to outlet) is removed — no return-to-outlet path remains. |
| D2 | Stock movement is identical for both decisions: submit moves goods outlet → reject warehouse (non-system purposes); Close issues them out (Material Issue to company `stock_adjustment_account`). No "Approved" warehouse. |
| D3 | Crew liability is recorded only (option A): a liability table on the request. No accounting entry beyond the Material Issue. Data shape must allow a later Journal Entry per liability row (option B). |
| D4 | On Rejected, HO picks the liable crew — one or many — with amounts. Table prefilled with the requester at full amount; adding rows splits evenly; amounts editable; server enforces sum == `total_amount`. |
| D5 | Purpose is a master DocType maintained by HO. Cashier picks from enabled, non-system purposes. "Production Loss" is a system purpose: not deletable, not pickable, created only by the system. |
| D6 | Monthly limit counts both Approved and Rejected (both are lost goods). Drafts and Cancelled excluded as today. |
| D7 | Strict late mode auto-decides **Rejected**, liability = requester (owner) at full amount. |
| D8 | Production Loss: draft still auto-created (anti-manipulation, unchanged). POS ProductionDialog history shows the request status per Work Order with an "Ajukan" (Submit report) action that opens the draft in RejectRequestDialog. |
| D9 | Existing UAT data is deleted manually on `production.localhost` and `posnext.localhost` (with user confirmation at that time). No migration patch. |

## Data model

### New DocType: POS Reject Purpose
- `purpose_name` (Data, unique, autoname `field:purpose_name`), `enabled` (Check, default 1), `is_system` (Check, read-only).
- Install/migrate creates `Unsold` (is_system 0) and `Production Loss` (is_system 1) if missing (idempotent, in `after_install` + an `after_migrate`-safe helper, same pattern as existing role setup).
- Controller: `on_trash` throws for `is_system`; `validate` blocks clearing `is_system` or renaming a system purpose.
- Perms: System Manager full; POSNext Reject Approver read+write+create; outlet roles read.
- Desk sidebar: "Reject Purpose" under Configuration.

### POS Reject Request changes
- `purpose`: Select → Link "POS Reject Purpose".
- `decision`: options `\nApproved\nRejected`.
- `status`: options `Draft\nPending\nApproved\nRejected\nClosed\nCancelled` (unchanged list; meaning of Rejected changes to "decided, crew liable, awaiting close").
- `video_link`: Data with `options: "URL"` (clickable in Desk read-only; verify render on installed Frappe, else formatter in `pos_reject_request.js`).
- New table `liabilities` (child DocType **POS Reject Liability**): `user` (Link User, reqd), `amount` (Currency, reqd). Server-owned lifecycle field — cleared in `validate` like other server fields (C1 rule), written only by the service via `db_set`/child insert under `ignore_permissions`.

### Validation rules (service)
- Crew lane (`save_reject_request`): `purpose` must exist, enabled, and `is_system == 0`. Remove the forced `"Unsold"`.
- System lane: `is_system_generated` docs must have the system purpose (Production Loss); manual creation of a system purpose throws (existing D6 guard, now keyed on `is_system` instead of literal name).
- Submit stock transfer: skipped iff purpose `is_system` (replaces `purpose == "Production Loss"` checks).

## Service changes (`pos_next/services/reject_request.py`)

- `DECISIONS = ("Approved", "Rejected")`.
- `apply_decision(doc, decision, notes=None, liabilities=None, system=False)`:
  - Rejected: notes required; `liabilities` list of `{user, amount}`; default `[{owner, total_amount}]` when None; each user must be enabled; amounts > 0; `abs(sum - total_amount) <= precision tolerance`; duplicate users rejected.
  - Approved: liabilities must be empty.
  - Sets `decision`, `status = decision`, `decided_by/on`, writes liabilities; segregation-of-duties (approver ≠ owner) unchanged.
- `close_request`: allowed when status in (Approved, Rejected); always `_write_off_reject_stock` for non-system purposes; nothing for system purpose. Delete `_return_to_outlet`.
- Strict auto path → `apply_decision(doc, "Rejected", _("Auto: past deadline (Strict mode)"), system=True)` (liability default = owner).
- `_month_total`: drop `status != 'Rejected'`.
- Notifications text updated for two decisions.

## API changes (`pos_next/api/reject_request.py`)
- `save_reject_request`: accept `purpose`.
- `decide_reject_request(name, decision, notes=None, liabilities=None)` (JSON list).
- Destruction-photo attach gate: status in (Approved, Rejected).
- New `get_reject_purposes()` → enabled non-system purposes (for POS dropdown).
- `get_production_history` rows gain `reject_request` and `reject_status` (one extra query over the page's Work Orders, no N+1) for the ProductionDialog badges. No new endpoint.

## Desk
- `pos_reject_request.js`: two buttons. Approved → confirm dialog, optional notes. Rejected → dialog with notes (reqd) + Table field (user, amount) prefilled with owner/total; client shows running total vs `total_amount`; server is the authority.
- Sidebar `pos_next/pos_next/sidebar/posnext/posnext.json`: "Reject Request" (DocType) under Head Office; "POS Reject Report" (Report) under Report; "Reject Purpose" under Configuration. Also add the same three links to the workspace `pos_next/pos_next/workspace/posnext/posnext.json` (Configuration and Reports cards).

### Script Report: POS Reject Report
- Filters: from_date, to_date (incident_date), pos_profile, purpose, decision, user (liable crew), view (`Per Request` | `Per Liability`).
- Per Request columns: request, incident date, outlet, purpose, status, decision, total amount, liable crew (comma list), decided by, closed on.
- Per Liability: one row per liability row (user, amount) + one row per Approved request with bearer "Company" and total amount.
- Summary cards: total borne by company, total borne by crew, request count. Chart: bar per outlet stacked company vs crew.
- Only submitted docs (docstatus 1). Export via Frappe built-in.
- Roles: System Manager, POSNext Reject Approver.

## POS frontend
- `RejectRequestDialog.vue`: purpose dropdown in New view (from `get_reject_purposes`, default first); detail view shows decision Approved/Rejected + liability list when Rejected; video link input gets an "open ↗" anchor next to it when the URL is valid; close/destruction step enabled for both decided statuses; remove any return-to-outlet wording.
- `ProductionDialog.vue` history: per row with loss > 0, a badge — Belum diajukan (draft, with "Ajukan" button opening the request in RejectRequestDialog), Menunggu HO (Pending), Approved, Rejected, Closed. Opening path reuses POSMenuDialog's reject-request view with a preselected request name.
- i18n: new/changed strings added to `id.csv` and `ar.csv` (only rows this feature adds; user's other uncommitted translation edits left as is).

## Testing
- BE `test_reject_request.py`: replace Write Off/Charge Crew/old-Rejected cases; add: liability default, split sum mismatch throws, duplicate user throws, Approved with liabilities throws, close from Rejected issues stock, month total counts Rejected, Strict auto → Rejected with owner liability, crew cannot pick system purpose / disabled purpose, purpose master on_trash guard, forged `liabilities` cleared on save/submit (C1).
- BE report test: per-liability rows + company rows sum to request totals.
- `test_production.py`: still green (incl. 3 known pre-existing failures).
- FE vitest: RejectRequestDialog purpose dropdown + liability display; ProductionDialog badge states + Ajukan emits open.

## Out of scope
Request-more-evidence, dispute/appeal, employee receivable JE, payroll integration, image-type validation on evidence (M2, still parked).
