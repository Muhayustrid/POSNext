/**
 * iMin driver — the SOLE location for iMin JS SDK v1.4.0 specifics.
 *
 * v1.4.0 facts this encodes (probe-verified on device 2026-09-02):
 *   - transport: ws://<host>:8081/websocket + POST http://<host>:8081/upload
 *   - printSingleBitmap resolves on QUEUE, not on print.
 *   - completion is detected by polling getPrinterStatus() back to 0.
 *   - THIS v1.4.0 build does NOT cut or feed inside printSingleBitmap.
 *     A feed after the bitmap is REQUIRED, not forbidden — without it the
 *     receipt stays inside the mechanism until the NEXT job pushes it out
 *     (observed on-device as "nothing comes out, second run prints").
 *   - setPageFormat: 1 = 58mm, 0 = 80mm.
 *
 * Correction history: an earlier revision forbade feeding, taken from the
 * note at the top of iMin's demo imin-customer-odoo.js ("printSingleBitmap
 * 内部已经做了 partialCut"). That claim does not hold for this build — the
 * same demo's sendPrintingJobFixed() appends printAndFeedPaper(100) +
 * partialCut(), and the code confirms no cut inside printSingleBitmap. The
 * prohibition was correct only for SDK builds that really do auto-cut.
 */
import { renderHTMLToBitmap } from "./receipt_renderer"
import {
	copyLabelFor,
	loadDeviceConfig,
	resolvePrintConfig,
	saveDeviceConfig,
	withCopyLabel,
} from "./receipt_layout"

export { loadDeviceConfig, saveDeviceConfig }
const STATUS_POLL_MS = 500
const STATUS_TIMEOUT_MS = 15000
// The bitmap command resolves once queued; give the head a moment to commit it
// to the print buffer before feeding, or the feed can overtake the raster.
const SETTLE_MS = 200
// Feed / tail / copies defaults and clamps live in
// receipt_layout.resolvePrintConfig — the same resolver the Direct Print
// preview uses, so a preview cannot drift from what actually prints.

/** 58mm -> pageFormat 1; 80mm -> 0; custom keeps 58mm's value by dot count. */
function pageFormatFor(paper, dots) {
	if (paper === "80mm") return 0
	if (paper === "58mm") return 1
	return dots <= 384 ? 1 : 0
}

// Conservative thermal throughput used ONLY to reserve wall-clock time for
// the copy that is still printing when getPrinterStatus() already reports 0.
// 8 dots/mm at a slow ~100 mm/s -> ~800 dots/s. Deliberately on the slow
// side: underestimating the speed just adds idle wait before the next copy,
// overestimating it would re-introduce the swallowed tear-off pause.
const PRINT_DOTS_PER_SECOND = 800

/**
 * Estimated wall-clock time the head needs for a bitmap of `heightDots`.
 * Fake/test bitmaps may carry no height -> 0 (no reservation).
 */
function bitmapPrintMs(heightDots) {
	const h = Number(heightDots)
	if (!Number.isFinite(h) || h <= 0) return 0
	return Math.round((h / PRINT_DOTS_PER_SECOND) * 1000)
}

/**
 * @param {object} [deps] - injectable for tests.
 * @param {() => object} [deps.factory] - returns the SDK printer instance.
 * @param {() => object} [deps.loadConfig]
 * @param {number} [deps.statusTimeoutMs] - injectable timeout for tests (default 15000)
 * @param {number} [deps.statusPollMs] - injectable poll interval for tests (default 500)
 */
