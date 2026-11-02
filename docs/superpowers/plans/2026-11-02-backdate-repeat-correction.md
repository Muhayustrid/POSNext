# Backdate Entry: Repeat Corrections, Auto-Expected Cash, Frozen Handover — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A closed shift can be backdate-corrected any number of times (including correcting an invoice that a previous entry created), closing amounts default to the new expected amounts, and a backdate never rewrites other shifts' submitted documents.

**Architecture:** All changes stay inside the existing POS Backdate Entry flow. Server: relax the `amended_from` block, add a stale-draft guard keyed on the already existing `cancelled_closing_shift` field, expose the current closing's `period_end_date` in `get_shift_context`, and reduce `_move_handover` to "repoint + refresh proposed amount + comment" (the recursive `_apply_handover` from d6cedee1 is deleted). Desk form: closing rows default to expected for every mode (cash included, still editable), closing date/time default to the current closing's end, and the form self-heals a stale draft.

**Tech Stack:** Frappe v16 / ERPNext v16 (Python), Desk form JS (`frappe.ui.form`), translations CSV.

**Spec:** No spec file (bounded change, design agreed in chat on 2026-11-02). The agreed decisions are restated in "Design decisions" below; treat that section as the spec.

## Design decisions (agreed with the user)

1. Closing amount for every mode of payment, cash included, defaults to the new expected amount; the user may edit it; an edited row is never overwritten by a recompute. The cash row shows the old counted amount as a hint.
2. An invoice created by a previous backdate correction can be corrected again. Double correction of the same invoice stays blocked (the original is cancelled, and only a *submitted* amendment counts as "already corrected").
3. Closing Date/Time default to the `period_end_date` of the shift's current (submitted) closing, not to "now".
4. Stale-draft guard: an entry remembers which closing it was prepared against (`cancelled_closing_shift`). If the shift's current closing differs at save/submit, it is rejected with a reload message. The Desk form detects this on load and re-bases the draft.
5. Handover frozen: a backdate only repoints `handover_from` of the next opening(s) to the new closing, refreshes their `proposed_amount`, and adds a comment with old/new system cash. It never changes Modal Awal (`amount`) of any opening, nor any other closing. `_apply_handover` recursion is removed.
6. POS Backdate Entry itself stays non-cancellable.
7. Existing dev data (BDE-2026-00001..00006 and the shifts they touched) is not migrated or corrected.

## Global Constraints

- Code comments, plan, PROJECT_STATE in English; user-facing strings via `_()` / `__()` and added to `pos_next/translations/id.csv`.
- `id.csv` rows are CRLF and must stay byte-exact: append new rows with a binary-safe edit (no Python text-mode rewrite of the whole file). Verify with `git diff --stat` that only added lines appear.
- No new DocType fields, no patch, no new dependency.
- Backend tests run serially on the fix stack only: `docker exec erpnext-fix-backend-1 bash -lc "cd /home/frappe/frappe-bench && PN_SITE=fix.localhost FRAPPE_STREAM_LOGGING=1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>"`. The fix backend bind-mounts host `pos_next`; never `docker cp` into it.
- Working tree has unrelated uncommitted changes (sidebar, hooks, hq_monitoring, awesomebar). Stage only the files named in each task. Commits and pushes need explicit user approval of the exact message; descriptions in Indonesian, Conventional Commit with scope `backdate`.
- Dev site holds production data: `docker/erp backup dev` before `docker/erp rebuild dev backend`.

## Review Focus

1. Two admins drafting on the same shift; the second submits after the first → must be rejected, never silently close with numbers from an older closing (Task 2 test + lock-time check in Task 2 Step 3).
2. Correcting an invoice that is already cancelled (original of a correction) → must still fail with "must be submitted" (Task 1 test assertion).
3. A successor shift that accepted Modal Awal as proposed and already closed → its opening amount and its closing rows stay byte-identical after a backdate (Task 3 test).
4. Draft opened before the change, saved after a newer closing exists, from the Desk form → form re-bases instead of erroring on every save (Task 4 manual check).
5. Closing date default from a closing whose `period_end_date` is empty (legacy rows) → form falls back to the current behaviour ("now"), server validation unchanged (Task 4 Step 2 rule).

