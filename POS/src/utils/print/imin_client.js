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
 * Sheet layout (2026-09-06): a job prints `copies` IDENTICAL receipt sheets,
 * then at most one crew slip — strictly last. The slip is gated purely by
 * the crewSlipEnabled knob (server config; > device override only under test
 * injection), never by the copy count; the tear-off pause runs between every
 * pair of sheets, none after the last one.
 *
 * Correction history: an earlier revision forbade feeding, taken from the
 * note at the top of iMin's demo imin-customer-odoo.js ("printSingleBitmap
 * 内部已经做了 partialCut"). That claim does not hold for this build — the
 * same demo's sendPrintingJobFixed() appends printAndFeedPaper(100) +
 * partialCut(), and the code confirms no cut inside printSingleBitmap. The
 * prohibition was correct only for SDK builds that really do auto-cut.
 */
import { logger } from "@/utils/logger"
import { __ } from "@/utils/translation"

import { binarize, renderHTMLToBitmap } from "./receipt_renderer"
import {
	DEFAULT_PRINT_SPEED,
	loadDeviceConfig,
	resolvePrintConfig,
	saveDeviceConfig,
	stripDeviceLayoutKeys,
} from "./receipt_layout"

import { DOTS_PER_MM } from "./paper"
import {
	buildCrewSlipTextJob,
	buildReceiptTextJob,
	colsFor,
	RECEIPT_LOGO_MM,
	RECEIPT_LOGO_URL,
	textSizes,
} from "./receipt_text"

export { loadDeviceConfig, saveDeviceConfig, stripDeviceLayoutKeys }
const log = logger.create("IminClient")
const STATUS_POLL_MS = 500
const STATUS_TIMEOUT_MS = 15000
const STATUS_REPLY_TIMEOUT_MS = 5000
// The bitmap command resolves once queued; give the head a moment to commit it
// to the print buffer before feeding, or the feed can overtake the raster.
const SETTLE_MS = 200
// Feed / tail / copies defaults and clamps live in
// receipt_layout.resolvePrintConfig — the same resolver the Direct Print
// preview uses, so a preview cannot drift from what actually prints.

/**
 * The printer host is a device attachment (like the QZ printer name), so it
 * stays in localStorage — layout knobs do NOT come from here any more; they
 * resolve from the server config (opts.config) alone.
 */
function loadPrinterHost() {
	return loadDeviceConfig().host
}

/** 58mm -> pageFormat 1; 80mm -> 0; custom keeps 58mm's value by dot count. */
function pageFormatFor(paper, dots) {
	if (paper === "80mm") return 0
	if (paper === "58mm") return 1
	return dots <= 384 ? 1 : 0
}

// Thermal throughput used ONLY to reserve wall-clock time for the sheet that
// is still printing when getPrinterStatus() already reports 0. The SDK exposes
// no print-completion signal (status is fault-only: 0 = normal, no busy state,
// every command is fire-and-forget into the device queue), so this rate is the
// only anchor for "the previous sheet has physically finished".
// Over-estimating sends sheet N+1 while sheet N is still coming out and
// swallows the tear-off pause; under-estimating leaves the till idle between
// sheets. The old fixed 200 dots/s came from a measurement that also counted
// render + upload + the reservation itself, so it made a receipt + crew slip
// job wait seconds after the head had stopped. The rate is now the
// imin_print_speed knob (POS Settings); this is only its default.
export const PRINT_DOTS_PER_SECOND = DEFAULT_PRINT_SPEED

/**
 * Estimated wall-clock time the head needs for `heightDots` of paper.
 * Fake/test bitmaps may carry no height -> 0 (no reservation).
 */
function printMs(heightDots, dotsPerSecond = PRINT_DOTS_PER_SECOND) {
	const h = Number(heightDots)
	if (!Number.isFinite(h) || h <= 0) return 0
	return Math.round((h / dotsPerSecond) * 1000)
}

// iMin setTextTypeface: 0 DEFAULT, 1 MONOSPACE. Text mode lays receipts out
// in fixed columns, so it needs the monospace face. (VERIFY ON DEVICE.)
const TYPEFACE_DEFAULT = 0
const TYPEFACE_MONOSPACE = 1
// Rough line pitch of printer text, as a multiple of the text size; only
// feeds the reservation estimate, never the layout.
const TEXT_LINE_PITCH = 1.25

