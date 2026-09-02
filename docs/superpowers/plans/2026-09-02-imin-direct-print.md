# iMin Direct Print Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Print POSNext receipts and end-of-day reports directly on an iMin Android terminal's built-in thermal printer, with output locked to the printer's true dot width so it stops coming out skewed.

**Architecture:** Every existing print path in the POS SPA already funnels into one call (`qzPrintHTML`). That call site becomes a transport router that dispatches to one of three drivers (iMin / QZ Tray / browser) behind a four-method contract, with an optional fallback chain. A pure renderer converts print HTML to a bitmap whose width is exactly the paper's dot count (384 at 58 mm, 576 at 80 mm), which is the root fix for skewed output. One DocType records attempts for field diagnosis.

**Tech Stack:** Vue 3 + Pinia + vue-router (POS SPA, Vite), Vitest, Frappe/ERPNext v16 (Python), `imin-printer.js` v1.4.0 (vendored), `html2canvas` (new pinned dependency), existing `qz-tray`.

**Spec:** `docs/superpowers/specs/2026-09-02-imin-direct-print-design.md`

## Global Constraints

- **Hardware geometry is fixed and must never be re-derived from assumptions:** 205 DPI = 8 dots/mm. 58 mm paper → 48 mm effective → **384 dots**. 80 mm paper → 72 mm effective → **576 dots**. Dot counts are always multiples of 8; the hardware maximum is 576.
- **SDK behaviour is versioned, not hardware truth.** All command types, endpoints, and semantics below describe `imin-printer.js` **v1.4.0**. They are provisional until Task 1's probe passes on the real device. Every v1-specific detail stays inside `imin_client.js`.
- **Never append a feed or line-feed call after a bitmap print** under v1.4.0: `printSingleBitmap` resolves when the command is *queued*, not printed, so trailing feeds land at the top of the *next* receipt. Gate completion on `getPrinterStatus()` returning `0`.
- **Per-device settings go in `localStorage`, never `sessionStorage`** — the iMin WebView clears `sessionStorage` on app restart.
- Indentation is **tabs** throughout JS and Python; formatting is enforced by Biome (`cd POS && yarn lint`).
- New npm dependencies are pinned to an **exact** version (no `^`).
- Python tests run through the project harness: `pos_next/_pn_run_tests.py`, serial only, inside the Docker container (`erpnext16_dev-frappe-1`, bench at `/workspace/development/frappe-bench`).
- Frontend tests run from `POS/`: `yarn vitest run <path>`. There is no global vitest config, so any test needing DOM must declare `// @vitest-environment jsdom` at the top of the file.
- Branch: `feat/imin-direct-print`. Commit after every task.

## Deviation from the spec

The spec placed the Direct Print page in `pos_next/www/` as a second Vite entry. This plan instead adds it as a route inside the existing POS SPA at **`/pos/direct-print`**, because that SPA already carries the transport, the settings store, and the session guard — a second build target would duplicate all three. The Home shortcut still exists; it points at `/pos/direct-print`.

## File Structure

**New — frontend print subsystem (`POS/src/utils/print/`):**

| File | Responsibility |
| --- | --- |
| `paper.js` | Paper→dot geometry. Pure. The only place dot counts are derived. |
| `receipt_renderer.js` | Print HTML → dot-exact monochrome bitmap data URL. |
| `imin_client.js` | iMin driver. The sole home of v1.4.0 SDK specifics. |
| `qz_client.js` | QZ Tray driver — thin adapter over existing `utils/qzTray.js`. |
| `browser_client.js` | Browser driver — thin adapter over existing popup printing. |
| `transport.js` | Driver selection, fallback chain, log dispatch. |
| `*.test.js` | Co-located tests, matching `utils/packageQuote.test.js`. |

**New — backend:**

- `pos_next/pos_next/doctype/pos_print_log/` — DocType, controller, test.
- `pos_next/api/printing.py` — `get_print_config`, `log_print_attempt`, `get_print_logs`.

**New — UI and diagnostics:**

- `POS/src/pages/DirectPrint.vue` — status, paper choice, test print, recent log.
- `pos_next/www/imin_probe.html` — standalone Phase 0 probe (no build step).
- `pos_next/public/js/lib/imin/1.4.0/imin-printer.js` — vendored SDK.

**Modified:**

- `POS/src/utils/printInvoice.js` — swap `qzPrintHTML` for the transport.
- `POS/src/utils/printEod.js` — no logic change; rides the transport via `printInvoice.js`.
- `POS/src/router.js` — add the `/direct-print` route.
- `POS/src/stores/posSettings.js` — expose the new settings.
- `POS/package.json` — add pinned `html2canvas`.
- `pos_next/pos_next/doctype/pos_settings/pos_settings.json` — new printing fields.
- `pos_next/pos_next/workspace/posnext/posnext.json` — Home shortcut.
- `pos_next/hooks.py` — probe page route.

### Task 1: Phase 0 — device probe page

**Files:**
- Create: `pos_next/www/imin_probe.html`
- Modify: `pos_next/hooks.py:279-281` (`website_route_rules`)

**Interfaces:**
- Consumes: nothing.
- Produces: a human-readable probe report on the device; the recorded findings gate Task 4.

The probe is a standalone page with no build step and no auth, so it can be opened on a
bare device before anything else exists. It answers the six Phase 0 questions from the spec.

- [ ] **Step 1: Create the probe page**

Create `pos_next/www/imin_probe.html` — a single self-contained HTML file (inline JS, no
imports). It must:

1. Attempt `new WebSocket("ws://127.0.0.1:8081/websocket")` and report connect/refuse.
2. On open, send the v1 ping `{data:{text:"ping"},type:0}` and report whether a
   `request`/ping reply arrives within 3 s.
3. Render a 384×120 test bitmap (black text on white) to a data URL and `POST` it as
   `FormData` field `file` to `http://127.0.0.1:8081/upload`; report HTTP status.
4. If the upload succeeded, send WS `{data:{text:"",value:-1},type:26}` (print bitmap, no
   alignment) and report; then poll `getPrinterStatus`-equivalent by sending
   `{data:{text:"",value:-1},type:2}` and reading the reply's `value` until it is `0` or
   10 s elapse.
5. Buttons to repeat the print with `type:27` + `value:1` (centred) and to send
   `type:25,value:1` then `value:0` (`setPageFormat` 58/80) before printing, so the
   operator can see which setting changes the output width.
6. Print the device's `navigator.userAgent` and the page's `window.location.origin` at the
   top of the report.

Every result is appended to an on-page `<pre>` log with a timestamp. No alerts, no
external resources.

- [ ] **Step 2: Route the page**

In `pos_next/hooks.py`, extend `website_route_rules`:

```python
website_route_rules = [
	{"from_route": "/pos/<path:app_path>", "to_route": "pos"},
	{"from_route": "/imin-probe", "to_route": "imin_probe"},
]
```

