import { logger } from "@/utils/logger"
import { getTransport } from "./print/transport"
import { silentPrintDoc } from "./printInvoice"

const log = logger.create("PrintEod")

const EOD_PRINT_FORMAT = "POS Next EOD Report"

/**
 * Print the EOD (POS Closing Shift) report.
 *
 * Primary path is the silent/direct print driver via silentPrintDoc(). When
 * that throws, fall back to Frappe's /printview browser window — unless the
 * transport is in strict driver mode (fallback_enabled === false), which is a
 * deliberate operator choice, so the original error is rethrown instead.
 *
 * @param {string} closingShiftName Name of the POS Closing Shift document
 * @param {string|null} posProfile When available, lets the iMin lane re-resolve
 *   paper/copies per-POS Profile instead of falling back to browser defaults.
 *   The caller (ShiftClosingDialog) has closingData.pos_profile available.
 * @returns {Promise<{method: "silent"|"printview", success: boolean}>}
 *   Which path actually produced the print.
 */
export async function printEODReport(closingShiftName, posProfile) {
	try {
		await silentPrintDoc(
			"POS Closing Shift",
			closingShiftName,
			EOD_PRINT_FORMAT,
			posProfile,
			// Its own lane: the EOD report carries its own layout knobs and never
			// a crew slip.
			"eod",
		)
		return { method: "silent", success: true }
	} catch (error) {
		const cfg = getTransport().getConfig() || {}
		if (cfg.fallback_enabled === false) throw error

		// The fallback must not swallow why the driver failed: nothing else
		// logs this (POS Print Log rows are only written inside the transport),
		// so keep a trace before switching lanes.
		log.warn(
			"Silent print failed, falling back to /printview:",
			error?.message || error,
		)

		const params = new URLSearchParams({
			doctype: "POS Closing Shift",
			name: closingShiftName,
			format: EOD_PRINT_FORMAT,
			no_letterhead: 1,
			_lang: "en",
			trigger_print: 1,
			_t: Date.now(),
		})
		const printWindow = window.open(
			`/printview?${params}`,
			"_blank",
			"width=800,height=600",
		)
		if (!printWindow) {
			throw new Error("Popup blocked — check your browser settings.")
		}
		return { method: "printview", success: true }
	}
}
