# POS Spend — Stage 1 (Foundation + Expenses) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cashiers and managers record outlet expenses and non-stock purchases paid immediately from the POS SPA. The server creates native Journal Entries or paid Purchase Invoices with per-company accounts resolved from HQ templates. Spend paid from a shift drawer lowers that shift's expected cash exactly once.

**Architecture:** One configuration doctype (`POS Spend Template` + child tables) and one shift-source table on POS Settings. A resolver service maps reference accounts to each company by `account_number` with type checks. `api/spend.py` builds and submits native documents in one DB transaction, idempotent through a unique custom field. Closing, the EOD print and the Sales Recap read drawer spend from native GL Entries of stamped vouchers.

**Tech Stack:** Frappe/ERPNext v16 (Python, doctype JSON, `create_custom_fields`), Vue 3 + Pinia + Vitest in `POS/`.

**Spec:** `docs/superpowers/specs/2026-11-02-pos-cash-out-design.md` (approved 2026-11-02). Read §4–§7, §10–§14.

**Scope of this plan:** spec §17 stage 1 only. Out of scope here, each gets its own plan after stage 1 ships:
- stock purchases (`update_stock`), UOM, taxes, Debit Note, reverse JE, reclassification;
- pay-later PI, Payment Entry from POSNext, amount limits, HQ report, account provisioning.

Stage 1 still adds every custom field the later stages need, so that patch runs only once. The fields are listed in Task 1.

## Global Constraints

- No account, category, company, mode of payment or document rule hardcoded. Everything comes from `POS Spend Template`, POS Settings, Mode of Payment Account, or the `spend_reference_company` global setting.
- Accounts are matched across companies by `account_number` only, never by `account_name`. A mismatched parent number is a readiness warning, not a block.
- Template Account strategy never falls back to Item Default. Item Default strategy on a non-stock item rejects a company-default fallback.
- Only POSNext endpoints create spend documents. Each endpoint checks role, company and shift, then saves with `ignore_permissions`. No DocPerm on PI/JE/PE is granted to cashier roles.
- A closing that is already submitted is never changed. A stamped voucher whose shift has a submitted closing cannot be cancelled, from POSNext or from Desk.
- The feature is online only (`requiresOnline`).
- New custom fields go in `install.py` `CUSTOM_FIELDS` and are synced by a patch. Every one is `no_copy=1`.
- UI strings use `__()`. New strings get rows in `pos_next/translations/id.csv`, which must keep CRLF and stay byte-exact for untouched rows. Append only; never rewrite the file through a text round-trip.
- Backend tests run serially on the fix stack: `docker exec erpnext-fix-backend-1 bash -lc "cd /home/frappe/frappe-bench && PN_SITE=fix.localhost FRAPPE_STREAM_LOGGING=1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>"`. The fix backend bind-mounts host `pos_next`; never `docker cp` into it. Run `bench --site fix.localhost migrate` after doctype or patch changes. Verify the site is up first; if it is not, stop and report.
- Frontend tests run inside `POS/`: `npx vitest run <file>`. Build: `npm run build`.
- Commits: Conventional Commits, scope required, description in Indonesian. Every commit message is proposed to the user and waits for approval. No pushes.
- Before touching dev: `docker/erp backup dev`, then `docker/erp rebuild dev backend`. Check the exit code and the served asset marker.

## Review Focus

1. **Closing races a spend submit on the same shift.** The spend must land either in that closing or be refused because the shift is closed, never in neither. Pinned by `test_spend_after_closing_is_refused` in Task 4, which relies on the shared `FOR UPDATE` lock on POS Opening Shift.
2. **The Mode of Payment Account mapping changes after a spend.** Closing must still count the account that was actually credited (`posa_shift_account`), not the current mapping. Pinned by `test_closing_uses_stamped_account_after_mapping_change` in Task 5.
3. **A cashier retries after a timeout** with the same `client_request_id`. They must get the first document back, not a second one, including when two requests race. Pinned by `test_retry_returns_same_document` and `test_duplicate_request_id_hits_unique_index` in Task 4.
4. **A group company (`OUTLET`) is in the template's `companies` table.** Every leaf descendant counts as in scope. Overrides on a group row are rejected. Pinned by `test_group_company_row_covers_descendants` and `test_override_on_group_row_rejected` in Task 2.
5. **Rounding.** A PI total of 12,345 must be paid exactly 12,345, not a rounded total. Pinned by `test_paid_pi_not_rounded` in Task 4 (`disable_rounded_total=1`, `paid_amount == grand_total`).