- [ ] **Step 3: Verify it loads**

Run: `curl -s http://127.0.0.1:8000/imin-probe | head -5`
Expected: the HTML head of the probe page (200, not a 404).

- [ ] **Step 4: Commit**

```bash
git add pos_next/www/imin_probe.html pos_next/hooks.py
git commit -m "feat(print): add Phase 0 iMin device probe page"
```

- [ ] **Step 5: Run the probe on the real device and record findings**

Open `https://<site>/imin-probe` on the iMin terminal. Fill in
`docs/superpowers/specs/2026-09-02-imin-device-probe.md` with: WS connect result, ping
reply, upload status, print result, whether `setPageFormat` visibly changes width, whether
the print auto-cut, the status codes seen, and the device model / Android API level from
the user agent. **Do not proceed to Task 4 until this file exists and the findings are
recorded.** If any finding contradicts the v1.4.0 assumptions in the spec, amend the spec
first.

---

### Task 2: Paper geometry module

**Files:**
- Create: `POS/src/utils/print/paper.js`
- Test: `POS/src/utils/print/paper.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: `PAPER_PROFILES`, `dotsForPaper(paper, customDots)` — used by the renderer,
  the iMin driver, the settings store, and the Direct Print page.

- [ ] **Step 1: Write the failing tests**

```js
// POS/src/utils/print/paper.test.js
import { describe, expect, it } from "vitest";

import { PAPER_PROFILES, dotsForPaper } from "./paper";

describe("dotsForPaper", () => {
	it("maps 58mm to 384 dots", () => {
		expect(dotsForPaper("58mm")).toBe(384);
	});

	it("maps 80mm to 576 dots", () => {
		expect(dotsForPaper("80mm")).toBe(576);
	});

	it("uses the custom dot count when paper is custom", () => {
		expect(dotsForPaper("custom", 416)).toBe(416);
	});

	it("defaults custom to 384 when no custom value given", () => {
		expect(dotsForPaper("custom")).toBe(384);
	});

	it("snaps a custom value down to a multiple of 8", () => {
		expect(dotsForPaper("custom", 420)).toBe(416);
	});

	it("clamps custom to the hardware maximum of 576", () => {
		expect(dotsForPaper("custom", 999)).toBe(576);
	});

	it("rejects a non-positive custom value", () => {
		expect(() => dotsForPaper("custom", 0)).toThrow();
	});

	it("rejects an unknown paper key", () => {
		expect(() => dotsForPaper("60mm")).toThrow();
	});
});

