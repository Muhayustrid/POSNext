# Separate Refund Code + Per-Company Code Gate — design (2026-10-13)

> Builds on `2026-09-06-discount-code-gate-design.md` (discount code gate v2). That spec stays authoritative for discount detection, offer/coupon exemptions and the SEC-10 claim verification; this one changes only the code pools, the on/off switch and the code format.

## Problem

1. Refunds reuse the discount pool: `POS Discount Confirmation Code` is validated by the same `validate_code()` and stored in the same invoice field (`discount_confirmation_code`) for both. HQ sees one list and a discount code also unlocks refunds.
2. The "no code needed" switch is incomplete: refunds have `require_refund_code` (POS Settings per profile, else Global Settings); discounts have no switch (gate hardcoded on, `get_status` returns `enabled: True`).
3. Codes must be easy to set by hand (e.g. `MESSI123`), but the manual-entry path rejects any character outside the dictation-safe alphabet (no `0 O 1 I L`), so `MESSI123` is refused.

## Decisions (confirmed with the user)

1. **Separate doctype for refund codes** — new `POS Refund Code`. The existing discount code doctype is untouched and stays the discount pool.
2. **Switch per Company (outlet)**, one for discounts and one for refunds, independent of each other.
3. **Codes must be unique and settable by hand.** Risk of guessable codes accepted by the user.
4. **Usage trail**: keep the current "last use" audit on the code; add a "View usage" button that lists the invoices carrying the code.

## 1. `POS Refund Code` doctype

Same shape as `POS Discount Confirmation Code`: `code`, `valid_from`, `valid_upto`, `status` (Active/Disabled), `notes`, `company_scope` (All Outlets / Selected Outlets / All Outlets Except), `companies` (child table reusing `POS Discount Code Company`), audit fields (`used_count`, `last_used_by`, `last_used_in_invoice`, `last_used_on`).

- Same permissions as the discount code doctype (System Manager full; POSNext Manager and Sales Manager create/read).
- Own Desk JS (`pos_refund_code.js`, list JS) with its own **Generate Codes** button, backed by its own `generate_codes`.
- Autoname/uniqueness/validation logic is shared with the discount doctype, not copied: the pieces that are identical (random generation, code-format validation, uniqueness check, scope validation) move to one module-level helper used by both controllers.
- New Sales Invoice custom field `refund_confirmation_code` (Data, read-only, no_copy, print_hide), defined in `install.py` next to `discount_confirmation_code`. Returns read and stamp this field; sales keep using `discount_confirmation_code`.
- The two pools never validate each other's codes (a discount code does not pass the refund gate and vice versa).
- Verify in the plan whether the `POS Invoice` doctype (which mirrors `discount_confirmation_code`) needs the new field too.

`pos_next/overrides/discount_code.py` stays the single module that enforces both gates. `validate_code` takes the code doctype (and the invoice field name where needed) as parameters; `_code_usability_error` and `_scoped_outlets` are reused as-is, the child-table lookup being parameterised by parent doctype.

## 2. Per-Company switches

Two Custom Fields on `Company`, defined in `install.py` (the `"Company"` block, same place as the payback target fields):

- `pos_require_discount_code` (Check, default 1)
- `pos_require_refund_code` (Check, default 1)

Semantics:
- Unset/NULL = required (fail closed), same as `refund_code_required` today.
- Switch off → the server skips the code check for that kind: `validate_invoice_discounts` skips `validate_code` (sales: when `invoice_has_manual_discount`; returns: always), and `record_code_usage_on_submit` skips the usage stamp.
- Discount detection is untouched: offers/coupons exemption logic still decides whether a discount is manual. The switch only removes the *code* requirement for outlets that opted out.
- `refund_code_required(pos_profile)` is replaced by `refund_code_required(company)`; a new `discount_code_required(company)` mirrors it. Invoice without a company → required (fail closed).
- Only System Manager may change either field. Enforced by a `Company` `validate` doc_event (throws when either field changed and the user lacks System Manager). A `permlevel` was rejected: standard Company perms let Accounts Manager / HR Manager write at permlevel 0, and permlevel 1 would need a Custom DocPerm on Company, which makes Frappe drop the standard perms of that doctype.

API: `pos_next.api.discount_code.get_status(company)` returns `{discount_code_required, refund_code_required}` (the hardcoded `enabled: True` goes away; keep `enabled` equal to `discount_code_required` for the existing store test until the frontend is updated in the same change). Add a `refund` flag/doctype argument to `check_code` so the return dialog validates against the refund pool; `validate_confirmation_code` (cart-based discount check) stays discount-only.

