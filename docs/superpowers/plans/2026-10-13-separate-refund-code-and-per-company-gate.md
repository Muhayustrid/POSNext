# Separate Refund Code + Per-Company Code Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give refunds their own code pool (`POS Refund Code`), make the "no code needed" switch per Company for both discounts and refunds, and allow hand-typed unique codes like `MESSI123`.

**Architecture:** One shared code-controller helper (`pos_next/services/confirmation_code.py`) used by both code doctypes. `pos_next/overrides/discount_code.py` stays the single enforcement module and gains a doctype parameter plus per-Company switches. Frontend reads the two switches from `get_status(company)`.

**Tech Stack:** Frappe 16 / ERPNext, Python, Vue 3 + Pinia + Vitest, Desk JS.

**Spec:** `docs/superpowers/specs/2026-10-13-separate-refund-code-and-per-company-gate-design.md`

## Global Constraints

- Backend runs in Docker: `docker exec -w /workspace/development/frappe-bench erpnext16_dev-frappe-1 ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py <module>` (serial, set `FRAPPE_STREAM_LOGGING`). Migrate BOTH sites: `posnext.localhost` and `roti-posnext-test.localhost`. Frontend `npm` also inside the container, in `POS/`.
- Code format: manual codes `A-Z0-9`, 6–20 chars, upper-cased; auto-generated keep alphabet `ABCDEFGHJKMNPQRSTUVWXYZ23456789`, 8 chars. Uniqueness per doctype, explicit message. A code with `used_count > 0` can't change value.
- Switch fields on Company: `pos_require_discount_code`, `pos_require_refund_code` (Check, default 1). NULL/unset = required (fail closed). Invoice without company = required.
- Invoice fields: sales keep `discount_confirmation_code`; returns use new `refund_confirmation_code` (Data, read_only, no_copy, print_hide) on Sales Invoice AND POS Invoice.
- The two pools never validate each other's codes.
- API contract: `get_status(company)` → `{"enabled": bool, "discount_code_required": bool, "refund_code_required": bool}` (`enabled` == `discount_code_required`). `check_code(code, company, kind="discount")` with `kind in ("discount","refund")`.
- New UI strings need `pos_next/translations/id.csv` rows (`"source","terjemahan",""`); run `python scripts/validate_id_csv.py`.
- Don't commit. Don't touch unrelated code. Comments in English.

## Review Focus

- Refund with company switch off → saves, no code, no usage stamp.
- Discounted sale with discount switch off, refund switch on (independent switches).
- Discount code presented on a return, refund code on a discounted sale → both rejected.
- Company switch NULL / invoice with no company → code required.
- `messi123` typed in lowercase stored as `MESSI123`; `ab1` too short; `messi 123` / `messi-123` rejected; duplicate rejected with clear message; same string allowed across the two doctypes.
- Non-System-Manager changing a Company switch is rejected.

---

### Task 1: Shared code controller helper + `POS Refund Code` doctype

**Files:**
- Create: `pos_next/services/confirmation_code.py`
- Modify: `pos_next/pos_next/doctype/pos_discount_confirmation_code/pos_discount_confirmation_code.py` (delegate to helper, keep `generate_codes` whitelisted name/signature and `CODE_DOCTYPE`)
- Create: `pos_next/pos_next/doctype/pos_refund_code/{__init__.py,pos_refund_code.json,pos_refund_code.py}` (same fields/perms/naming as the discount doctype; child table option `POS Discount Code Company`; label/description say "refund")
- Create: `pos_next/public/js/pos_refund_code.js`, `pos_next/public/js/pos_refund_code_list.js` (copies of the discount JS pointing at the refund doctype/`generate_codes`), plus "View usage" button in BOTH form JS files (`pos_discount_code.js`, `pos_refund_code.js`) opening Sales Invoice list filtered by `discount_confirmation_code` / `refund_confirmation_code` = the code
- Modify: `pos_next/pos_next/workspace/posnext/posnext.json`, `pos_next/pos_next/sidebar/posnext/posnext.json`, `pos_next/workspace_sidebar/posnext.json` (add POS Refund Code next to the discount code entries, mirroring them)
- Test: `pos_next/api/test_confirmation_code.py` (unittest-style, mocked like `test_discount_code.py::TestControllerValidate`)

**Interfaces:**
- Produces in `pos_next/services/confirmation_code.py`:
  - `random_code() -> str`
  - `prepare_code(doc) -> None` — normalizes `doc.code` (strip, upper), generates when empty, validates manual format, uniqueness against `doc.doctype` excluding `doc.name`, used-code immutability, date order, scope-needs-outlets. Raises `frappe.ValidationError`.
  - `generate_codes_for(doctype: str, count: int, company: str | None, notes: str | None) -> dict` returning `{"codes": [...]}`; checks `frappe.has_permission(doctype, "create", throw=True)`, count 1–500.
- `POSRefundCode.generate_codes(count=1, company=None, notes=None)` whitelisted POST in `pos_refund_code.py`, same behavior as the discount one.
- hooks.py is owned by Task 2; it will register `public/js/pos_refund_code.js` and `pos_refund_code_list.js`.