---

### Task 1: Custom fields, unique index, settings fields

**Files:**
- Modify: `pos_next/install.py` (`CUSTOM_FIELDS` dict at line 39)
- Create: `pos_next/patches/v2_36_0/__init__.py`, `pos_next/patches/v2_36_0/spend_custom_fields.py`
- Modify: `pos_next/patches.txt` (append `pos_next.patches.v2_36_0.spend_custom_fields`)
- Create: `pos_next/tests/test_spend_setup.py`

**Interfaces:**
- Produces these custom fields on each of `Purchase Invoice`, `Journal Entry` and `Payment Entry`, all `no_copy=1`, `read_only=1`, in a collapsible section "POS Spend":

  | Field | Type |
  |---|---|
  | `posa_client_request_id` | Data, `unique=1`, `length=64` |
  | `posa_spend_template` | Link `POS Spend Template` |
  | `posa_pos_profile` | Link `POS Profile` |
  | `posa_pos_opening_shift` | Link `POS Opening Shift`, `search_index=1` |
  | `posa_shift_account` | Link `Account` |
  | `posa_vendor_name` | Data |
  | `posa_spend_status` | Select, options `\nRequested\nNeeds Review` |
  | `posa_correction_of` | Dynamic Link, plus `posa_correction_of_type` Link `DocType` |

- Produces `Supplier.posa_is_walk_in_supplier` (Check).
- Patch `execute()` calls `pos_next.install.sync_custom_fields(quiet=True)`.

- [ ] **Step 1: Write failing tests** in `test_spend_setup.py`:
  - `test_spend_fields_exist`: for each of the 3 doctypes, `frappe.get_meta(dt).get_field(f)` is not None for every field above, and `.no_copy == 1`.
  - `test_request_id_unique_index`: `frappe.db.sql("SHOW INDEX FROM `tabJournal Entry` WHERE Column_name='posa_client_request_id' AND Non_unique=0")` is non-empty. Same for PI and PE.
  - `test_walk_in_flag_on_supplier`.
- [ ] **Step 2: Run** `... _pn_run_tests.py pos_next.tests.test_spend_setup`. Expected: FAIL (fields missing).
- [ ] **Step 3:** Add the fields to `CUSTOM_FIELDS`, then add the patch and the patches.txt line.
- [ ] **Step 4:** `bench --site fix.localhost migrate`, rerun the tests. Expected: PASS.
- [ ] **Step 5:** Propose commit `feat(spend): tambah custom field penanda POS Spend di PI, JE, PE dan Supplier`.

### Task 2: Configuration doctypes and their validation

**Files:**
- Create doctype folders under `pos_next/pos_next/doctype/`. Child doctypes are `istable=1`.

  | Doctype | Fields |
  |---|---|
  | `pos_spend_template` | see below |
  | `pos_spend_template_item` | `item_code` Link Item, `item_group` Link Item Group |
  | `pos_spend_template_payment_source` | `mode_of_payment` Link reqd, `is_default` Check |
  | `pos_spend_template_company` | `company` Link reqd, `debit_account` Link Account, `cost_center` Link Cost Center, `purchase_taxes_template` Link (stage 2 uses it, field added now) |
  | `pos_spend_template_role` | `role` Link Role reqd |
  | `pos_shift_spend_source` | `mode_of_payment` Link reqd |

  `POS Spend Template` fields:
  - `title` Data reqd unique, `category` Data reqd, `enabled` Check default 1;
  - `document_type` Select `Journal Entry\nPurchase Invoice` reqd;
  - `payment_timing` Select `Pay Now\nPay Later` default `Pay Now`;
  - `update_stock` Check;
  - `debit_account_strategy` Select `Template Account\nItem Default` default `Template Account`;
  - `reference_company` Link Company; `reference_debit_account` Link Account (query filtered to `reference_company`);
  - `default_supplier` Link Supplier; `allow_supplier_change` Check; `require_vendor_name` Check;
  - `apply_purchase_tax` Check; `requires_attachment` Check;
  - `max_amount` Currency; `over_limit_policy` Select `\nRequest First\nRecord and Flag`;
  - tables `items`, `payment_sources`, `companies`, `roles`.

  Permissions: System Manager and Accounts Manager full; POSNext Manager read.