describe("PAPER_PROFILES", () => {
	it("exposes the two hardware profiles with correct dots", () => {
		expect(PAPER_PROFILES["58mm"].dots).toBe(384);
		expect(PAPER_PROFILES["58mm"].effectiveMm).toBe(48);
		expect(PAPER_PROFILES["80mm"].dots).toBe(576);
		expect(PAPER_PROFILES["80mm"].effectiveMm).toBe(72);
	});
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd POS && yarn vitest run src/utils/print/paper.test.js`
Expected: FAIL — `./paper` does not exist.

- [ ] **Step 3: Implement the module**

```js
// POS/src/utils/print/paper.js
/**
 * iMin thermal paper geometry.
 *
 * Hardware facts (official iMin documentation, version-independent):
 *   205 DPI = 8 dots/mm.
 *   58mm paper -> 48mm effective print width -> 384 dots.
 *   80mm paper -> 72mm effective print width -> 576 dots.
 * The full paper width is never the print area; the content budget is dots.
 */

export const DOTS_PER_MM = 8;
export const MAX_DOTS = 576;

export const PAPER_PROFILES = {
	"58mm": { label: "58mm", paperMm: 58, effectiveMm: 48, dots: 384 },
	"80mm": { label: "80mm", paperMm: 80, effectiveMm: 72, dots: 576 },
};

/**
 * Resolve a paper setting to a dot count.
 *
 * @param {string} paper - "58mm" | "80mm" | "custom"
 * @param {number} [customDots] - required when paper is "custom"; snapped to a
 *   multiple of 8 and clamped to MAX_DOTS.
 * @returns {number} dot count, always a multiple of 8 in [8, 576].
 */
export function dotsForPaper(paper, customDots) {
	if (paper === "custom") {
		const raw = Number(customDots ?? 384);
		if (!Number.isFinite(raw) || raw <= 0) {
			throw new Error(`Invalid custom dot count: ${customDots}`);
		}
		const snapped = Math.min(MAX_DOTS, Math.floor(raw / 8) * 8);
		if (snapped < 8) throw new Error(`Custom dot count too small: ${customDots}`);
		return snapped;
	}

	const profile = PAPER_PROFILES[paper];
	if (!profile) throw new Error(`Unknown paper profile: ${paper}`);
	return profile.dots;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd POS && yarn vitest run src/utils/print/paper.test.js`
Expected: PASS (all 9).

- [ ] **Step 5: Commit**

```bash
git add POS/src/utils/print/paper.js POS/src/utils/print/paper.test.js
git commit -m "feat(print): paper-to-dot geometry module"
```

---

### Task 3: Receipt renderer

**Files:**
- Create: `POS/src/utils/print/receipt_renderer.js`
- Test: `POS/src/utils/print/receipt_renderer.test.js`
- Modify: `POS/package.json` (add `html2canvas`)

**Interfaces:**
- Consumes: `dotsForPaper` from `./paper`.
- Produces: `renderHTMLToBitmap(html, opts) -> Promise<{dataURL, width, height}>`,
  `normalizeWidthPlan(sourceWidth, targetDots)`, `binarize(imageData, threshold)`.

The renderer is split so the two pure helpers are unit-testable without a real canvas.
`normalizeWidthPlan` decides how a source canvas of arbitrary width is brought to the exact
dot count; `binarize` forces pure black/white because thermal heads have no grey levels.

- [ ] **Step 1: Add the pinned dependency**

In `POS/package.json`, under `dependencies`, add exactly:

```json
"html2canvas": "1.4.1"
```

Run: `cd POS && yarn install`

- [ ] **Step 2: Write the failing tests**

```js
// POS/src/utils/print/receipt_renderer.test.js
import { describe, expect, it } from "vitest";

import { binarize, normalizeWidthPlan } from "./receipt_renderer";

describe("normalizeWidthPlan", () => {
	it("keeps an exact-width source untouched", () => {
		const plan = normalizeWidthPlan(384, 384);
		expect(plan).toEqual({ action: "none", targetWidth: 384 });
	});

	it("pads a narrower source centred", () => {
		const plan = normalizeWidthPlan(300, 384);
		expect(plan.action).toBe("pad");
		expect(plan.targetWidth).toBe(384);
		expect(plan.offsetX).toBe(Math.floor((384 - 300) / 2));
	});

	it("trims a wider source centred", () => {
		const plan = normalizeWidthPlan(576, 384);
		expect(plan.action).toBe("trim");
		expect(plan.targetWidth).toBe(384);
		expect(plan.offsetX).toBe(Math.floor((576 - 384) / 2));
	});
});

describe("binarize", () => {
	it("maps light pixels to white and dark to black", () => {
		const data = new Uint8ClampedArray([
			255, 255, 255, 255, // white stays white
			10, 10, 10, 255, // dark stays black
			127, 127, 127, 255, // mid grey -> white (default threshold 128)
			128, 128, 128, 255, // at threshold -> black
		]);
		binarize({ data, width: 4, height: 1 }, 128);
		expect(Array.from(data.slice(0, 3))).toEqual([255, 255, 255]);
		expect(Array.from(data.slice(4, 7))).toEqual([0, 0, 0]);
		expect(Array.from(data.slice(8, 11))).toEqual([255, 255, 255]);
		expect(Array.from(data.slice(12, 15))).toEqual([0, 0, 0]);
	});

	it("respects a custom threshold", () => {
		const data = new Uint8ClampedArray([200, 200, 200, 255]);
		binarize({ data, width: 1, height: 1 }, 220);
		expect(Array.from(data.slice(0, 3))).toEqual([0, 0, 0]);
	});
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd POS && yarn vitest run src/utils/print/receipt_renderer.test.js`
Expected: FAIL — `./receipt_renderer` does not exist.

- [ ] **Step 4: Implement the module**

```js
// POS/src/utils/print/receipt_renderer.js
import html2canvas from "html2canvas";

import { dotsForPaper } from "./paper";

const DEFAULT_THRESHOLD = 128;

/**
 * Decide how to bring a source canvas of `sourceWidth` px to exactly
 * `targetDots` px. Padding/trimming is centred so the receipt stays on the
 * paper's optical centre line.
 */
export function normalizeWidthPlan(sourceWidth, targetDots) {
	if (sourceWidth === targetDots) {
		return { action: "none", targetWidth: targetDots, offsetX: 0 };
	}
	if (sourceWidth < targetDots) {
		return {
			action: "pad",
			targetWidth: targetDots,
			offsetX: Math.floor((targetDots - sourceWidth) / 2),
		};
	}
	return {
		action: "trim",
		targetWidth: targetDots,
		offsetX: Math.floor((sourceWidth - targetDots) / 2),
	};
}

/**
 * Force an ImageData buffer to pure black/white. Thermal heads have no grey
 * levels, so dithered grey prints muddy.
 */
export function binarize(imageData, threshold = DEFAULT_THRESHOLD) {
	const { data } = imageData;
	for (let i = 0; i < data.length; i += 4) {
		const luminance = 0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2];
		const v = luminance < threshold ? 0 : 255;
		data[i] = v;
		data[i + 1] = v;
		data[i + 2] = v;
	}
}

/**
 * Render print HTML to a monochrome bitmap whose width is exactly the paper's
 * dot count. 1 CSS px == 1 printer dot, so html2canvas scale is forced to 1
 * (never left to the device pixel ratio — that is what skewed the old app).
 *
 * @param {string} html - full HTML document or fragment to print.
 * @param {object} opts
 * @param {string} opts.paper - "58mm" | "80mm" | "custom"
 * @param {number} [opts.customDots] - when paper is "custom"
 * @param {number} [opts.threshold] - binarize threshold (default 128)
 * @returns {Promise<{dataURL:string,width:number,height:number}>}
 */
export async function renderHTMLToBitmap(html, opts) {
	const dots = dotsForPaper(opts.paper, opts.customDots);

	const host = document.createElement("div");
	host.style.cssText =
		"position:fixed;left:-10000px;top:0;pointer-events:none;overflow:hidden;";
	const frame = document.createElement("div");
	frame.style.cssText = `width:${dots}px;background:#fff;color:#000;`;
	frame.innerHTML = html;
	host.appendChild(frame);
	document.body.appendChild(host);

	try {
		const canvas = await html2canvas(frame, {
			scale: 1,
			backgroundColor: "#ffffff",
			windowWidth: dots,
		});

		const plan = normalizeWidthPlan(canvas.width, dots);
		const out = document.createElement("canvas");
		out.width = plan.targetWidth;
		out.height = canvas.height;
		const ctx = out.getContext("2d");
		ctx.fillStyle = "#ffffff";
		ctx.fillRect(0, 0, out.width, out.height);
		if (plan.action === "none") {
			ctx.drawImage(canvas, 0, 0);
		} else if (plan.action === "pad") {
			ctx.drawImage(canvas, plan.offsetX, 0);
		} else {
			ctx.drawImage(canvas, plan.offsetX, 0, plan.targetWidth, canvas.height, 0, 0, plan.targetWidth, canvas.height);
		}

		const imageData = ctx.getImageData(0, 0, out.width, out.height);
		binarize(imageData, opts.threshold);
		ctx.putImageData(imageData, 0, 0);

		return {
			dataURL: out.toDataURL("image/png"),
			width: out.width,
			height: out.height,
		};
	} finally {
		host.remove();
	}
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd POS && yarn vitest run src/utils/print/receipt_renderer.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add POS/package.json POS/yarn.lock POS/src/utils/print/receipt_renderer.js POS/src/utils/print/receipt_renderer.test.js
git commit -m "feat(print): dot-exact monochrome receipt renderer"
```

---
### Task 4: iMin driver

**Files:**
- Create: `POS/src/utils/print/imin_client.js`
- Create: `pos_next/public/js/lib/imin/1.4.0/imin-printer.js` (vendored)
- Test: `POS/src/utils/print/imin_client.test.js`

**Interfaces:**
- Consumes: `renderHTMLToBitmap` from `./receipt_renderer`; the vendored SDK.
- Produces: a driver object `{ id, printHTML, getStatus, isAvailable, describe, saveDeviceConfig, loadDeviceConfig }`.

This is the **only** file allowed to know v1.4.0 specifics (endpoints, command types, the queued-vs-printed trap, explicit cut). All of it sits behind the driver contract, so if the Task 1 probe shows the device speaks a different SDK, only this file changes.

- [ ] **Step 1: Vendor the SDK**

Copy the exact file `/Users/rotiropi/iMinJSPrinterSDK/js-demo/html-demo/imin-printer.js` (v1.4.0 header) to `pos_next/public/js/lib/imin/1.4.0/imin-printer.js`. Confirm the first line reads `imin-printer v1.4.0`. Do not edit it.

- [ ] **Step 2: Write the failing tests**

The driver takes an injected printer factory so tests never touch a real socket. The key behaviour to lock is the completion contract: never feed after the bitmap, and gate on `getPrinterStatus()` reaching `0`.

```js
// POS/src/utils/print/imin_client.test.js
import { beforeEach, describe, expect, it, vi } from "vitest";

import { createIminDriver } from "./imin_client";

function makeFakePrinter(overrides = {}) {
	return {
		connect: vi.fn().mockResolvedValue(true),
		initPrinter: vi.fn(),
		getPrinterStatus: vi.fn().mockResolvedValue({ value: 0 }),
		setPageFormat: vi.fn(),
		printSingleBitmap: vi.fn().mockResolvedValue(1),
		partialCut: vi.fn(),
		printAndFeedPaper: vi.fn(),
		...overrides,
	};
}

let printer;
let driver;

beforeEach(() => {
	printer = makeFakePrinter();
	driver = createIminDriver({
		factory: () => printer,
		loadConfig: () => ({ host: "127.0.0.1", paper: "58mm", cut: true }),
	});
});

describe("createIminDriver", () => {
	it("renders to a dot-exact bitmap and prints it", async () => {
		await driver.printHTML("<div>receipt</div>", {
			render: async () => ({ dataURL: "data:image/png;base64,AAA", width: 384 }),
		});
		expect(printer.printSingleBitmap).toHaveBeenCalledWith(
			"data:image/png;base64,AAA",
			expect.any(Number)
		);
	});

	it("never feeds paper after the bitmap", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "data:,", width: 384 }),
		});
		expect(printer.printAndFeedPaper).not.toHaveBeenCalled();
	});

	it("cuts only when the device config enables it", async () => {
		await driver.printHTML("<div/>", { render: async () => ({ dataURL: "x", width: 384 }) });
		expect(printer.partialCut).toHaveBeenCalledTimes(1);

		const noCut = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "58mm", cut: false }),
		});
		await noCut.printHTML("<div/>", { render: async () => ({ dataURL: "x", width: 384 }) });
		expect(printer.partialCut).toHaveBeenCalledTimes(1);
	});

	it("waits for status to reach 0 before resolving", async () => {
		printer.getPrinterStatus
			.mockResolvedValueOnce({ value: -1 })
			.mockResolvedValue({ value: 0 });
		await driver.printHTML("<div/>", { render: async () => ({ dataURL: "x", width: 384 }) });
		expect(printer.getPrinterStatus.mock.calls.length).toBeGreaterThan(1);
	});

	it("applies the page format for the chosen paper", async () => {
		await driver.printHTML("<div/>", { render: async () => ({ dataURL: "x", width: 384 }) });
		expect(printer.setPageFormat).toHaveBeenCalledWith(1); // 58mm

		const w80 = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "80mm", cut: false }),
		});
		await w80.printHTML("<div/>", { render: async () => ({ dataURL: "x", width: 576 }) });
		expect(printer.setPageFormat).toHaveBeenCalledWith(0); // 80mm
	});

	it("reports a not-connected error when status never recovers", async () => {
		printer.getPrinterStatus.mockResolvedValue({ value: -1 });
		await expect(
			driver.printHTML("<div/>", { render: async () => ({ dataURL: "x", width: 384 }) })
		).rejects.toThrow(/not connected/i);
	});
});
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd POS && yarn vitest run src/utils/print/imin_client.test.js`
Expected: FAIL — `./imin_client` does not exist.

- [ ] **Step 4: Implement the driver**

```js
// POS/src/utils/print/imin_client.js
/**
 * iMin driver — the SOLE location for iMin JS SDK v1.4.0 specifics.
 *
 * v1.4.0 facts this encodes (all provisional on the Phase 0 probe — see spec):
 *   - transport: ws://<host>:8081/websocket + POST http://<host>:8081/upload
 *   - printSingleBitmap resolves on QUEUE, not on print. NEVER feed after it.
 *   - completion is detected by polling getPrinterStatus() back to 0.
 *   - v1.4.0 does not auto-cut; partialCut() is explicit.
 *   - setPageFormat: 1 = 58mm, 0 = 80mm.
 */