- [ ] **Step 1:** Write failing tests in `test_confirmation_code.py`: `messi123` → stored `MESSI123`; `ab1` rejected (too short); `messi 123`, `messi-123`, 21-char code rejected; duplicate rejected with message containing the code; auto-generated code uses only the safe alphabet and is 8 chars; used code (`used_count>0`) value change rejected; valid_upto < valid_from rejected; Selected Outlets without outlets rejected; `generate_codes_for` rejects count 0 and 501.
- [ ] **Step 2:** Run to see them fail.
- [ ] **Step 3:** Implement the helper; move logic out of the discount controller (no behavior change except the new manual format rule and clear duplicate message).
- [ ] **Step 4:** Create the refund doctype (copy JSON, change name/description/`modified`), controller delegating to the helper, JS files, workspace/sidebar entries.
- [ ] **Step 5:** Run `pos_next.api.test_confirmation_code` and the existing `pos_next.api.test_discount_code` (`TestControllerValidate`, `TestGenerateCodes` must still pass; adjust only assertions that encoded the old restrictive format).

### Task 2: Gate logic, custom fields, Company switches, API, patch

**Files:**
- Modify: `pos_next/install.py` — `CUSTOM_FIELDS["Sales Invoice"]` and `["POS Invoice"]`: add `refund_confirmation_code` (insert_after `discount_confirmation_code`); `["Company"]`: add `pos_require_discount_code`, `pos_require_refund_code` (Check, default 1, description "Uncheck to let this outlet skip the HQ code"), placed after `pos_overall_target_from`
- Modify: `pos_next/overrides/discount_code.py`
- Modify: `pos_next/api/discount_code.py`
- Modify: `pos_next/hooks.py` — `doctype_js`/`doctype_list_js` entries for `POS Refund Code` (files from Task 1); `doc_events["Company"]["validate"]`
- Create: `pos_next/overrides/company_code_switch.py` — `guard_code_switches(doc, method=None)`: if `pos_require_discount_code` or `pos_require_refund_code` changed (`doc.has_value_changed`) and `"System Manager" not in frappe.get_roles()` → `frappe.throw(_("Only a System Manager can change the code requirement of an outlet."))`
- Create: `pos_next/patches/v2_18_0/seed_company_code_switches.py` + line in `patches.txt` (`[post_model_sync]` style as existing patches). For each Company: `pos_require_discount_code = 1`; `pos_require_refund_code = 0` only if the company has ≥1 POS Profile and every one of them resolves `require_refund_code` to 0 (via `get_effective_pos_setting`), else 1
- Modify: `pos_next/pos_next/doctype/pos_settings/pos_settings.json`, `pos_settings.py` (drop `require_refund_code` from the field list at line ~26), `pos_next_global_settings.json` — remove the field from `field_order` and `fields` (DB column stays)
- Test: `pos_next/api/test_discount_code.py` (extend), `pos_next/api/test_code_gate_company_switch.py` (new)

**Interfaces:**
- Consumes: Task 1 doctype names `POS Refund Code`, child `POS Discount Code Company`.
- Produces in `pos_next/overrides/discount_code.py`:
  - `REFUND_CODE_DOCTYPE = "POS Refund Code"`
  - `discount_code_required(company: str | None) -> bool` and `refund_code_required(company: str | None) -> bool` (replaces the `pos_profile` argument; NULL/missing company → True; value read with `frappe.db.get_value("Company", company, field)`, `None` → True)
  - `validate_code(code_value, company, doctype=CODE_DOCTYPE)`; `_scoped_outlets(code_name, parent_doctype=CODE_DOCTYPE)`
  - `validate_invoice_discounts`: returns skip the check when `not refund_code_required(company)`, else `validate_code(doc.refund_confirmation_code, company, REFUND_CODE_DOCTYPE)`; sales skip when `not discount_code_required(company)`; the stamped-coupon lane stays untouched and above the switches
  - `record_code_usage_on_submit`: returns audit against the refund pool/field, sales against the discount pool/field, each only when its gate is on
- Produces in `pos_next/api/discount_code.py`: `get_status(company=None, pos_profile=None)` per the API contract (keep `pos_profile` param accepted and ignored for old clients); `check_code(code, company, kind="discount")`.
- `POS Invoice` events (`pos_next/pos_invoice_events.py`) call the same functions; verify they need no change.
- `prepare_return_invoice` in `pos_next/api/invoices.py` (~4045): `refund_confirmation_code` is `no_copy` so the mapper drops it; confirm and add nothing unless a test shows otherwise.