- Modify: `pos_next/pos_next/doctype/pos_settings/pos_settings.json`. Add a section "Spend" with table `posa_shift_spend_sources` (options `POS Shift Spend Source`).
- Modify: `pos_next/pos_next/doctype/pos_next_global_settings/pos_next_global_settings.json`. Add `spend_reference_company` (Link Company).
- Test: `pos_next/pos_next/doctype/pos_spend_template/test_pos_spend_template.py`

**Interfaces:**
- Produces `POSSpendTemplate.validate()` and
  `pos_next.pos_next.doctype.pos_spend_template.pos_spend_template.template_companies(template) -> list[str]`.
  The function returns leaf companies in scope: a group row expands with `get_descendants_of("Company", c)` filtered to `is_group=0`. The result is sorted and has no duplicates.
- Produces `POSSettings` validation: each `posa_shift_spend_sources` mode must exist in the POS Profile `payments` table and resolve through `get_bank_cash_account(mode, profile.company)` to an account whose `account_type` is Cash or Bank.

- [ ] **Step 1: Write failing tests**, using fix-site fixtures created in `setUpClass`: a group company with two leaf children, numbered accounts on each, and a reference company.
  - `test_je_template_rejects_items_tax_and_pay_later`: each of `items` non-empty, `apply_purchase_tax=1`, `payment_timing="Pay Later"` and `debit_account_strategy="Item Default"` on a JE template raises `frappe.ValidationError`.
  - `test_reference_account_must_belong_to_reference_company`.
  - `test_reference_account_type_rejected`: `account_type` in Stock, Payable, Receivable, Cash, Bank or Tax, `is_group=1`, or a root_type other than Expense/Asset raises.
  - `test_template_account_strategy_requires_reference_or_overrides`: no reference account and a leaf company row without `debit_account` raises.
  - `test_override_account_must_belong_to_row_company`; `test_override_cost_center_must_belong_to_row_company`.
  - `test_override_on_group_row_rejected`.
  - `test_group_company_row_covers_descendants`: `template_companies` returns both leaf children and not the group.
  - `test_payment_sources_required_and_one_default`: no rows raises; two `is_default` rows raise.
  - `test_reference_company_defaults_from_global_setting`: blank `reference_company` gets `spend_reference_company`.
  - `test_pos_settings_shift_source_must_be_profile_payment` (in the same file, using a POS Settings row).
- [ ] **Step 2: Run, expect FAIL** (doctype missing).
- [ ] **Step 3:** Create the doctypes and implement `validate` and `template_companies`. Readiness is Task 3; `validate` checks only template-level rules and Link ownership.
- [ ] **Step 4:** Migrate the fix site, run. Expected: PASS.
- [ ] **Step 5:** Propose commit `feat(spend): doctype POS Spend Template dan sumber kas shift di POS Settings`.

### Task 3: Account resolution and readiness

**Files:**
- Create: `pos_next/services/spend_accounts.py`
- Create: `pos_next/tests/test_spend_accounts.py`
- Modify: `pos_next/pos_next/doctype/pos_spend_template/pos_spend_template.js` (button "Check Readiness" shows a dialog table)
- Create: Script Report `pos_next/pos_next/report/spend_template_readiness/` (filters: template optional; columns: template, company, status, reasons; one row per template × company)

**Interfaces:**
- `class SpendConfigError(frappe.ValidationError)`.
- `resolve_account(reference_account: str, company: str) -> str`. Implements spec §6, rules 1–5:
  - exactly one leaf account with the same `account_number` in `company`;
  - same root_type and account_type; not disabled and not frozen; currency equal to the company currency;
  - otherwise raises `SpendConfigError` with a reason.
- `resolve_debit_account(template, company: str, item_code: str | None = None) -> str | None`:
  - **Template Account:** the company row override, else `resolve_account(reference_debit_account, company)`.
  - **Item Default:** return `None` for a stock item (ERPNext decides). For a non-stock item, return the expense account from `get_item_defaults` or `get_item_group_defaults` for that company. Raise `SpendConfigError` when neither has one.
