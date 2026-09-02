# iMin Direct Print — Design

Date: 2026-09-02
Branch: `feat/imin-direct-print`
Status: Approved design, pending implementation plan

## Problem

POSNext currently prints through two paths only: the browser (`/printview` popup) and
QZ Tray (`POS/src/utils/qzTray.js`). On an iMin Android POS terminal neither works well —
QZ Tray is a desktop Java app that does not run on Android, so cashiers fall back to the
browser print dialog on a device with a built-in thermal printer.

A standalone app (`github.com/Muhayustrid/pos_direct_print`, branch `version-16`) already
prints successfully on iMin hardware on both Android <11 and >11, but it is not usable as-is:

1. **Skewed / stretched output.** It sends whatever bitmap `html2canvas` produces to
   `printSingleBitmap()`. When the bitmap width does not match the printer's dot width the
   firmware scales it, producing skewed receipts.
2. **Paper width setting has no effect.** There is a setting, but the render path never
   locks the canvas to the paper's dot count, so changing it changes nothing visible.
3. **Structurally too heavy.** 4 DocTypes (`POS Print Job`, `POS Print Attempt`,
   `POS Print Terminal`, `POS Print Settings` + Profile), a reservation module, a state
   machine, receipt-hash vectors, a parallel receipt builder, and 18 contract documents —
   all for what is fundamentally `connect → check status → print bitmap`.

This design brings iMin printing into `pos_next` itself, fixes the two output bugs at
their root, and routes **every** existing print path (checkout receipt, offline receipt,
invoice reprint, and the end-of-day report at shift closing) through one transport.

## Specification layers

Two things are commonly conflated and must be kept separate in this design:
**hardware geometry**, which is fixed by the printer and confirmed by official iMin
documentation, and **SDK/API behaviour**, which belongs to a specific client library and
local print service and is *not* guaranteed across versions. All layout math uses the
first; the driver implementation must be validated against the second on the actual
target device before any code is assumed correct.

### Hardware paper/dot geometry (official, stable)

| Property | 58mm paper | 80mm paper |
| --- | --- | --- |
| Physical paper width | 58 mm | 80 mm |
| Effective printing width | 48 mm | 72 mm |
| Effective dots | **384** | **576** |

- Resolution is **205 DPI = 8 dots/mm** (384 dots ÷ 48 mm).
- The full paper width is never the print area. The content budget is dots, snapped to a
  multiple of 8.
- Paper chute depth: 40 mm on M2 / D1 / D1 Pro, 80 mm on D1w / D4 / S1. Informational only —
  it bounds roll diameter, not print width.
- Monospace text budget (Font A, if text APIs are ever used): 32 chars/line at 58 mm,
  48 at 80 mm.

These numbers hold regardless of which SDK the device exposes; they are properties of the
print head and paper path.

### JS SDK / print-service API (versioned — validate on target)

Everything below describes **`imin-printer.js` v1.4.0 (2022, MIT, author "archiesong")**,
the library bundled in every demo under `/Users/rotiropi/iMinJSPrinterSDK` and vendored by
the old `pos_direct_print` app. iMin ships newer surfaces — an `imin-printer-v2` npm package
and "iMinPrinterSDK 2.0" developer documentation exist — and V2 behaviour must not be
assumed identical to V1.x. Treat this table as **provisional until the Phase 0 probe passes**
against the actual POSNext device:

- v1.4.0 transport: `ws://<host>:8081/websocket` (heartbeat ping ~3 s, auto-reconnect ~4 s,
  both built into the SDK) plus an HTTP `POST http://<host>:8081/upload` used by
  `printSingleBitmap(dataURL, alignmentMode?)`, which then sends WS command type `26`
  (no alignment) or `27` (with alignment).
- **Resolve means "queued", not "printed".** The promise resolves as soon as the upload
  succeeds and the command is written to the socket. The SDK ships its own bug report about
  this (`imin-customer-odoo.js`): a customer appended three `printAndLineFeed()` calls after
  awaiting `printSingleBitmap`, and those feeds executed *after* the cut, so every following
  receipt began with blank lines in that demo build (it did `partialCut()` inside
  `printSingleBitmap` before the extra feeds). In THIS vendored build
  (`1.4.0/imin-printer.js`) `printSingleBitmap` only queues the bitmap
  (upload + type 26) — it does not feed or cut — so a **feed after the
  bitmap IS required** to push the receipt past the tear bar; otherwise
  the content sits inside the mechanism until the next job drags it out
  (probe v2→v3). The safe recipe for this build is bitmap -> 200 ms
  settle -> `printAndFeedPaper(100)` -> optional `partialCut()`.
  Use `getPrinterStatus()` returning `0` as the completion gate before starting the next job.
