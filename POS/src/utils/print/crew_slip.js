/**
 * Compact crew slip — the second copy when a profile prints two.
 *
 * The customer copy carries prices, totals and payments; the outlet crew only
 * needs to know WHAT was ordered and by whom. The slip deliberately mirrors
 * the customer receipt's typography (same DejaVu/Arial family, same 11px
 * header rows with the same label column) so the pair looks like one
 * stationery; the item lines alone are BOLD and a size up — they are what
 * the crew actually reads:
 *
 *             ORDER                <- title: literal, bold, centred
 *   --------------------------------
 *   Invoice : {invoice_no}
 *   Cashier : {cashier_name}
 *   Date    : {date} {time}
 *   Customer: {customer_name}
 *   --------------------------------
 *   1x {item_name}            <- bold, a size up from the header rows
 *   2x {other_item}              (no closing rule — the slip just ends)
 *
 * Item lines read exactly like the customer receipt's, minus the amount.
 * Nothing money-shaped is rendered at all, which is also why the slip is
 * safe to hand across the counter, and no banner is printed above either
 * copy. The crew font-scale knob (imin_crew_font_scale, default 100 = the
 * same size as the receipt) can still embiggen the whole slip per till.
 *
 * The stylesheet is self-contained; it still goes through the same bitmap
 * pipeline as any other receipt: the <style> block is extracted, scoped to
 * the frame and DPI-translated. No renderer special case.
 *
 * Pure string building — no DOM, no transport, unit-testable.
 */

import { DOTS_PER_MM } from "./paper"

/**
 * Slip typography. `dots` is the paper width in printer dots, exactly as
 * receiptStylesFor() took it — the body width is derived from it so the slip
 * is sized for the paper the caller (driver or Direct Print preview) resolved.
 */
function crewSlipStyles(dots) {
	const mm = dots / DOTS_PER_MM
	return `
	* { margin: 0; padding: 0; box-sizing: border-box; }
	body {
		font-family: 'DejaVu Sans', 'Arial', sans-serif;
		padding: 4px; width: ${mm}mm; margin: 0; max-width: ${mm}mm;
		font-weight: normal; color: black; line-height: 1.3;
	}
	/* Rules sit at the SAME distance from the rows they frame: rule margins
	 * are symmetric and every row carries 1px top AND bottom, so the info
	 * group's top and bottom rules cannot drift apart. */
	.slip-rule { border-top: 1px dashed #000; margin: 14px 0 4px; }
	.receipt { padding-bottom: 10px; }
	.slip-title { text-align: center; font-weight: bold; font-size: 11px; margin: 2px 0; }
	/* Header rows: label column, colon starts the value cell — the same
	 * alignment (and 54px column) the customer receipt's info block uses. */
	.slip-row { display: flex; margin: 1px 0; font-size: 11px; }
	.slip-label { min-width: 54px; }
	.slip-value { font-size: 11px; }
	.slip-value::before { content: ": "; }
	/* Item lines: the only bold thing on the slip, a size up from the
	 * header rows — the crew scans these, the header is bookkeeping. */
	.slip-line { font-size: 14px; font-weight: bold; margin: 4px 0; }
	@media print {
		@page { size: ${mm}mm auto; margin: 0; }
		body { width: ${mm}mm; padding: 2mm; margin: 0; }
	}
`
}

/**
 * The Date+Time value exactly as the server receipt template renders it:
 * `{{ doc.posting_date }} {{ (doc.posting_time|string).split('.')[0] ... }}` —
 * the fractional second dropped, so the two copies never disagree at the
 * counter. No time -> the date alone; a value that would print as nonsense
 * ("undefined", "Invalid Date") is dropped rather than printed.
 */
function crewTimestamp(doc) {
	const date = doc.posting_date == null ? "" : String(doc.posting_date).trim()
	const time = String(doc.posting_time ?? "")
		.split(".")[0]
		.trim()
	const safeTime = /\d/.test(time) ? time : ""
	if (date && safeTime) return `${date} ${safeTime}`
	return date || safeTime
}

/** One header row: label in the fixed column, value after the aligned colon. */
function rowHTML(label, value) {
	return `<div class="slip-row"><span class="slip-label">${label}</span><span class="slip-value">${value}</span></div>`
}

export function buildCrewSlipHTML(invoiceData, { dots } = {}) {
	const doc = invoiceData || {}
	const items = Array.isArray(doc.items) ? doc.items : []

	// Same party fallback the customer copy uses, so the two copies never
	// disagree about who the sale was for.
	const buyerName = (doc.buyer_name || "").trim()
	const partyValue = buyerName || doc.customer_name || doc.customer || ""
	const cashierName = (doc.cashier_name || doc.cashier || "").trim()

	// Header rows in the same order the customer receipt prints them;
	// each guards itself away when its data is missing so the rules around
	// the section stay single.
	const rows = []
	if (doc.name) rows.push(rowHTML(__("Invoice"), doc.name))
	if (cashierName) rows.push(rowHTML(__("Cashier"), cashierName))
	if (crewTimestamp(doc)) rows.push(rowHTML(__("Date"), crewTimestamp(doc)))
	if (partyValue) rows.push(rowHTML(__("Customer"), partyValue))

	const itemLines = items
		.map((item) => {
			const label = item.item_name || item.item_code
			if (!label) return ""
			// `??` keeps a deliberate 0 (a zero-qty line) from being replaced by
			// the other field's value.
			const qty = item.qty ?? item.quantity
			return `<div class="slip-line">${qty == null ? "" : `${qty}x `}${label}</div>`
		})
		.join("")

	// Title (literal ORDER, like the receipt's company line) → rule → header
	// rows → rule → item lines (no closing rule: the slip ends where the last
	// item ends), with every piece optional and no two rules ever adjacent.
	const sections = [rows.join(""), itemLines].filter(Boolean)
	const title = `<div class="slip-title">${__("ORDER")}</div>`
	const body =
		title +
		sections
			.map((section) => `<div class="slip-rule"></div>${section}`)
			.join("")

	return `
		<!DOCTYPE html>
		<html>
		<head>
			<meta charset="UTF-8">
			<title>${__("Crew copy - {0}", [doc.name || ""])}</title>
			<style>${crewSlipStyles(dots ?? 576)}</style>
		</head>
		<body>
			<div class="receipt">${body}</div>
		</body>
		</html>`
}