---

### Task 0: Baseline

**Files:** none modified.

- [ ] **Step 1: Confirm the fix stack is up and the bind mount points at this checkout**

Run: `docker exec erpnext-fix-backend-1 ls /home/frappe/frappe-bench/sites` → expect `fix.localhost`.
Run: `docker exec erpnext-fix-backend-1 grep -c "_apply_handover" /home/frappe/frappe-bench/apps/pos_next/pos_next/pos_next/doctype/pos_backdate_entry/pos_backdate_entry.py` → expect a non-zero count (same file as host).

- [ ] **Step 2: Record the baseline of the two backdate modules**

Run the runner (Global Constraints) with `pos_next.pos_next.doctype.pos_backdate_entry.test_pos_backdate_entry pos_next.api.test_backdate_invoices`.
Write the list of failing/erroring test names into `PROJECT_STATE.md` under the current-work line for this task (previous note: "backdate module 21 run, same 8 baseline failures"). Later tasks compare against this list; only new failures count as regressions.

---

### Task 1: Allow correcting an invoice produced by an earlier correction

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_backdate_entry/pos_backdate_entry.py:274-293` (`_validate_invoices`, Correction branch)
- Test: `pos_next/pos_next/doctype/pos_backdate_entry/test_pos_backdate_entry.py`

**Interfaces:**
- Consumes: existing `TestPOSBackdateEntry._make_bde`, `_submit_original_sale`, `_close_shift`.
- Produces: nothing new.

- [ ] **Step 1: Write the failing test** `test_second_entry_corrects_invoice_created_by_first_correction`

```python
orig, _ = self._submit_original_sale(qty=1, rate=100.0, mode_of_payment=self.mode[0])
old_closing = self._close_shift()
corr = lambda against, rate: self._make_bde(
    invoices=[{"invoice_no": 1, "entry_type": "Correction", "against_invoice": against,
               "against_doctype": self.doctype, "customer": self.customer,
               "posting_date": add_days(nowdate(), -1), "posting_time": "10:00:00"}],
    items=[{"invoice_no": 1, "item_code": self.item, "qty": 1, "rate": rate, "warehouse": self.profile.warehouse}],
    payments=[{"invoice_no": 1, "mode_of_payment": self.mode[0], "amount": rate}],
)
first = corr(orig["name"], 80.0); first.insert(); first.submit()
x = first.invoices[0].created_invoice
second = corr(x, 60.0); second.insert(); second.submit()

assert frappe.db.get_value(self.doctype, x, "docstatus") == 2
y = second.invoices[0].created_invoice
assert frappe.db.get_value(self.doctype, y, "amended_from") == x
assert second.cancelled_closing_shift == first.new_closing_shift
assert frappe.db.get_value("POS Closing Shift", first.new_closing_shift, "docstatus") == 2
expected = frappe.db.get_value("POS Closing Shift Detail",
    {"parent": second.new_closing_shift, "mode_of_payment": self.mode[0]}, "expected_amount")
assert flt(expected) == 60.0
# the cancelled original can never be corrected again
third = corr(orig["name"], 50.0)
with self.assertRaises(frappe.ValidationError) as ctx: third.insert()
self.assertIn("must be submitted", str(ctx.exception))
```

(Write with `self.assertEqual`, matching the file's style.)

- [ ] **Step 2: Run it, expect FAIL** with "has already been amended" on `second.insert()`.

- [ ] **Step 3: Implement**

In the Correction branch: delete the `if against_info.amended_from: throw(... has already been amended ...)` block. Change `already_amended` to count only submitted amendments: add `"docstatus": 1` to both `frappe.db.exists(... {"amended_from": inv.against_invoice})` filters. Leave `amended_from` in the fetched fields only if still used; otherwise drop it from the tuple.

- [ ] **Step 4: Update `test_scenario_10_correction_blocked_scenarios` part C**

Part C asserted "has already been amended". Replace it with the opposite expectation: an invoice whose `amended_from` is set (and has no submitted amendment) passes `insert()` (then delete that draft). Part D (another *submitted* invoice amends it → "has already been corrected") stays as is.

- [ ] **Step 5: Run the module, expect PASS** for the new test and scenario 10; no failure outside the Task 0 baseline.

- [ ] **Step 6: Propose commit** (await approval): `fix(backdate): izinkan koreksi ulang atas faktur hasil koreksi backdate` — files: the two above.

---

### Task 2: Stale-draft guard

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_backdate_entry/pos_backdate_entry.py` (`_validate_shift`, `_execute_submit`)
- Modify: `pos_next/translations/id.csv` (append 1 row)
- Test: `pos_next/pos_next/doctype/pos_backdate_entry/test_pos_backdate_entry.py`

