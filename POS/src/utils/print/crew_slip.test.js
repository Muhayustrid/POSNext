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

	it("renders items as LARGE bold lines — name flush left, xqty flush right, no ITEM/QTY header", () => {
		const body = bodyOf(buildCrewSlipHTML(doc, {}))
		expect(body).not.toContain(">ITEM<")
		expect(body).not.toContain(">QTY<")
		expect(body).toContain('<span class="name">Kopi Susu</span>')
		expect(body).toContain('<span class="qty">x2</span>')
		expect(body).toContain('<span class="name">Croissant</span>')
		expect(body).toContain('<span class="qty">x1</span>')

		const codeOnly = { ...doc, items: [{ item_code: "SKU-9", qty: 3 }] }
		expect(bodyOf(buildCrewSlipHTML(codeOnly, {}))).toContain(
			'<span class="name">SKU-9</span>',
		)
		expect(bodyOf(buildCrewSlipHTML(codeOnly, {}))).toContain(
			'<span class="qty">x3</span>',
		)
	})

	it("rows share the item typography and wrap instead of clip", () => {
		const longInvoice = {
			...doc,
			name: "ACC-SINV-2026-00027",
		}
		const body = bodyOf(buildCrewSlipHTML(longInvoice, {}))
		// INVOICE / DATE / CUSTOMER ride the same line as their values.
		expect(body).toContain("INVOICE")
		expect(body).toContain("DATE")
		expect(body).toContain("CUSTOMER")
		expect(body).toContain("slip-row")
		// The stacked-label classes and the INVOICE→DATE gap are gone.
		expect(body).not.toContain("slip-label")
		expect(body).not.toContain("slip-gap")
		// A long invoice number is IN the markup so the stylesheet's wrap (see
		// the stylesheet test) can carry it onto a second line — the frame's
		// overflow:hidden must never be what "handles" it by clipping.
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

	it("ships its own stylesheet with dashed section rules and one shared row typography", () => {
		const html = buildCrewSlipHTML(doc, { dots: 384 })
		// Self-contained stylesheet, not the receipt sheet.
		expect(html).toContain("<style>")
		expect(html).not.toContain("receiptStylesFor")
		// The operator's sketch: dashed separators and LARGE (=16px) BOLD text.
		expect(html).toMatch(/border-top:\s*1px dashed/)
		expect(html).toContain("monospace")
		expect(html).toMatch(/font-size:\s*16px/)
		expect(html).toMatch(/font-weight:\s*bold/)
		expect(html).toMatch(/font-weight:\s*normal/)
		// 10px header rows read as a smudge on the thermal paper, so a header
		// row now shares the item line's typography — there is no small size
		// left anywhere on the slip.
		const rowRule = html.match(/\.slip-row\s*\{[^}]*\}/)
		expect(rowRule).toBeTruthy()
		expect(rowRule[0]).toContain("font-size: 16px")
		expect(rowRule[0]).toContain("font-weight: bold")
		expect(html).not.toMatch(/font-size:\s*10px/)
		// A long value wraps UNDER its label (a lone flex item on a wrapped line
		// starts at the line's left edge) instead of being clipped by the frame.
		expect(rowRule[0]).toMatch(/flex-wrap:\s*wrap/)
		expect(rowRule[0]).toMatch(/column-gap:\s*\d+px/)
	})

	it("exposes its line spacing to the shared line-spacing pass", () => {
		// The body carries an explicit unitless line-height so the renderer's
		// lineSpacing knob controls the slip exactly like the receipt; without
		// it the slip would inherit the frame's baseline and ignore the knob.
		expect(buildCrewSlipHTML(doc, { dots: 384 })).toMatch(
			/line-height:\s*1\.2;/,
		)
	})

	it("survives an empty item list and a missing buyer", () => {
		const html = buildCrewSlipHTML(
			{ name: "SINV-9", customer: "Walk-in", items: [] },
			{},
		)
		expect(html).toContain("SINV-9")
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
