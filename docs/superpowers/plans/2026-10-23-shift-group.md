# Shift Group Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One POS Profile per outlet with shift time slots from a Shift Group, one open shift per outlet, company-wide sales lists with a shift badge and filter, cash-only "Modal Awal" opening, and closing that always uses expected amounts.

**Architecture:** `POS Profile Group` gains a `shifts` child table; the opening flow resolves the slot whose window contains now and snapshots it onto `POS Opening Shift` (existing `pos_schedule_*` fields + new `shift_slot`), so deadline / auto-close keep working unchanged. Sales list endpoints switch from one profile to "all profiles of the current profile's company" through one helper. Closing ignores client counts. A patch merges same-warehouse profiles of a company.

**Tech Stack:** Frappe v16 / ERPNext (Python), Vue 3 + Pinia + Vitest (POS/), frappe-ui.

**Spec:** `docs/superpowers/specs/2026-10-23-shift-group-design.md` (decisions D1-D14).

## Global Constraints

- Branch `feat/shift-group`; never commit to `main`; no push.
- Subagents never run git state-changing commands (`add`, `commit`, `stash`, `checkout`, `reset`); the orchestrator commits.
- Backend tests: `docker exec -e FRAPPE_STREAM_LOGGING=1 -e PN_SITE=optest.localhost erpnext-fix-backend-1 sh -c 'cd /home/frappe/frappe-bench && ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>'` (container bind-mounts this repo). Run serially.
- Frontend tests: `docker exec erpnext-fix-backend-1 bash -lc 'source ~/.nvm/nvm.sh >/dev/null; cd /home/frappe/frappe-bench/apps/pos_next/POS && npx vitest run <path>'`.
- Schema changes need `bench --site optest.localhost migrate` in `erpnext-fix-backend-1` before tests.
- New user-facing strings get a row in `pos_next/translations/id.csv`, appended byte-wise (file has CRLF lines; never rewrite it in text mode).
- Code comments in English. Ruff (host) on changed Python files; no new findings.
- Every bug-fix-style change gets a test that fails on the old code first.
- Known noise: teardown deadlocks in `test_stock_opname`; pre-existing failures listed in PROJECT_STATE.md.

## Review Focus

1. Opening at exactly `end_time` (exclusive) or across midnight (`end <= start`) — must refuse at end, accept 23:30 for a 22:00-06:00 slot.
2. Two cashiers opening at the same second on the same profile — the profile-row lock must still serialize; the on-the-spot close of an expired shift must not leave two open shifts.
3. A same-company invoice of a disabled (merged) profile — must still show in lists and be returnable; a foreign-company invoice must stay refused.
4. Closing payload carrying a tampered `closing_amount` — server must still store expected.
5. Patch run twice, or with an open shift — second run no-op; open-shift company skipped and logged.

---

### Task 1: Shift slots on the Shift Group + slot resolution at opening

**Files:**
- Create: `pos_next/pos_next/doctype/pos_shift_slot/{__init__.py,pos_shift_slot.json,pos_shift_slot.py}` (istable)
- Modify: `pos_next/pos_next/doctype/pos_profile_group/pos_profile_group.json` (table field `shifts` → `POS Shift Slot`, in the schedule section), `pos_profile_group.py` (validate slots; `sync_members` stops pushing schedule fields when `shifts` has rows)
- Modify: `pos_next/pos_next/doctype/pos_opening_shift/pos_opening_shift.json` (field `shift_slot`, Data, read-only, after `pos_profile`)
- Modify: `pos_next/shift_schedule.py` (`SCHEDULE_FIELDS` unchanged; add `get_profile_slots`, `open_slots_now`; `apply_schedule_snapshot` uses the chosen slot; `freeze_schedule_snapshot` also freezes `shift_slot`)
- Modify: `pos_next/api/shifts.py` (`get_opening_dialog_data` returns `slots` per profile; `create_opening_shift(pos_profile, company, balance_details, shift_slot=None)`)
- Test: `pos_next/tests/test_shift_slots.py`