**Interfaces:**
- Produces: `cancelled_closing_shift` on a draft = name of the closing the draft was prepared against. Set on first validate when empty; after submit it still holds the closing actually cancelled (unchanged meaning).
- Produces: error string `STALE_MSG = "Shift {0} was changed by another backdate entry (closing {1} is now {2}). Reload this entry and check the amounts again."` — Task 4 relies on the form never hitting it in the normal reload path.

- [ ] **Step 1: Write the failing test** `test_stale_draft_is_rejected_after_another_entry`

```python
self._submit_original_sale(qty=1, rate=100.0)
old_closing = self._close_shift()
a = self._make_bde(); a.insert()
b = self._make_bde(); b.insert()
self.assertEqual(b.cancelled_closing_shift, old_closing)
a.submit()
with self.assertRaises(frappe.ValidationError) as ctx: b.submit()
self.assertIn("changed by another backdate entry", str(ctx.exception))
self.assertEqual(frappe.db.get_value("POS Backdate Entry", b.name, "docstatus"), 0)
# re-based draft goes through
b.reload(); b.cancelled_closing_shift = a.new_closing_shift; b.closing_payments = []
b.submit()
self.assertEqual(b.cancelled_closing_shift, a.new_closing_shift)
```

- [ ] **Step 2: Run it, expect FAIL** (`b.cancelled_closing_shift` is None after insert).

- [ ] **Step 3: Implement**

In `_validate_shift`, after the existing closing checks: if `not self.cancelled_closing_shift`, set it to `shift.pos_closing_shift`; elif it differs from `shift.pos_closing_shift` (validate only runs on draft save and on submit, never after, so no docstatus condition is needed), `frappe.throw(_(STALE_MSG).format(self.pos_opening_shift, self.cancelled_closing_shift, shift.pos_closing_shift), title=_("Backdate Entry"))`.
In `_execute_submit`, right after the `FOR UPDATE` lock and status check, repeat the comparison against the freshly read `shift.pos_closing_shift` (this is the race-safe check; validate alone runs before the lock). Then the existing `_reopen` line overwrites `cancelled_closing_shift` with the same value — keep it.
Existing tests that build entries with `_make_bde` are unaffected because the field starts empty.

- [ ] **Step 4: Append the id.csv row** for STALE_MSG: `"Shift {0} was changed by another backdate entry (closing {1} is now {2}). Reload this entry and check the amounts again.","Shift {0} sudah diubah oleh entri backdate lain (closing {1} sekarang {2}). Muat ulang entri ini dan periksa kembali angkanya.",""` + CRLF.

- [ ] **Step 5: Run the module, expect PASS**; Task 1 test still passes (its second entry is created after the first submitted, so it is based on the new closing).

- [ ] **Step 6: Propose commit**: `fix(backdate): tolak draft basi bila shift sudah diubah entri backdate lain`.

---

### Task 3: Freeze handover (remove the cascade)

**Files:**
- Modify: `pos_next/pos_next/doctype/pos_backdate_entry/pos_backdate_entry.py:528-622` (`_move_handover`; delete `_apply_handover`)
- Modify: `pos_next/translations/id.csv` (append 1 row; the old "Cash handover moved…" row stays because the first sentence is reused)
- Test: `pos_next/pos_next/doctype/pos_backdate_entry/test_pos_backdate_entry.py`