export function createIminDriver(deps = {}) {
	const loadConfig = deps.loadConfig || loadDeviceConfig
	const statusTimeoutMs = deps.statusTimeoutMs ?? STATUS_TIMEOUT_MS
	const statusPollMs = deps.statusPollMs ?? STATUS_POLL_MS
	let printer = null

	async function ensurePrinter() {
		if (printer) return printer
		if (!deps.factory) {
			throw new Error("iMin SDK not loaded (window.IminPrinter missing)")
		}
		const cfg = loadConfig()
		const p = deps.factory()
		if (cfg.host) p.address = cfg.host
		const connected = await p.connect()
		if (!connected) throw new Error("Could not connect to iMin print service")
		p.initPrinter("SPI")
		printer = p
		return p
	}

	// The SDK's getPrinterStatus() only resolves when a type===2 reply
	// arrives; there is no internal timeout. If the service drops the query
	// under load (seen on-device: a reply arrives ~2 s later or not at all
	// during repeated Test Prints), `await p.getPrinterStatus()` would hang
	// forever and freeze the cashier. The per-call race below keeps the
	// status gate bounded by the driver's own statusTimeoutMs.
	function callStatus(p) {
		return new Promise((resolve) => {
			let settled = false
			const timer = setTimeout(() => {
				if (settled) return
				settled = true
				resolve({ value: -1, timedOut: true })
			}, statusPollMs * 4)
			p.getPrinterStatus().then(
				(s) => {
					if (settled) return
					settled = true
					clearTimeout(timer)
					resolve(s)
				},
				() => {
					if (settled) return
					settled = true
					clearTimeout(timer)
					resolve({ value: -1, timedOut: true })
				},
			)
		})
	}

	async function waitIdle(p) {
		const deadline = Date.now() + statusTimeoutMs
		for (;;) {
			const status = await callStatus(p)
			const code = Number(status?.value)
			if (code === 0) return
			if (Date.now() > deadline) {
				if (code === 8 || code === 7) throw new Error("Printer out of paper")
				throw new Error(`Printer not connected (status ${code})`)
			}
			await new Promise((r) => setTimeout(r, statusPollMs))
		}
	}

	return {
		id: "imin",

		async isAvailable() {
			try {
				await ensurePrinter()
				return true
			} catch {
				return false
			}
		},

		async getStatus() {
			try {
				const p = await ensurePrinter()
				const status = await callStatus(p)
				const code = Number(status?.value)
				const out = { ok: code === 0, code }
				if (status?.timedOut) out.message = "no status reply (device busy?)"
				return out
			} catch (err) {
				return { ok: false, code: -1, message: err.message }
			}
		},

		/**
		 * @param {string} html
		 * @param {object} [opts]
		 * @param {(html, o) => Promise<{dataURL:string}>} [opts.render] - injected for tests
		 * @param {object} [opts.config] - server (transport) config used as the
		 *   fallback below each device value. An explicit device value
		 *   (including false / "58mm") always wins; only an ABSENT device key
		 *   falls through to the server value. `??` (not `||`) keeps that
		 *   distinction. Returns the EFFECTIVE { paper, dots } so the caller
		 *   can log what was actually printed.
		 * @returns {Promise<{paper:string, dots:number}>}
		 */
		async printHTML(html, opts = {}) {
			const r = resolvePrintConfig(loadConfig(), opts.config || {})
			const {
				paper,
				customDots,
				cut,
				copies,
				copyDelayMs,
				dots,
				tailDots,
				feedDots,
				fontScale,
				useLabels,
				labels,
			} = r
			const render = opts.render || ((h, o) => renderHTMLToBitmap(h, o))

			const p = await ensurePrinter()
			p.setPageFormat(pageFormatFor(paper, dots))

			const renderOpts = { paper, customDots, tailDots, fontScale }
			let bitmap = null
			let bitmaps = null
			if (useLabels) {
				bitmaps = await Promise.all(
					Array.from({ length: copies }, (_, idx) =>
						render(withCopyLabel(html, labels[idx]), renderOpts),
					),
				)
			} else {
				bitmap = await render(html, renderOpts)
			}

			for (let i = 0; i < copies; i++) {
				const bmp = useLabels ? bitmaps[i] : bitmap
				const tQueued = Date.now()
				await p.printSingleBitmap(bmp.dataURL, 1) // 1 = centre alignment

				// Resolve above means "queued" — the raster may not be in the print
				// buffer yet. Reference flows wait before advancing the paper.
				await new Promise((r) => setTimeout(r, SETTLE_MS))

				// The feed is what makes the receipt physically leave the printer.
				// Omitting it is what caused the "first run prints nothing, second
				// run prints the first one" behaviour seen on device.
				p.printAndFeedPaper(feedDots)
				if (cut) p.partialCut()

				await waitIdle(p)

				// Tear-off pause before the next copy; never after the final one.
				//
				// Measured from QUEUE time, not from here. On device (2026-09-03)
				// getPrinterStatus() already reports 0 while the head is still
				// printing, so waitIdle passes instantly and a bare copyDelayMs is
				// consumed by the copy still coming out: the pause visibly existed
				// with fontScale 60 (short bitmap) and vanished at fontScale 100
				// (tall bitmap). Reserve the bitmap's estimated print time first,
				// so the next sheet starts no earlier than "printed + delay".
				// The configured delay stays the minimum tear-off window.
				if (i < copies - 1) {
					const elapsed = Date.now() - tQueued
					const minCycleMs = SETTLE_MS + bitmapPrintMs(bmp.height) + copyDelayMs
					const remaining = minCycleMs - elapsed
					if (remaining > 0) {
						await new Promise((r) => setTimeout(r, remaining))
					}
				}
			}

			return { paper, dots, copies, tailDots }
		},

		describe() {
			const cfg = loadConfig()
			return {
				id: "imin",
				label: "iMin Direct",
				detail: cfg.host || "127.0.0.1:8081",
			}
		},
	}
}
