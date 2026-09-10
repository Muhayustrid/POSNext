# POS Invoice DocType Support Design

Date: 2026-09-10
App: POS Next
Scope: Global invoice doctype switch — create `POS Invoice` (ERPNext v16) instead of `Sales Invoice` for new POS transactions.

## Problem Statement

Every POS Next transaction today creates a `Sales Invoice` (`is_pos=1`, `update_stock=1`). The POS invoices therefore mix into the same list as non-POS Sales Invoices. ERPNext v16 ships a dedicated `POS Invoice` doctype (a `SalesInvoice` subclass with its own table and list) that keeps POS traffic separated and consolidates into Sales Invoices for accounting at shift close.

The bench runs ERPNext v16.33 (`version-16` branch). **The site already has POS Invoice documents produced by ERPNext's built-in POS** (which the product owner actively used before/beside POS Next). The POS Invoice awareness already present in POS Next is the owner's own coexistence patching, not groundwork for this feature:

- Closing-shift consolidation handling, `POS Invoice Merge Log` cancel/reopen logic, and `POS Invoice` branches in `pos_closing_print`.
- `hooks.py` `"POS Invoice"` entry with two `validate` hooks.
- `invoice_type` Select field on POS Next `POS Settings` — added as a **compat field only** (commit cc76198; its own description states "POS Next always creates Sales Invoices").
- `invoices.py` accepts `data.get("doctype", "Sales Invoice")` in places, but nothing resolves or sends another doctype.

This means the site has **two POS Invoice populations once this feature ships**: invoices owned by POS Next (`posa_pos_opening_shift` set) and invoices owned by the built-in POS (`pos_opening_entry` set, consolidated by ERPNext `POS Closing Entry`). The design must keep POS Next effects off built-in-POS invoices.

What is missing: the switch itself, the full hook chain, a controller override, custom fields, offline dedup, returns, reports, and frontend awareness.

- `invoice_type` Select field on POS Next `POS Settings` — added as a **compat field only** (commit cc76198; its own description states "POS Next always creates Sales Invoices").
- `POS Closing Shift` already imports `consolidate_pos_invoices`, cancels `POS Invoice Merge Log` + consolidated Sales Invoices on reopen, and `_set/_clear_closing_entry_invoices` already handles both doctypes.
- `hooks.py` has a `"POS Invoice"` entry with two `validate` hooks (min/max pricing, shift schedule).
- `invoices.py` accepts `data.get("doctype", "Sales Invoice")` in places, but nothing resolves or sends another doctype.

What is missing: the switch itself, the full hook chain, a controller override, custom fields, offline dedup, returns, reports, and frontend awareness.

## Goal

When the global setting is `POS Invoice`, every new POS transaction (online and offline-synced) creates a `POS Invoice`. Accounting happens via consolidation at shift close (already wired in `POS Closing Shift`). Historical Sales Invoices remain untouched and still appear in reports alongside new POS Invoices.

Decisions agreed with the product owner:

1. **Global switch** (site-wide, one choice for all POS profiles) — mirrors the ERPNext v16 model.
2. **Credit sale and partial payment are blocked** in POS Invoice mode. `POSInvoice` requires `is_pos=1` and full payment; outstanding-amount features are impossible on this doctype.
3. **Reports and HQ dashboard read POS Invoice in real time** plus legacy Sales Invoices, excluding consolidated Sales Invoices (`is_consolidated=1`) to avoid double counting.
4. **No data migration** — forward-looking separation only.
5. Single pipeline (Approach 1): the doctype is resolved server-side from the setting; no separate submit path, client never chooses the doctype.

## Non-Goals

- Migrating historical Sales Invoices to POS Invoice.
- Per-POS-Profile invoice type.
- POS Invoice support on ERPNext v15 (this bench is v16; no version sniffing needed).
- New consolidation logic — ERPNext `consolidate_pos_invoices` + `POS Closing Shift` integration is reused as-is.

## ERPNext v16 Facts This Design Relies On

**Verified live on `posnext.localhost` (spike, 2026-09-10):**