import { dotsForPaper } from "./paper";
import { renderHTMLToBitmap } from "./receipt_renderer";

const DEVICE_CONFIG_KEY = "pos_imin_device_config";
const STATUS_POLL_MS = 500;
const STATUS_TIMEOUT_MS = 15000;

/** 58mm -> pageFormat 1; 80mm -> 0; custom keeps 58mm's value by dot count. */
function pageFormatFor(paper, dots) {
	if (paper === "80mm") return 0;
	if (paper === "58mm") return 1;
	return dots <= 384 ? 1 : 0;
}

export function loadDeviceConfig() {
	try {
		return JSON.parse(localStorage.getItem(DEVICE_CONFIG_KEY) || "{}");
	} catch {
		return {};
	}
}

export function saveDeviceConfig(patch) {
	const next = { ...loadDeviceConfig(), ...patch };
	localStorage.setItem(DEVICE_CONFIG_KEY, JSON.stringify(next));
	return next;
}

/**
 * @param {object} [deps] - injectable for tests.
 * @param {() => object} [deps.factory] - returns the SDK printer instance.
 * @param {() => object} [deps.loadConfig]
 */
export function createIminDriver(deps = {}) {
	const loadConfig = deps.loadConfig || loadDeviceConfig;
	let printer = null;

	async function ensurePrinter() {
		if (printer) return printer;
		if (!deps.factory) {
			throw new Error("iMin SDK not loaded (window.IminPrinter missing)");
		}
		const cfg = loadConfig();
		const p = deps.factory();
		if (cfg.host) p.address = cfg.host;
		const connected = await p.connect();
		if (!connected) throw new Error("Could not connect to iMin print service");
		p.initPrinter("SPI");
		printer = p;
		return p;
	}

	async function waitIdle(p) {
		const deadline = Date.now() + STATUS_TIMEOUT_MS;
		for (;;) {
			const status = await p.getPrinterStatus();
			const code = Number(status?.value);
			if (code === 0) return;
			if (Date.now() > deadline) {
				if (code === 8 || code === 7) throw new Error("Printer out of paper");
				throw new Error(`Printer not connected (status ${code})`);
			}
			await new Promise((r) => setTimeout(r, STATUS_POLL_MS));
		}
	}

	return {
		id: "imin",

		async isAvailable() {
			try {
				await ensurePrinter();
				return true;
			} catch {
				return false;
			}
		},

		async getStatus() {
			try {
				const p = await ensurePrinter();
				const status = await p.getPrinterStatus();
				return { ok: Number(status?.value) === 0, code: Number(status?.value) };
			} catch (err) {
				return { ok: false, code: -1, message: err.message };
			}
		},

		/**
		 * @param {string} html
		 * @param {object} [opts]
		 * @param {(html, o) => Promise<{dataURL:string}>} [opts.render] - injected for tests
		 */
		async printHTML(html, opts = {}) {
			const cfg = loadConfig();
			const paper = cfg.paper || "58mm";
			const dots = dotsForPaper(paper, cfg.customDots);
			const render = opts.render || ((h, o) => renderHTMLToBitmap(h, o));

			const p = await ensurePrinter();
			p.setPageFormat(pageFormatFor(paper, dots));

			const bitmap = await render(html, { paper, customDots: cfg.customDots });
			await p.printSingleBitmap(bitmap.dataURL, 1); // 1 = centre alignment

			if (cfg.cut) p.partialCut();
			// No feeds here — under v1.4.0 they land on the NEXT receipt.
			await waitIdle(p);
			return true;
		},

		describe() {
			const cfg = loadConfig();
			return { id: "imin", label: "iMin Direct", detail: cfg.host || "127.0.0.1:8081" };
		},
	};
}
```

> Note: the driver resolves the SDK instance via an injected factory (used by tests). Production wiring lives in `transport.js` (Task 7), which loads the vendored `/assets/pos_next/js/lib/imin/1.4.0/imin-printer.js` and passes `factory: () => new window.IminPrinter()`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd POS && yarn vitest run src/utils/print/imin_client.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pos_next/public/js/lib/imin POS/src/utils/print/imin_client.js POS/src/utils/print/imin_client.test.js
git commit -m "feat(print): iMin SDK driver behind a version-isolated contract"
```

