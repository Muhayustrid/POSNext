/**
 * iMin 2.0 driver ("imin2"): prints through the POSNext Shell Android app
 * (android-shell/), which loads /pos in a WebView and hands raw ESC/POS bytes
 * to the iMin Printer SDK v2 of the built-in printer. No iMin JS service, no
 * Chrome local-network permission, no print dialog.
 *
 * The app injects `window.PosNextNative` (androidx.webkit
 * addWebMessageListener) only into the configured server origin. Messages are
 * JSON strings carrying an `id`; the app answers with the same `id`:
 *   {id, cmd:"hello"}                    -> {id, ok, app, version, sdk, model, android}
 *   {id, cmd:"status"}                   -> {id, ok, code, message}
 *   {id, cmd:"printRaw", data:<base64>}  -> {id, ok, code?, message?, printed?}
 * `printed: true` on a failed printRaw means paper may already be out.
 *
 * Layout is the RawBT one (escpos.js): text mode needs the invoice / closing
 * doc, otherwise the receipt prints as a raster bitmap.
 */
import { logger } from "@/utils/logger"
import { __ } from "@/utils/translation"

import { buildEscPosPrint, bytesToBase64 } from "./escpos"

const log = logger.create("NativeClient")

export const NATIVE_BRIDGE = "PosNextNative"
const HELLO_TIMEOUT_MS = 2000
const STATUS_TIMEOUT_MS = 5000
// The app answers once the SDK took the job; a long receipt is still one call.
const PRINT_TIMEOUT_MS = 30000

// iMin SDK v2 getPrinterStatus() codes (official PrinterDemo Utils.java):
// 0 normal, 3 cover open, 4 low power, 5 overheat, 6 cutter error,
// 7 paper out, -1 service not bound. 8 kept as paper out like imin_client.
const FAULT_MESSAGES = {
	3: "Printer cover is open",
	7: "Printer out of paper",
	8: "Printer out of paper",
}

function faultMessage(code) {
	const fault = FAULT_MESSAGES[code]
	return fault
		? __(fault)
		: __("Printer not connected (status {0})", { 0: code })
}

function defaultBridge() {
	return typeof window !== "undefined" ? window[NATIVE_BRIDGE] || null : null
}

/** True when this page runs inside the POSNext Shell app. */
export function hasNativeBridge() {
	return Boolean(defaultBridge())
}

/**
 * @param {object} [deps] - injectable for tests.
 * @param {() => object|null} [deps.bridge] - the injected PosNextNative object
 * @param {(html, opts) => Promise<{imageData:ImageData}>} [deps.render]
 * @param {(widthDots:number, paperDots:number) => Promise<ImageData|null>} [deps.loadLogo]
 * @param {{hello?:number, status?:number, print?:number}} [deps.timeouts]
 */
export function createNativeDriver(deps = {}) {
	const getBridge = deps.bridge || defaultBridge
	const encodeDeps = { render: deps.render, loadLogo: deps.loadLogo }
	const timeouts = {
		hello: HELLO_TIMEOUT_MS,
		status: STATUS_TIMEOUT_MS,
		print: PRINT_TIMEOUT_MS,
		...deps.timeouts,
	}

	const pending = new Map()
	let seq = 0
	let wired = null
	let helloInfo = null
	let helloJob = null

	function onMessage(event) {
		let msg
		try {
			msg = typeof event?.data === "string" ? JSON.parse(event.data) : null
		} catch {
			msg = null
		}
		const entry = msg && pending.get(msg.id)
		if (!entry) return
		pending.delete(msg.id)
		clearTimeout(entry.timer)
		entry.resolve(msg)
	}

	/** One request; resolves with the reply, rejects (timedOut) on silence. */
	function request(cmd, payload, timeoutMs) {
		const bridge = getBridge()
		if (!bridge) {
			return Promise.reject(
				new Error(__("POSNext app (iMin SDK 2.0) not detected")),
			)
		}
		if (wired !== bridge) {
			bridge.addEventListener("message", onMessage)
			wired = bridge
		}
		seq += 1
		const id = `${Date.now()}-${seq}`
		return new Promise((resolve, reject) => {
			const timer = setTimeout(() => {
				pending.delete(id)
				const err = new Error(__("Printer did not respond"))
				err.timedOut = true
				reject(err)
			}, timeoutMs)
			pending.set(id, { resolve, timer })
			try {
				bridge.postMessage(JSON.stringify({ id, cmd, ...payload }))
			} catch (err) {
				pending.delete(id)
				clearTimeout(timer)
				reject(err)
			}
		})
	}

	function hello() {
		if (helloInfo) return Promise.resolve(helloInfo)
		if (!helloJob) {
			helloJob = request("hello", {}, timeouts.hello)
				.then((r) => {
					if (!r?.ok) throw new Error(r?.message || "hello failed")
					helloInfo = r
					return r
				})
				.finally(() => {
					helloJob = null
				})
		}
		return helloJob
	}

	async function available() {
		if (!getBridge()) return false
		try {
			await hello()
			return true
		} catch (err) {
			log.warn("POSNext app did not answer hello:", err?.message)
			return false
		}
	}

	async function status() {
		const r = await request("status", {}, timeouts.status)
		const code = Number(r?.code ?? -1)
		return { ok: Boolean(r?.ok) && code === 0, code, message: r?.message }
	}

	return {
		id: "imin2",

		isAvailable: available,

		async getStatus() {
			try {
				if (!(await available())) {
					return {
						ok: false,
						code: -1,
						message: __("POSNext app (iMin SDK 2.0) not detected"),
					}
				}
				const s = await status()
				if (!s.ok && s.code !== 0) s.message = faultMessage(s.code)
				return s
			} catch (err) {
				return { ok: false, code: -1, message: err?.message || String(err) }
			}
		},

		/**
		 * Errors before the app took the job reject plainly (the transport may
		 * fall back); once paper may be out they carry postPrint (COR-FE-10).
		 * @returns {Promise<{paper:string, dots:number, copies:number}>}
		 */
		async printHTML(html, opts = {}) {
			const { bytes, r, textMode, sheets } = await buildEscPosPrint(
				html,
				opts,
				encodeDeps,
			)
			const s = await status()
			if (s.code !== 0) throw new Error(faultMessage(s.code))

			log.info("native job", {
				printMode: textMode ? "text" : "bitmap",
				bytes: bytes.length,
				sheets,
			})
			let reply
			try {
				reply = await request(
					"printRaw",
					{ data: bytesToBase64(bytes) },
					timeouts.print,
				)
			} catch (err) {
				// No answer after the bytes left: the head may be printing.
				// Retrying on another driver would risk a duplicate receipt.
				if (err?.timedOut) err.postPrint = true
				throw err
			}
			if (!reply?.ok) {
				const code = Number(reply?.code)
				const err = new Error(
					FAULT_MESSAGES[code]
						? faultMessage(code)
						: reply?.message || __("Print failed"),
				)
				if (reply?.printed) err.postPrint = true
				throw err
			}
			return { paper: r.paper, dots: r.dots, copies: sheets }
		},

		describe() {
			const h = helloInfo
			return {
				id: "imin2",
				label: __("iMin SDK 2.0 (POSNext app)"),
				detail: h
					? `${__("App")} ${h.version || "?"} · SDK ${h.sdk || "?"}`
					: "",
			}
		},

		/** Last hello reply ({version, sdk, model, android}) or null. */
		info() {
			return helloInfo
		},
	}
}
