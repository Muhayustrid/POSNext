# POS Purchase Order / Purchase Receipt Revamp Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Internal-only PO/PR in the POS SPA with ERPNext-style full-page detail views, top action bar with a single Save→Submit cycle, a clean Receive form, Excel/CSV attachments, hidden prices for cashiers and a workflow-safe PR submit.

**Architecture:** Backend changes stay in `pos_next/api/purchase_orders.py` and `pos_next/api/purchase_receipts.py` (filters, price stripping, workflow helper). Frontend keeps the two existing dialogs and adds a `view` state per dialog like `POS/src/components/pos/StockOpnameDialog.vue`; a new presentational `PurchaseDocView.vue` renders the detail page for both PO and PR.

**Tech Stack:** Frappe v16 / ERPNext v16 (Python, FrappeTestCase), Vue 3.5 + Vitest + Vue Test Utils, frappe-ui `Button`/`FeatherIcon`.

**Spec:** `docs/superpowers/specs/2026-10-18-purchase-po-pr-revamp-design.md`

## Global Constraints

- SPA = internal suppliers only (`is_internal_supplier = 1`); external PO/PR hidden entirely (D1).
- Price fields hidden unless user has `POSNext Manager`, `System Manager`, or is Administrator; enforced in API responses (D2).
- Price field keys stripped: `rate`, `amount`, `net_total`, `total_taxes_and_charges`, `grand_total`, `price_list_rate`, `price_list`, `taxes_and_charges`, `buying_price_list`.
- No PR without PO, no Approve/Reject in SPA, no new routes (D3, D4, D6).
- Actions in the top header bar, no bottom footer (D7). One primary button: new/dirty → `Save`; saved clean Draft → `Submit` or the workflow action label (D8). No "Save Draft" button anywhere.
- Attachments accept `image/*,.pdf,.xlsx,.xls,.csv`, 10 MB limit unchanged (D5).
- Shared API endpoints are not deleted (`get_purchase_receipt_draft`, `save_purchase_receipt(submit=1)` stay).
- New UI strings get rows in `pos_next/translations/id.csv` (ar/pt-br: English fallback is acceptable, note in summary).
- Backend tests run inside container via `pos_next/_pn_run_tests.py`, serially, with `FRAPPE_STREAM_LOGGING` set; frontend `npm run test:run` inside container in `POS/`.
- Work on branch `feat/purchase-revamp` from `main`. Commits: conventional, scope, Indonesian description. Subagents never run git state-changing commands; the controller commits.

## Review Focus

1. A cashier opening a PR/PO via a hand-crafted API call must still not see any price key — test every read endpoint (get, list, drafts, item details), not just the summary.
2. A cashier posting a tampered `rate` on PO or PR save must get the server price — pinned in Task 2.
3. Workflow active but the user's role has no transition from the initial state → Submit must show a clear error, not a 500 — pinned in Task 3.
4. A PO detail opened while the PO has a Draft PR from an earlier Receive → linked receipts list shows it and Receive stays usable for the remaining qty — pinned in Task 6.
5. Leaving the PO form with unsaved edits via Back must not lose the doc silently on a saved Draft: Back from a dirty form returns to the doc view showing the last saved state (no auto-save) — pinned in Task 6.

---

### Task 1: Internal-only scope (backend)

**Files:**
- Modify: `pos_next/api/purchase_orders.py` (`search_suppliers`, `get_purchase_order`, `get_purchase_orders`, `save_purchase_order`)
- Modify: `pos_next/api/purchase_receipts.py` (`get_purchase_receipt`, `get_purchase_receipts`)
- Test: `pos_next/api/test_purchase_orders.py`, `pos_next/api/test_purchase_receipts.py`

**Interfaces:**
- Produces: `_assert_internal(doc)` in `purchase_orders.py` — throws `frappe.ValidationError` with `_("{0} {1} is from an external supplier. Open it in ERPNext.").format(doc.doctype, doc.name)` when `not cint(doc.is_internal_supplier)`. Imported by `purchase_receipts.py`.

Note: both test modules currently use external suppliers for most cases. Move their shared fixture supplier to an internal supplier (`is_internal_supplier=1`, `represents_company` = another company, Allowed To Transact With the test company — `test_purchase_receipts.py:100,286` already builds one; reuse that pattern) and keep one explicit external supplier for the refusal tests.