- v1.4.0 does **not** auto-cut (older SDK versions did). Cutting is explicit: `partialCut()`
  → command type `5`.
- `setAlignment(a)`: `0` left, `1` centre, `2` right (type `6`).
- `setPageFormat(style)` (type 25): `1` = 58 mm, `0` = 80 mm — the *meaning* is API-level and
  must be probe-verified; the underlying paper geometry above is not in doubt.
- `printAndFeedPaper(n)` clamped `0..255`; `setTextWidth`/`setLeftMargin` clamped to 576
  (which independently corroborates the 576-dot figure for 80 mm).
- `getPrinterStatus()` values: `0` normal, `1` not powered on, `3` head open, `7` no paper
  feed, `8` paper running out, `-1` not connected, `99` other error.

**Evidence available now:** `pos_direct_print` bundles the same v1.4.0 and the user has
printed successfully on iMin devices running Android <11 and >11 — so those units do expose
a v1-compatible :8081 service. That is encouraging, not sufficient: the POSNext target unit
may be a different model/firmware exposing v2 semantics.

## Phase 0 — device probe (gate before `imin_client.js`)

Nothing that hard-codes v1.4.0 semantics may be implemented until this probe has been run
against the actual target device and recorded here. The probe is a throwaway page (a few
lines over raw WebSocket + fetch, or the bundled v1.4.0 itself) that answers:

1. Does `ws://127.0.0.1:8081/websocket` connect and speak the v1 ping/`request` protocol?
   If not, does a v2 endpoint (or different port) answer instead?
2. Does `POST /upload` + command `26/27` exist and print the bitmap? At what widths?
3. Does `setPageFormat(1)` vs `(0)` visibly change the output width on the installed roll?
4. Does a successful bitmap print auto-cut on this device, or is `partialCut()` needed?
5. What do `getPrinterStatus()` codes look like for: idle, head open, no paper?
6. Version handshake: any service-reported version string, plus the device model and Android
   API level (`navigator.userAgent` inside the POSNext WebView).

The probe script ships as part of the Direct Print page ("Diagnostics" tab) so field
technicians can re-run it on any new unit; its findings are recorded in
`docs/superpowers/specs/2026-09-02-imin-device-probe.md`. Depending on the result, the
pinned vendored SDK file is chosen (v1.4.0 or the v2 package) and any divergent behaviours
above are corrected in the spec before driver code is written.

The architecture below is deliberately probe-proof: every v1-specific detail lives behind
the single `imin_client.js` wrapper.

## Approach

Three options were considered.

**A — Thin wrapper.** A shortcut plus a test-print page using the SDK as-is. Rejected: it
does not fix the skew or the paper-width setting, and end-of-day printing would still go
through QZ Tray.

**B — Unified print transport with a dot-locked renderer. Chosen.** One router that all
existing print callers already funnel into, three drivers behind a shared contract, and a
renderer that locks the bitmap to the printer's dot width. Fixes both output bugs, picks up
every print path including shift closing at no extra cost, and adds one DocType for
auditability.

**C — Port `pos_direct_print` wholesale.** Rejected: reservations, a state machine, and
receipt hashing defend against contention on shared multi-terminal queues. An iMin terminal
is a single device with a built-in printer; that machinery would be complexity with no
corresponding benefit.

Two ideas are carried over from the old app: the **fallback chain** and a **narrow driver
contract**. Everything else is left behind deliberately.

## Architecture

### Print transport

Today every print path converges on `qzPrintHTML(html)` in `POS/src/utils/printInvoice.js`.
That single call site becomes the transport:

```
checkout receipt ──┐
offline receipt  ──┤
invoice reprint  ──┼──> print/transport.js .printHTML(html, opts)
EOD report       ──┘         │
  (printEod.js)              ├── imin_client.js     WebSocket :8081 + POST /upload
                             ├── qz_client.js       existing qzTray.js, wrapped
                             └── browser_client.js  /printview popup
```

The driver comes from `POS Settings.print_driver`. On failure the transport walks the chain
`iMin → QZ → browser` when `print_fallback_enabled` is set, and records which driver actually
succeeded.

`ShiftClosingDialog.vue` is not modified. `printEod.js` swaps one import, so the
"Print EOD Report" button and its "EOD report pending print" retry state ride the new
transport unchanged.

### Driver contract

Four methods, no inheritance ceremony:

| Method | Returns |
| --- | --- |
| `isAvailable()` | `boolean` — can this driver run on this device right now |
| `getStatus()` | `{ ok, code, message }` — normalised across drivers |
| `printHTML(html, opts)` | resolves when the job is **accepted and confirmed**, not merely queued |
| `describe()` | `{ id, label, detail }` — for the settings and diagnostics UI |