- A POS Invoice **can be created and submitted** on this site with pos_next's existing `"POS Invoice"` hooks active (no crashes). Payment rows must be appended **before** `insert()`; `update_multi_mode_of_payments` never auto-fills amounts during validate.
- On POS Invoice submit: **GL entries = 0 and Stock Ledger Entries = 0** (both verified). `POSInvoice.on_submit` overrides `SalesInvoice.on_submit` and never calls `update_stock_ledger()` / GL creation. Accounting and stock land on the **consolidated Sales Invoice**: `map_doc` copies `update_stock=1` from the POS Invoice to the consolidated SI, whose submit writes GL + SLE.
- `validate_pos_opening_entry` (inherited from SalesInvoice) **requires exactly one open `POS Opening Entry`** for the `pos_profile` with `period_start_date == today`. Without one, POS Invoice creation throws. A stale open entry (older `period_start_date`) also blocks creation ("outdated"), and **cannot be cancelled while unconsolidated invoices reference it** — the site currently has such leftovers from earlier built-in-POS testing (32 POS Invoices, 8 merge logs, one stuck-open entry for OUTLET TRAINING).
- pos_next's closing-shift builder sees **0 transactions** for POS Invoices (verified) — no linkage custom field exists on POS Invoice (only `custom_nama_customer`), and `consolidate_pos_invoices` is imported in `pos_closing_shift.py` but **never called**. The existing "POS Invoice support" in pos_next is cosmetic.

**Design consequences (added after the spike):**

- `CustomPOSInvoice` must override `validate_pos_opening_entry` to accept a POS Next `POS Opening Shift` (open) instead of demanding an ERPNext `POS Opening Entry`.
- **Effective stock**: because SLEs lag until consolidation, server-side stock validation and the stock APIs must, in POS Invoice mode, compute *effective qty = Bin qty − sum of submitted-but-unconsolidated POS Invoice quantities* (per item+warehouse). Otherwise multi-terminal overselling becomes possible intraday.
- Closing-shift submit must **actually call** `consolidate_pos_invoices` for the shift's POS Next POS Invoices (grouped per customer, returns as credit notes) — the import exists, the call does not.

Other facts:

- `POSInvoice(SalesInvoice)` — submittable, own table (`tabPOS Invoice`), child table `POS Invoice Item`; `payments` reuses `Sales Invoice Payment`.
- `POSInvoice.on_submit` auto-creates a consolidated **return** Sales Invoice only when `invoice_type_in_pos == "Sales Invoice"`. `invoice_type_in_pos` is read via `frappe.db.get_single_value("POS Settings", "invoice_type")`; because POS Next replaced `POS Settings` with a non-single doctype, that read returns `None`, so no auto-consolidation fires at submit — returns consolidate at shift close, which is what we want.
- ERPNext blocks switching its own `POS Settings.invoice_type` while opening entries are open; we mirror this with POS Next's own shift doctype.

## Design

### 1. Global setting & switch gate

New **single** DocType `POS Next Invoice Settings`:

- `invoice_type` — Select `Sales Invoice\nPOS Invoice`, default `Sales Invoice`.
- `validate`: reject change when any `POS Opening Shift` is `docstatus=1, status=Open`, or when any `Offline Invoice Sync` row is pending (not yet synced). Errors explain why.
- Auto-creates with the default row; exposed in Desk for admins only.

Helper `get_pos_invoice_doctype()` (new small module, e.g. `pos_next/invoice_type.py`): reads the single (request-cached via `frappe.local`), returns `"Sales Invoice"` or `"POS Invoice"`. This is the **only** reader of the setting in code paths.

The per-profile `invoice_type` field on POS Next `POS Settings` stays as the compat field (untouched).

### 2. Server pipeline (invoices.py and friends)

- `update_invoice`, `submit_invoice`, `get_invoices`, `get_draft_invoices`, `cleanup_old_drafts`, returnable/search/return-preparation functions, and `check_offline_invoice_synced` resolve the doctype via `get_pos_invoice_doctype()` instead of defaulting to `Sales Invoice`.
- Client-sent `doctype` is stripped as a server-managed field; the server is authoritative.
- `submit_invoice` in POS Invoice mode: build doc with `is_pos=1`, full payment rows, `update_stock=1`; credit-sale / redeem-credit / zero-payment paths raise a clear validation error before touching the doc.
- **Effective stock (POS Invoice mode)**: server-side stock validation (`validate_cart_items`) and stock APIs compute *effective qty = Bin qty − submitted-but-unconsolidated POS Invoice qty* (per item+warehouse, POS Next–owned invoices), so intraday multi-terminal overselling stays blocked even though SLEs only appear at consolidation.
- **Offline Invoice Sync**: add `pos_invoice` (Link → POS Invoice) beside `sales_invoice`. Dedup by `offline_id` looks up **either** column, so an invoice queued offline in one mode and synced after a mode switch still resolves to the original document (belt-and-braces beyond the switch gate).
- `install.py` (`after_install`/`after_migrate`): idempotently add to `POS Invoice` the same custom fields POS Next adds to Sales Invoice today — `pos_queue_number`, `pos_queue_date`, `discount_confirmation_code`, `pos_applied_offer_rules`, `pos_offer_item_rules` (wherever they live today: header/child as applicable), and `posa_pos_opening_shift`. Shift-scoped queries (`get_shift_history`, session summary, closing data) filter on `posa_pos_opening_shift` of the resolved doctype.

