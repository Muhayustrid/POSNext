import { logger } from "@/utils/logger"
import { __ } from "@/utils/translation"
import { getTransport } from "./print/transport"
import { silentPrintDoc } from "./printInvoice"

const log = logger.create("PrintEod")

const EOD_PRINT_FORMAT = "POS Next EOD Report"

/**
 * Print the EOD (POS Closing Shift) report.
 *
 * Primary path is the silent/direct print driver via silentPrintDoc(). When
 * that throws there is NO automatic fallback: the failure is marked on the
 * returned object so the caller can show the retry panel (Print Ulang /
 * Export PDF / Tutup). The PDF itself is produced on demand by
 * downloadEodPDF(), server-rendered from the same print format.
 *
 * @param {string} closingShiftName Name of the POS Closing Shift document
 * @param {string|null} posProfile When available, lets the iMin lane re-resolve
 *   paper/copies per-POS Profile instead of falling back to browser defaults.
 *   The caller (ShiftClosingDialog) has closingData.pos_profile available.
 * @returns {Promise<{method: "silent", success: boolean, printed: boolean}>}
 *   printed === false means the sheet did NOT come out and the caller must
 *   offer the retry panel.
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
		return { method: "silent", success: true, printed: true }
	} catch (error) {
		// The failure must not be swallowed: nothing else logs this (POS Print
		// Log rows are only written inside the transport).
		log.warn("Silent print failed:", error?.message || error)
		return { method: "silent", success: false, printed: false, error }
	}
}

/**
 * Download the EOD report as a PDF — the approved fallback when the printer
 * refuses. Server-rendered (wkhtmltopdf) from the same EOD print format.
 */
export async function downloadEodPDF(closingShiftName) {
	const response = await fetch(
		"/api/method/frappe.utils.print_format.download_pdf",
		{
			method: "POST",
			headers: {
				"Content-Type": "application/x-www-form-urlencoded",
				"X-Frappe-CSRF-Token": window.csrf_token || "",
			},
			body: new URLSearchParams({
				doctype: "POS Closing Shift",
				name: closingShiftName,
				format: EOD_PRINT_FORMAT,
				no_letterhead: 1,
				_lang: "en",
			}),
		},
	)
	if (!response.ok) throw new Error(__("Could not create the EOD report PDF"))
	const url = URL.createObjectURL(await response.blob())
	const link = document.createElement("a")
	link.href = url
	link.download = `EOD ${closingShiftName}.pdf`
	document.body.appendChild(link)
	link.click()
	link.remove()
	URL.revokeObjectURL(url)
}