**Interfaces:**
- Produces: `_move_handover(old_closing: str, new_closing: str, pos_profile: str, entry: str) -> None` — new `entry` arg (the BDE name) for the comment; single caller in `_execute_submit` step 5.

- [ ] **Step 1: Rewrite the d6cedee1 test** `test_backdate_carries_accepted_modal_awal_into_closed_successor` → rename to `test_backdate_leaves_closed_successor_untouched`. Same setup (successor accepted 100 as Modal Awal, then closed). New assertions:

```python
opening_row: amount == 100.0, proposed_amount == 150.0
closing_row (successor_closing, cash): opening_amount == 100.0, expected_amount == before.expected_amount,
                                       closing_amount == before.closing_amount, difference == before.difference
handover_from of successor == bde.new_closing_shift
a Comment on the successor opening contains bde.name and "150"
```

Capture `before` (the successor closing cash row) right after `submit_closing_shift`, before the backdate.

- [ ] **Step 2: Run it, expect FAIL** (current code moves amount to 150).

- [ ] **Step 3: Implement**

`_move_handover`: keep the openings query, `modal_awal_modes`, and the `expected` dict. Per opening: repoint `handover_from`; for each `POS Opening Shift Detail` row set only `proposed_amount = expected.get(mode, 0)`, collecting `(mode, old_proposed, new_proposed, amount)` for modal-awal modes whose proposal changed; insert one Comment with the existing sentence plus one line per collected mode:
`_("{0}: system cash {1} -> {2}, Modal Awal stays {3} ({4})").format(mode, fmt(old), fmt(new), fmt(amount), entry)` with `fmt = lambda v: frappe.format_value(v, {"fieldtype": "Currency"})`.
Delete `_apply_handover` entirely and update the docstring to state the freeze rule in one line ("A backdate corrects records, not the cash that was physically handed over: Modal Awal and later shifts are never rewritten.").
Pass `self.name` from `_execute_submit`.

- [ ] **Step 4: Append the id.csv row**: `"{0}: system cash {1} -> {2}, Modal Awal stays {3} ({4})","{0}: kas sistem {1} -> {2}, Modal Awal tetap {3} ({4})",""` + CRLF.

- [ ] **Step 5: Run the module + `pos_next.tests.test_cash_handover`, expect PASS** (incl. unchanged `test_backdate_shift_whose_cash_was_handed_over`); nothing outside the baseline. Confirm no remaining reference: `grep -rn "_apply_handover" pos_next` → empty.

- [ ] **Step 6: Propose commit**: `refactor(backdate): bekukan serah terima kas, hanya perbarui angka sistem shift berikutnya`.

---

### Task 4: Desk form — auto-expected closing, default closing date, stale re-base

**Files:**
- Modify: `pos_next/api/backdate_invoices.py:246-315` (`get_shift_context`)
- Modify: `pos_next/pos_next/doctype/pos_backdate_entry/pos_backdate_entry.js` (`load_shift_context` ~298, `recompute_closing_payments` ~877-966)
- Modify: `pos_next/translations/id.csv` (append rows for new `__()` strings)
- Test: `pos_next/pos_next/doctype/pos_backdate_entry/test_pos_backdate_entry.py` (`test_helper_get_shift_context`)