/**
 * Text-mode logo: the receipt logo drawn once at its paper width, forced to
 * pure black/white, kept as a data URL. Cached per width for the session (and
 * in localStorage so an offline reload still prints it). null when the image
 * cannot be loaded — the receipt then simply prints without it.
 */
const _logoCache = new Map()
function defaultLoadLogo(widthDots) {
	if (_logoCache.has(widthDots)) return _logoCache.get(widthDots)
	const key = `pos_next_imin_logo:v1:${widthDots}`
	const job = (async () => {
		try {
			const cached = localStorage.getItem(key)
			if (cached) return JSON.parse(cached)
		} catch {
			// Storage unavailable: render it.
		}
		const img = new Image()
		img.src = RECEIPT_LOGO_URL
		await img.decode()
		const width = widthDots
		const height = Math.max(
			1,
			Math.round((img.naturalHeight / img.naturalWidth) * width),
		)
		const canvas = document.createElement("canvas")
		canvas.width = width
		canvas.height = height
		const ctx = canvas.getContext("2d")
		ctx.fillStyle = "#ffffff"
		ctx.fillRect(0, 0, width, height)
		ctx.drawImage(img, 0, 0, width, height)
		const data = ctx.getImageData(0, 0, width, height)
		binarize(data)
		ctx.putImageData(data, 0, 0)
		const logo = { dataURL: canvas.toDataURL("image/png"), height }
		try {
			localStorage.setItem(key, JSON.stringify(logo))
		} catch {
			// Quota / private mode: the in-memory cache still holds it.
		}
		return logo
	})().catch((err) => {
		log.warn("receipt logo unavailable, printing without it:", err?.message)
		_logoCache.delete(widthDots)
		return null
	})
	_logoCache.set(widthDots, job)
	return job
}

/**
 * @param {object} [deps] - injectable for tests.
 * @param {() => object} [deps.factory] - returns the SDK printer instance.
 * @param {() => object} [deps.loadConfig] - per-device override layer for
 *   resolvePrintConfig; defaults to empty (layout comes from the server
 *   config passed per print). Injected by tests to exercise precedence.
 * @param {number} [deps.statusTimeoutMs] - injectable timeout for tests (default 15000)
 * @param {number} [deps.statusPollMs] - injectable poll interval for tests (default 500)
 * @param {number} [deps.statusReplyMs] - injectable reply timeout for tests (default 5000)
 * @param {(widthDots:number) => Promise<{dataURL:string, height:number}|null>} [deps.loadLogo]
 *   - text-mode logo source; injected by tests.
 */
