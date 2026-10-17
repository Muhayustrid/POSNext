# POS Next

Point-of-sale for bakery/café outlets on ERPNext: cashiers sell at the till, head office (HQ) monitors and controls outlets.

## Language

### Offline selling

**Offline Invoice**:
A sale completed at the till while the server is unreachable, held in the device queue until it is synced.
_Avoid_: pending invoice, local invoice

**Offline Sync**:
Submitting queued Offline Invoices to the server once the device is back online.
_Avoid_: upload, replay

**Price Replay**:
The server re-running the offer pricing on a submitted invoice to confirm every discounted or free line matches what the offers actually grant.
_Avoid_: price check, revalidation

**Offline Price Mismatch**:
An Offline Invoice whose charged prices differ from the Price Replay; it is accepted as charged (the customer already paid) and flagged for HQ review.
_Avoid_: sync failure, price error

### Printing

**Receipt Template**:
The single customer-receipt layout ("POS Next Receipt"); online and offline receipts must look the same.
_Avoid_: print format (when meaning the layout), offline template

### Stock

**Stock Opname**:
An outlet's physical stock count, entered in the POS app as a draft Stock Reconciliation and approved by HQ in Desk; only approval moves stock and GL.
_Avoid_: stock count, inventory count, stock take

**Blind Count**:
A count where the outlet enters only the quantity on the shelf and never sees the system quantity, valuation rate or difference.
_Avoid_: unguided count, open count
