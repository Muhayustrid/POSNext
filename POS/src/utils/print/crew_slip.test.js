import { describe, expect, it } from "vitest"

// Same trivial translation helper printInvoice.test.js installs. crew_slip
// only runs labels through __(), never content it does not control.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import { buildCrewSlipHTML } from "./crew_slip"

// Prices are deliberately awkward strings so "does not contain prices" can
// assert on the exact formatted values the customer copy would print.
const doc = {
	doctype: "Sales Invoice",
	name: "SINV-0007",
	posting_date: "2026-09-03",
	posting_time: "21:04:07.123456",
	buyer_name: "  Kafe Satu  ",
	customer_name: "Walk-in Customer",
	customer: "Walk-in Customer",
	grand_total: 12345.67,
	items: [
		{
			item_code: "SKU-1",
			item_name: "Kopi Susu",
			qty: 2,
			quantity: 2,
			rate: 12345.67,
			price_list_rate: 12345.67,
		},
		{ item_code: "SKU-2", item_name: "Croissant", quantity: 1, rate: 18000 },
	],
	payments: [{ mode_of_payment: "Cash", amount: 12345.67 }],
}

/** The rendered body only — the embedded stylesheet legitimately talks about
 * layout, and the money/border assertions are about what is PRINTED. */
function bodyOf(html) {
	return html.split("<body>")[1]
}

