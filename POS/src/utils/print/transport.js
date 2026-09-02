/**
 * Print transport router.
 *
 * Chooses a driver from config, walks the fallback chain
 * (imin -> qz -> browser, or just the configured driver when fallback is
 * disabled), and logs one POS Print Log attempt per print. `createTransport`
 * takes injectable drivers/config/logSink so unit tests need no network or
 * sockets; the production singleton at the bottom wires the real drivers.
 */
import { call } from "@/utils/apiWrapper"
import { logger } from "@/utils/logger"

import { createBrowserDriver } from "./browser_client"
import { createIminDriver } from "./imin_client"
import { createQzDriver } from "./qz_client"

const log = logger.create("PrintTransport")

const FALLBACK_ORDER = {
	imin: ["imin", "qz", "browser"],
	qz: ["qz", "browser"],
	browser: ["browser"],
}

/**
 * Build a transport. `drivers` and `config` are injectable so unit tests need
 * no network or sockets. The production singleton below wires the real ones.
 */
export function createTransport({ drivers, config = {}, logSink } = {}) {
	let current = { ...config }

	async function logAttempt(attempt) {
		if (logSink) {
			logSink.attempts.push(attempt)
			return
		}
		try {
			await call("pos_next.api.printing.log_print_attempt", attempt)
		} catch (err) {
			log.warn("print log failed:", err?.message)
		}
	}

	function chain() {
		if (current.fallback_enabled === false) return [current.driver]
		return FALLBACK_ORDER[current.driver] || [current.driver]
	}

	async function printHTML(html, opts = {}) {
		const started = Date.now()
		const errors = []
		for (const [idx, id] of chain().entries()) {
			const driver = drivers[id]
			if (!driver) continue
			let ok = true
			let effectivePaper = current.paper
			try {
				if (!(await driver.isAvailable())) {
					errors.push(`skipped ${id} (unavailable)`)
					continue
				}
				const result = await driver.printHTML(html, {
					...opts,
					config: {
						paper: current.paper,
						customDots: current.custom_dots,
						cut: current.cut,
					},
				})
				if (result && typeof result.paper === "string") {
					effectivePaper = result.paper
				}
			} catch (err) {
				ok = false
				errors.push(`${id}: ${err?.message || err}`)
			}
			if (ok) {
				await logAttempt({
					...opts.logContext,
					driver: id,
					status: idx === 0 ? "Success" : "Fallback",
					// Keep the reason the earlier drivers failed on the row.
					// This log exists to answer "why didn't it come out of the
					// iMin" — dropping error_message on the fallback path hides
					// exactly that, which is the case operators ask about most.
					error_message: errors.length ? errors.join(" | ") : undefined,
					paper_width: effectivePaper,
					duration_ms: Date.now() - started,
				})
				return { driver: id }
			}
		}
		await logAttempt({
			...opts.logContext,
			driver: current.driver,
			status: "Failed",
			error_message: errors.join(" | "),
			paper_width: current.paper,
			duration_ms: Date.now() - started,
		})
		throw new Error(errors.join(" | ") || "No print driver available")
	}

	return {
		printHTML,
		setConfig(next) {
			current = { ...current, ...next }
		},
		getConfig() {
			return { ...current }
		},
		getDriver(id) {
			return drivers[id || current.driver]
		},
	}
}

function loadIminFactory() {
	// window.IminPrinter is defined by the vendored SDK, injected once by
	// ensureIminSdk() below on first iMin use.
	return typeof window !== "undefined" && window.IminPrinter
		? () => new window.IminPrinter()
		: undefined
}

let _sdkPromise = null
function ensureIminSdk() {
	if (_sdkPromise) return _sdkPromise
	_sdkPromise = new Promise((resolve) => {
		if (loadIminFactory()) return resolve(true)
		const s = document.createElement("script")
		s.src = "/assets/pos_next/js/lib/imin/1.4.0/imin-printer.js"
		s.onload = () => resolve(true)
		s.onerror = () => resolve(false)
		document.head.appendChild(s)
	})
	return _sdkPromise
}

let _singleton = null
export function getTransport() {
	if (_singleton) return _singleton
	_singleton = createTransport({
		drivers: {
			imin: createIminDriver({ factory: () => new window.IminPrinter() }),
			qz: createQzDriver(),
			browser: createBrowserDriver(),
		},
	})
	return _singleton
}

export async function printHTML(html, opts) {
	await ensureIminSdk()
	return getTransport().printHTML(html, opts)
}

export async function initTransportFromServer(posProfile) {
	const cfg = await call("pos_next.api.printing.get_print_config", {
		pos_profile: posProfile,
	})
	getTransport().setConfig({
		driver: cfg.driver,
		paper: cfg.paper,
		custom_dots: cfg.custom_dots,
		cut: cfg.cut,
		fallback_enabled: cfg.fallback_enabled,
	})
	return cfg
}
