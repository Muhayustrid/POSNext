# Discount & Refund Code Gate

Head office (HQ) controls two things at the POS with confirmation codes: manual **discounts** and **refunds (returns)**. Each has its own code pool and its own on/off switch in POS Settings.

Design: `docs/superpowers/specs/2026-10-13-separate-refund-code-and-per-company-gate-design.md`.

## Two separate code pools

| | Discount | Refund |
|---|---|---|
| Doctype | `POS Discount Confirmation Code` | `POS Refund Code` |
| Invoice field | `discount_confirmation_code` | `refund_confirmation_code` |
| Needed when | a sale has a manual discount (item or cart) | an invoice is a return |

A discount code is never accepted on a return, and a refund code is never accepted for a discount. The same string may exist in both pools.

Both doctypes: multi-use until disabled, validity window (`valid_from` / `valid_upto`), outlet scope (All Outlets / Selected Outlets / All Outlets Except), and a "last use" audit (`used_count`, last user, last invoice, last time). Use **View usage** on a code to open the POS Invoice list filtered by that code (the list shows matching invoices; it is not a complete history — drafts, cancelled invoices and usages recorded under the other invoice doctype are included or missing accordingly, while `used_count` counts submits only).

## Switches (POS Settings)

`require_discount_code` (Discounts & Pricing section) and `require_refund_code` (Returns section), both default on. They live in **POS Next Global Settings** and in each profile's **POS Settings**, resolved like every other setting:

- An **enabled** POS Settings row for the invoice's POS Profile wins; otherwise the global value applies. An invoice without a profile uses the global value.
- Unset means **required** (fail closed).
- Only a System Manager can change what a profile resolves to: flipping a switch on a row, or creating, enabling or disabling a row whose switches differ from the global value. Other POS Settings fields stay editable for managers; the POS app pins both switches on save. Global Settings is System Manager only anyway.
- The code scope still follows the **POS Profile company** (a doc naming both a company and a profile of another company is rejected).

## Code format

- Auto-generated (Generate Codes): 8 characters, no `0 O 1 I L`.
- Typed by hand (e.g. `MESSI123`): letters A-Z and digits 0-9, 6-20 characters, stored upper-case. No spaces or symbols.
- A code must be unique within its doctype. A code that has been used cannot change value (only its status).
- Short manual codes are easy to guess; the check endpoints are rate-limited (20 attempts / 5 minutes).

## Upgrade notes

1. **Create refund codes BEFORE upgrading** for every outlet that still needs them. Existing discount codes are not copied into the refund pool, so returns at those outlets are blocked until refund codes exist.
2. The migration `v2_32_0.move_code_switches_to_pos_settings` sets both global switches on, copies each Company's old switches onto every POS Settings row of its profiles (NULL = on), then deletes the Company fields and the retired `POS Code Gate Group` doctype. Profiles without a POS Settings row now follow the global value.
3. Returns cannot be created offline (the return dialog is disabled without connection), so there is no offline return queue to migrate; every return is validated live against the refund gate.
4. Run `bench migrate` on every site (`posnext.localhost` and the test site).
