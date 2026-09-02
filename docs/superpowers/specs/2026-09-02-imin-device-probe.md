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

- ~~Probe page `pollStatus` reads `value` at the top level~~ — **fixed in probe v2**: replies
  are matched by `type` and the status code is read at `data.value` (the nested path the SDK
  uses). A valid `{"type":2,"data":{"value":0}}` reply no longer logs "No status reply".
- ~~Probe page upload 400~~ — **the 400 was a probe artifact, not the device.** v1 posted the
  data URL as a string (`fd.append("file", dataUrl)` → a text form field, no `filename`); the
  print service wants a multipart file part. The production driver was never affected:
  `imin_client.js` calls `printSingleBitmap()`, which converts to a Blob internally
  (`imin-printer.js:590`). Probe v2 posts variant A (SDK-canonical Blob) and an
  "upload variants" button (A blob / B raw / C json / D the old string) confirms the accepted
  format on any new unit.
- `paper.js` `dotsForPaper("constructor")` returns via the prototype chain — fixed with
  `Object.hasOwn`, but keep in mind for other lookups.
- `qz_client.getStatus` has no try/catch (the transport's fallback chain covers it).
- `printInvoice.js` config init is once-per-session and not keyed on profile: an EOD-first
  session with no profile leaves the transport on defaults until reload.
- Direct Print page: logs-error state replaces cached rows; paper pill shows the transport
  (server) value, not the just-saved device value.
- ~~`POS Print Log` Fallback row dropped `error_message`~~ — **fixed**: the transport now
  records why earlier drivers failed (and marks a skipped driver) on the Fallback row, so
  "why did this not come out of the iMin" is answerable from the log. See `transport.js`.