**Interfaces:**
- Produces: `get_profile_slots(pos_profile) -> list[dict]` (keys `shift_name,start_time,end_time,warning_minutes,enforce_closing`; empty when the group has no slots); `open_slots_now(pos_profile, now_dt=None) -> list[dict]` (slots whose `resolve_window` contains now, ordered by window start descending — first = default); `POS Opening Shift.shift_slot`; `create_opening_shift(..., shift_slot=None)`; `get_opening_dialog_data()["slots"] = {profile: [slot dicts incl. "open_now": bool]}`.

- [ ] Step 1: Write failing tests in `test_shift_slots.py`: `test_open_inside_slot_snapshots_slot` (slot 05:00-15:00, now 10:00 → `shift_slot` set, `pos_schedule_start/end` = slot, deadline today 15:00); `test_open_outside_every_slot_refused` (now 16:00 → throws); `test_end_is_exclusive` (now 15:00 → refused); `test_overnight_slot` (22:00-06:00, now 23:30 → deadline tomorrow 06:00); `test_overlap_requires_named_slot_and_defaults_latest` (05-15 + 13-21, now 13:30: `open_slots_now` order = [13-21, 05-15]; `shift_slot=None` picks 13-21); `test_unknown_or_closed_slot_name_refused` (pass a slot not open now → throws); `test_reopen_same_slot_after_closing` (open, close, open again at 11:00 → OK); `test_group_without_slots_keeps_profile_schedule` (old behaviour); `test_slot_snapshot_frozen_on_save`. Freeze time with `frappe.utils.now_datetime` patched (`unittest.mock.patch("pos_next.shift_schedule.now_datetime")`).
- [ ] Step 2: Run, expect failures (no doctype / no field).
- [ ] Step 3: Implement doctype + fields; migrate optest; implement `get_profile_slots`, `open_slots_now`; in `apply_schedule_snapshot(opening)` when slots exist: pick `opening.shift_slot` if it is among `open_slots_now` else (if blank) the first; none → throw `"{0} is outside every shift of {1}. A shift cannot be opened now."`; set `pos_schedule_enabled=1`, start/end/warning/enforce from slot, deadline = window end. Group validation: slot names unique, start != end, warning >= 0.
- [ ] Step 4: Tests pass; also run `pos_next.tests.test_shift_schedule` (no regressions).
- [ ] Step 5: Commit `feat(shift): slot shift di Shift Group dan pilih slot saat buka shift`.

### Task 2: One open shift per outlet with on-the-spot close of an expired shift

**Files:**
- Modify: `pos_next/api/shifts.py` (`create_opening_shift`)
- Test: `pos_next/tests/test_shift_slots.py` (new class `TestOneOpenShift`)

**Interfaces:**
- Consumes: `get_active_profile_shift(pos_profile)` (`pos_opening_shift.py:11`), `_submit_closing(name, auto_balance=True, auto_closed=True)` (`pos_closing_shift.py:863`).

