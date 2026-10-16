# Accept Offline Price Mismatch instead of rejecting

An Offline Invoice is a sale that already happened: the customer paid and left with a receipt. When the server's Price Replay disagrees with the offline price, rejecting forces the cashier to edit a completed sale until it no longer matches the cash drawer. So for invoices arriving via Offline Sync we accept the price as charged, flag the invoice for HQ review, and log the difference. Online invoices still reject.

## Considered Options

- Reject (previous behaviour): strict, but leaves the cashier stuck and the books wrong.
- Global audit-only mode (`price_replay_audit_only`): too broad, it also loosens online sales.

## Consequences

A forged payload that claims to be offline can get through at a wrong price. Two things limit this: claimed rules must be real, enabled rules of the invoice company, and every mismatch is flagged and visible to HQ.