---

### Task 5: QZ and browser drivers

**Files:**
- Create: `POS/src/utils/print/qz_client.js`
- Create: `POS/src/utils/print/browser_client.js`
- Test: `POS/src/utils/print/drivers.test.js`

**Interfaces:**
- Consumes: existing `@/utils/qzTray` (`printHTML`, `connect`) and the popup print already in `@/utils/printInvoice`.
- Produces: two driver objects with the same shape as `createIminDriver`.

Wrappers only — no logic moves out of `qzTray.js`. They exist so the transport can treat all three uniformly.

- [ ] **Step 1: Write the failing tests**

```js
// POS/src/utils/print/drivers.test.js
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/utils/qzTray", () => ({
	connect: vi.fn(),
	printHTML: vi.fn(),
}));

import * as qzTray from "@/utils/qzTray";
import { createBrowserDriver } from "./browser_client";
import { createQzDriver } from "./qz_client";

beforeEach(() => {
	vi.clearAllMocks();
});

describe("createQzDriver", () => {
	it("is available only when connect succeeds", async () => {
		qzTray.connect.mockResolvedValue(true);
		await expect(createQzDriver().isAvailable()).resolves.toBe(true);
		qzTray.connect.mockResolvedValue(false);
		await expect(createQzDriver().isAvailable()).resolves.toBe(false);
	});

	it("delegates printHTML to qzTray", async () => {
		qzTray.printHTML.mockResolvedValue(true);
		await expect(createQzDriver().printHTML("<html/>")).resolves.toBe(true);
		expect(qzTray.printHTML).toHaveBeenCalledWith("<html/>", undefined, {});
	});
});

describe("createBrowserDriver", () => {
	it("is always available", async () => {
		await expect(createBrowserDriver().isAvailable()).resolves.toBe(true);
	});

	it("reports describe metadata", () => {
		expect(createBrowserDriver().describe().id).toBe("browser");
	});
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd POS && yarn vitest run src/utils/print/drivers.test.js`
Expected: FAIL — neither module exists.

- [ ] **Step 3: Implement `qz_client.js`**

```js
// POS/src/utils/print/qz_client.js
import { connect, printHTML } from "@/utils/qzTray";

export function createQzDriver() {
	return {
		id: "qz",
		async isAvailable() {
			try {
				return await connect();
			} catch {
				return false;
			}
		},
		async getStatus() {
			const ok = await connect();
			return { ok, code: ok ? 0 : -1 };
		},
		async printHTML(html, opts = {}) {
			const { printerName, ...options } = opts;
			return printHTML(html, printerName, options);
		},
		describe() {
			return { id: "qz", label: "QZ Tray", detail: "desktop helper app" };
		},
	};
}
```

- [ ] **Step 4: Implement `browser_client.js`**

```js
// POS/src/utils/print/browser_client.js
/**
 * Browser driver. printHTML here is only used as the final fallback when the
 * transport is invoked directly with an HTML string (e.g. Test Print). The
 * normal browser path still goes through printInvoice's /printview popup.
 */
export function createBrowserDriver() {
	return {
		id: "browser",
		async isAvailable() {
			return true;
		},
		async getStatus() {
			return { ok: true, code: 0 };
		},
		async printHTML(html) {
			const w = window.open("", "_blank", "width=380,height=600");
			if (!w) throw new Error("Popup blocked — check browser settings");
			w.document.write(html);
			w.document.close();
			w.onload = () => setTimeout(() => w.print(), 250);
			return true;
		},
		describe() {
			return { id: "browser", label: "Browser", detail: "system print dialog" };
		},
	};
}
```

- [ ] **Step 5: Run to verify they pass**

Run: `cd POS && yarn vitest run src/utils/print/drivers.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add POS/src/utils/print/qz_client.js POS/src/utils/print/browser_client.js POS/src/utils/print/drivers.test.js
git commit -m "feat(print): qz and browser driver adapters"
```

---

### Task 6: Backend — POS Print Log + printing API

**Files:**
- Create: `pos_next/pos_next/doctype/pos_print_log/` (JSON + controller + test)
- Create: `pos_next/api/printing.py`
- Test: `pos_next/api/test_printing.py`

**Interfaces:**
- Consumes: `POS Settings`.
- Produces: whitelisted `pos_next.api.printing.get_print_config`, `log_print_attempt`, `get_print_logs`; the `POS Print Log` DocType.

- [ ] **Step 1: Create the DocType**

`pos_next/pos_next/doctype/pos_print_log/pos_print_log.json` — a regular (non-single, autoname `hash`) DocType, module `POS Next`, with fields:
`reference_doctype` (Link → Sales Invoice), `reference_name` (Dynamic Link on `reference_doctype`), `driver` (Select: `imin`/`qz`/`browser`), `status` (Select: `Success`/`Failed`/`Fallback`), `error_code` (Data), `error_message` (Small Text), `paper_width` (Select: `58mm`/`80mm`/`custom`), `duration_ms` (Int), `pos_profile` (Link → POS Profile), `printed_by` (Link → User, `read_only:1`, default `frappe.session.user`). Permissions: `System Manager` full; `Sales Manager` and `Nexus POS Manager` read+create; `POSNext Cashier` read+create. Set `track_changes: 0`, `sort_field: creation`, `sort_order: DESC`.

`pos_next/pos_next/doctype/pos_print_log/pos_print_log.py`:

```python
# Copyright (c) 2026, POS Next and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class POSPrintLog(Document):
	pass
```

`pos_next/pos_next/doctype/pos_print_log/__init__.py`: empty file.

