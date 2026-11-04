/**
 * RawBT driver: an alternative to the iMin JS print service.
 *
 * RawBT ("RawBT ESC/POS print service", package ru.a402d.rawbtprinter) is an
 * Android app that accepts raw ESC/POS bytes and forwards them to whatever
 * printer it is set up for: the built-in printer of POS devices it supports,
 * a Bluetooth (classic SPP or BLE) printer, USB, or a LAN printer. Use it on
 * a till where the iMin service on ws://127.0.0.1:8081 does not answer
 * (plugin missing, service version mismatch, Chrome local-network block).
 *
 * Two print modes, from the same POS Settings knob as iMin (imin_print_mode /
 * imin_eod_print_mode):
 *   - "bitmap": the same bitmap the iMin bitmap lane prints (one renderer,
 *     so the paper looks the same), encoded as ESC/POS raster (GS v 0).
 *     Heavy: an html2canvas render plus tens of KB per sheet.
 *   - "text": the receipt_text layout (the one iMin text mode uses) sent as
 *     plain ESC/POS text in the printer's own font A (12x24 dots). No
 *     html2canvas and a few KB per job; only the small logo is a raster. The
 *     printer font is fixed, so the font-scale knobs do not apply; line
 *     spacing and the side/top margins do.
 * Text mode needs the invoice / closing doc; a print without one (test page,
 * server-HTML fallback) stays bitmap, the same rule as the iMin driver.
 *
 * Two ways to hand the bytes over (per device, Direct Print page):
 *   - "intent" (default): navigate to
 *       intent:base64,<bytes>#Intent;scheme=rawbt;package=ru.a402d.rawbtprinter;end;
 *     Android opens RawBT, which prints. No network, so no Local Network
 *     Access prompt. Fire-and-forget: the page never learns if paper came out.
 *   - "websocket": send the bytes to RawBT's WebSocket server
 *       ws://127.0.0.1:40213/
 *     (RawBT 7+: the separate "RawBT websocket server" app). No app switch,
 *     but it is a loopback connection, so Chrome's local-network permission
 *     applies just like the iMin service.
 *
 * Every sheet (copies, then the crew slip) is followed by the feed (+ cut).
 * Intent mode sends them as ONE job (each intent is an app switch), so there
 * is no tear-off pause. Websocket mode sends one message per sheet and waits
 * the estimated head time plus copyDelayMs / crewDelayMs in between.
 */
import { logger } from "@/utils/logger"
import { __ } from "@/utils/translation"

import { buildEscPosPrint, bytesToBase64 } from "./escpos"
import { loadDeviceConfig } from "./receipt_layout"

// The encoder moved to escpos.js (shared with the iMin Native driver);
// re-exported so existing imports keep working.
export {
	FONT_A_DOTS,
	asciiBytes,
	buildEscPosJob,
	buildEscPosSheets,
	bytesToBase64,
	encodeTextOps,
	escCut,
	escFeed,
	escInit,
	escRaster,
	escposCols,
} from "./escpos"

const log = logger.create("RawbtClient")

export const RAWBT_PACKAGE = "ru.a402d.rawbtprinter"
export const RAWBT_WS_URL = "ws://127.0.0.1:40213/"
export const RAWBT_MODES = ["intent", "websocket"]
const WS_OPEN_TIMEOUT_MS = 5000
// Above this the Android intent may not survive the Binder transaction.
const INTENT_WARN_BYTES = 400 * 1024

/** The Android intent URL RawBT registers for (base64 payload). */
export function rawbtIntentURL(bytes) {
	return `intent:base64,${bytesToBase64(bytes)}#Intent;scheme=rawbt;package=${RAWBT_PACKAGE};end;`
}

function isAndroid() {
	return (
		typeof navigator !== "undefined" &&
		/android/i.test(navigator.userAgent || "")
	)
}

function loadMode() {
	const m = loadDeviceConfig().rawbtMode
	return RAWBT_MODES.includes(m) ? m : "intent"
}

function defaultLaunch(url) {
	window.location.href = url
}

/**
 * Send bytes to RawBT's WebSocket server. Rejects (so the transport may fall
 * back) only when nothing was sent; once send() ran the job is RawBT's.
 */