### Retired setting
`require_refund_code` in POS Settings and POS Next Global Settings is removed from the UI and no longer read. The DB columns are left in place (model sync does not drop them). Tests that set `require_refund_code = 0` (about 8 files) switch to setting `pos_require_refund_code = 0` on the test Company.

## 3. Code format and uniqueness (both doctypes)

- Auto-generated codes keep the dictation-safe alphabet and 8 characters.
- Manually typed codes: `A–Z` and `0–9` only (input is upper-cased, so `messi123` is stored and matched as `MESSI123`), 6–20 characters, no spaces or symbols. The minimum length of 6 is a floor against trivially guessable values; the validation endpoints are already rate-limited (20 attempts / 5 min).
- Uniqueness: explicit check in `validate()` with a clear message ("Code MESSI123 is already in use") instead of a raw DB duplicate error. Uniqueness is per doctype — the same string may exist in both pools since the pools never validate each other.
- A code with `used_count > 0` still cannot change value (audit anchor).

## 4. Usage trail

Existing audit fields (last user / invoice / time, `used_count`) remain; they only describe the latest use. For the full history each invoice already stores its code: add a **View usage** button on both code forms that opens the Sales Invoice list filtered by `discount_confirmation_code` (or `refund_confirmation_code`) = the code. No new log doctype.

## 5. Frontend

- `POS/src/stores/discountRestriction.js`: read `discount_code_required` from `get_status(company)`; when false, `needsCodeForCart` is false and the discount dialogs (EditItemDialog, PaymentDialog) don't ask for a code or lock the discount inputs.
- `ReturnInvoiceDialog.vue`: use `refund_code_required` from `get_status(company)` (company instead of profile), validate through the refund pool, and send `refund_confirmation_code` instead of `discount_confirmation_code`.
- `POSSale.vue`, `useInvoice.js`, `offline.worker.js` already carry `discount_confirmation_code` in payloads; return payloads gain `refund_confirmation_code`. Offline queue: a return queued before the upgrade carries the old field — see Migration.
- `backdate_entry.js` (Desk page): same `get_status` change as the return dialog.
- New UI strings get CSV entries (`docs/LOCALIZATION.md`).

## 6. Migration

Patch `v2_18_0` (post_model_sync, after the Company custom fields exist; `install.py` already creates them on migrate):

1. For every Company, set `pos_require_refund_code` from the current effective `require_refund_code` of its POS Profiles: required if any enabled profile of that company requires it (or none is configured); off only when every profile of the company has it off. `pos_require_discount_code` = 1.
2. Existing discount codes are NOT copied into `POS Refund Code`.

**Release note (must be in the changelog):** before upgrading, HQ generates refund codes for outlets that still require them; otherwise returns at those outlets are blocked after the upgrade. Offline returns queued before the upgrade (with only `discount_confirmation_code`) will be rejected on sync for outlets that still require a refund code, and must be re-entered with a refund code.

## 7. Testing

Backend (serial, via `_pn_run_tests.py`):
- Discount gate: switch on/off per company; off ⇒ discounted invoice saves with no code and no usage stamp.
- Refund gate: switch on/off per company; on ⇒ return needs an active `POS Refund Code`; a valid *discount* code is rejected on a return; a valid *refund* code is rejected on a discounted sale.
- Scope (Selected / All Except) for refund codes; validity window; disabled code.
- Code format: `MESSI123` accepted and stored upper-case; too short / symbols / spaces rejected; duplicate rejected with the clear message; auto-generate still uses the safe alphabet; used code value immutable.
- Unset company switch ⇒ required (fail closed); invoice without company ⇒ required.
- Migration patch: mapping from `require_refund_code` to the company switch.
- Update the ~8 existing tests that disable `require_refund_code`.

Frontend (inside the container, `npm run test:run`): `discountRestriction.test.js` for the switch, return dialog skipping/using the refund code.

## Out of scope

- Cross-pool uniqueness (same string in both pools) — allowed.
- A usage-history doctype — the invoice list filter covers it.
- Changing the discount detection/exemption rules.
- Moving the existing discount codes anywhere.

## Risks

- Guessable manual codes (accepted by the user). Mitigated by min length 6 and the endpoint rate limit.
- Anyone with System Manager can open the gate by editing the Company switches; that is the same trust level that can already disable codes.
- Upgrade ordering (refund codes must exist first) — release note above.