- `resolve_payment_account(mode_of_payment: str, company: str) -> str`. Uses `get_bank_cash_account`, then checks: leaf, not disabled, `account_type` in Cash/Bank, currency = company currency.
- `resolve_cost_center(template, company, pos_profile=None) -> str`. Order: company row, then the profile's `cost_center`, then the company's `cost_center`. The result must belong to the company.
- `resolve_credit_to(supplier: str, company: str) -> str`. Uses the Supplier's Party Account for the company, else the company's `default_payable_account`. Must be `account_type == "Payable"`. Used by the PI path.
- `check_readiness(template, company: str) -> dict`, returning `{"company", "ready": bool, "reasons": [str], "warnings": [str], "accounts": {...}}`. Runs all resolvers above for every payment source and every allowed item. A parent-number mismatch goes to `warnings`.
- Whitelisted `get_spend_template_readiness(template: str) -> list[dict]` over `template_companies(template)`. Requires the `System Manager`, `Accounts Manager` or `POSNext Manager` role.

- [ ] **Step 1: Write failing tests:**
  - `test_resolve_by_number_ignores_name`: reference "Biaya Listrik", target "Biaya PLN Kantor", same number → resolves.
  - `test_resolve_rejects_type_mismatch`, `test_resolve_rejects_disabled`, `test_resolve_rejects_currency`, `test_resolve_missing_number`, `test_reference_without_number_needs_override`.
  - `test_parent_mismatch_is_warning_only`: `check_readiness` is ready, with one warning.
  - `test_template_account_ignores_item_default`: an item with an Item Default expense account in the company still resolves to the template account.
  - `test_item_default_rejects_company_fallback`: a non-stock item without item or group defaults raises.
  - `test_payment_account_rejects_non_cash_bank`.
  - `test_credit_to_must_be_payable`: a company whose `default_payable_account` is not Payable raises. This mirrors the dev TRAINING data, where it is Hutang Pajak; confirm its type in the test setup.
  - `test_readiness_lists_every_leaf_company`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5:** Propose commit `feat(spend): resolusi akun lintas company dan cek kesiapan template`.

### Task 4: `submit_spend` for JE and non-stock pay-now PI

**Files:**
- Create: `pos_next/api/spend.py`
- Create: `pos_next/api/test_spend.py`

**Interfaces:**
- Consumes Tasks 1–3, `pos_next.api.purchase_orders._purchase_company_options`, `attach_purchase_files`, and `pos_next.utils.error_reason`.
- `get_spend_templates(company: str, pos_profile: str | None = None) -> list[dict]` (whitelisted). Returns enabled templates where `company in template_companies(t)`, the user holds a template role (empty = POSNext Cashier + POSNext Manager), and `check_readiness(t, company)["ready"]`. Each entry carries:
  - template fields used by the form;
  - `payment_sources: [{mode_of_payment, is_default, is_shift_source}]`;
  - `items` expanded to item codes (an item group expands to purchase-enabled, non-disabled items).
- `submit_spend(data: str | dict) -> dict` (whitelisted).
  - Input: `{client_request_id, template, company, pos_profile, mode_of_payment, supplier?, vendor_name?, bill_no?, bill_date?, remarks?, lines: [{item_code?, description?, qty?, rate?, amount?}], files?: [...]}`.
  - Returns `{"doctype", "name", "amount", "shift": str | None, "duplicate": bool}`.
- Internal `_shift_for_source(pos_profile, mode_of_payment, user) -> str | None`. Returns the user's open opening shift on that profile, locked `FOR UPDATE`, when the mode is in the profile's effective `posa_shift_spend_sources`. Throws when it is a shift source and no open shift exists. Throws when the shift already has a submitted POS Closing Shift.

Order inside `submit_spend` (spec §7.2):
1. Validate and authorize.
2. Look up an existing document by request id.
3. Get the shift lock.
4. Resolve accounts.
5. Build the document:
   - **JE:** `voucher_type` is `Cash Entry` when the payment account type is Cash, else `Bank Entry` with `cheque_no=bill_no or name` and `cheque_date=bill_date or today`. One debit line per input line, one credit line for the total.
   - **PI:** `is_paid=1`, `mode_of_payment`, `cash_bank_account`, `disable_rounded_total=1`, `paid_amount=grand_total` set after `set_missing_values`/`calculate_taxes_and_totals`, each item row's `expense_account` and `cost_center` set from the resolvers, and `credit_to` from `resolve_credit_to`.