### 3. Hooks & controller override

- `hooks.py` `"POS Invoice"` doc_events mirrors the full `"Sales Invoice"` chain:
  - `validate`: `sales_invoice_hooks.validate`, `validate_wallet_payment`, `validate_invoice_packages`, `apply_min_max_price_discounts`, `validate_invoice_discounts`, `validate_invoice_offers`, `shift_schedule.validate_invoice`.
  - `before_cancel`, `on_submit` (stock realtime, loyalty→wallet, one-time offer usage, discount-code usage, queue bump), `on_cancel` (stock realtime, release offer usage), `after_insert` (invoice-created realtime).
  - **Ownership guard** — doc_events fire for every POS Invoice on the site, including those created by ERPNext's built-in POS. POS Next–specific effects (queue bump, offer usage ledger, one-time usage, discount-code usage, loyalty→wallet, wallet reversal, invoice-created realtime) run **only when `posa_pos_opening_shift` is set** (POS Next–owned). Exceptions:
    - `pos_stock_update` realtime fires for **all** POS Invoices — POS Next terminals' stock caches should reflect built-in-POS sales too (today they go stale for those).
    - Min/max pricing and shift-schedule validation are safe for any document and stay ungated.
  - Audit each hooked function for hardcoded `Sales Invoice` queries/links; make them `doc.doctype`-aware (expected: they take a doc and are already agnostic).
- **Guard consolidated Sales Invoices**: hooks that fire on `Sales Invoice` (offer usage ledger, queue bump, one-time usage, wallet conversion) skip docs with `is_consolidated=1` — the underlying POS Invoice already recorded those effects at its own submit.
- New override in `pos_next/overrides/sales_invoice.py` (or a sibling module):

  ```python
  class CustomPOSInvoice(CustomSalesInvoice, POSInvoice):
      """POS Invoice lifecycle (ERPNext) + POS Next customizations."""
  ```

  C3 MRO gives: `CustomPOSInvoice → CustomSalesInvoice → POSInvoice → SalesInvoice`. POS Invoice's `validate`/`on_submit` lifecycle wins over SalesInvoice's; POS Next's `update_packing_list` (BOGO packed-item merge) and `use_serial_batch_fields` forcing are inherited. `make_pos_gl_entries` is never called on POS Invoice (GL happens on the consolidated SI, which already passes through `CustomSalesInvoice`). Register via `override_doctype_class: {"POS Invoice": ...}`.
- **`validate_pos_opening_entry` override** in `CustomPOSInvoice`: when `posa_pos_opening_shift` is set, validate that the POS Next shift is open (and belongs to the same profile) instead of demanding an ERPNext `POS Opening Entry`; otherwise fall through to the inherited check.
- The packed-item keying monkey-patch is audited to also cover `POS Invoice Item`.

### 4. Closing shift, wallet, returns

- `get_closing_shift_data` / `submit_closing_shift`: in POS Invoice mode populate `pos_transactions.pos_invoice` (the child already has both columns; the read side already branches). **Closing-shift submit must call `consolidate_pos_invoices`** for the shift's POS Next POS Invoices (grouped per customer; returns via credit notes) — today the function is imported but never invoked (spike finding), so consolidation currently never happens. The consolidated SI (`is_consolidated=1`, `update_stock=1`) is where GL + SLE land.
- Wallet: `pos_next/api/wallet.py` hardcodes `invoice_type: "Sales Invoice"` in the loyalty→wallet lookup — derive from `doc.doctype`. Wallet Transaction / reversal flows record the actual doctype and name; any Link-typed invoice reference gains a doctype-appropriate companion field or becomes doctype-aware (audited during implementation).
- Returns: `get_returnable_invoices`, `get_invoice_for_return`, `prepare_return_invoice` operate on the resolved doctype. A POS Invoice return is itself a POS Invoice (`is_return=1`) and consolidates at close (`process_merging_into_credit_notes`). Refund-to-wallet and wallet reversal ride the POS Invoice `on_submit`/`on_cancel` hooks.