export function createIminDriver(deps = {}) {
	const loadConfig = deps.loadConfig || (() => ({}))
	const statusTimeoutMs = deps.statusTimeoutMs ?? STATUS_TIMEOUT_MS
	const statusPollMs = deps.statusPollMs ?? STATUS_POLL_MS
	const statusReplyMs = deps.statusReplyMs ?? STATUS_REPLY_TIMEOUT_MS
	const loadLogo = deps.loadLogo || defaultLoadLogo
	let printer = null
	let connecting = null
	let statusChain = Promise.resolve()
	let printing = 0

	// The SDK auto-reconnects every 4s by replacing this.ws without re-sending
	// initPrinter, and close() sets ws=null so send() throws in the gap.
	// The driver is the single owner of reconnects: reuse the cached printer
	// only while its WebSocket is OPEN, otherwise drop it and create a fresh one.
	// In-flight connects are deduped so concurrent calls don't orphan sockets.
	async function ensurePrinter() {
		if (printer?.ws?.readyState === 1) {
			return printer
		}
		if (connecting) {
			return connecting
		}
		connecting = (async () => {
			try {
				if (printer) {
					try {
						printer.close?.()
					} catch {
						// Guarded
					}
					printer = null
				}
				if (!deps.factory) {
					throw new Error(
						__("iMin SDK not loaded (window.IminPrinter missing)"),
					)
				}
				const p = deps.factory()
				// Disable SDK auto-reconnect: driver owns reconnects
				p.reconnect = () => {}
				const host = loadPrinterHost()
				if (host) p.address = host
				let connected = false
				try {
					connected = await p.connect()
				} catch (err) {
					try {
						p.close?.()
					} catch {
						// Guarded
					}
					throw err
				}
				if (!connected) {
					try {
						p.close?.()
					} catch {
						// Guarded
					}
					throw new Error(__("Could not connect to iMin print service"))
				}
				p.initPrinter("SPI")
				printer = p
				return p
			} finally {
				connecting = null
			}
		})()
		return connecting
	}

	// The SDK's getPrinterStatus() only resolves when a type===2 reply
	// arrives; there is no internal timeout. If the service drops the query
	// under load (seen on-device: a reply arrives ~2 s later or not at all
	// during repeated Test Prints), `await p.getPrinterStatus()` would hang
	// forever and freeze the cashier. Also the SDK only has ONE callback slot,
	// so concurrent calls overwrite each other. We serialize calls with a
	// promise chain and bound each query by statusReplyMs.
	function callStatus(p) {
		const doCall = () =>
			new Promise((resolve) => {
				let settled = false
				const timer = setTimeout(() => {
					if (settled) return
					settled = true
					resolve({ value: -1, timedOut: true })
				}, statusReplyMs)
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

		const run = statusChain.then(doCall, doCall)
		statusChain = run.catch(() => {})
		return run
	}

	async function waitIdle(p) {
		const deadline = Date.now() + statusTimeoutMs
		for (;;) {
			const status = await callStatus(p)
			const code = Number(status?.value)
			if (code === 0) return
			if (Date.now() > deadline) {
				if (code === 8 || code === 7)
					throw new Error(__("Printer out of paper"))
				throw new Error(__("Printer not connected (status {0})", { 0: code }))
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
				if (status?.timedOut) {
					out.message = __("no status reply (device busy?)")
					// An OPEN socket that never answers (e.g. opened before the
					// operator allowed this origin) is dead weight: drop it so the
					// next probe reconnects. Never mid-print — the busy device
					// answering late is normal there.
					if (!printing && printer === p) {
						try {
							p.close?.()
						} catch {
							// Guarded
						}
						printer = null
					}
				}
				return out
			} catch (err) {
				return { ok: false, code: -1, message: err.message }
			}
		},

		/**
		 * @param {string} html
		 * @param {object} [opts]
		 * @param {(html, o) => Promise<{dataURL:string}>} [opts.render] - injected for tests
		 * @param {object} [opts.receiptDoc] - the invoice dict. When present and
		 *   the resolved printMode is "text", the receipt and crew slip are
		 *   laid out by receipt_text and sent as printer text commands; `html`
		 *   and `crewHTML` are then unused (they stay for the other drivers).
		 * @param {string} [opts.crewHTML] - complete document for the compact
		 *   crew slip. Gated ONLY by the resolved crewSlipEnabled (never by
		 *   the copy count), it renders at the resolved crewFontScale and
		 *   prints exactly once AFTER every receipt copy — nothing is
		 *   prepended to it, and nothing is printed above any sheet.
		 * @param {object} [opts.config] - server (POS Settings) config, the
		 *   layout source in production: resolvePrintConfig layering only
		 *   kicks in when a deps.loadConfig override is injected (tests).
		 *   An explicit override value (including false / "58mm") always
		 *   wins; only an ABSENT key falls through to the server value.
		 *   `??` (not `||`) keeps that distinction. Returns the EFFECTIVE
		 *   { paper, dots } so the caller can log what was actually printed.
		 * @returns {Promise<{paper:string, dots:number, copies:number}>}
		 *   copies counts every sheet that reached the printer: the receipt
		 *   copies plus the crew slip when it applied.
		 */
		async printHTML(html, opts = {}) {
			printing++
			try {
				return await printSheets(html, opts)
			} finally {
				printing--
			}
		},

		describe() {
			return {
				id: "imin",
				label: __("iMin Direct"),
				detail: loadPrinterHost() || "127.0.0.1:8081",
			}
		},
	}

	async function printSheets(html, opts) {
		const tStart = Date.now()
		const r = resolvePrintConfig(loadConfig(), opts.config || {}, {
			kind: opts.kind,
		})
		const {
			paper,
			customDots,
			cut,
			printMode,
			printSpeed,
			copies,
			crewSlipEnabled,
			copyDelayMs,
			dots,
			tailDots,
			feedDots,
			fontScale,
			crewFontScale,
			lineSpacing,
			sideMarginDots,
			topMarginDots,
			queueGapDots,
		} = r

		// The crew slip is gated purely by its own toggle, not by the copy
		// count: crewSlipEnabled on means exactly one slip after ALL receipt
		// copies — even for a single-copy job.
		// Text mode needs the invoice doc (it lays the receipt out itself);
		// a print without one (Closing report, test page) stays bitmap.
		const textMode = printMode === "text" && Boolean(opts.receiptDoc)
		const crewApplies =
			crewSlipEnabled && (textMode ? true : Boolean(opts.crewHTML))

		const p = await ensurePrinter()
		const tConnected = Date.now()
		p.setPageFormat(pageFormatFor(paper, dots))

		const sheets = textMode
			? await buildTextSheets(opts.receiptDoc, {
					copies,
					crewApplies,
					dots,
					tailDots,
					fontScale,
					crewFontScale,
				})
			: await buildBitmapSheets(html, opts, {
					copies,
					crewApplies,
					renderOpts: {
						// lineSpacing is deliberately NOT per-copy: there is one
						// vertical density for everything direct printed, receipt and
						// slip alike. The side/top margins ride the same object for the
						// same reason — they are a property of the paper, not of which
						// sheet is on it.
						paper,
						customDots,
						tailDots,
						fontScale,
						lineSpacing,
						sideMarginDots,
						topMarginDots,
						queueGapDots,
					},
					crewFontScale,
				})
		const tPrepared = Date.now()

		// COR-FE-10: once the first sheet is queued, any failure below
		// happens with paper already (partially) out of the printer.
		// Tag it postPrint so the transport stops the chain instead of
		// letting the next driver or the browser fallback print the
		// same receipt a second time.
		let firstSheetQueued = false
		let tFirstQueued = 0
		for (let i = 0; i < sheets.length; i++) {
			try {
				const sheet = sheets[i]
				const tQueued = Date.now()
				await sheet.send(p)
				firstSheetQueued = true
				if (!tFirstQueued) tFirstQueued = Date.now()

				// A bitmap resolves on "queued" — the raster may not be in the
				// print buffer yet, so reference flows wait before advancing the
				// paper. Text commands ride the same socket in order: no wait.
				if (sheet.settleMs) {
					await new Promise((res) => setTimeout(res, sheet.settleMs))
				}

				// The feed is what makes the receipt physically leave the printer.
				// Omitting it is what caused the "first run prints nothing, second
				// run prints the first one" behaviour seen on device.
				p.printAndFeedPaper(feedDots)
				if (cut) p.partialCut()

				await waitIdle(p)

				// Tear-off pause before the next SHEET (receipt or slip alike);
				// never after the final one.
				//
				// Measured from QUEUE time, not from here: getPrinterStatus()
				// reports 0 while the head is still printing, so a bare
				// copyDelayMs would be consumed by the sheet still coming out.
				// The gap between two sheets is at least copyDelayMs ON TOP of the
				// estimated print time; the reservation shrinks to zero when the
				// pipeline already overran the estimate.
				const elapsed = Date.now() - tQueued
				const reserveMs = Math.max(
					0,
					(sheet.settleMs || 0) +
						printMs(sheet.heightDots, printSpeed) -
						elapsed,
				)
				const isLastSheet = i === sheets.length - 1
				const pauseMs = isLastSheet ? 0 : reserveMs + copyDelayMs
				// The reservation is invisible to POS Print Log (it only sees the
				// whole print), so say per sheet how the wall clock was spent —
				// this is what makes a swallowed pause on site diagnosable after
				// the fact. kind tells a swallowed crew slip apart from a copy.
				log.info("sheet printed", {
					sheet: i + 1,
					kind: sheet.kind,
					mode: textMode ? "text" : "bitmap",
					heightDots: sheet.heightDots,
					elapsedMs: elapsed,
					reserveMs,
					pauseMs,
				})
				if (!isLastSheet) {
					await new Promise((res) => setTimeout(res, pauseMs))
				}
			} catch (err) {
				if (firstSheetQueued && err && typeof err === "object") {
					err.postPrint = true
				}
				throw err
			}
		}

		// Where the time went, per phase: connect, prepare (render or layout),
		// until the first sheet was handed to the head, and the whole job.
		log.info("print timing", {
			mode: textMode ? "text" : "bitmap",
			connectMs: tConnected - tStart,
			prepareMs: tPrepared - tConnected,
			firstSheetMs: tFirstQueued ? tFirstQueued - tStart : null,
			totalMs: Date.now() - tStart,
			sheets: sheets.length,
		})

		return { paper, dots, copies: sheets.length, tailDots }
	}

	async function buildBitmapSheets(
		html,
		opts,
		{ copies, crewApplies, renderOpts, crewFontScale },
	) {
		const render = opts.render || ((h, o) => renderHTMLToBitmap(h, o))
		// Every receipt sheet is the same html with the same options, so one
		// bitmap serves them all; only the slip is a second render. The sheet
		// list fixes the order: copies identical receipts, slip last.
		const [bitmap, crewBitmap] = await Promise.all([
			render(html, renderOpts),
			crewApplies
				? render(opts.crewHTML, { ...renderOpts, fontScale: crewFontScale })
				: null,
		])
		const sheet = (bmp, kind) => ({
			kind,
			heightDots: Number(bmp?.height) || 0,
			settleMs: SETTLE_MS,
			send: (p) => p.printSingleBitmap(bmp.dataURL, 1), // 1 = centre alignment
		})
		const sheets = Array.from({ length: copies }, () =>
			sheet(bitmap, "receipt"),
		)
		if (crewApplies) sheets.push(sheet(crewBitmap, "crew"))
		return sheets
	}

	async function buildTextSheets(
		doc,
		{ copies, crewApplies, dots, tailDots, fontScale, crewFontScale },
	) {
		const sizes = textSizes(fontScale)
		const crewSizes = textSizes(crewFontScale)
		const colsOf = (sz) => ({
			base: colsFor(dots, sz.base),
			big: colsFor(dots, sz.big),
			queue: colsFor(dots, sz.queue),
		})
		const logoWidth = Math.min(dots, RECEIPT_LOGO_MM * DOTS_PER_MM)
		// Started now, awaited by the first sheet that needs it: a cached logo
		// costs nothing, a first-ever load overlaps the connect.
		const logo = loadLogo(logoWidth)
		const receiptOps = buildReceiptTextJob(doc, { cols: colsOf(sizes) })
		const crewOps = crewApplies
			? buildCrewSlipTextJob(doc, { cols: colsOf(crewSizes) })
			: null
		// The tail is white space INSIDE a bitmap; text has no raster, so it
		// becomes a feed at the end of the sheet instead.
		const tail = (ops) =>
			tailDots > 0 ? [...ops, { op: "feed", dots: tailDots }] : ops
		const logoHeight = (await logo)?.height || 0
		const sheet = (ops, sz, kind) => ({
			kind,
			heightDots: estimateTextHeight(ops, sz, logoHeight),
			settleMs: 0,
			send: (p) => sendTextOps(p, ops, sz, dots, logo),
		})
		const sheets = Array.from({ length: copies }, () =>
			sheet(tail(receiptOps), sizes, "receipt"),
		)
		if (crewOps) sheets.push(sheet(tail(crewOps), crewSizes, "crew"))
		return sheets
	}
}

/** Paper a text sheet will use, for the reservation estimate only. */
function estimateTextHeight(ops, sizes, logoHeight) {
	let h = 0
	for (const o of ops) {
		if (o.op === "text")
			h += Math.round((sizes[o.size] || sizes.base) * TEXT_LINE_PITCH)
		else if (o.op === "feed") h += o.dots
		else if (o.op === "logo") h += logoHeight
	}
	return h
}

/**
 * Execute text ops on the SDK. Style commands are only sent when they change;
 * everything travels over the one WebSocket, so order is preserved. The logo
 * is the one async step (HTTP upload) and is awaited, plus SETTLE_MS, so text
 * sent after it cannot overtake it. Styles are reset at the end so a later bitmap job or
 * another app starts from the printer defaults.
 */
async function sendTextOps(p, ops, sizes, dots, logoPromise) {
	let align = null
	let size = null
	let bold = null
	p.setTextTypeface(TYPEFACE_MONOSPACE)
	for (const o of ops) {
		if (o.op === "text") {
			if (o.align !== align) {
				align = o.align
				p.setAlignment(align)
			}
			const sz = sizes[o.size] || sizes.base
			if (sz !== size) {
				size = sz
				p.setTextSize(size)
			}
			const b = o.bold ? 1 : 0
			if (b !== bold) {
				bold = b
				p.setTextStyle(bold)
			}
			p.printText(o.text)
		} else if (o.op === "logo") {
			const logo = await logoPromise
			if (logo) {
				await p.printSingleBitmap(logo.dataURL, 1)
				// Same settle the bitmap lane takes before its feed: a command
				// sent right after a queued bitmap can overtake its raster.
				await new Promise((res) => setTimeout(res, SETTLE_MS))
			}
		} else if (o.op === "feed") {
			p.printAndFeedPaper(o.dots)
		}
	}
	p.setTextStyle(0)
	p.setAlignment(0)
	p.setTextTypeface(TYPEFACE_DEFAULT)
}