6. Stamp the fields.
7. `insert(ignore_permissions=True)`, then `submit()`.
8. Attach files.

A `frappe.DuplicateEntryError` on `posa_client_request_id` → `frappe.db.rollback()`, re-read the existing document, and return it with `duplicate=True`.

Rejected in stage 1: `update_stock=1` templates, `Pay Later`, a total above `max_amount` when set ("not available yet"), attachment missing when required, a supplier change when not allowed, an empty vendor name when required.

- [ ] **Step 1: Write failing tests.** Setup: a profile on the fix site, an open shift, a cash mode configured as a shift source, and one JE and one PI template ready for the company.
  - `test_je_posts_expected_gl`: GL is Dr resolved expense / Cr cash for the amount; `posa_pos_opening_shift`, `posa_shift_account` and `posa_client_request_id` are stamped.
  - `test_pi_nonstock_paid_posts_expected_gl`: net GL is Dr template account / Cr cash; status Paid; `outstanding_amount == 0`.
  - `test_paid_pi_not_rounded`: lines totalling 12345 → `paid_amount == grand_total == 12345`.
  - `test_non_shift_source_not_stamped`: mode not in `posa_shift_spend_sources` → `posa_pos_opening_shift` empty, and no shift is required.
  - `test_shift_source_requires_open_shift`.
  - `test_spend_after_closing_is_refused`: closing submitted, then a spend on that shift raises.
  - `test_retry_returns_same_document`: the same id twice → one JE, `duplicate=True` on the second call.
  - `test_duplicate_request_id_hits_unique_index`: insert a JE stamped with an id directly, then `submit_spend` with that id returns it. Covers the race path through the index.
  - `test_cashier_other_company_refused`; `test_role_not_in_template_refused`; `test_unready_company_refused_with_reason`.
  - `test_stage1_rejects_update_stock_and_pay_later`.
  - `test_required_attachment_enforced`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5:** Propose commit `feat(spend): endpoint submit_spend untuk JE beban dan PI non-stok bayar langsung`.

### Task 5: Shift, closing, EOD print, Sales Recap

**Files:**
- Create: `pos_next/services/spend_shift.py`
- Create child doctype `pos_next/pos_next/doctype/pos_closing_shift_spend/` (`voucher_type` Link DocType, `voucher_no` Dynamic Link, `mode_of_payment` Link, `account` Link Account, `amount` Currency). Add table field `spend_entries` to `pos_closing_shift.json`, read-only.
- Modify: `pos_next/pos_next/doctype/pos_closing_shift/pos_closing_shift.py`
  - `make_closing_shift_from_opening`, after the payment-entry loop at about line 857: one `_aggregate_payment(payments, mode, -amount)` per mode, and set `spend_entries`.
- Modify: `pos_next/pos_next/utils/pos_closing_print.py:391`. `total_expense` = sum of `spend_entries.amount` where `_is_cash_mode(mode)`.
- Modify: `pos_next/services/sales_recap.py:518-545`.
  - `expense` = sum of `shift_spend(...)` for `scope.shifts`, cash modes only; `expense_supported=True`.
  - `cash_expected = opening_cash + cash_collected - expense`, and `cash_in_hand` gets the same value.
- Test: `pos_next/tests/test_spend_shift.py`

**Interfaces:**
- `shift_spend(opening_shift: str) -> list[dict]`. Returns `[{voucher_type, voucher_no, mode_of_payment, account, amount}]`, one row per stamped voucher. `amount` = Σ(`credit_in_account_currency − debit_in_account_currency`) over GL Entry with `is_cancelled=0`, `voucher_type`/`voucher_no` = the voucher, and `account` = its `posa_shift_account`. Only vouchers with `docstatus=1` and `posa_pos_opening_shift = opening_shift` across PI, JE and PE are included. `mode_of_payment` is read from the voucher's own field.
- `shift_spend_by_mode(opening_shift) -> dict[str, float]`.

