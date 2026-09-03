/**
 * Sample receipt for the Direct Print page (Test Print + preview).
 *
 * A test print only proves something if it exercises the REAL output, so the
 * page prints the last invoice of the active POS profile through the same
 * server template ("POS Next Receipt") a checkout uses. Three calls: the last
 * invoice name, its full doc (the crew slip needs the items) and the rendered
 * print format.
 *
 * This page is a diagnostic tool that must never nag, and it runs on tills
 * that may be offline or brand new (no invoices yet). So every failure path
 * below lands on the same fallback — the local test receipt — logged as a
 * warning and surfaced as a one-line note, never as a toast or error card.
 *
 * Caching is the caller's business (one bundle per page mount, shared by Test
 * Print and the preview) so the module stays stateless.
 */
import { call } from "@/utils/apiWrapper"
import { logger } from "@/utils/logger"
import { fetchServerPrintHTML } from "@/utils/printInvoice"

const log = logger.create("SampleReceipt")

const SAMPLE_PRINT_FORMAT = "POS Next Receipt"

/**
 * @param {string|null} posProfile - active POS profile name, if known.
 * @param {object} fallbackDoc - invoice dict for the local test receipt, used
 *   as the bundle's doc whenever the server path is unavailable.
 * @returns {Promise<{source:'server'|'fallback', serverHTML:string|null,
 *   invoiceDoc:object}>} serverHTML is a full document (null on fallback).
 */
export async function fetchSampleReceiptBundle(posProfile, fallbackDoc) {
	const fallback = (reason) => {
		log.warn("Sample receipt: using the built-in test receipt —", reason)
		return { source: "fallback", serverHTML: null, invoiceDoc: fallbackDoc }
	}

	try {
		if (!posProfile) throw new Error("no POS profile in scope")

		// Same list call the sale page uses; limit 1 because only the most
		// recent receipt is interesting as a sample.
		const rows = await call("pos_next.api.invoices.get_invoices", {
			pos_profile: posProfile,
			limit: 1,
		})
		const last = Array.isArray(rows) ? rows[0] : null
		if (!last?.name) throw new Error("no invoices for this profile yet")

		const invoiceDoc = await call("pos_next.api.invoices.get_invoice", {
			invoice_name: last.name,
		})
		if (!invoiceDoc) throw new Error(`could not read ${last.name}`)

		// Same fetch, wrapper and error contract as a real silent print.
		const serverHTML = await fetchServerPrintHTML(
			"Sales Invoice",
			last.name,
			SAMPLE_PRINT_FORMAT,
		)
		return { source: "server", serverHTML, invoiceDoc }
	} catch (err) {
		return fallback(err?.message || err)
	}
}
