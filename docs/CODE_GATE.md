# Discount & Refund Code Gate

Head office (HQ) controls two things at the POS with confirmation codes: manual **discounts** and **refunds (returns)**. Each has its own code pool and its own per-outlet on/off switch.

Design: `docs/superpowers/specs/2026-10-13-separate-refund-code-and-per-company-gate-design.md`.

## Two separate code pools

| | Discount | Refund |
|---|---|---|
| Doctype | `POS Discount Confirmation Code` | `POS Refund Code` |
| Invoice field | `discount_confirmation_code` | `refund_confirmation_code` |
| Needed when | a sale has a manual discount (item or cart) | an invoice is a return |

A discount code is never accepted on a return, and a refund code is never accepted for a discount. The same string may exist in both pools.

Both doctypes: multi-use until disabled, validity window (`valid_from` / `valid_upto`), outlet scope (All Outlets / Selected Outlets / All Outlets Except), and a "last use" audit (`used_count`, last user, last invoice, last time). Use **View usage** on a code to open the Sales Invoice list filtered by that code (full history).

## Per-outlet switches

On the **Company** form: `pos_require_discount_code` and `pos_require_refund_code` (both default on). Uncheck one to let that outlet skip the code for that kind.

- Unset (NULL) means **required** (fail closed).
- Only a System Manager can change them (enforced on Company save).
- The server decides the outlet from the invoice's **POS Profile company**, not from a value the client sends.
- The old `require_refund_code` checkbox in POS Settings / Global Settings is retired and no longer read.

## Code format

- Auto-generated (Generate Codes): 8 characters, no `0 O 1 I L`.
- Typed by hand (e.g. `MESSI123`): letters A-Z and digits 0-9, 6-20 characters, stored upper-case. No spaces or symbols.
- A code must be unique within its doctype. A code that has been used cannot change value (only its status).
- Short manual codes are easy to guess; the check endpoints are rate-limited (20 attempts / 5 minutes).

## Upgrade notes

1. **Create refund codes BEFORE upgrading** for every outlet that still needs them. Existing discount codes are not copied into the refund pool, so returns at those outlets are blocked until refund codes exist.
2. The migration (`v2_18_0.seed_company_code_switches`) sets each Company's refund switch from the old setting: off only if the company has at least one POS Profile and all of them had `require_refund_code` off; otherwise on. The discount switch is on for all.
3. Offline returns queued before the upgrade carry only `discount_confirmation_code`; they are rejected on sync where a refund code is still required and must be re-entered with a refund code.
4. Run `bench migrate` on every site (`posnext.localhost` and the test site).
