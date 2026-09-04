/**
 * Sample closing shift for the Direct Print page (EOD test print + preview).
 *
 * Mirrors sample_receipt.js: the sample only proves something if it exercises
 * the REAL output, so it renders the latest POS Closing Shift through the same
 * server template ("POS Next EOD Report") an end-of-day print uses. One call to
 * find the shift, then the same fetch, wrapper and error contract as a real
 * silent print.
 *
 * This page is a diagnostic tool that must never nag, and it runs on tills
 * that may be offline or brand new (nothing closed yet). So every failure path
 * lands on the same empty bundle — logged as a warning, never a toast.
 *
 * Unlike the receipt sample this module caches: Test Print and the preview
 * share one bundle, and refresh re-fetches because the print format can change
 * out from under a mounted page. Only a hit is cached — the module outlives a
 * page mount, and a cached miss would hide a shift closed afterwards.
 */
import { call } from "@/utils/apiWrapper"
import { logger } from "@/utils/logger"
import { fetchServerPrintHTML } from "@/utils/printInvoice"

const log = logger.create("SampleClosing")

export const EOD_PRINT_FORMAT = "POS Next EOD Report"

/** @type {{source:'server', name:string, serverHTML:string}|null} */
let cached = null

/**
 * @param {{refresh?:boolean}} [options]
 * @returns {Promise<{source:'server'|'none', name:string|null,
 *   serverHTML:string|null}>} serverHTML is a full document (null when none).
 */
export async function fetchSampleClosingBundle({ refresh = false } = {}) {
	if (cached && !refresh) return cached

	const empty = { source: "none", name: null, serverHTML: null }
	try {
		const name = await call("pos_next.api.printing.get_latest_closing_shift")
		if (!name) throw new Error("no closing shift yet")

		const serverHTML = await fetchServerPrintHTML(
			"POS Closing Shift",
			name,
			EOD_PRINT_FORMAT,
		)
		cached = { source: "server", name, serverHTML }
		return cached
	} catch (err) {
		cached = null
		log.warn("Sample EOD report: nothing to print —", err?.message || err)
		return empty
	}
}
