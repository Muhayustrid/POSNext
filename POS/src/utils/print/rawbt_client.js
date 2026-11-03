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
 * The receipt is the same bitmap the iMin bitmap lane prints (one renderer,
 * so the paper looks the same), encoded as ESC/POS raster (GS v 0), followed
 * by the feed and the optional cut. Text mode is iMin-only; RawBT always
 * prints the bitmap.
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
 * Every sheet (copies, then the crew slip) travels in ONE job, separated by
 * feed (+ cut). There is no tear-off pause between sheets: RawBT prints the
 * job in one go, so enable the cut when printing more than one sheet.
 */
import { logger } from "@/utils/logger"
import { __ } from "@/utils/translation"

import { loadDeviceConfig, resolvePrintConfig } from "./receipt_layout"
import { renderHTMLToBitmap } from "./receipt_renderer"

const log = logger.create("RawbtClient")

export const RAWBT_PACKAGE = "ru.a402d.rawbtprinter"
export const RAWBT_WS_URL = "ws://127.0.0.1:40213/"
export const RAWBT_MODES = ["intent", "websocket"]
const WS_OPEN_TIMEOUT_MS = 5000
// Rows per GS v 0 command. Many cheap heads cap one raster command; small
// bands keep every printer RawBT drives happy at no cost in speed.
const RASTER_BAND_ROWS = 128
// Above this the Android intent may not survive the Binder transaction.
const INTENT_WARN_BYTES = 400 * 1024

const ESC = 0x1b
const GS = 0x1d

/** ESC @: reset the printer to its defaults. */
export function escInit() {
	return [ESC, 0x40]
}

/** ESC J n, split so any length fits the 0..255 parameter. */
export function escFeed(dots) {
	const out = []
	let left = Math.max(0, Math.round(Number(dots) || 0))
	while (left > 0) {
		const n = Math.min(255, left)
		out.push(ESC, 0x4a, n)
		left -= n
	}
	return out
}

/** GS V 1: partial cut (ignored by printers without a cutter). */
export function escCut() {
	return [GS, 0x56, 0x01]
}

/**
 * ESC/POS raster bytes (GS v 0, normal density) for a binarized RGBA image.
 * A pixel is black when its red channel is below 128 — binarize() already
 * forced every pixel to pure black or white, and the renderer paints on
 * white, so transparent areas never occur.
 *
 * @param {{width:number, height:number, data:Uint8ClampedArray}} image
 * @returns {number[]}
 */
export function escRaster(image) {
	const { width, height, data } = image || {}
	if (!(width > 0) || !(height > 0) || !data) return []
	const bytesPerRow = Math.ceil(width / 8)
	const out = []
	for (let top = 0; top < height; top += RASTER_BAND_ROWS) {
		const rows = Math.min(RASTER_BAND_ROWS, height - top)
		out.push(
			GS,
			0x76,
			0x30,
			0x00,
			bytesPerRow & 0xff,
			(bytesPerRow >> 8) & 0xff,
			rows & 0xff,
			(rows >> 8) & 0xff,
		)
		for (let y = top; y < top + rows; y++) {
			const rowStart = y * width * 4
			for (let bx = 0; bx < bytesPerRow; bx++) {
				let byte = 0
				for (let bit = 0; bit < 8; bit++) {
					const x = bx * 8 + bit
					if (x >= width) continue
					const i = rowStart + x * 4
					// Alpha 0 is paper, whatever the colour channels hold.
					if (data[i + 3] !== 0 && data[i] < 128) byte |= 0x80 >> bit
				}
				out.push(byte)
			}
		}
	}
	return out
}

/**
 * One ESC/POS job for every sheet: init, then per sheet raster + feed
 * (+ cut). The feed is what pushes the receipt past the tear bar, exactly as
 * on the iMin lane.
 */
export function buildEscPosJob(images, { feedDots = 0, cut = false } = {}) {
	const out = [...escInit()]
	for (const img of images) {
		out.push(...escRaster(img))
		out.push(...escFeed(feedDots))
		if (cut) out.push(...escCut())
	}
	return Uint8Array.from(out)
}

/** Base64 of raw bytes, chunked so large receipts do not blow the stack. */
export function bytesToBase64(bytes) {
	let bin = ""
	const CHUNK = 0x8000
	for (let i = 0; i < bytes.length; i += CHUNK) {
		bin += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
	}
	return btoa(bin)
}

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
 */
export function createRawbtDriver(deps = {}) {
	const mode = deps.mode || loadMode
	const launch = deps.launch || defaultLaunch
	const sendWs = deps.sendWs || defaultSendWs
	const android = deps.isAndroid || isAndroid
	const render = deps.render || ((h, o) => renderHTMLToBitmap(h, o))

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
			const r = resolvePrintConfig({}, opts.config || {}, { kind: opts.kind })
			const renderOpts = {
				paper: r.paper,
				customDots: r.customDots,
				tailDots: r.tailDots,
				fontScale: r.fontScale,
				lineSpacing: r.lineSpacing,
				sideMarginDots: r.sideMarginDots,
				topMarginDots: r.topMarginDots,
				queueGapDots: r.queueGapDots,
			}
			const crewApplies =
				opts.kind !== "eod" && r.crewSlipEnabled && Boolean(opts.crewHTML)
			if (!html) throw new Error(__("Nothing to print"))
			const [receipt, crew] = await Promise.all([
				render(html, renderOpts),
				crewApplies
					? render(opts.crewHTML, { ...renderOpts, fontScale: r.crewFontScale })
					: null,
			])
			const images = Array.from({ length: r.copies }, () => receipt.imageData)
			if (crew) images.push(crew.imageData)
			const bytes = buildEscPosJob(images, { feedDots: r.feedDots, cut: r.cut })

			const m = mode()
			log.info("rawbt job", {
				mode: m,
				bytes: bytes.length,
				sheets: images.length,
			})
			if (m === "websocket") {
				await sendWs(bytes)
			} else {
				if (bytes.length > INTENT_WARN_BYTES) {
					log.warn(
						"RawBT intent payload is large; enable websocket mode if it does not print",
						bytes.length,
					)
				}
				launch(rawbtIntentURL(bytes))
			}
			return { paper: r.paper, dots: r.dots, copies: images.length }
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