- [ ] **Step 1:** Write failing tests (`test_code_gate_company_switch.py`, real Company rows via `frappe.db.set_value`, restored in tearDown): the four combinations of switch × discount/refund; cross-pool rejection both directions; NULL switch and no-company fail closed; usage stamp only when the gate is on; Company guard rejects non-System-Manager change (use `frappe.set_user`) and allows System Manager; patch mapping (company whose profiles all have `require_refund_code=0` → 0, mixed → 1, no profiles → 1).
- [ ] **Step 2:** Run to see them fail.
- [ ] **Step 3:** Implement install.py fields, overrides, API, hooks, guard, patch, settings cleanup.
- [ ] **Step 4:** Update `test_discount_code.py` (`TestRefundCodeRequired`, `TestGetStatusAPI`, `TestCheckCodeAPI`, return-related `TestValidateInvoiceDiscounts`/`TestRecordCodeUsageOnSubmit` cases) to the new signatures.
- [ ] **Step 5:** Migrate both sites (`bench --site <site> migrate`), then run `pos_next.api.test_code_gate_company_switch` and `pos_next.api.test_discount_code`.

### Task 3: Frontend

**Files:**
- Modify: `POS/src/stores/discountRestriction.js` (+ `discountRestriction.test.js`): `status` default stays `{ enabled: true }`; `fetchStatus(company)` keeps calling `get_status({ company })`; `applicable` follows `status.enabled`; `reset()` unchanged. No new state.
- Modify: `POS/src/components/sale/ReturnInvoiceDialog.vue`: `fetchRefundGateStatus` calls `get_status` with `company: originalInvoice.value?.company || ""` (re-run when the original invoice loads); `verifyRefundCode` calls `check_code` with `kind: "refund"`; payload sends `refund_confirmation_code` and no longer `discount_confirmation_code`; `maxlength` on the input raised to 20
- Modify: `pos_next/pos_next/page/backdate_entry/backdate_entry.js`: `_load_refund_gate` passes `company: this.shift.company`; `_verify_refund_code` passes `kind: "refund"`; the RETURN payload (~1327) sends `refund_confirmation_code`; the sale payload (~1282) is unchanged
- Modify: `POS/src/components/settings/POSSettings.vue`: remove the `require_refund_code` CheckboxField and the default at ~1720
- Modify: code input `maxlength="8"` in EditItemDialog/PaymentDialog to 20 (manual codes may be up to 20); check their other `maxlength` uses first
- Modify: `pos_next/translations/id.csv` (new/changed strings only), then `python scripts/validate_id_csv.py`
- Test: `POS/src/stores/discountRestriction.test.js` (add: `get_status` payload with `enabled:false` already covered — add `discount_code_required:false` case if the store reads it), new `POS/src/components/sale/ReturnInvoiceDialog.test.js` (mock `@/utils/apiWrapper` `call`): dialog hides the code input and enables submit when `refund_code_required:false`; sends `refund_confirmation_code` when a verified code is entered; `check_code` called with `kind:"refund"`

**Interfaces:**
- Consumes the API contract in Global Constraints (Task 2 implements it; mock it in tests).

- [ ] **Step 1:** Write failing Vitest tests as listed.
- [ ] **Step 2:** Run in container: `docker exec -w /workspace/development/frappe-bench/apps/pos_next/POS erpnext16_dev-frappe-1 npm run test:run -- <file>` — see failures.
- [ ] **Step 3:** Implement the changes above.
- [ ] **Step 4:** Run the whole frontend suite (`npm run test:run`) and `npm run lint`; baseline Biome has 4 pre-existing findings in POSSale.vue / POSSale.test.js — report any new ones only.

### Task 4: Update existing backend tests that disabled the old setting

**Files (modify, depends on Task 2):** `pos_next/api/test_backdate_invoices.py` (~374–443, 478–490, 539–543), `test_return_requires_return_against.py` (~82–115), `test_return_tax_flag_mirror.py` (~100–130), `test_wallet_return_hardening.py` (~118–151, 833–836, 897–920), `test_session_summary.py` (~136–153), `pos_next/tests/test_hq_monitoring.py` (~107–122, 1406–1419), `pos_next/test_promotions.py` (~380–401), plus `test_discount_gate_hardening.py`, `test_pos_invoice_submit.py`, `test_offline_replay_price_validation.py` where they send `discount_confirmation_code` on a RETURN.

- [ ] **Step 1:** Replace each `require_refund_code = 0` setup/teardown with setting `pos_require_refund_code = 0` on the company the test's invoices use (back up and restore the previous value), and send `refund_confirmation_code` instead of `discount_confirmation_code` in return payloads that supply a code.
- [ ] **Step 2:** Run each changed module serially; every module that passed before must pass.

### Task 5: Full verification and docs

- [ ] **Step 1:** Run all touched backend modules serially, then the frontend suite.
- [ ] **Step 2:** Manual check on the dev site: create a refund code `messi123`, confirm stored `MESSI123`; Company switch off → return needs no code; on → needs refund code and a discount code is refused.
- [ ] **Step 3:** Add a "Refund & Discount Codes" section to the release notes/docs (`docs/PROJECT_STATE.md` is historical; put it in a new `docs/CODE_GATE.md`) covering: two pools, per-Company switches, code format, and the upgrade note (generate refund codes BEFORE upgrading; offline returns queued before the upgrade are rejected on sync where a refund code is still required).