- [ ] **Step 1: Write failing tests**
  - `test_list_excludes_external_suppliers`: one internal + one external PO saved → `get_purchase_orders()["orders"]` names contain the internal one only. Same for `get_purchase_receipts()` (create the external PR via `frappe.get_doc(...).insert()` as Admin).
  - `test_get_external_po_refused` / `test_get_external_pr_refused`: `assertRaises(frappe.ValidationError)` on `get_purchase_order(ext_po)` / `get_purchase_receipt(ext_pr)`.
  - `test_search_suppliers_internal_only`: every row of `search_suppliers("")["suppliers"]` has `is_internal_supplier == 1` (query the Supplier table).
  - `test_save_po_external_supplier_refused`: `save_purchase_order({"supplier": ext, ...})` raises `frappe.ValidationError`.
- [ ] **Step 2: Run, expect FAIL** — `docker exec -e FRAPPE_STREAM_LOGGING=1 erpnext16_dev-frappe-1 bash -c "cd /workspace/development/frappe-bench && ./env/bin/python apps/pos_next/pos_next/_pn_run_tests.py posnext.localhost pos_next.api.test_purchase_orders pos_next.api.test_purchase_receipts"` (check the runner's usage line first; match its argument form).
- [ ] **Step 3: Implement** — add `["is_internal_supplier", "=", 1]` to both list filters; `search_suppliers` filter `{"disabled": 0, "is_internal_supplier": 1}`; `_assert_internal` after the permission check in both getters; in `save_purchase_order` refuse when `frappe.db.get_value("Supplier", doc.supplier, "is_internal_supplier")` is falsy, message `_("Supplier {0} is external. Use ERPNext for external purchases.")`.
- [ ] **Step 4: Run, expect PASS** (same command; only the known pre-existing failures listed in PROJECT_STATE may remain).
- [ ] **Step 5: Commit** — `feat(purchase): batasi PO dan PR di POS hanya untuk supplier internal`

### Task 2: Price visibility (backend)

**Files:**
- Modify: `pos_next/api/purchase_orders.py` (`_po_summary` callers, `get_purchase_orders`, `get_purchase_item_details`, `save_purchase_order`)
- Modify: `pos_next/api/purchase_receipts.py` (`_draft_payload`, `_pr_summary`, `get_purchase_receipts`, `save_purchase_receipt`)
- Test: same two test modules

**Interfaces:**
- Produces in `purchase_orders.py`:
  - `PRICE_FIELDS` = the key tuple from Global Constraints.
  - `_can_see_prices() -> bool` — `frappe.session.user == "Administrator"` or roles intersect `("POSNext Manager", "System Manager")`.
  - `_hide_prices(payload: dict) -> dict` — when `not _can_see_prices()`, pops `PRICE_FIELDS` from the dict and from each dict in `payload["items"]`; returns payload. Applied at every return of summaries, list rows, drafts and item details.
- Server-side rates: PO save ignores `row["rate"]` when `not _can_see_prices()` (leave rate unset so `set_missing_values` prices it from the buying price list). PR save **always** ignores the client rate: `rate` = `Delivery Note Item.rate` when `delivery_note_item` is set, else `Purchase Order Item.rate` for `purchase_order_item`, else unset.

- [ ] **Step 1: Write failing tests** (cashier = the existing test user without POSNext Manager; manager = add a user/role in setUpClass, restore in cleanup)
  - `test_cashier_sees_no_prices`: as cashier, `get_purchase_order`, `get_purchase_orders` rows, `get_purchase_item_details`, `get_purchase_receipt`, `get_purchase_receipts` rows, `get_intercompany_receipt_draft` items: no key from `PRICE_FIELDS` present (top level and items).
  - `test_manager_sees_prices`: as manager, `get_purchase_order(name)["grand_total"]` present and `items[0]["rate"]` equals the saved rate.
  - `test_cashier_tampered_po_rate_ignored`: cashier saves PO row with `rate: 1`; `frappe.db.get_value("Purchase Order Item", {...}, "rate")` equals the price-list rate set up in the fixture (not 1).
  - `test_tampered_pr_rate_ignored`: save PR from the intercompany draft with `rate: 1` → saved row rate equals the DN item rate.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run both modules, expect PASS.**
- [ ] **Step 5: Commit** — `feat(purchase): sembunyikan harga PO dan PR dari kasir di API`

### Task 3: Workflow-safe PR submit + document links (backend)

**Files:**
- Modify: `pos_next/api/purchase_receipts.py`
- Modify: `pos_next/api/purchase_orders.py` (`_po_summary` adds `receipts`)
- Test: `pos_next/api/test_purchase_receipts.py`

**Interfaces:**
- Produces in `purchase_receipts.py`:
  - `_pr_workflow()` → active Workflow doc for `"Purchase Receipt"` or `None` (same as `stock_opname._workflow`, via `get_workflow_name` / `get_workflow`).
  - `_submit_actions(doc) -> list[str]` — `[]` when no workflow; else actions from `get_transitions(doc)` when `doc.workflow_state == wf.states[0].state`.
  - `_submit_pr(doc, action=None)` — no workflow → `doc.submit()`; workflow → resolve action (given and in list / single / error listing choices / error `_("You have no action available on {0}").format(doc.name)` when empty), then `apply_workflow(doc, action)`, `doc.reload()`.
  - `@frappe.whitelist() submit_purchase_receipt(name, action=None) -> dict` — guest + `submit` permission + docstatus 0 + `_assert_internal`, then `_submit_pr`, returns `_pr_summary(doc)`.
  - `save_purchase_receipt(submit=1)` now calls `_submit_pr(doc)` instead of `doc.submit()`.
  - `_pr_summary` adds `workflow_state` (`doc.get("workflow_state")`), `submit_actions` (`_submit_actions(doc)`), `editable` (`docstatus == 0` and (no workflow or state is initial)), `inter_company_reference`, `purchase_orders` (sorted unique `items[].purchase_order`).
  - `get_purchase_receipts` adds `workflow_state` to fields only when `frappe.get_meta("Purchase Receipt").has_field("workflow_state")`.
- Produces in `purchase_orders.py`: `_po_summary` adds `receipts: list[{name, posting_date, status, docstatus, workflow_state}]` — PRs (docstatus < 2) with any `Purchase Receipt Item.purchase_order == doc.name`, newest first.

- [ ] **Step 1: Write failing tests**
  - `test_submit_without_workflow`: save PR draft, `submit_purchase_receipt(name)` → `docstatus == 1`.
  - `test_submit_with_active_workflow`: in the test create (if missing) Workflow States `_PNXT Draft`, `_PNXT Pending`, Workflow Action Master `_PNXT Send`, and Workflow `_PNXT PR Test` on Purchase Receipt, `is_active=1`, states Draft(0)→Pending(0), transition `_PNXT Send` allowed to the test user's role. `addCleanup`: delete the test workflow, restore every other PR workflow's `is_active` snapshot, `frappe.clear_cache(doctype="Purchase Receipt")`. Assert: summary `submit_actions == ["_PNXT Send"]`, after `submit_purchase_receipt` → `workflow_state == "_PNXT Pending"`, `docstatus == 0`, `editable is False`.
  - `test_submit_with_workflow_no_action_for_role`: same workflow, transition allowed to a role the user lacks → `assertRaises(frappe.ValidationError)`.
  - `test_po_summary_lists_receipts`: after saving a PR draft from the PO, `get_purchase_order(po)["receipts"][0]["name"] == pr_name`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run both modules, expect PASS.**
- [ ] **Step 5: Commit** — `feat(purchase): submit PR aman dengan atau tanpa workflow`

### Task 4: `PurchaseDocView.vue` + attachments accept (frontend)

**Files:**
- Create: `POS/src/components/purchase/PurchaseDocView.vue`
- Create: `POS/src/components/purchase/PurchaseDocView.test.js`
- Modify: `POS/src/utils/attachments.js:4` (`ATTACHMENT_ACCEPT = "image/*,.pdf,.xlsx,.xls,.csv"`), and the hint text in `PurchaseAttachments.vue` / its callers (`"PDF, image, Excel or CSV · max 10 MB"`).

**Interfaces:**
- Produces `PurchaseDocView` (presentational, no API calls):
  - props: `title: String` (breadcrumb root, e.g. "Purchase Order"), `doc: Object` (a summary from Tasks 1–3), `kind: "po" | "pr"`, `loading: Boolean`, `pendingFiles: Array`, `canAttach: Boolean`.
  - slot `actions` (rendered in the header, right side — callers own their buttons).
  - slot `after-items` (PO puts the linked receipts list there).
  - emits: `back`, `attach-add(files)`, `attach-remove(index)`, `open-erpnext`.
  - Layout mirrors `StockOpnameDialog.vue:120-235`: header (`data-testid="doc-header"`, back arrow `doc-back`, breadcrumb `title / doc.name`, status dot + `doc.workflow_state || doc.status`, `Factory SO` badge when `doc.inter_company_order_reference`), Details card (`data-testid="doc-details"`: supplier_name, date — `transaction_date` or `posting_date`, `schedule_date` (po), `set_warehouse`, `inter_company_order_reference` (po) / `purchase_orders` + `inter_company_reference` (pr), remarks), Items table (`data-testid="doc-items"`: item_name + item_code, qty, received_qty (po, when present), uom, rate + amount columns only when `doc.items[0]` has `rate`), totals block only when `"grand_total" in doc`, Attachments section via `PurchaseAttachments`.

- [ ] **Step 1: Write failing tests** — `renders header with name and workflow_state over status`; `hides rate column and totals when price keys are absent`; `shows rate column and grand total when present`; `emits back on arrow click`; `renders actions slot inside doc-header`; `ATTACHMENT_ACCEPT includes .csv and .xlsx`.
- [ ] **Step 2: Run, expect FAIL** — `docker exec erpnext16_dev-frappe-1 bash -c "cd /workspace/development/frappe-bench/apps/pos_next/POS && npx vitest run src/components/purchase/PurchaseDocView.test.js"`.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(purchase): komponen detail dokumen PO/PR dan lampiran Excel/CSV`

### Task 5: Purchase Receipt dialog doc view (frontend)

**Files:**
- Modify: `POS/src/components/purchase/PurchaseReceiptDialog.vue`
- Create: `POS/src/components/purchase/PurchaseReceiptDialog.test.js` (mock pattern: copy the `call` mock setup of `PurchaseOrderDialog.test.js:1-240`)

**Interfaces:**
- Consumes: `PurchaseDocView` (Task 4); `get_purchase_receipt`, `submit_purchase_receipt(name, action)`, summary keys `workflow_state`, `submit_actions`, `editable` (Task 3).
- Produces: `view` ref `"list" | "doc"`; `openDoc(name)` (exported via `defineExpose` for Task 6 reuse is NOT needed — Task 6 renders `PurchaseDocView` itself).
- Behavior: row click / Enter → `openDoc` (replaces `toggleReceipt` expand; remove the peek markup and the now-unused `expandedReceipt`/`receiptDetails` refs). Header actions: when `doc.editable` and `submit_actions.length <= 1` and permission → primary button labelled `submit_actions[0] || __("Submit")`; when >1 → "Actions" dropdown (same markup as `StockOpnameDialog.vue:167-193`); `...` menu with "Open in ERPNext". Submit asks `window.confirm(__("Submit {0}? Stock will be updated and the receipt can no longer be edited.", [name]))` — use the project's existing confirm helper if `StockOpnameDialog.vue` uses one. After submit, reload doc and list. List rows show `workflow_state || status`. Attachments addable while `docstatus === 0` (upload immediately via `uploadPurchaseFiles`, then reload doc).

- [ ] **Step 1: Write failing tests** — `row click opens doc view with items`; `Back returns to list`; `Draft shows Submit in doc-header and calls submit_purchase_receipt`; `workflow action label replaces Submit`; `submitted receipt shows no primary action`; `no Save Draft text rendered`.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(purchase): halaman detail Purchase Receipt dengan aksi di header`

### Task 6: Purchase Order dialog doc view + Save→Submit form (frontend)

**Files:**
- Modify: `POS/src/components/purchase/PurchaseOrderDialog.vue`
- Modify: `POS/src/components/purchase/PurchaseOrderDialog.test.js` (rewrite the expand-peek and "Save Draft" tests; keep the rest green)

**Interfaces:**
- Consumes: `PurchaseDocView`; `get_purchase_order` summary incl. `receipts` (Task 3); existing `save_purchase_order`, `submit_purchase_order`, `cancel_purchase_order`.
- Behavior:
  - `view`: `"list" | "doc" | "form" | "receive" | "receipt"`. Row click → `openDoc(name)` → `view = "doc"` (replace `toggleOrder` peek; remove its unused refs).
  - Doc header actions (slot): Draft → `Edit` (subtle) + primary `Submit` (needs `canSubmitPO`); submitted with pending qty → primary `Receive` (existing gate: `!poDefaults.receive_requires_delivery_note || order.delivery_ready`, plus `canReceivePR`); `...` menu: `Cancel` (hidden when `isFactoryLinked`), `Open in ERPNext`.
  - `after-items` slot: "Purchase Receipts" list from `doc.receipts`; click → `view = "receipt"` rendering `PurchaseDocView kind="pr"` loaded via `get_purchase_receipt`, with the same Submit action as Task 5; Back → PO doc.
  - Form: header bar replaces the `#actions` footer (delete the `view === 'form'` and `view === 'receive'` footer blocks). Primary = `Save` when `!form.name || dirty`, else `Submit`. `dirty` = JSON snapshot compare like `StockOpnameDialog.vue:910-923`. Save → `save_purchase_order(submit: 0)` → snapshot → `view = "doc"` on the saved PO. Back from form → doc (saved PO) or list (new); no auto-save.
  - Submit confirmation as in Task 5.
  - Doc attachments: `canAttach` while `docstatus === 0` or status `To Receive` / `To Receive and Bill`; upload immediately via `uploadPurchaseFiles("Purchase Order", name, files)`, then reload the doc.
- [ ] **Step 1: Write failing tests** — `row click opens PO doc with items and linked receipts`; `attaching on a submitted To Receive PO uploads and reloads`; `Draft doc shows Edit and Submit in doc-header`; `new form primary reads Save; after save view is doc and primary reads Submit`; `editing a saved Draft flips primary back to Save`; `Back from dirty form shows last saved doc without calling save`; `clicking a linked receipt opens the receipt view`; `PO with a Draft linked receipt still shows Receive and lists the draft with a Draft pill`; `no Save Draft text rendered`; `Cancel hidden for factory-linked PO` (keep existing assertion).
- [ ] **Step 2: Run, expect FAIL** — `npx vitest run src/components/purchase/PurchaseOrderDialog.test.js`.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, expect PASS.**
- [ ] **Step 5: Commit** — `feat(purchase): halaman detail Purchase Order, aksi di header, Save lalu Submit`

### Task 7: Receive form redesign (frontend)

**Files:**
- Modify: `POS/src/components/purchase/PurchaseOrderDialog.vue` (receive view, `openReceive`, `saveReceipt`)
- Modify: `POS/src/components/purchase/PurchaseOrderDialog.test.js`

**Interfaces:**
- Consumes: `get_intercompany_receipt_draft` (always — drop the `get_purchase_receipt_draft` branch in `openReceive`, internal-only); `save_purchase_receipt(data, submit=0)`; Task 6's `"receipt"` view.
- Behavior:
  - Header bar: back arrow, breadcrumb `Purchase Order / {po} / Receive`, primary `Save`.
  - Info card: supplier_name (once), PO, Delivery Note (`inter_company_reference`), warehouse, posting date input.
  - Table columns: Item (name + code), Ordered, Received, Remaining (`pending_qty`), Qty to receive (right-aligned input `data-testid="receive-qty"`, width ≥ 7rem) + UOM text. Row with `qty > pending_qty` gets an amber note `__("More than remaining ({0})", [pending])`; Save blocked while any row is over.
  - Buttons above the table: `Fill remaining` (qty = pending_qty for all) and `Clear` (qty = 0).
  - Attachment zone under the table (hint from Task 4).
  - Save → `save_purchase_receipt(submit: 0)` with rows qty > 0 (no `rate` key sent) → upload pending files → `view = "receipt"` on the new PR, where the primary is `Submit` (Task 6 receipt view).
- [ ] **Step 1: Write/adjust failing tests** — `Receive always calls get_intercompany_receipt_draft`; `supplier name rendered once`; `Fill remaining sets every qty to pending, Clear sets 0`; `qty over remaining shows warning and blocks Save`; `Save posts submit 0 without rate and opens the receipt view with Submit`; replace the old `submits only rows with qty > 0 ... with the submit flag` test with the submit-0 variant.
- [ ] **Step 2: Run, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run full frontend suite, expect PASS** — `npm run test:run` in `POS/` (inside container); `npm run lint` shows no new findings in touched files.
- [ ] **Step 5: Commit** — `feat(purchase): rapikan form Receive dan alur Save lalu Submit`

### Task 8: Translations, build, E2E

**Files:**
- Modify: `pos_next/translations/id.csv` (every new `__()` string from Tasks 1–7; grep the diff for `__("`)

- [ ] **Step 1:** Add id.csv rows; run `bench --site posnext.localhost build --app pos_next` equivalent used by the repo (`npm run build` in `POS/`), expect success.
- [ ] **Step 2:** Run both backend modules on both sites and the full frontend suite; expect only known pre-existing failures.
- [ ] **Step 3: Manual E2E on posnext (`http://<mac-ip>:8001/pos`) as a cashier and as a POSNext Manager:** list shows internal only; PO detail opens; New PO → Save → Submit; factory SO + DN → Receive → Save → Submit; PR list → detail viewable after receive; cashier sees no money anywhere (check network tab responses); attach a `.csv` and `.xlsx`; temporarily activate the `Purchase Receipt` workflow → Submit moves to "Approval Pending by Stock Manager", then restore `is_active=0`.
- [ ] **Step 4: Commit** — `chore(purchase): terjemahan id untuk revamp PO/PR`
- [ ] **Step 5:** Update `PROJECT_STATE.md` (outcome, user-facing behaviour change: external PO/PR gone from SPA).
