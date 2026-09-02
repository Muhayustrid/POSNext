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
import { dotsForPaper } from "./paper"
import { renderHTMLToBitmap } from "./receipt_renderer"

const DEVICE_CONFIG_KEY = "pos_imin_device_config"
const STATUS_POLL_MS = 500
const STATUS_TIMEOUT_MS = 15000
// Dots to feed after a bitmap so the printed content clears the tear-off bar.
// 8 dots/mm (205 DPI) -> 100 dots ~= 12.5mm. Matches the value in iMin's own
// sendPrintingJobFixed() reference flow.
const DEFAULT_FEED_DOTS = 100
// The bitmap command resolves once queued; give the head a moment to commit it
// to the print buffer before feeding, or the feed can overtake the raster.
const SETTLE_MS = 200

/** 58mm -> pageFormat 1; 80mm -> 0; custom keeps 58mm's value by dot count. */
function pageFormatFor(paper, dots) {
	if (paper === "80mm") return 0
	if (paper === "58mm") return 1
	return dots <= 384 ? 1 : 0
}

export function loadDeviceConfig() {
	try {
		return JSON.parse(localStorage.getItem(DEVICE_CONFIG_KEY) || "{}")
	} catch {
		return {}
	}
}

export function saveDeviceConfig(patch) {
	const next = { ...loadDeviceConfig(), ...patch }
	localStorage.setItem(DEVICE_CONFIG_KEY, JSON.stringify(next))
	return next
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

	async function waitIdle(p) {
		const deadline = Date.now() + statusTimeoutMs
		for (;;) {
			const status = await p.getPrinterStatus()
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
				const status = await p.getPrinterStatus()
				return { ok: Number(status?.value) === 0, code: Number(status?.value) }
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
			const cfg = loadConfig()
			const serverCfg = opts.config || {}
			const paper = cfg.paper ?? serverCfg.paper ?? "58mm"
			const customDots = cfg.customDots ?? serverCfg.customDots ?? undefined
			const cut = cfg.cut ?? serverCfg.cut ?? false
			const dots = dotsForPaper(paper, customDots)
			const render = opts.render || ((h, o) => renderHTMLToBitmap(h, o))

			const p = await ensurePrinter()
			p.setPageFormat(pageFormatFor(paper, dots))

			const bitmap = await render(html, { paper, customDots })
			await p.printSingleBitmap(bitmap.dataURL, 1) // 1 = centre alignment

			// Resolve above means "queued" — the raster may not be in the print
			// buffer yet. Reference flows wait before advancing the paper.
			await new Promise((r) => setTimeout(r, SETTLE_MS))

			// The feed is what makes the receipt physically leave the printer.
			// Omitting it is what caused the "first run prints nothing, second
			// run prints the first one" behaviour seen on device.
			const feedDots = cfg.feedDots ?? serverCfg.feedDots ?? DEFAULT_FEED_DOTS
			p.printAndFeedPaper(feedDots)
			if (cut) p.partialCut()

			await waitIdle(p)
			return { paper, dots }
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
