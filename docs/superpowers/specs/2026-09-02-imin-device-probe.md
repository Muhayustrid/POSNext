# iMin Device Probe & On-Device Verification — Findings Log

Status: **OPEN — requires physical iMin hardware.** No agent can fill this in.

This file is the Phase 0 gate from `2026-09-02-imin-direct-print-design.md` plus Task 10 of
the implementation plan. The branch `feat/imin-direct-print` is code-complete without it, but
**do not deploy to production devices until Part A is recorded** — every SDK-level assumption
in the code is provisional on it. All v1.4.0 specifics live in
`POS/src/utils/print/imin_client.js`, so a divergent result costs one file's rework.

## Part A — Phase 0 probe (open `https://<site>/imin-probe` on the device)

| # | Question | Result | Notes |
| --- | --- | --- | --- |
| 1 | Does `ws://127.0.0.1:8081/websocket` connect and answer the v1 ping? | | If not: which endpoint/port does? |
| 2 | Does `POST /upload` + WS `type:26` print the bitmap? | | HTTP status; visible output |
| 3 | Does `setPageFormat` (`type:25`, value `1` vs `0`) change the printed width? | | Confirms 1=58mm / 0=80mm |
| 4 | Does a bitmap print auto-cut, or is `partialCut` (`type:5`) required? | | v1.4.0 expects explicit cut |
| 5 | `getPrinterStatus` codes for: idle / head open / no paper | | Expect 0 / 3 / 7-8 |
| 6 | Device model + Android API level + any service version string | | From the page header |

Record the 80mm dot count observed here — the code assumes **576**.

## Part B — On-device verification (Task 10)

Open `/pos/direct-print` from the POSNext home shortcut.

- [ ] Test Print at 58mm — straight, not skewed, full width used, no leading blank lines
- [ ] Test Print at 80mm — same, at the wider width
- [ ] Checkout receipt prints via the iMin driver; `POS Print Log` row shows `Success` + the effective paper
- [ ] Printer off → checkout again: transport falls back (browser) and the log shows `Fallback`
- [ ] Close a shift, print the EOD report — rides the same transport
- [ ] Restart the iMin app — device config (host / paper / cut) survives (it is in `localStorage`)
- [ ] Both Android units (<11 and >11) behave the same

## Part C — Deferred minors (fix if the device work exposes them)

- Probe page: dead `wsReady` var; duplicated status-reply branch; `connectWS` leaks the previous
  socket on repeat press; `pollStatus` reads `value` at the top level — if a reply looks empty,
  read the raw `WS recv:` line above it (replies may be nested).
- `paper.js` `dotsForPaper("constructor")` returns via the prototype chain — fixed with
  `Object.hasOwn`, but keep in mind for other lookups.
- `qz_client.getStatus` has no try/catch (the transport's fallback chain covers it).
- `printInvoice.js` config init is once-per-session and not keyed on profile: an EOD-first
  session with no profile leaves the transport on defaults until reload.
- Direct Print page: logs-error state replaces cached rows; paper pill shows the transport
  (server) value, not the just-saved device value.