- [ ] **Step 1: Write failing tests:**
  - `test_drawer_spend_lowers_expected`: opening 200,000, cash sale 100,000, JE spend 30,000 → `expected_amount` for cash = 270,000, and `spend_entries` has 1 row of 30,000.
  - `test_non_shift_spend_not_counted`.
  - `test_cancelled_spend_not_counted`.
  - `test_je_counts_credit_on_drawer_account_only`: a JE whose debit total includes an unrelated debit/credit pair counts only the drawer credit.
  - `test_closing_uses_stamped_account_after_mapping_change`: after the spend, change the Mode of Payment Account; closing still subtracts the spend.
  - `test_eod_total_expense_and_recap_expense`: `get_sales_recap(closing)["cash"]["total_expense"] == 30000`; shift recap `expense == 30000`, `expense_supported is True`.
  - `test_handover_follows_expected`: the next shift's `get_cash_handover` proposal = expected after spend.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4:** Run the new tests, then the regression modules `pos_next.tests.test_cash_handover`, `pos_next.tests.test_closing_always_expected`, `pos_next.tests.test_pos_invoice_closing`, `pos_next.api.test_printing` and `pos_next.tests.test_closing_shift_race`. Expected: new tests PASS; regression modules show only the baseline failures listed in `PROJECT_STATE.md`. Record HEAD's results for those modules before the change, for comparison.
- [ ] **Step 5:** Propose commit `feat(spend): kurangi kas shift dari pengeluaran laci dan isi Total Expense di EOD dan Sales Recap`.

### Task 6: Cancel and post-closing guard

**Files:**
- Modify: `pos_next/api/spend.py` (add `cancel_spend`)
- Create: `pos_next/overrides/spend_guard.py`
- Modify: `pos_next/hooks.py` `doc_events`: `"before_cancel": "pos_next.overrides.spend_guard.before_cancel"` for `Purchase Invoice`, `Journal Entry` and `Payment Entry`. Merge with existing entries if those doctypes are already present.
- Test: `pos_next/api/test_spend_cancel.py`

**Interfaces:**
- `before_cancel(doc, method=None)`: if `doc.posa_pos_opening_shift` has a POS Closing Shift with `docstatus=1`, throw `frappe.ValidationError` with the message "This spend belongs to a closed shift ({0}). Record a correction instead of cancelling." Unstamped documents pass through.
- `cancel_spend(doctype: str, name: str, reason: str) -> dict` (whitelisted).
  - `doctype` must be one of PI/JE and the document must carry `posa_spend_template`; `reason` is required.
  - Allowed for the document owner while its shift is open, or for a management user with company in scope.
  - Adds a Comment with the reason, then calls `doc.cancel()` with `ignore_permissions`.

- [ ] **Step 1: Write failing tests:**
  - `test_cancel_in_open_shift_reverses_gl_and_drops_from_closing`.
  - `test_cancel_after_closing_blocked_in_api`.
  - `test_cancel_after_closing_blocked_from_desk`: Administrator calls `doc.cancel()` directly → raises.
  - `test_unstamped_spend_cancel_allowed`.
  - `test_other_cashier_cannot_cancel`; `test_reason_required`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5:** Propose commit `feat(spend): batalkan pengeluaran saat shift terbuka dan tolak pembatalan setelah closing`.

### Task 7: Unified history

**Files:**
- Modify: `pos_next/api/spend.py` (add `get_spend_history`)
- Test: add to `pos_next/api/test_spend.py`

**Interfaces:**
- `get_spend_history(company: str, pos_profile: str | None = None, from_date=None, to_date=None, status: str | None = None, limit: int = 50) -> list[dict]` (whitelisted).
  - Company scope follows `_purchase_company_options`.
  - Unions PI, JE and PE with `posa_spend_template` set, newest first.
  - Each row: `{doctype, name, posting_date, template, category, amount, mode_of_payment, docstatus, status, shift, vendor_name, bill_no, owner, can_cancel, attachments}`.
  - `status`: PI uses its native `status`, JE uses Submitted/Cancelled.
  - `can_cancel` uses the same rule as `cancel_spend`.

- [ ] **Step 1: Write failing tests:**
  - `test_history_merges_pi_and_je_newest_first`.
  - `test_history_scoped_to_company`.
  - `test_history_can_cancel_false_after_closing`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5:** Propose commit `feat(spend): riwayat gabungan pembelian dan pengeluaran`.