- [ ] Step 1: Failing tests: `test_second_open_refused_while_first_inside_window` (cashier B opens while A's shift deadline is in the future → `throw_profile_in_use` message); `test_expired_open_shift_closed_then_new_opens` (A's `pos_schedule_deadline` in the past, B opens → A has a submitted closing with `auto_closed=1`, closing amounts = expected, B's shift open); `test_manager_resume_unchanged` (manager on active non-expired shift still resumes).
- [ ] Step 2: Run, expect the expired-shift test to fail.
- [ ] Step 3: In `create_opening_shift`, after the profile-row lock: if `active` and its `pos_schedule_deadline` < `now_datetime()`, call `_submit_closing(active.name, auto_balance=True, auto_closed=True)` and continue to create the new shift; otherwise keep today's branch. Fetch the deadline with `frappe.db.get_value("POS Opening Shift", active.name, "pos_schedule_deadline")`.
- [ ] Step 4: Tests pass + `pos_next.tests.test_auto_close_shifts`.
- [ ] Step 5: Commit `feat(shift): tutup otomatis shift kedaluwarsa saat shift berikutnya dibuka`.

### Task 3: Closing always expected; remove `auto_balance_closing`

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py` (`submit_closing_shift` passes `auto_balance=True` and no counted; `_submit_closing` drops the `get_effective_pos_setting(..., "auto_balance_closing")` lookup — keep the `auto_balance` param for callers)
- Modify: remove the field + references from `pos_settings.json`, `pos_next_global_settings.json`, `pos_settings.py` (SALES_MANAGEMENT_FIELDS), `pos_next/api/constants.py`, `POS/src/stores/posSettings.js`, `POS/src/stores/posEvents.js`
- Modify: `POS/src/components/ShiftClosingDialog.vue` (remove every counted input and the counted-vs-expected difference UI unconditionally; table shows mode / expected; submit sends rows without `closing_amount`)
- Delete: `pos_next/tests/test_auto_balance_closing.py`; drop the toggle tests from `POS/src/stores/posSettings.test.js` (delete the file if nothing remains) and `ShiftClosingDialog.test.js`; drop `autoBalanceClosing` from `ResponsiveDialogs.test.js` mock
- Test: `pos_next/tests/test_closing_always_expected.py`, `POS/src/components/ShiftClosingDialog.test.js`

- [ ] Step 1: Failing tests: backend `test_submit_ignores_client_counted_amount` (payload `closing_amount` 1 vs expected 5000 → stored 5000, difference 0); `test_backdate_counted_path_kept` (`_submit_closing(name, counted={...})` still stores counted). Frontend: closing dialog renders no amount inputs and the submit payload has no `closing_amount`.
- [ ] Step 2: Run, expect failures.
- [ ] Step 3: Implement; leave the DB column of the removed Check alone (Frappe drops nothing on field removal — acceptable).
- [ ] Step 4: Backend `test_closing_always_expected`, `test_auto_close_shifts`, `test_shift_schedule`; full frontend suite + `npm run build`.
- [ ] Step 5: Commit `feat(closing): closing selalu pakai expected dan hapus toggle auto-balance`.

### Task 4: Opening dialog — slot picker + "Modal Awal" cash only

**Files:**
- Modify: `POS/src/components/ShiftOpeningDialog.vue`, `POS/src/components/ShiftOpeningDialog.test.js`, `pos_next/translations/id.csv`

**Interfaces:**
- Consumes: `get_opening_dialog_data().slots` and `create_opening_shift(..., shift_slot)` from Task 1.

- [ ] Step 1: Failing tests: only cash modes render an input; label "Modal Awal"; non-cash modes still in `balance_details` with `opening_amount: 0`; with two open slots a picker shows both, default = first; with one, no picker but its name shows; with none, an "outside shift hours" message and the open button disabled; `shift_slot` sent.
- [ ] Step 2: Run, expect failures.
- [ ] Step 3: Implement (`__("Modal Awal")` — add id.csv row `Modal Awal,Modal Awal`; reuse existing button/select styles in the dialog).
- [ ] Step 4: Dialog tests + full frontend suite + build.
- [ ] Step 5: Commit `feat(shift): dialog buka shift pilih slot dan modal awal hanya cash`.

### Task 5: Company-wide sales lists (backend)

**Files:**
- Modify: `pos_next/api/invoices.py` (`get_invoices`, `get_returnable_invoices`, `search_invoice_by_number`, `search_invoices_for_return`, `_return_scope_for`, `_return_access_denied`), `pos_next/api/partial_payments.py` (`get_partial_paid_invoices`, `get_unpaid_invoices`, `get_partial_payment_summary`, `get_unpaid_summary`)
- Test: `pos_next/tests/test_company_sales_scope.py`; update `pos_next/api/test_invoice_authorization_security.py` cases that assert `wrong_outlet` for a same-company foreign profile (they now expect access; keep the foreign-company cases).

**Interfaces:**
- Produces: `company_profiles(pos_profile) -> list[str]` in `pos_next/api/invoices.py`: checks membership-or-management on `pos_profile` (raise `frappe.PermissionError`), returns every POS Profile (enabled or disabled) with the same company. Every list row gains `pos_profile` and `shift_slot` (LEFT JOIN `tabPOS Opening Shift` on `posa_pos_opening_shift`; NULL for legacy).

- [ ] Step 1: Failing tests: profiles P1, P2 (same company, P2 disabled), P3 (other company), cashier member of P1 only; invoices on each. For each list endpoint called with `pos_profile=P1`: rows include P1 + P2 invoices, exclude P3, carry `pos_profile` and `shift_slot`. `get_invoice_for_return` / `prepare_return_invoice` on a P2 invoice succeed with a P1 open shift; on a P3 invoice raise. Non-member of P1 → PermissionError.
- [ ] Step 2: Run, expect failures.
- [ ] Step 3: Implement: replace `pos_profile = %s` with `pos_profile IN %(profiles)s` from `company_profiles`; `_return_scope_for` returns `company_profiles(open shift profile)`; `_return_access_denied` allows any profile of the open shift's company.
- [ ] Step 4: New tests + `test_invoice_authorization_security`, `test_submit_stock_no_orphan_draft`, partial payment tests (`grep -l partial_payments pos_next/**/test_*.py`).
- [ ] Step 5: Commit `feat(sales): daftar penjualan satu company lintas POS Profile dengan info shift`.

### Task 6: Shift badge + filter on the SPA lists

**Files:**
- Modify: `POS/src/components/invoices/InvoiceManagement.vue`, `POS/src/components/sale/InvoiceHistoryDialog.vue`, `POS/src/components/sale/ReturnInvoiceDialog.vue`, `POS/src/components/partials/PartialPayments.vue`; tests next to each (existing `*.test.js` where present); `pos_next/translations/id.csv`
- Create (only if two or more components need it): `POS/src/composables/useShiftFilter.js` exporting `useShiftFilter(rowsRef) -> { options, selected, filtered }` where badge = `row.shift_slot || row.pos_profile`, `options` = distinct badges sorted, `selected=""` means All.

- [ ] Step 1: Failing tests: rows from two badges render a badge each; selecting one badge hides the other; "All" shows both; follow `docs/UI_GUIDELINES.md` for pill style.
- [ ] Step 2: Run, expect failures.
- [ ] Step 3: Implement; strings `All shifts` → `Semua shift`.
- [ ] Step 4: Full frontend suite + build + Biome on changed files (no new findings).
- [ ] Step 5: Commit `feat(sales): badge dan filter shift di riwayat, return dan unpaid`.

### Task 7: Migration patch

**Files:**
- Create: `pos_next/patches/v2_26_0/__init__.py`, `pos_next/patches/v2_26_0/merge_shift_profiles.py`; register in `pos_next/patches.txt` (post_model_sync)
- Test: `pos_next/tests/test_merge_shift_profiles.py`

**Interfaces:**
- Produces: `execute()` and `merge_company(company) -> dict` (`{"status": "merged"|"single"|"skipped_open_shift"|"noop", "kept": str|None, "disabled": [str], "group": str|None}`).

- [ ] Step 1: Failing tests on fixture data: company with two enabled profiles same warehouse (schedules 05-15 and 13-21, the first with more opening shifts) → one new group named `"<company> Shifts"` with two slots named after each profile's old group (else profile name), kept = more shifts, other profile disabled, its `POS Profile User` rows added to kept (no duplicates), only the kept profile linked to the new group; single scheduled profile → one-slot group; open shift in company → `skipped_open_shift` and `frappe.log_error`; second run → `noop`; profiles with different warehouses → untouched.
- [ ] Step 2: Run, expect failures.
- [ ] Step 3: Implement with Document API (`frappe.get_doc(...).insert/save`) so group validation runs; unlink the kept profile from its old group first (`validate_members` refuses a profile in another group).
- [ ] Step 4: Tests pass; dry-run on a copy: `bench --site optest.localhost execute pos_next.patches.v2_26_0.merge_shift_profiles.execute` is covered by the tests — production data check happens at dev migrate, outside this plan.
- [ ] Step 5: Commit `feat(shift): patch gabung POS Profile per shift jadi satu profile per outlet`.

### Task 8: Whole-branch verification

- [ ] Run every touched backend module + `test_shift_schedule`, `test_auto_close_shifts`, `test_invoice_authorization_security`; full frontend suite; `npm run build`; ruff + Biome on changed files.
- [ ] Opus whole-branch review against the spec; fix wave with tests.
- [ ] Update PROJECT_STATE.md; no push.
