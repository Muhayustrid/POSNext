# iMin Device Probe & On-Device Verification — Findings Log

Status: **Part A CLOSED (probe v3, 2026-09-02, unit = iMin 58mm). Part B: core flows verified
2026-09-03 (rounds 1–3); open items listed inline.** All v1.4.0 specifics live in
`POS/src/utils/print/imin_client.js`.

## Part A — Phase 0 probe (open `https://<site>/imin-probe` on the device)

| # | Question | Result | Notes |
| --- | --- | --- | --- |
| 1 | Does `ws://127.0.0.1:8081/websocket` connect and answer the v1 ping? | **YES** (probe v1–v3, 2026-09-02) | Heartbeat ~3 s; auto-reconnect ~4 s per SDK. |
| 2 | Does `POST /upload` + WS `type:26` print the bitmap? | **YES** (probe v2) | Service wants a multipart **file** part (SDK-canonical Blob); posting the data URL as a plain string field is a 400. `printSingleBitmap()` already does it right. |
| 3 | Does `setPageFormat` (`type:25`, `1` vs `0`) change the printed width? | **1 = 58mm confirmed** on this unit | Unit has 58mm paper; the 576-dot 80mm figure stays SDK-sourced (clamps in v1.4.0), not device-measured. |
| 4 | Does a bitmap print auto-cut, or is `partialCut` (`type:5`) required? | **Explicit cut required** | Verified in the vendored v1.4.0 source: no `partialCut` inside `printSingleBitmap`. The demo-header claim ("内部已经做了 partialCut") does not hold for this build — see the feed finding in Part C. |
| 5 | `getPrinterStatus` codes | **0 = "normal", 3/7/8/99 = faults; NO busy state** | Critical: replies 0 **while the head is still printing** (observed 2026-09-03), so it can never signal print completion. Under load the service can also drop or ~2 s-delay the reply — the driver wraps each call in a race timeout. |
| 6 | Device model + Android API level | Both an Android <11 and an Android >11 unit verified working with the iMin JS print service (user-confirmed); model/service version string not captured. | |

## Part B — On-device verification (Task 10)

Open `/pos/direct-print` from the POSNext home shortcut.

- [x] Test Print at 58mm — straight, full width, correct physical size (after the 96→205 DPI
  translation, commit `fa7ff68`)
- [ ] Test Print at 80mm — **not testable on the current 58mm unit**
- [x] Checkout receipt prints via the iMin driver; `POS Print Log` row `Success` + effective
  paper `58mm` (round 2, 2026-09-03)
- [ ] Printer off → checkout again: **OPEN** (scheduled re-test). Note from 2026-09-03 logs:
  out-of-paper correctly failed the imin driver (`Printer out of paper`), but the browser
  fallback was then **popup-blocked** in the fire-and-forget background context — the EOD
  printview-fallback lane (`9440c7b`) covers EOD, not checkout.
- [x] Close a shift → EOD printed directly on the iMin (round 2; template 471 fixed:
  `company_address` Link → `Address.address_line1`)
- [x] Restart the iMin app — device config survives (localStorage; v2 migration heals the
  v1 `copyDelayMs: 0` corruption)
- [x] Both Android units behave the same (user-confirmed for the print service)

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
- **Feed rule corrected by the device (probe v3 run).** First press printed all-green
  logs (WS OK · upload A 200 · status 0 = job complete) yet NO paper came out; the second
  press produced the first run's receipt. Cause: the driver forbade feeding after
  `printSingleBitmap`, on the strength of a claim in the iMin demo header
  ("内部已经做了 partialCut") that does **not** hold for the vendored v1.4.0 build
  (verified: no `partialCut` call inside it). With no feed, content stays inside the
  mechanism until the next job drags it out — and `getPrinterStatus()==0` is a useless
  completion signal for "has paper physically left the printer" because the service
  considers the job done the moment it is queued. Fixed in `imin_client.js` and probe v3:
  bitmap -> 200 ms settle -> `printAndFeedPaper(100)` -> optional `partialCut()`, matching
  iMin's own `sendPrintingJobFixed()` reference flow.
- ~~`POS Print Log` Fallback row dropped `error_message`~~ — **fixed**: the transport now
  records why earlier drivers failed (and marks a skipped driver) on the Fallback row, so
  "why did this not come out of the iMin" is answerable from the log. See `transport.js`.

## Part D — Print rounds 1–3 (2026-09-03) — behavioural findings

- **Tear-off pause between copies.** With no busy state in the SDK (Part A #5), the only
  anchor for "the previous copy physically finished" is a wall-clock reservation
  `SETTLE + height / PRINT_DOTS_PER_SECOND`. At 400 dots/s it under-reserved and the
  `imin_copy_delay_ms` pause was physically swallowed — and the taller the bitmap the worse
  the swallow (checkout invoices printed back-to-back while the short test receipt still
  paused). Device evidence: ~13 s for a 2-copy 1-item receipt at fontScale 100 → real
  throughput ~200–250 dots/s. Fixed at `PRINT_DOTS_PER_SECOND = 200` + one `log.info` per
  copy (`copy/heightDots/elapsedMs/reserveMs/pauseMs`; enable with
  `posLogger.setEnabled(true)` + `setLevel("INFO")`), commit `8d78ea6`. Test-print verified;
  checkout re-check pending 2026-09-04.
- **Popup gating at checkout.** Popup success ALWAYS shows first; the auto direct print is
  fire-and-forget and gated only by the POS Profile's `print_receipt_on_order_complete`
  (standard ERPNext); `silent_print` merely picks the direct-vs-browser lane. Concurrent
  prints of the same invoice dedupe onto the running job.
- **EOD printview fallback** (`9440c7b`): total direct-print failure + `fallback_enabled`
  opens `/printview` with `trigger_print=1` (format "POS Next EOD Report"); strict mode
  rethrows.
- **PWA gotcha (device-side staleness).** `/pos` assets ride a CacheFirst SW + 12 h HTTP
  cache on unhashed URLs; after a rebuild, verify the "build {timestamp}" marker on the
  Direct Print card and Clear & reset site data once (device config in localStorage must be
  re-entered) before judging behaviour.
