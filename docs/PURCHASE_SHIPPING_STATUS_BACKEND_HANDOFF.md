# Purchase shipping status: backend handoff

Date: 2026-10-25. **Frontend implemented; backend pending.**

## Task for the next AI agent

Implement the backend shipping-status resolver for the existing internal/intercompany purchase flow. Read this document and the repository CLAUDE.md before editing. This is an extension of the existing flow, not a new purchasing system.

### User-requested workflow

```text
Outlet creates Purchase Order
    -> Factory creates and submits linked Sales Order
       -> Outlet PO shows "Barang disiapkan"
    -> Factory creates and submits linked Delivery Note
       -> Outlet PO shows "Dalam proses pengiriman"
    -> Outlet records and submits Purchase Receipt
       -> Once every ordered line is fully received: "Sudah diterima"
```

The separate shipping indicator must not replace or mutate ERPNext's native PO status (such as To Receive and Bill, To Bill, or Completed). "Factory SO" is a document-link label, not an adequate shipping status. Do not merely relabel it and call the backend complete.

### Boundaries

- The user explicitly requested the SO -> preparation and DN -> in-transit milestones. Full-receipt display and unknown-value fallback were included in the approved UI proposal.
- Partial shipment and return rules below are implementation guidance for accurate quantities, not authorization to redesign purchasing or introduce a new workflow/DocType.
- SO/DN creation remains the factory's existing process; this task does not authorize automatic document creation or submission.
- Do not alter prices, taxes, permissions, receipt eligibility, stock/accounting posting, or cancellation rules. Ask before any consequential workflow or schema change.
- Inspect current source and git status: frontend work may still be uncommitted alongside an unrelated menu animation. Preserve other work. Commit/push only with explicit authorization and approved commit message.

### Implementation sequence and completion criteria

1. Trace current PO -> factory SO -> DN -> PR references and native received/returned quantity semantics in this installed ERPNext version. Reuse existing helpers where correct.
2. Add regression tests demonstrating missing shipping-status output before implementing the resolver.
3. Add the smallest batched read-only resolver and expose its result through the existing list API. No migration or stored status field should be needed for a derived display value; justify any exception before changing architecture.
4. Run backend purchase tests and the frontend purchase suite; build the SPA if frontend code changes. Do not delete failing coverage to make the suite pass.
5. Verify transitions on an isolated test site/fixtures. Dev contains a production-data copy: do not create/submit/cancel real business documents there merely for testing without authorization.
6. Follow the sanctioned dev backup/rebuild/clear-cache procedure after verification. Migrate only if an authorized schema change actually requires it; never touch prod.
7. Report changed files, actual test results, deployment result, remaining limits, and any material decisions needing user input. Update local PROJECT_STATE.md. Do not claim backend completion while all statuses still depend on mocks.

## Shipped UI

- PO and PR lists no longer show row chevrons or action buttons. Click or Enter opens detail; document actions remain there.
- PR subtitle removed. Native ERPNext status is unchanged.
- PO list has a separate Shipping status column (labeled row on mobile).
- No backend, schema, transaction, or permission logic was changed.
- Until the API provides a recognized `shipping_status`, the UI displays `—`. It never infers shipping status from `delivery_ready`, SO references, or native PO status.

## API contract

Add optional `shipping_status` to each order returned by `pos_next.api.purchase_orders.get_purchase_orders`. Keep all existing fields and behavior. Optionally return the same field from `get_purchase_order` using the same resolver.

| Value | English label | Indonesian label |
| --- | --- | --- |
| `preparing` | Goods being prepared | Barang disiapkan |
| `in_transit` | In transit | Dalam proses pengiriman |
| `received` | Received | Sudah diterima |
| null / missing / unknown | — | — |

The frontend mapping is in `POS/src/components/purchase/PurchaseOrderDialog.vue`; translations are in `pos_next/translations/id.csv`. Backend should return stable codes, not translated labels.

## Resolver requirements

1. Draft/cancelled PO: null. No submitted linked SO and no reliable tracking evidence: null.
2. Resolve the selling-company SO through its `inter_company_order_reference = PO.name`, restricted to submitted documents. The PO-side reference is not reliably populated in ERPNext v16.
3. Submitted SO with undelivered outstanding goods and no outstanding dispatched goods: `preparing`.
4. Submitted linked DN with positive outstanding quantity not yet received: `in_transit`.
5. Every ordered PO line fully received through submitted, valid linked PRs: `received`. Full receipt takes precedence over preparation. Do not classify a fully received partial shipment as the whole order received when other PO lines remain outstanding.
6. After a partial shipment is completely received, remaining undispatched order quantities mean `preparing`; a subsequent dispatch returns it to `in_transit`.
7. Ignore cancelled/draft DNs and PRs. Handle cancelled SOs and reopened/closed orders deliberately; return null where there is no trustworthy lifecycle evidence.

### Quantity correctness

- Compare **per PO/SO item linkage**, not grand sums across different items or UOMs. Excess receipt of one item must not hide missing receipt of another.
- Normalize into stock units using native conversion factors/stock quantities and precision; respect ERPNext tolerances without hiding materially outstanding quantities.
- Match receipt lines to their native intercompany DN/item references where available. An unrelated receipt must not clear a shipment's outstanding quantity.
- Inspect native ERPNext return semantics first. Return document quantities are normally already signed negative: do not subtract signed negative quantities a second time. Avoid double counting native returned/received counters alongside return rows.
- Cover multiple SOs/DNs, split shipments, returns, PR cancellation, and receipts without a matching tracked DN. Do not use `delivery_ready` as a shipment lifecycle: it only gates outstanding receivable DN goods.

## Security and performance

- Preserve the existing permission-aware PO listing and internal-supplier/company scoping. Do not replace `frappe.get_list` with unrestricted SQL for the visible order list.
- Batch related-document lookups only for already authorized PO names. Avoid one SO/DN/PR query per order and skip empty IN sets.
- Preserve intentional intercompany visibility: a cashier may lack direct read permission on the factory SO/DN while being allowed to see their own PO's coarse shipping status. Review the existing access model; never broaden direct access to factory documents or leak their details.
- Do not change receive eligibility, cancellation guards, accounting, stock posting, or native statuses as part of this display resolver.

## Verification for the backend agent

Extend `pos_next/api/test_purchase_orders.py` (existing purchase API tests), with any shared resolver tests needed:

- Draft/cancelled/no submitted SO -> null.
- Submitted SO, no submitted DN -> preparing; draft/cancelled DN ignored.
- Submitted outstanding DN -> in_transit.
- Partial shipment fully received, order still outstanding -> preparing.
- All order lines received -> received; mixed items/UOMs and overreceipt on one line cannot mask another missing line.
- Multiple/split DNs, returns with signed quantities, cancelled receipts/DNs/SOs, and native rounding/tolerances.
- PO company/permission isolation; factory document details not exposed; bounded batch queries.
- Existing receiving, cancellation, and native status tests remain green.

Run frontend purchase tests after implementing the contract, then verify real list refreshes as SO/DN/PR lifecycle changes. Until then, `—` on dev is expected and is not evidence of backend completion.