### 5. Reports & HQ monitoring

- Helper `get_sales_report_doctypes()` (same module as §1): returns `["Sales Invoice"]` in legacy mode; `["POS Invoice", "Sales Invoice"]` in POS Invoice mode.
- The 5 script reports, `hq_monitoring.get_sales_monitoring`, session summary, and cashier-related queries are updated to iterate the returned doctypes, with Sales Invoice queries **excluding `is_consolidated=1`** rows. Column sets are compatible because POS Invoice shares the Sales Invoice schema.
- Built-in-POS sales stay represented exactly once in both modes: today they appear in reports as consolidated Sales Invoices; in POS Invoice mode they appear as their POS Invoice rows (real-time, before the built-in POS shift closes) while the consolidated SI is excluded. Totals are identical across the mode switch.
- Shift-scoped reports (Sales vs Shifts, session summary) filter POS Next invoices by `posa_pos_opening_shift`; built-in-POS invoices never match and stay out of POS Next shift reporting, same as today.
- `pos_closing_print` utilities already handle both doctypes — verified during implementation.

### 6. Frontend (POS/)

- `bootstrap.get_initial_data` exposes `invoice_type`; `posSettings` store holds it.
- PaymentDialog hides credit-sale / pay-on-receivable paths when `invoice_type === "POS Invoice"` (server still validates — UI hiding is cosmetic).
- Invoice history / returns / drafts / offline dialogs render whatever the APIs return; APIs now include `doctype` per row and the UI uses it for Desk deep-links and return flow routing. Receipt rendering is unchanged (identical field names).
- Partial Payments management continues to work for legacy unpaid Sales Invoices; no new partial invoices can be created in POS Invoice mode.

### 7. Testing

Extend the `_pn_run_tests.py` suite against `posnext.localhost`:

1. POS Invoice mode end-to-end: submit invoice → hooks fire (queue number assigned, offer usage ledger row, wallet ledger), `pos_stock_update` emitted, **GL entries = 0 and SLE = 0 at submit** (spike-verified baseline), and effective-stock validation rejects a second sale that would oversell the unconsolidated qty.
2. Close shift → consolidation produces one consolidated Sales Invoice per customer with `is_consolidated=1` **carrying the GL and Stock Ledger entries**; consolidated SI does **not** re-bump queue / duplicate offer usage.
3. Reports union: totals over `POS Invoice + legacy SI (non-consolidated)` match the sum of parts; no double counting.
4. Offline dedup cross-mode: queue invoice in SI mode, switch mode (queue empty), sync → returns the original SI, no duplicate.
5. Gate: switching `invoice_type` rejected with an open shift; credit sale rejected in POS Invoice mode.
6. **Coexistence**: a built-in-POS POS Invoice (no `posa_pos_opening_shift`) submitted alongside a POS Next one — POS Next effects skip it (no queue bump, no offer ledger, no wallet conversion), `pos_stock_update` fires for it, reports count it exactly once.
7. Regression: Sales Invoice mode — existing tests pass unchanged.

## Risks & Open Items (resolved during implementation)

- Exact custom-field set on Sales Invoice to mirror (inventory from `install.py`) — POS Invoice child table names differ (`POS Invoice Item`).
- `POS Offer Usage` / `One Time Customer Offer Usage` / `POS Print Log` link fields that assume Sales Invoice need doctype-aware storage.
- `Sales Invoice Reference` child of `POS Closing Shift` rows must be populated with the right link column per mode.
- ERPNext `POSInvoice.validate_pos_opening_entry` expects ERPNext's `POS Opening Entry`; POS Next's `posa_pos_opening_shift` custom field is what our queries use — confirm validate passes with `pos_opening_entry` unset.
- `CustomSalesInvoice.make_pos_gl_entries` v15/v16 `post_change_gl_entries` detection applies only to consolidated SIs — no change expected.
