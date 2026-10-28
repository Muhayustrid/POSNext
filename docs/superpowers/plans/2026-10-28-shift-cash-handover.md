# Shift Cash Handover Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The next shift on the same POS Profile on the same day sees a server-computed proposal of the previous closing's expected drawer cash, and stores proposal + received amount on its opening.

**Architecture:** Two new read-only fields on existing doctypes (`POS Opening Shift.handover_from`, `POS Opening Shift Detail.proposed_amount`). One server helper `get_cash_handover(pos_profile)` in `pos_next/api/shifts.py`, used by `get_opening_dialog_data` (display) and `create_opening_shift` (authoritative, after the profile lock). `ShiftOpeningDialog.vue` switches label/validation when a handover exists.

**Tech Stack:** Frappe v16 (Python, doctype JSON), Vue 3 + Vitest.

**Spec:** `docs/superpowers/specs/2026-10-28-shift-cash-handover-design.md`

## Global Constraints

- Doctype changes go through the doctype JSON (synced by `bench migrate`); no patch, no Custom Field.
- `amount` keeps its meaning and Desk label "Modal Awal"; it is the amount actually received.
- Proposal only for modes in `get_modal_awal_modes(pos_profile)`; amounts = `POS Closing Shift Detail.expected_amount`.
- Eligible closing: `docstatus = 1`, same `pos_profile`, its `POS Opening Shift.posting_date = nowdate()`; latest by `period_end_date desc`.
- Client-sent `proposed_amount` / `handover_from` are ignored; server recomputes inside `create_opening_shift` after the `for_update` lock and after the auto-close block.
- `auto_balance` / `test_closing_always_expected.py` behavior unchanged. No invoices or Payment Entries created.
- UI copy (English source, Indonesian in `pos_next/translations/id.csv`):
  - "Cash Received from Previous Shift" → "Kas Diterima dari Shift Sebelumnya"
  - "System: {0} (shift {1}, closed {2})" → "Menurut sistem: {0} (shift {1}, tutup {2})"
  - "closed automatically" → "ditutup otomatis"
  - "Difference: {0}" → "Selisih: {0}"
  - "Count the cash and enter the amount received" → "Hitung uang lalu isi jumlah yang diterima"
- Subagents never run git state-changing commands. Commits only by the main session after the user approves the message.
- Backend tests: `docker exec -e PN_SITE=fix.localhost -e FRAPPE_STREAM_LOGGING=1 -w /home/frappe/frappe-bench erpnext-fix-backend-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>` (serial only; fix stack bind-mounts this checkout; the doctype JSON needs `bench --site fix.localhost migrate` in that container first).
- Frontend tests: in `POS/`, `npx vitest run <file>` on the host.

## Review Focus

- Mode present in previous closing but no longer a Modal Awal mode → not proposed (Task 1 test `test_non_modal_awal_mode_not_proposed`).
- A cancelled previous closing (`docstatus = 2`) → not proposed (Task 1 test `test_cancelled_closing_not_proposed`).
- Previous closing produced by the auto-close inside the same `create_opening_shift` call → it is the proposal (Task 1 test `test_auto_close_in_same_request_is_proposed`).
- Cashier types `0` → valid and opens (Task 2 test "accepts 0").
- Manager resume path returns the active shift without writing handover fields (covered by not touching that branch; Task 1 asserts resume test still passes in `test_shifts_authorization`).

---