**Interfaces:**
- Produces: `get_shift_context(...)` gains two keys: `current_closing: str` (= `shift.pos_closing_shift`) and `closing_period_end: str | None` (that closing's `period_end_date` as `str`, None when empty).
- Consumes: Task 2's `cancelled_closing_shift` semantics.

- [ ] **Step 1: Failing test** — extend `test_helper_get_shift_context` part A:

```python
closing = self._close_shift()   # use the returned name instead of discarding it
self.assertEqual(ctx["current_closing"], closing)
self.assertEqual(ctx["closing_period_end"],
                 str(frappe.db.get_value("POS Closing Shift", closing, "period_end_date")))
```

Run → FAIL (KeyError). Implement in `get_shift_context` (one `get_value` on the closing). Run → PASS.

- [ ] **Step 2: Default closing date/time** in `load_shift_context` callback, only when `frm.doc.docstatus === 0 && frm.is_new()` and `ctx.closing_period_end` is set: split into date and time (`moment(ctx.closing_period_end)`), `frm.set_value` both. If empty, keep the onload "now" default.

- [ ] **Step 3: Stale re-base** in the same callback for saved drafts (`docstatus === 0 && !frm.is_new()`): if `(!frm.doc.cancelled_closing_shift && frm.doc.closing_payments?.length) || (frm.doc.cancelled_closing_shift && frm.doc.cancelled_closing_shift !== ctx.current_closing)`, then `frm.set_value("cancelled_closing_shift", ctx.current_closing)`, clear `closing_payments` and `frm._touched_closing_modes`, call `recompute_closing_payments(frm)`, `frm.dirty()`, and `frappe.msgprint(__("This shift was changed by another backdate entry. Closing amounts were recalculated from {0}; check them and the invoice rows before saving.", [ctx.current_closing]))`. For new drafts (or saved drafts without closing payments) set `cancelled_closing_shift` to `ctx.current_closing` silently (server would set the same).

- [ ] **Step 4: Closing defaults to expected** in `recompute_closing_payments`: replace the `is_cash ? old.closing_amount : new_expected` branch with `closing_amount = new_expected` (touched rows keep the user's value as today). Then set the field description once per recompute: if the cash mode row exists in `old_counts`, `frm.set_df_property("closing_payments", "description", __("Previously counted {0}: {1}", [cash_mode, format_currency(old.closing_amount, frm._shift_context.currency)]))`. `cash_mode` / `is_cash` detection stays as it is (only used for the hint now).

- [ ] **Step 5: id.csv rows** (CRLF): the msgprint string and `"Previously counted {0}: {1}","Hitungan sebelumnya {0}: {1}",""`.

- [ ] **Step 6: Run the module, expect PASS** (nothing outside baseline). JS has no unit harness for this Desk form; it is verified in Task 5.

- [ ] **Step 7: Propose commit**: `feat(backdate): closing otomatis sama dengan expected dan default tanggal closing dari shift`.

---

### Task 5: Full regression, dev deploy, browser verification

**Files:** `PROJECT_STATE.md` (local, untracked).

- [ ] **Step 1: Regression** — run serially: `pos_next.pos_next.doctype.pos_backdate_entry.test_pos_backdate_entry pos_next.api.test_backdate_invoices pos_next.tests.test_cash_handover pos_next.api.test_shifts_authorization`. Expected: new tests pass; failures identical to Task 0 baseline (list both in PROJECT_STATE).
- [ ] **Step 2: Ruff** on the two touched Python files inside the container (`ruff check` + `ruff format --check`); no new findings vs `git stash`-free HEAD comparison of the same files.
- [ ] **Step 3: Dev** — `docker/erp backup dev`, `docker/erp rebuild dev backend`, check exit code 0 and that the served `pos_backdate_entry.js` contains `closing_period_end`.
- [ ] **Step 4: Browser E2E on dev (Desk, TRAINING profile, Playwright subagent)** on a closed TRAINING shift:
  1. New entry → pick shift: Closing Date/Time equal the closing's end, cash closing row = expected, hint shows previous count.
  2. Correction of one invoice → submit OK.
  3. Second new entry on the same shift → correct the invoice created in (2) → submit OK; the shift's closing is the third closing.
  4. Open a draft saved before (3)'s submit → form shows the re-base message and recalculated rows; submit OK.
  5. If the next TRAINING opening has `handover_from`: its Modal Awal unchanged, proposed amount updated, comment present.
  Record document names in PROJECT_STATE.
- [ ] **Step 5: Report to the user** with test numbers, dev doc names, and anything not verified. No push, no prod deploy without explicit approval.

---

## Out of scope (named, not done)

- Correcting the existing dev handover data changed by d6cedee1 (e.g. POPEN-RDSKY-26110201 chain).
- Batching the per-invoice return lookups in `get_shift_context` (2 queries per invoice).
- Validating that a re-close does not overlap the next shift's start.
- POS SPA (Vue) changes: the Backdate flow is Desk-only.
