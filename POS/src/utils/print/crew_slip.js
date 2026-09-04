/**
 * Compact crew slip — the second copy when a profile prints two.
 *
 * The customer copy carries prices, totals and payments; the outlet crew only
 * needs to know WHAT was ordered. The operator's final sketch:
 *
 *   --------------------------------
 *   INVOICE          {invoice_no}
 *   DATE             {date} {time}
 *   CUSTOMER         {customer_name}
 *   --------------------------------
 *   {item_name} x{qty}                  <- LARGE + bold, one line per item
 *   --------------------------------
 *
 * The header rows share the item lines' typography (label:value on one line,
 * wrapping under the label when a value is long) and the item lines are the
 * hero — chunky and bold. Nothing money-shaped is rendered at all, which
 * is also why the slip is safe to hand across the counter, and no banner is
 * printed above either copy.
 *
 * The stylesheet is self-contained rather than receiptStylesFor(): the base
 * here is NORMAL weight (bold is reserved for the item lines, per the sketch)
 * and the section rules are drawn as 1px dashed dividers. It still goes
 * through the same bitmap pipeline as any other receipt: the <style> block is
 * extracted, scoped to the frame and DPI-translated, and the crew font-scale
 * knob multiplies every px. No renderer special case.
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
		font-family: 'Courier New', monospace;
		padding: 4px; width: ${mm}mm; margin: 0; max-width: ${mm}mm;
		font-weight: normal; color: black;
		/* Explicit so the renderer's lineSpacing knob drives the slip exactly
		 * like the receipt; see receipt_layout.scopeReceiptCSS. */
		line-height: 1.2;
	}
	.slip-rule { border-top: 1px dashed #000; margin: 6px 0; }
	/* Header rows: label left, value right, the SAME typography as the item
	 * lines (10px rows printed as an unreadable smudge on the thermal paper).
	 * The value wraps UNDER its label when the pair does not fit — on a wrapped
	 * line a lone flex item starts at the left edge — instead of being clipped
	 * by the frame's overflow:hidden. */
	.slip-row { display: flex; justify-content: space-between; flex-wrap: wrap; column-gap: 8px; font-size: 16px; font-weight: bold; margin: 2px 0; }
	/* SIZE:LARGE + BOLD, name flush left / qty flush right. */
	.slip-line { display: flex; justify-content: space-between; font-size: 16px; font-weight: bold; margin: 5px 0; }
	.slip-line .name { flex: 1; padding-right: 8px; }
	.slip-line .qty { white-space: nowrap; }
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

export function buildCrewSlipHTML(invoiceData, { dots } = {}) {
	const doc = invoiceData || {}
	const items = Array.isArray(doc.items) ? doc.items : []

	// Same party fallback the customer copy uses, so the two copies never
	// disagree about who the sale was for.
	const buyerName = (doc.buyer_name || "").trim()
	const partyValue = buyerName || doc.customer_name || doc.customer || ""

	// Header rows in sketch order; each guards itself away when its data is
	// missing so the rules around the section stay single.
	const rows = []
	if (doc.name) rows.push(rowHTML(__("INVOICE"), doc.name))
	if (crewTimestamp(doc)) rows.push(rowHTML(__("DATE"), crewTimestamp(doc)))
	if (partyValue) rows.push(rowHTML(__("CUSTOMER"), partyValue))

	const itemLines = items
		.map((item) => {
			const label = item.item_name || item.item_code
			if (!label) return ""
			// `??` keeps a deliberate 0 (a zero-qty line) from being replaced by
			// the other field's value.
			const qty = item.qty ?? item.quantity
			return `<div class="slip-line"><span class="name">${label}</span>${
				qty == null ? "" : `<span class="qty">x${qty}</span>`
			}</div>`
		})
		.join("")

	// rule → header rows → rule → item lines → rule, with every piece optional
	// and no two rules ever adjacent.
	const sections = [rows.join(""), itemLines].filter(Boolean)
	const body =
		sections
			.map((section) => `<div class="slip-rule"></div>${section}`)
			.join("") + (sections.length ? `<div class="slip-rule"></div>` : "")

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

/** One header row: label left, value right, same line. */
function rowHTML(label, value) {
	return `<div class="slip-row"><span>${label}</span><span>${value}</span></div>`
}