- [ ] **Step 2: Write the failing API test**

```python
# pos_next/api/test_printing.py
import frappe
from frappe.tests.utils import FrappeTestCase

from pos_next.api.printing import get_print_config, log_print_attempt


class TestPrintingAPI(FrappeTestCase):
	def setUp(self):
		self.profile = frappe.db.get_value("POS Profile", {"disabled": 0}, "name")
		if not self.profile:
			self.skipTest("no POS Profile on this site")

	def test_get_print_config_returns_drivers(self):
		cfg = get_print_config(self.profile)
		self.assertIn("driver", cfg)
		self.assertIn("paper", cfg)

	def test_log_print_attempt_creates_row(self):
		before = frappe.db.count("POS Print Log")
		log_print_attempt(
			reference_doctype="Sales Invoice",
			reference_name="ACC-SINV-TEST",
			driver="imin",
			status="Success",
		)
		self.assertEqual(frappe.db.count("POS Print Log"), before + 1)
```

- [ ] **Step 3: Run it to verify it fails**

Inside the container: `cd /workspace/development/frappe-bench && python apps/pos_next/pos_next/_pn_run_tests.py pos_next/api/test_printing.py`
Expected: FAIL — `pos_next.api.printing` has no attribute `get_print_config`.

- [ ] **Step 4: Implement the API**

```python
# pos_next/api/printing.py
"""Whitelisted helpers for the POS print transport.

The frontend transport calls get_print_config once per session and logs every
print attempt. Logging is fire-and-forget and never blocks a print.
"""

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit

PRINT_CONFIG_FIELDS = (
	"print_driver",
	"imin_paper_width",
	"imin_custom_dots",
	"imin_cut_paper",
	"print_fallback_enabled",
)


@frappe.whitelist()
def get_print_config(pos_profile):
	"""Resolve the print configuration for a POS Profile."""
	if not pos_profile:
		frappe.throw(_("POS Profile is required"))

	settings = frappe.db.get_value(
		"POS Settings",
		{"pos_profile": pos_profile, "enabled": 1},
		list(PRINT_CONFIG_FIELDS),
		as_dict=True,
	)
	if not settings:
		settings = {field: None for field in PRINT_CONFIG_FIELDS}

	return {
		"driver": settings.print_driver or "browser",
		"paper": settings.imin_paper_width or "58mm",
		"custom_dots": settings.imin_custom_dots or 384,
		"cut": bool(settings.imin_cut_paper),
		"fallback_enabled": bool(settings.print_fallback_enabled),
	}


@frappe.whitelist()
@rate_limit(limit=30, seconds=60)
def log_print_attempt(**kwargs):
	"""Persist one print attempt. Best-effort; callers must not await its failure."""
	allowed = {
		"reference_doctype",
		"reference_name",
		"driver",
		"status",
		"error_code",
		"error_message",
		"paper_width",
		"duration_ms",
		"pos_profile",
	}
	doc = frappe.get_doc({"doctype": "POS Print Log", **{k: v for k, v in kwargs.items() if k in allowed}})
	doc.insert(ignore_permissions=True)
	return doc.name


@frappe.whitelist()
def get_print_logs(pos_profile=None, reference_name=None, limit=50):
	filters = {}
	if pos_profile:
		filters["pos_profile"] = pos_profile
	if reference_name:
		filters["reference_name"] = reference_name
	return frappe.get_list(
		"POS Print Log",
		filters=filters,
		fields=["name", "reference_name", "driver", "status", "error_message", "paper_width", "creation"],
		order_by="creation desc",
		limit_page_length=min(int(limit or 50), 200),
	)
```

- [ ] **Step 5: Run it to verify it passes**

Inside the container: `cd /workspace/development/frappe-bench && bench --site <site> migrate` then re-run the test command from Step 3.
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pos_next/pos_next/doctype/pos_print_log pos_next/api/printing.py pos_next/api/test_printing.py
git commit -m "feat(print): POS Print Log doctype and printing API"
```

---

### Task 7: Transport router + POS Settings fields

**Files:**
- Create: `POS/src/utils/print/transport.js`
- Test: `POS/src/utils/print/transport.test.js`
- Modify: `pos_next/pos_next/doctype/pos_settings/pos_settings.json` (printing section)
- Modify: `POS/src/stores/posSettings.js`

**Interfaces:**
- Consumes: the three driver factories; `get_print_config`; `log_print_attempt`.
- Produces: singleton `printHTML(html, opts)` used by `printInvoice.js` (Task 8) and the Direct Print page (Task 9); `getDriver()`; `setConfig()`.

- [ ] **Step 1: Write the failing tests**

The transport's job is driver choice + fallback + logging. Tests inject fake drivers.

```js
// POS/src/utils/print/transport.test.js
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn().mockResolvedValue({}) }));
vi.mock("@/utils/logger", () => ({ logger: { create: () => ({ warn: vi.fn(), info: vi.fn(), error: vi.fn() }) } }));

function okDriver(id) {
	return { id, isAvailable: vi.fn().mockResolvedValue(true), getStatus: vi.fn().mockResolvedValue({ ok: true, code: 0 }), printHTML: vi.fn().mockResolvedValue(true), describe: () => ({ id }) };
}
function failDriver(id, err) {
	return { id, isAvailable: vi.fn().mockResolvedValue(true), getStatus: vi.fn().mockResolvedValue({ ok: false, code: -1 }), printHTML: vi.fn().mockRejectedValue(new Error(err)), describe: () => ({ id }) };
}

let createTransport, log;
beforeEach(async () => {
	const mod = await import("./transport");
	createTransport = mod.createTransport;
	log = { attempts: [] };
});