describe("buildCrewSlipHTML", () => {
	it("carries the invoice id and the posting date + time", () => {
		const html = buildCrewSlipHTML(doc, { dots: 384 })
		expect(html).toContain("SINV-0007")
		expect(html).toContain("2026-09-03")
		expect(html).toContain("21:04:07")
	})

	it("shows the time in ONE Date row, exactly as the server template renders it", () => {
		const body = bodyOf(buildCrewSlipHTML(doc, {}))
		// Server: {{ doc.posting_date }} {{ (doc.posting_time|string).split('.')[0] }}
		// — the customer copy and the slip must not disagree at the counter.
		expect(body).toContain("2026-09-03 21:04:07")
		// The fractional-second part never survives, and the date is not
		// repeated on a second row.
		expect(body).not.toContain(".123456")
		expect(body.match(/2026-09-03/g)).toHaveLength(1)
	})

	it("keeps a whole-second time and drops only the fractional part", () => {
		const body = bodyOf(
			buildCrewSlipHTML({ ...doc, posting_time: "07:05:09" }, {}),
		)
		expect(body).toContain("2026-09-03 07:05:09")
	})

	it("prints the date alone when posting_time is absent — never 'undefined'/'Invalid'", () => {
		for (const posting_time of [undefined, null, "", "Invalid Date"]) {
			const body = bodyOf(buildCrewSlipHTML({ ...doc, posting_time }, {}))
			expect(body).toContain("2026-09-03")
			expect(body).not.toContain("undefined")
			expect(body).not.toContain("Invalid")
		}
	})

	it("prints no banner at all — neither copy is labelled on the paper", () => {
		const html = buildCrewSlipHTML(doc, { dots: 384 })
		expect(html).not.toContain("CREW COPY")
		expect(html).not.toContain("CUSTOMER COPY")
		expect(html).not.toContain("pn-copy-label")
	})

	it("uses the buyer, then customer_name, then customer — like buildReceiptHTML", () => {
		expect(buildCrewSlipHTML(doc, {})).toContain("Kafe Satu")
		expect(buildCrewSlipHTML(doc, {})).not.toContain("Walk-in Customer")

		const noBuyer = { ...doc, buyer_name: null }
		expect(buildCrewSlipHTML(noBuyer, {})).toContain("Walk-in Customer")

		const noCustomerName = { ...noBuyer, customer_name: undefined }
		expect(buildCrewSlipHTML(noCustomerName, {})).toContain("Walk-in Customer")
	})

	it("renders item lines like the customer receipt's, minus the amount", () => {
		const body = bodyOf(buildCrewSlipHTML(doc, {}))
		expect(body).not.toContain(">ITEM<")
		expect(body).not.toContain(">QTY<")
		expect(body).toContain('<div class="slip-line">2x Kopi Susu</div>')
		expect(body).toContain('<div class="slip-line">1x Croissant</div>')

		const codeOnly = { ...doc, items: [{ item_code: "SKU-9", qty: 3 }] }
		expect(bodyOf(buildCrewSlipHTML(codeOnly, {}))).toContain(
			'<div class="slip-line">3x SKU-9</div>',
		)
	})

	it("prints a Cashier row when the doc carries a cashier", () => {
		const withCashier = { ...doc, cashier_name: "Yusuf Daryanto" }
		expect(bodyOf(buildCrewSlipHTML(withCashier, {}))).toContain("Cashier")
		expect(bodyOf(buildCrewSlipHTML(withCashier, {}))).toContain(
			"Yusuf Daryanto",
		)

		// Absent cashier -> the row is simply not there (never an empty label).
		const body = bodyOf(buildCrewSlipHTML(doc, {}))
		expect(body).not.toContain("Cashier")
	})

	it("rows ride one line with their values — label column, aligned colon", () => {
		const longInvoice = {
			...doc,
			name: "ACC-SINV-2026-00027",
		}
		const body = bodyOf(buildCrewSlipHTML(longInvoice, {}))
		expect(body).toContain("Invoice")
		expect(body).toContain("Date")
		expect(body).toContain("Customer")
		expect(body).toContain("slip-row")
		// The colon is drawn by the stylesheet at the value cell's edge, so the
		// label markup never pads with spaces and a long value just wraps.
		expect(body).toContain("ACC-SINV-2026-00027")
	})

	it("carries no money at all — no prices, totals or payments", () => {
		const body = bodyOf(buildCrewSlipHTML(doc, {}))
		// The formatted values the customer copy prints for these amounts.
		expect(body).not.toContain("12345")
		expect(body).not.toContain("12,345")
		expect(body).not.toContain("18000")
		expect(body).not.toContain("Cash")
		expect(body).not.toMatch(/total/i)
		expect(body).not.toMatch(/payment/i)
		expect(body).not.toMatch(/x\s*\d+\.\d{2}/)
	})

	it("mirrors the customer receipt's header typography; items are bold and a size up", () => {
		const html = buildCrewSlipHTML(doc, { dots: 384 })
		// Self-contained stylesheet, not the receipt sheet.
		expect(html).toContain("<style>")
		expect(html).not.toContain("receiptStylesFor")
		// Same family as the POS Next Receipt format; dashed rules.
		expect(html).toMatch(/border-top:\s*1px dashed/)
		expect(html).toContain("'DejaVu Sans', 'Arial', sans-serif")
		expect(html).not.toContain("monospace")
		// Header rows: 11px normal, 54px label column (the receipt's width).
		const rowRule = html.match(/\.slip-row\s*\{[^}]*\}/)
		expect(rowRule[0]).toContain("font-size: 11px")
		expect(html.match(/\.slip-label\s*\{[^}]*\}/)[0]).toContain(
			"min-width: 54px",
		)
		// Item lines: the ONLY bold thing, 16px.
		const lineRule = html.match(/\.slip-line\s*\{[^}]*\}/)
		expect(lineRule[0]).toContain("font-size: 14px")
		expect(lineRule[0]).toContain("font-weight: bold")
		expect(rowRule[0]).not.toContain("bold")
		// Only 11px and 16px exist on the slip.
		expect(html).not.toMatch(/font-size:\s*(?!11px|14px)\d+px/)
	})

	it("orders header rows like the customer receipt: Invoice, Cashier, Date, Customer", () => {
		const body = bodyOf(
			buildCrewSlipHTML({ ...doc, cashier_name: "Yusuf Daryanto" }, {}),
		)
		const order = [
			body.indexOf("Invoice"),
			body.indexOf("Cashier"),
			body.indexOf("Date"),
			body.indexOf("Customer"),
		]
		expect(order.every((i) => i >= 0)).toBe(true)
		expect([...order].sort((a, b) => a - b)).toEqual(order)
	})

	it("exposes its line spacing to the shared line-spacing pass", () => {
		// The body carries an explicit unitless line-height so the renderer's
		// lineSpacing knob controls the slip exactly like the receipt; without
		// it the slip would inherit the frame's baseline and ignore the knob.
		expect(buildCrewSlipHTML(doc, { dots: 384 })).toMatch(
			/line-height:\s*1\.3;/,
		)
	})

	it("survives an empty item list and a missing buyer", () => {
		const html = buildCrewSlipHTML(
			{ name: "SINV-9", customer: "Walk-in", items: [] },
			{},
		)
		expect(html).toContain("SINV-9")
		expect(html).not.toContain("x2")
		expect(html).not.toContain("×")
	})

	it("does not throw on a bare dict", () => {
		expect(() => buildCrewSlipHTML({}, {})).not.toThrow()
	})

	it("keeps the dots-aware width contract the preview and driver rely on", () => {
		// Same contract buildReceiptDocumentHTML has: the embedded stylesheet is
		// sized for the paper the slip prints on.
		expect(buildCrewSlipHTML(doc, { dots: 384 })).toContain("48mm")
		expect(buildCrewSlipHTML(doc, {})).toContain("72mm")
	})
})
