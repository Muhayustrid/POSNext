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
import { logger } from "@/utils/logger"

import { renderHTMLToBitmap } from "./receipt_renderer"
import {
	loadDeviceConfig,
	resolvePrintConfig,
	saveDeviceConfig,
} from "./receipt_layout"

export { loadDeviceConfig, saveDeviceConfig }
const log = logger.create("IminClient")
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

// Thermal throughput used ONLY to reserve wall-clock time for the copy that is
// still printing when getPrinterStatus() already reports 0. The SDK exposes no
// print-completion signal (status is fault-only: 0 = normal, no busy state,
// every command is fire-and-forget into the device queue), so this rate is the
// only anchor for "the previous copy has physically finished". On-device
// measurements (POS Print Log 2026-09-03: ~13 s for a 2-copy, 1-item receipt
// at fontScale 100) put real throughput at ~200-250 dots/s, so the estimate
// must sit AT OR BELOW the slowest real device: over-estimating sends copy N+1
// while copy N is still coming out and swallows the tear-off pause, while
// under-estimating merely adds idle time before the next copy.
export const PRINT_DOTS_PER_SECOND = 200

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
		 * @param {string} [opts.crewHTML] - complete document to print INSTEAD OF
		 *   the second copy (the compact crew slip). Rendered with the resolved
		 *   crewFontScale and printed exactly as handed over — nothing is
		 *   prepended to it, and nothing is printed above any copy.
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
				crewFontScale,
				lineSpacing,
				sideMarginDots,
			} = r
			const render = opts.render || ((h, o) => renderHTMLToBitmap(h, o))

			const p = await ensurePrinter()
			p.setPageFormat(pageFormatFor(paper, dots))

			// lineSpacing is deliberately NOT per-copy: there is one vertical
			// density for everything direct printed, receipt and slip alike. The
			// side margin rides the same object for the same reason — it is a
			// property of the paper, not of which sheet is on it — so the crew
			// slip inherits it through the spread below.
			const renderOpts = {
				paper,
				customDots,
				tailDots,
				fontScale,
				lineSpacing,
				sideMarginDots,
			}
			// A crew slip only exists for a multi-copy job: with one copy there is
			// no second sheet to replace. When it applies, copy 2 (index 1) prints
			// the slip at its own font scale and every other copy prints the
			// receipt exactly as built — no banner above either sheet, so the paper
			// looks like one receipt and one order list.
			const crewApplies = Boolean(opts.crewHTML) && copies > 1
			// Every non-crew copy is the same html with the same options, so one
			// bitmap serves them all; only the slip is a second render.
			const [bitmap, crewBitmap] = await Promise.all([
				render(html, renderOpts),
				crewApplies
					? render(opts.crewHTML, { ...renderOpts, fontScale: crewFontScale })
					: null,
			])

			for (let i = 0; i < copies; i++) {
				const bmp = crewApplies && i === 1 ? crewBitmap : bitmap
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
				// (tall bitmap). Invariant: the gap between two copies is always
				// at least copyDelayMs, ON TOP of whatever the pipeline actually
				// took — the reservation only extends the pause (when status went
				// idle early) and shrinks to zero when the pipeline overran the
				// estimate, so slow uploads plus waitIdle polling overshoot can
				// no longer swallow the configured tear-off window.
				const elapsed = Date.now() - tQueued
				const reserveMs = Math.max(
					0,
					SETTLE_MS + bitmapPrintMs(bmp.height) - elapsed,
				)
				const isLastCopy = i === copies - 1
				const pauseMs = isLastCopy ? 0 : reserveMs + copyDelayMs
				// The reservation is invisible to POS Print Log (it only sees the
				// whole print), so say per copy how the wall clock was spent — this
				// is what makes a swallowed pause on site diagnosable after the fact.
				log.info("copy printed", {
					copy: i + 1,
					heightDots: bmp.height,
					elapsedMs: elapsed,
					reserveMs,
					pauseMs,
				})
				if (!isLastCopy) {
					await new Promise((r) => setTimeout(r, pauseMs))
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