it("uses the configured driver when available", async () => {
	const imin = okDriver("imin");
	const t = createTransport({ drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") }, config: { driver: "imin" }, logSink: log });
	await t.printHTML("<html/>");
	expect(imin.printHTML).toHaveBeenCalled();
	expect(log.attempts.at(-1).status).toBe("Success");
});

it("falls back down the chain and logs a Fallback status", async () => {
	const imin = failDriver("imin", "offline");
	const browser = okDriver("browser");
	const t = createTransport({ drivers: { imin, qz: okDriver("qz"), browser }, config: { driver: "imin", fallback_enabled: true }, logSink: log });
	await t.printHTML("<html/>");
	expect(browser.printHTML).toHaveBeenCalled();
	expect(log.attempts.at(-1).status).toBe("Fallback");
});

it("rethrows when fallback is disabled", async () => {
	const imin = failDriver("imin", "offline");
	const t = createTransport({ drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") }, config: { driver: "imin", fallback_enabled: false }, logSink: log });
	await expect(t.printHTML("<html/>")).rejects.toThrow("offline");
	expect(log.attempts.at(-1).status).toBe("Failed");
});

it("skips an unavailable driver and moves to the next", async () => {
	const imin = okDriver("imin");
	imin.isAvailable.mockResolvedValue(false);
	const browser = okDriver("browser");
	const t = createTransport({ drivers: { imin, qz: okDriver("qz"), browser }, config: { driver: "imin", fallback_enabled: true }, logSink: log });
	await t.printHTML("<html/>");
	expect(imin.printHTML).not.toHaveBeenCalled();
	expect(browser.printHTML).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run to verify they fail**

Run: `cd POS && yarn vitest run src/utils/print/transport.test.js`
Expected: FAIL — `./transport` does not exist.

- [ ] **Step 3: Implement the transport**

```js
// POS/src/utils/print/transport.js
import { call } from "@/utils/apiWrapper";
import { logger } from "@/utils/logger";

import { createBrowserDriver } from "./browser_client";
import { createIminDriver } from "./imin_client";
import { createQzDriver } from "./qz_client";

const log = logger.create("PrintTransport");

const FALLBACK_ORDER = { imin: ["imin", "qz", "browser"], qz: ["qz", "browser"], browser: ["browser"] };

/**
 * Build a transport. `drivers` and `config` are injectable so unit tests need
 * no network or sockets. The production singleton below wires the real ones.
 */
export function createTransport({ drivers, config = {}, logSink } = {}) {
	let current = { ...config };

	async function logAttempt(attempt) {
		if (logSink) {
			logSink.attempts.push(attempt);
			return;
		}
		try {
			await call("pos_next.api.printing.log_print_attempt", attempt);
		} catch (err) {
			log.warn("print log failed:", err?.message);
		}
	}

	function chain() {
		if (current.fallback_enabled === false) return [current.driver];
		return FALLBACK_ORDER[current.driver] || [current.driver];
	}

	async function printHTML(html, opts = {}) {
		const started = Date.now();
		const errors = [];
		for (const [idx, id] of chain().entries()) {
			const driver = drivers[id];
			if (!driver) continue;
			let ok = true;
			try {
				if (!(await driver.isAvailable())) continue;
				await driver.printHTML(html, opts);
			} catch (err) {
				ok = false;
				errors.push(`${id}: ${err?.message || err}`);
			}
			if (ok) {
				await logAttempt({
					...opts.logContext,
					driver: id,
					status: idx === 0 ? "Success" : "Fallback",
					paper_width: current.paper,
					duration_ms: Date.now() - started,
				});
				return { driver: id };
			}
		}
		await logAttempt({
			...opts.logContext,
			driver: current.driver,
			status: "Failed",
			error_message: errors.join(" | "),
			paper_width: current.paper,
			duration_ms: Date.now() - started,
		});
		throw new Error(errors.join(" | ") || "No print driver available");
	}

	return {
		printHTML,
		setConfig(next) {
			current = { ...current, ...next };
		},
		getConfig() {
			return { ...current };
		},
		getDriver(id) {
			return drivers[id || current.driver];
		},
	};
}

function loadIminFactory() {
	// window.IminPrinter is defined by the vendored SDK, injected once by
	// ensureIminSdk() below on first iMin use.
	return typeof window !== "undefined" && window.IminPrinter ? () => new window.IminPrinter() : undefined;
}

let _sdkPromise = null;
function ensureIminSdk() {
	if (_sdkPromise) return _sdkPromise;
	_sdkPromise = new Promise((resolve) => {
		if (loadIminFactory()) return resolve(true);
		const s = document.createElement("script");
		s.src = "/assets/pos_next/js/lib/imin/1.4.0/imin-printer.js";
		s.onload = () => resolve(true);
		s.onerror = () => resolve(false);
		document.head.appendChild(s);
	});
	return _sdkPromise;
}

let _singleton = null;
export function getTransport() {
	if (_singleton) return _singleton;
	_singleton = createTransport({
		drivers: {
			imin: createIminDriver({ factory: () => new window.IminPrinter() }),
			qz: createQzDriver(),
			browser: createBrowserDriver(),
		},
	});
	return _singleton;
}

export async function printHTML(html, opts) {
	await ensureIminSdk();
	return getTransport().printHTML(html, opts);
}

export async function initTransportFromServer(posProfile) {
	const cfg = await call("pos_next.api.printing.get_print_config", { pos_profile: posProfile });
	getTransport().setConfig({
		driver: cfg.driver,
		paper: cfg.paper,
		custom_dots: cfg.custom_dots,
		cut: cfg.cut,
		fallback_enabled: cfg.fallback_enabled,
	});
	return cfg;
}
```

- [ ] **Step 4: Run to verify they pass**

Run: `cd POS && yarn vitest run src/utils/print/transport.test.js`
Expected: PASS.

- [ ] **Step 5: Add the POS Settings fields**

In `pos_next/pos_next/doctype/pos_settings/pos_settings.json`, insert five field objects into the `fields` array immediately after the `silent_print` field, and add their fieldnames to `field_order` in the same position:

```json
{ "fieldname": "print_driver", "fieldtype": "Select", "label": "Print Driver", "options": "browser\nqz\nimin", "default": "browser", "description": "Driver used for silent printing" },
{ "fieldname": "imin_paper_width", "fieldtype": "Select", "label": "iMin Paper Width", "options": "58mm\n80mm\ncustom", "default": "58mm", "depends_on": "eval:doc.print_driver==='imin'" },
{ "fieldname": "imin_custom_dots", "fieldtype": "Int", "label": "iMin Custom Dots", "default": "384", "depends_on": "eval:doc.imin_paper_width==='custom'", "description": "Effective print width in dots (multiple of 8, max 576)" },
{ "fieldname": "imin_cut_paper", "fieldtype": "Check", "label": "Cut Paper After Print", "default": "1", "depends_on": "eval:doc.print_driver==='imin'" },
{ "fieldname": "print_fallback_enabled", "fieldtype": "Check", "label": "Enable Print Fallback", "default": "1", "description": "On failure, fall back down the chain (iMin → QZ → Browser)" }
```

Bump the doctype's top-level `modified` timestamp.

- [ ] **Step 6: Expose them in the store**

In `POS/src/stores/posSettings.js`, add to the printing part of the `settings` default:

```js
print_driver: "browser",
imin_paper_width: "58mm",
imin_custom_dots: 384,
imin_cut_paper: 1,
print_fallback_enabled: 1,
```

and to the Printing computed block:

```js
const printDriver = computed(() => settings.value.print_driver || "browser");
const iminPaper = computed(() => settings.value.imin_paper_width || "58mm");
const iminCutPaper = computed(() => Boolean(settings.value.imin_cut_paper));
```

Export all three in the store's returned object.

- [ ] **Step 7: Migrate and commit**

Inside the container: `cd /workspace/development/frappe-bench && bench --site <site> migrate`

```bash
git add pos_next/pos_next/doctype/pos_settings POS/src/stores/posSettings.js POS/src/utils/print/transport.js POS/src/utils/print/transport.test.js
git commit -m "feat(print): transport router with fallback chain + settings"
```

---

### Task 8: Wire existing print paths through the transport

**Files:**
- Modify: `POS/src/utils/printInvoice.js`
- Modify: `POS/src/utils/printEod.js`

**Interfaces:**
- Consumes: `printHTML`, `initTransportFromServer` from `@/utils/print/transport`.
- Produces: no new exports; behaviour change only.

`ShiftClosingDialog.vue` is not touched — its "Print EOD Report" button calls `printEODReport`, which now routes through the transport automatically.

- [ ] **Step 1: Route silent prints through the transport**

In `POS/src/utils/printInvoice.js`, replace the import on line 6:

```js
import { printHTML as qzPrintHTML } from "@/utils/qzTray";
```

with:

```js
import { printHTML as transportPrint } from "@/utils/print/transport";
```

Replace both `await qzPrintHTML(fullHTML);` calls (in `silentPrintDoc` and `silentPrintInvoiceFromDoc`) with a transport call that carries the log context:

```js
await transportPrint(fullHTML, {
	logContext: {
		reference_doctype: doctype,
		reference_name: name,
		pos_profile: posProfile,
	},
});
```

Where `silentPrintDoc` currently has no `doctype`/`name` in scope, use the values already passed as its arguments; for `silentPrintInvoiceFromDoc` use `invoiceData.name`. Ensure `silentPrintInvoice` calls `initTransportFromServer(invoiceData.pos_profile)` once per session before the first print (guard with a module-level boolean so it is not called on every receipt).

- [ ] **Step 2: Verify nothing else imports `qzPrintHTML` for silent print**

Run: `cd POS && grep -rn "qzTray" src/utils/printInvoice.js src/utils/printEod.js`
Expected: no direct `printHTML` usage from `qzTray` remains in these two files (the QZ driver reaches it through `qz_client.js`).

- [ ] **Step 3: Run the full frontend test suite**

Run: `cd POS && yarn test:run`
Expected: all pass (no existing test asserted on the QZ call site).

- [ ] **Step 4: Commit**

```bash
git add POS/src/utils/printInvoice.js POS/src/utils/printEod.js
git commit -m "feat(print): route receipt and EOD printing through transport"
```

---

### Task 9: Direct Print page + router route + Home shortcut

**Files:**
- Create: `POS/src/pages/DirectPrint.vue`
- Modify: `POS/src/router.js` (add route)
- Modify: `pos_next/pos_next/workspace/posnext/posnext.json` (Home shortcut)

**Interfaces:**
- Consumes: `getTransport`, `initTransportFromServer`, `saveDeviceConfig` / `loadDeviceConfig` from `@/utils/print/imin_client`, `get_print_logs`, `buildReceiptHTML` (for a test document), `PAPER_PROFILES`.
- Produces: the `/pos/direct-print` route and the Home shortcut.

- [ ] **Step 1: Add the route**

In `POS/src/router.js`, insert before the catch-all route:

```js
	{
		name: "DirectPrint",
		path: "/direct-print",
		component: () => import("@/pages/DirectPrint.vue"),
	},
```

- [ ] **Step 2: Build the page**

`POS/src/pages/DirectPrint.vue` — a `frappe-ui` screen with:

- A status card: current driver (`transport.getDriver().describe()`), connection state, and live `getStatus()` polled every 3 s while mounted.
- A device-config card bound to `loadDeviceConfig()` / `saveDeviceConfig()`: host input (default `127.0.0.1`), paper select (from `PAPER_PROFILES` + `custom`), custom-dots input when paper is `custom`, cut-paper toggle. Persisted to `localStorage`.
- A **Test Print** button that builds a small HTML receipt and calls `transport.printHTML(html, { logContext: { reference_doctype: "Sales Invoice", reference_name: "TEST" } })`; it must go through the same transport and renderer as a real print, not a bespoke path.
- A recent-attempts table fed by `get_print_logs()` (last 50 rows) with columns time / reference / driver / status / error.

Reuses existing components; no new global styles.

- [ ] **Step 3: Add the Home shortcut**

In `pos_next/pos_next/workspace/posnext/posnext.json`, append to the `shortcuts` array:

```json
{
 "color": "Grey",
 "doc_view": "",
 "label": "Direct Print",
 "type": "URL",
 "url": "/pos/direct-print"
}
```

Bump the workspace's top-level `modified` timestamp.

- [ ] **Step 4: Verify in the browser**

Run: `cd POS && yarn dev`, open `/pos/direct-print`. Confirm the page mounts, the driver and paper config render, and Test Print reaches the transport (a connection error on a non-iMin dev machine is the expected outcome and should surface in the recent-attempts log after the transport logs `Failed`).

- [ ] **Step 5: Lint and commit**

Run: `cd POS && yarn lint` (fix any Biome complaints).

```bash
git add POS/src/pages/DirectPrint.vue POS/src/router.js pos_next/pos_next/workspace/posnext/posnext.json
git commit -m "feat(print): Direct Print page and POSNext home shortcut"
```

---

### Task 10: On-device verification

**Files:** none (manual).

- [ ] **Step 1:** Re-run the Task 1 probe against the final page wiring to confirm the SDK assumptions still hold after integration.
- [ ] **Step 2:** On the iMin device: Test Print at 58 mm and at 80 mm — verify straight, unskewed output at the correct width and that the following receipt starts immediately (no leading blank lines, confirming the no-feed-after-bitmap rule).
- [ ] **Step 3:** Complete a checkout — receipt prints via the iMin driver. Force the printer off and re-checkout — confirm the transport falls back to the browser driver when enabled and that the `POS Print Log` shows a `Fallback` row.
- [ ] **Step 4:** Close a shift, print the EOD report — confirm it rides the same transport.
- [ ] **Step 5:** Restart the iMin app — confirm the device config survived (it is in `localStorage`, so host/paper/cut must still be set).
- [ ] **Step 6:** Record the results in `docs/superpowers/specs/2026-09-02-imin-device-probe.md` and commit that file.

---

## Self-Review Notes

- **Spec coverage:** every spec section maps to a task — probe/SDK-versioning (Task 1), geometry (Task 2), renderer/skew fix (Task 3), driver contract + iMin (Task 4), QZ/browser (Task 5), one DocType + API (Task 6), transport + fallback + settings (Task 7), EOD/receipt integration (Task 8), Home shortcut + diagnostics page (Task 9), on-device (Task 10).
- **Type consistency:** driver objects are the same shape across Tasks 4/5/9; `dotsForPaper` signature is stable from Task 2; the transport `logContext` fields match `log_print_attempt`'s allowed keys in Task 6.
- **No placeholders:** every code step contains the code to write.