### Task 1: Backend — fields, helper, dialog data, create

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_opening_shift/pos_opening_shift.json` (add `handover_from` after `shift_slot`)
- Modify: `pos_next/pos_next/doctype/pos_opening_shift_detail/pos_opening_shift_detail.json` (add `proposed_amount` after `amount`)
- Modify: `pos_next/api/shifts.py` (`get_opening_dialog_data`, `create_opening_shift`, new `get_cash_handover`)
- Test: `pos_next/tests/test_cash_handover.py` (new)

**Interfaces:**
- Produces: `get_cash_handover(pos_profile: str) -> dict | None` (not whitelisted). Returns
  `{"closing": str, "user": str, "user_full_name": str, "period_end_date": str, "auto_closed": 0|1, "amounts": {mode_of_payment: float}}`.
- Produces: `get_opening_dialog_data()` result gains `"handover": {pos_profile: <get_cash_handover result or None>}`.
- Produces: opening doc fields `handover_from` (Link POS Closing Shift, read_only, label "Handover From") and detail `proposed_amount` (Currency, read_only, label "Menurut Sistem", `options: company:company_currency`, default 0).

- [ ] **Step 1: Write failing tests** in `test_cash_handover.py`, class `TestCashHandover(POSInvoiceModeMixin, FrappeTestCase)` (same setUp pattern and invoice-type cleanup as `test_closing_always_expected.py`). Helper in the test: close `self.shift` via `submit_closing_shift({"pos_opening_shift": self.shift.name})`, store `self.closing`. `cash = next(iter(get_modal_awal_modes(self.profile.name)))`. Each test that opens shift B via `create_opening_shift` must cancel/delete it in `addCleanup`.
  - `test_no_closing_today_returns_none`: with no closing for the profile today (cancel any leftovers is NOT allowed — instead assert on a profile-filtered query: if a submitted closing for today already exists on the shared site, `skipTest`), `get_cash_handover(profile)` is `None`.
  - `test_proposal_is_previous_expected_cash`: set `self.shift` opening cash 200 (insert balance_details amount 200 for `cash` — override in test by creating its own opening instead of the mixin's if needed), sell 800 cash via `submit_invoice(self._payload(...))`, close → `get_cash_handover(profile)["amounts"][cash] == 1000`, `["closing"] == self.closing.name`.
  - `test_create_opening_stores_proposal_and_received`: after the close above, `create_opening_shift(profile, company, [{"mode_of_payment": cash, "opening_amount": 200, "proposed_amount": 999}], ...)` → opening `handover_from == self.closing.name`, detail row for `cash`: `proposed_amount == 1000`, `amount == 200`; then `make_closing_shift_from_opening` for B gives `opening_amount == 200` for `cash`.
  - `test_client_handover_fields_ignored`: client sends `handover_from="X"` in payload rows / extra key → stored value is the server's.
  - `test_non_modal_awal_mode_not_proposed`: a non-Modal-Awal mode row present in the closing is absent from `amounts`.
  - `test_cancelled_closing_not_proposed`: cancel `self.closing` → `get_cash_handover` returns `None` (or a different, older closing — assert `closing != cancelled name`).
  - `test_yesterday_closing_not_proposed`: `frappe.db.set_value("POS Opening Shift", self.shift.name, "posting_date", add_days(nowdate(), -1))` after closing → result is not that closing.
  - `test_auto_close_in_same_request_is_proposed`: leave `self.shift` open, set its `pos_schedule_deadline` in the past, call `create_opening_shift` → new opening's `handover_from` is the auto-closed closing of `self.shift`.
  - `test_dialog_data_includes_handover`: `get_opening_dialog_data()["handover"]` has the profile key when the session user is a profile user (run as a user from `POS Profile User` of the profile via `frappe.set_user`, restore in cleanup; `skipTest` if the profile has no users).
  - `test_no_sales_created`: count of `POS Invoice` + `Sales Invoice` + `Payment Entry` unchanged across `create_opening_shift`.
- [ ] **Step 2:** Run `pos_next.tests.test_cash_handover` → fails (ImportError on `get_cash_handover`).
- [ ] **Step 3:** Add the two JSON fields; run `bench --site fix.localhost migrate` in the fix container.
- [ ] **Step 4:** Implement `get_cash_handover` with one SQL join `tabPOS Closing Shift` × `tabPOS Opening Shift` (on `pcs.pos_opening_shift = pos.name`) filtered per Global Constraints, then read `tabPOS Closing Shift Detail` rows (`parent = closing`) for Modal Awal modes. `user_full_name` via `frappe.utils.get_fullname`.
- [ ] **Step 5:** In `get_opening_dialog_data`, add `data["handover"]` for each `pos_profiles_list` entry (empty dict when none).
- [ ] **Step 6:** In `create_opening_shift`, after the auto-close block and before building `new_pos_opening`: `handover = get_cash_handover(pos_profile)`; set `handover_from` and per-row `proposed_amount = handover["amounts"].get(mode, 0)` when present. Ignore any client `proposed_amount`.
- [ ] **Step 7:** Run `pos_next.tests.test_cash_handover pos_next.tests.test_closing_always_expected pos_next.tests.test_shift_slots pos_next.api.test_shifts_authorization` → all pass (known baseline failures listed in PROJECT_STATE excepted, reported separately).
- [ ] **Step 8:** `ruff check pos_next/api/shifts.py pos_next/tests/test_cash_handover.py` clean (run in container or host if available).

### Task 2: Frontend — opening dialog

**Files:**
- Modify: `POS/src/components/ShiftOpeningDialog.vue`
- Modify: `pos_next/translations/id.csv` (and `ar.csv`, `pt-br.csv` with the English source as value if those files require every key — follow how neighbouring rows look)
- Test: `POS/src/components/ShiftOpeningDialog.test.js`

**Interfaces:**
- Consumes: `dialogDataResource.data.handover[selectedProfile.name]` shaped as Task 1's `get_cash_handover` result.

- [ ] **Step 1: Write failing tests** in `ShiftOpeningDialog.test.js` (reuse its `DIALOG_DATA_URL` impl pattern; add `handover` to the dialog data):
  - no handover → label "Modal Awal" shown; Open Shift enabled with empty input (current behavior).
  - handover `{amounts: {Cash: 1000000}, user_full_name: "Kasir A", period_end_date: "2026-10-28 14:02:00", auto_closed: 0}` → text "Cash Received from Previous Shift" and "System:" line containing the formatted amount; Open Shift disabled while the Cash input is empty.
  - typing `0` enables Open Shift ("accepts 0"); submit payload `balance_details` Cash `opening_amount === 0`.
  - typing `200000` shows "Difference:" with the negative difference; typing `1000000` hides it.
  - `auto_closed: 1` → "closed automatically" appears.
- [ ] **Step 2:** `npx vitest run src/components/ShiftOpeningDialog.test.js` → new tests fail.
- [ ] **Step 3:** Implement: computed `handover` (from dialog data for the selected profile, else `null`); label switch; per-row system line using `formatCurrency` from `useFormatters` and `formatTime`/`formatDateTime`; difference = `parseAmountInput(input) − proposed`; Open Shift `:disabled="isOutsideShiftHours || handoverIncomplete"` where `handoverIncomplete` = handover exists and some Modal Awal input is `""`/undefined. Placeholder "Count the cash and enter the amount received" in handover mode. No emoji icons.
- [ ] **Step 4:** Add the 5 strings to translation CSVs.
- [ ] **Step 5:** Run the whole file, then `npx vitest run` (full suite) → only known baseline failures; `npx biome lint src/components/ShiftOpeningDialog.vue` → no new findings.

### Task 3: Verification and dev rollout (main session)

- [ ] **Step 1:** Review whole diff (fresh reviewer, Opus) against spec; fix confirmed findings.
- [ ] **Step 2:** Propose commit message(s) to user; commit only after approval.
- [ ] **Step 3:** After commit: `docker/erp backup dev`, `docker/erp rebuild dev backend`, check exit code, `bench --site dev.localhost migrate`, verify served bundle contains "Cash Received from Previous Shift". No transaction writes on dev data; user does the device check (cashier A close + logout, cashier B login).