`imin_client.printHTML` internally: `getStatus()` gate → render → upload+print → poll
`getPrinterStatus()` back to `0` → optional `partialCut()`. Nothing is appended after the
bitmap.

### Renderer — the skew fix

`print/receipt_renderer.js`, a pure module with no Vue or store dependency:

1. `dotsForPaper(paper)` → `384` for `58mm`, `576` for `80mm`, or the configured custom dot
   count. Derivation is `(paperMm - margin) * 8` snapped to a multiple of 8, expressed as one
   function rather than magic numbers scattered through the code.
2. Insert the HTML into an offscreen container whose width is **exactly** the dot count in
   CSS pixels, so 1 CSS px maps to 1 printer dot.
3. `html2canvas(container, { scale: 1, backgroundColor: '#fff' })` — `scale` is passed
   explicitly, never left to the device pixel ratio. This is where the old app went wrong:
   on a 1080-px-wide phone it produced a canvas far wider than 384 dots.
4. Assert `canvas.width === dots`; pad or trim to the exact dot count rather than letting
   the firmware scale.
5. Binarise to pure black/white — thermal heads have no grey levels, and dithered grey is a
   common source of muddy output.
6. `toDataURL('image/png')`.

Receipt CSS keeps the existing monospace treatment but targets the dot budget: 32 characters
per line at 58 mm, 48 at 80 mm, `letter-spacing: 0`, no fractional padding.

### Configuration

Server-side, on `POS Settings`:

| Field | Type | Default |
| --- | --- | --- |
| `print_driver` | Select: `Browser` / `QZ Tray` / `iMin Direct` | `Browser` |
| `imin_paper_width` | Select: `58mm` / `80mm` / `Custom` | `58mm` |
| `imin_custom_dots` | Int, shown when `Custom` | `384` |
| `imin_cut_paper` | Check | `1` |
| `print_fallback_enabled` | Check | `1` |

Per-device values — host, port, alignment, QZ printer name — live in **`localStorage`**, not
`sessionStorage`. The iMin WebView clears `sessionStorage` on app restart, which presents as
"printing suddenly stopped working after reboot".

`POS/src/stores/posSettings.js` gains computed accessors alongside the existing
`silentPrint` / `allowPrintLastInvoice`, following the file's established pattern.

### Print log

One DocType, `POS Print Log`: `reference_doctype`, `reference_name`, `driver`,
`status` (`Success` / `Failed` / `Fallback`), `error_code`, `error_message`, `paper_width`,
`duration_ms`, `pos_profile`, `user`. Written fire-and-forget from the transport via
`pos_next/api/printing.py::log_print_attempt` (whitelisted, rate-limited, never blocking a
print).

It answers the operational question "why did this receipt not come out" and supplies the
reprint list on the diagnostics page. There is no print queue and no job state machine.

`pos_next/api/printing.py` also exposes `get_print_config(pos_profile)` and
`get_print_logs(filters)`.

### Home shortcut and diagnostics page

- `pos_next/pos_next/workspace/posnext/posnext.json` gains a `Direct Print` shortcut of type
  `URL` pointing at `/direct-print`, matching the existing `Start POS` → `/pos/` entry.
- The page is served from `pos_next/www/` with a `website_route_rules` entry in `hooks.py`.
- Contents: connection state and live `getPrinterStatus()`, host/port entry, paper selection,
  **Test Print**, reprint of the last few invoices, and recent `POS Print Log` rows.
- Test Print goes through the same transport and renderer as a real receipt, so a passing
  test means real printing works — not a separate code path that happens to succeed.

### Error handling

`getStatus()` runs before each job. Codes map to cashier-facing messages: `1` / `-1`
"printer off or disconnected", `3` "print head open", `7` / `8` "out of paper". Every
outcome is logged. Fallback is transparent to the caller but visible in the log as
`Fallback`.

## Testing

- **Unit (Vitest, already configured in `POS/`):** `receipt_renderer` dot math and snapping
  (58/80/custom, multiples of 8, pad and trim); `transport` fallback chain against a fake
  driver; `imin_client` against a mocked WebSocket and `fetch`, including the
  "never feed after bitmap" ordering guarantee and the status-gate poll.
- **Python:** `pos_next/api/printing.py` permissions and log writes, run per the project's
  test harness (`pos_next/_pn_run_tests.py`, serial).
- **On device:** Test Print at 58 mm and 80 mm, a checkout receipt, an invoice reprint, and a
  shift-closing EOD report — on both the Android <11 and >11 units already verified to work
  with the standalone app. The 576-dot figure for 80 mm is confirmed here.

## Out of scope

Print queueing, multi-terminal reservation, receipt hashing, label printing, cash-drawer
control, and the iMin text/barcode/QR APIs. The bitmap path covers receipts; adding text-mode
printing would mean maintaining a second renderer with no benefit to output quality.