function defaultSendWs(
	bytes,
	{ url = RAWBT_WS_URL, timeoutMs = WS_OPEN_TIMEOUT_MS } = {},
) {
	return new Promise((resolve, reject) => {
		let settled = false
		let ws
		const fail = (msg) => {
			if (settled) return
			settled = true
			try {
				ws?.close()
			} catch {
				// Guarded
			}
			reject(new Error(msg))
		}
		const timer = setTimeout(
			() => fail(__("RawBT websocket server did not answer")),
			timeoutMs,
		)
		try {
			ws = new WebSocket(url)
		} catch (err) {
			clearTimeout(timer)
			fail(err?.message || __("RawBT websocket server did not answer"))
			return
		}
		ws.binaryType = "arraybuffer"
		ws.onerror = () => {
			clearTimeout(timer)
			fail(__("RawBT websocket server did not answer"))
		}
		ws.onopen = () => {
			clearTimeout(timer)
			if (settled) return
			settled = true
			ws.send(bytes)
			// Let the buffer drain before closing; close() would not drop it,
			// but some servers treat an early close as an aborted job.
			const closeWhenDrained = () => {
				if (ws.bufferedAmount > 0) return setTimeout(closeWhenDrained, 100)
				try {
					ws.close(1000, "Work complete")
				} catch {
					// Guarded
				}
			}
			closeWhenDrained()
			resolve(true)
		}
	})
}

/**
 * @param {object} [deps] - injectable for tests.
 * @param {() => string} [deps.mode] - "intent" | "websocket"
 * @param {(url:string) => void} [deps.launch] - opens the intent URL
 * @param {(bytes:Uint8Array) => Promise<any>} [deps.sendWs] - websocket send
 * @param {() => boolean} [deps.isAndroid]
 * @param {(html, opts) => Promise<{imageData:ImageData, height:number}>} [deps.render]
 * @param {(widthDots:number, paperDots:number) => Promise<ImageData|null>} [deps.loadLogo]
 */
export function createRawbtDriver(deps = {}) {
	const mode = deps.mode || loadMode
	const launch = deps.launch || defaultLaunch
	const sendWs = deps.sendWs || defaultSendWs
	const android = deps.isAndroid || isAndroid
	const sleep =
		deps.sleep || ((ms) => new Promise((res) => setTimeout(res, ms)))
	const encodeDeps = { render: deps.render, loadLogo: deps.loadLogo }

	async function available() {
		// The intent only resolves on Android; the websocket server is
		// reachable from any OS, so its failure surfaces at send time.
		return mode() === "websocket" ? true : android()
	}

	return {
		id: "rawbt",

		isAvailable: available,

		async getStatus() {
			const ok = await available()
			return ok
				? { ok: true, code: 0 }
				: {
						ok: false,
						code: -1,
						message: __("RawBT intent needs an Android device"),
					}
		},

		/**
		 * @returns {Promise<{paper:string, dots:number, copies:number}>}
		 */
		async printHTML(html, opts = {}) {
			const { bytes, r, textMode, sheets, parts } = await buildEscPosPrint(
				html,
				opts,
				encodeDeps,
			)

			const m = mode()
			log.info("rawbt job", {
				mode: m,
				printMode: textMode ? "text" : "bitmap",
				bytes: bytes.length,
				sheets,
			})
			if (m === "websocket") {
				// One message per sheet with the tear-off pause in between. RawBT
				// reports no "printed" event, so the pause runs from the send:
				// estimated head time (printSpeed) + the copy / crew delay.
				for (let i = 0; i < parts.length; i++) {
					try {
						await sendWs(parts[i].bytes)
					} catch (err) {
						// COR-FE-10: earlier sheets already went to paper.
						if (i > 0 && err && typeof err === "object") err.postPrint = true
						throw err
					}
					const next = parts[i + 1]
					if (!next) break
					const delay = next.kind === "crew" ? r.crewDelayMs : r.copyDelayMs
					const headMs = Math.round((parts[i].heightDots / r.printSpeed) * 1000)
					await sleep(headMs + delay)
				}
			} else {
				if (bytes.length > INTENT_WARN_BYTES) {
					log.warn(
						"RawBT intent payload is large; switch to text mode or websocket if it does not print",
						bytes.length,
					)
				}
				launch(rawbtIntentURL(bytes))
			}
			return { paper: r.paper, dots: r.dots, copies: sheets }
		},

		describe() {
			const m = mode()
			return {
				id: "rawbt",
				label: __("RawBT"),
				detail: m === "websocket" ? RAWBT_WS_URL : __("Android intent"),
			}
		},
	}
}
