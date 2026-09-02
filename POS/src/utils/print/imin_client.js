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
import { dotsForPaper } from "./paper"
import { renderHTMLToBitmap } from "./receipt_renderer"

const DEVICE_CONFIG_KEY = "pos_imin_device_config"
const STATUS_POLL_MS = 500
const STATUS_TIMEOUT_MS = 15000

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
		 */
		async printHTML(html, opts = {}) {
			const cfg = loadConfig()
			const paper = cfg.paper || "58mm"
			const dots = dotsForPaper(paper, cfg.customDots)
			const render = opts.render || ((h, o) => renderHTMLToBitmap(h, o))

			const p = await ensurePrinter()
			p.setPageFormat(pageFormatFor(paper, dots))

			const bitmap = await render(html, { paper, customDots: cfg.customDots })
			await p.printSingleBitmap(bitmap.dataURL, 1) // 1 = centre alignment

			if (cfg.cut) p.partialCut()
			// No feeds here — under v1.4.0 they land on the NEXT receipt.
			await waitIdle(p)
			return true
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