### Task 8: SPA "Purchases & Expenses"

**Files:**
- Create: `POS/src/components/spend/SpendDialog.vue` (embedded view, tabs: New, History)
- Create: `POS/src/components/spend/SpendDialog.test.js`
- Modify: `POS/src/components/pos/managementMenu.js`: item `{ id: "spend", icon: "credit-card", label: __("Purchases & Expenses"), group: "stock", requiresOnline: true }`.
- Modify: `POS/src/components/pos/POSMenuDialog.vue`: mount block `activeView === 'spend'` like the purchase views, with `CompanyPicker` for management users.
- Modify: `POS/src/components/sale/SessionSummary.vue` only if `expense_supported` needs a display change. `expenseDisplay` already handles non-null values; verify that first.
- Modify: `pos_next/translations/id.csv` (append rows)

**Interfaces:**
- Consumes `get_spend_templates`, `submit_spend`, `get_spend_history`, `cancel_spend` and `attach` through `utils/apiWrapper` `call`. Errors go through `humanizeError`. Reuses `PurchaseAttachments.vue` and `compressImage`.
- `client_request_id` = `generateUUID()` created when the form opens. It is kept across retries of the same form and reset only after a successful submit or a form reset.

UI:
- Templates are grouped by `category`.
- JE templates show description + amount lines; PI templates show item picker + qty + rate lines.
- Payment source select shows a "Shift drawer" badge when `is_shift_source`.
- Supplier field appears only if `allow_supplier_change`; vendor name and bill no are always shown.
- The total is shown in Rp.
- The submit button is disabled while in flight.
- History lists rows with status pills per `docs/UI_GUIDELINES.md`. A Cancel action asks for a reason and is shown only when `can_cancel`.
- Icons come from the icon set already used by the menu; no emoji.

- [ ] **Step 1: Write failing tests:**
  - `renders templates grouped by category`.
  - `retry after network error reuses client_request_id`.
  - `new form after success gets a new id`.
  - `je template shows amount lines, pi template shows item lines`.
  - `shift source badge shown`.
  - `cancel hidden when can_cancel false`.
  - `menu shows spend item only online`.
- [ ] **Step 2: Run** `npx vitest run src/components/spend/SpendDialog.test.js src/components/pos/POSMenuDialog.test.js`. Expected: FAIL.
- [ ] **Step 3: Implement**, and add id.csv rows for every new string.
- [ ] **Step 4:** Run the tests and the full FE suite (`npx vitest run`), then `npm run build`. Expected: PASS and the build OK. Biome shows no new findings on the touched files.
- [ ] **Step 5:** Propose commit `feat(spend): menu Pembelian & Pengeluaran di SPA POS`.

### Task 9: Dev rollout and E2E

**Files:** none in the repo, except a proof `docs/proofs/2026-11-02-pos-spend-stage1.md` in the ERPNext-Project root repo.

- [ ] **Step 1:** `docker/erp backup dev`, then `docker/erp rebuild dev backend`. Check exit 0, migrate success, and that the served POSSale bundle contains `submit_spend`.
- [ ] **Step 2: Configure on dev** (data, with user approval before writing):
  - `spend_reference_company = OUTLET`;
  - a JE template "Listrik" with reference `5230.001 - Biaya Listrik - OUTLET`, companies `OUTLET`, payment source Cash;
  - a PI template "Perlengkapan" for a non-stock item;
  - one outlet profile with perpetual inventory on, whose `Cash` is mapped to `1111.003 Kas Kasir`, with Cash as its shift source.
  - Run readiness and record the counts of ready and not-ready outlets.
- [ ] **Step 3: E2E** with a QA cashier in the browser:
  1. Open the shift.
  2. Record a JE "Listrik" of 30,000 from the drawer.
  3. Record a PI "Perlengkapan" from the drawer.
  4. Record the same JE through a non-shift source.
  5. Close the shift: expected cash = opening + sales − drawer spend; EOD Total Expense = drawer spend.
  6. Try to cancel after closing: blocked in the SPA and in Desk.
  - Record document names and GL rows in the proof.
- [ ] **Step 4:** Update `PROJECT_STATE.md`.
