/**
 * @vitest-environment jsdom
 */
import { describe, expect, it } from "vitest"

import {
	binarize,
	composeReceiptFrame,
	normalizeWidthPlan,
} from "./receipt_renderer"
import { DPI_SCALE } from "./receipt_layout"

describe("normalizeWidthPlan", () => {
	it("keeps an exact-width source untouched", () => {
		const plan = normalizeWidthPlan(384, 384)
		expect(plan).toEqual({ action: "none", targetWidth: 384, offsetX: 0 })
	})

	it("pads a narrower source centred", () => {
		const plan = normalizeWidthPlan(300, 384)
		expect(plan.action).toBe("pad")
		expect(plan.targetWidth).toBe(384)
		expect(plan.offsetX).toBe(Math.floor((384 - 300) / 2))
	})

	it("trims a wider source centred", () => {
		const plan = normalizeWidthPlan(576, 384)
		expect(plan.action).toBe("trim")
		expect(plan.targetWidth).toBe(384)
		expect(plan.offsetX).toBe(Math.floor((576 - 384) / 2))
	})
})

describe("binarize", () => {
	it("maps light pixels to white and dark to black", () => {
		const data = new Uint8ClampedArray([
			255,
			255,
			255,
			255, // white stays white
			10,
			10,
			10,
			255, // dark stays black
			127,
			127,
			127,
			255, // just below threshold -> black
			128,
			128,
			128,
			255, // at threshold -> white
		])
		binarize({ data, width: 4, height: 1 }, 128)
		expect(Array.from(data.slice(0, 3))).toEqual([255, 255, 255])
		expect(Array.from(data.slice(4, 7))).toEqual([0, 0, 0])
		expect(Array.from(data.slice(8, 11))).toEqual([0, 0, 0])
		expect(Array.from(data.slice(12, 15))).toEqual([255, 255, 255])
	})

	it("respects a custom threshold", () => {
		const data = new Uint8ClampedArray([200, 200, 200, 255])
		binarize({ data, width: 1, height: 1 }, 220)
		expect(Array.from(data.slice(0, 3))).toEqual([0, 0, 0])
	})
})

describe("composeReceiptFrame (DPI translation + scoping)", () => {
	const doc = (css) =>
		`<!DOCTYPE html><html><head><style>${css}</style></head><body><div class="receipt" style="font-size:12px">R</div></body></html>`

	it("extracts the receipt's <style>, scopes it, and keeps it inside the frame", () => {
		const { frame } = composeReceiptFrame(
			doc("body{padding:4px}.receipt{width:100%}"),
			{
				paper: "58mm",
			},
		)
		const style = frame.querySelector("style")
		expect(style).toBeTruthy()
		const css = style.textContent
		// body is mapped onto the frame; a bare body{} rule would leak to the
		// live page (and matched nothing inside the frame anyway).
		expect(css).toContain(".pn-receipt-frame{")
		expect(css).not.toMatch(/(^|\s)body\s*\{/)
	})

	it("scales stylesheet lengths to 205 DPI dots", () => {
		const { frame } = composeReceiptFrame(doc(".receipt{font-size:10px}"), {
			paper: "58mm",
		})
		expect(frame.querySelector("style").textContent).toContain(
			`font-size:${Math.round(10 * DPI_SCALE * 100) / 100}px`,
		)
	})

	it("scales inline style attributes too, but never the tail spacer", () => {
		const { frame, tailDots } = composeReceiptFrame(
			'<div style="font-size:12px">x</div>',
			{ paper: "58mm", tailDots: 24 },
		)
		const inner = frame.querySelector("div:not(.pn-receipt-tail)")
		expect(inner.style.fontSize).toBe(
			`${Math.round(12 * DPI_SCALE * 100) / 100}px`,
		)
		const tail = frame.querySelector(".pn-receipt-tail")
		expect(tail.style.height).toBe("24px") // authored in dots already
		expect(tailDots).toBe(24)
	})

	it("applies the fontScale knob on top of the DPI ratio", () => {
		const { frame } = composeReceiptFrame(doc(".receipt{font-size:10px}"), {
			paper: "58mm",
			fontScale: 200,
		})
		expect(frame.querySelector("style").textContent).toContain(
			`font-size:${Math.round(20 * DPI_SCALE * 100) / 100}px`,
		)
	})

	it("locks the frame width to the paper dots", () => {
		const { frame, dots } = composeReceiptFrame("<div/>", { paper: "80mm" })
		expect(frame.style.width).toBe("576px")
		expect(dots).toBe(576)
	})

	describe("lineSpacing knob (vertical density)", () => {
		it("rewrites unitless line-height declarations in the scoped css", () => {
			const { scopedCss } = composeReceiptFrame(doc("body{line-height:1.4}"), {
				paper: "58mm",
				lineSpacing: 80,
			})
			expect(scopedCss).toContain("line-height:1.12")
		})

		it("scales px line-height values, then the DPI translation", () => {
			const { scopedCss } = composeReceiptFrame(doc("body{line-height:20px}"), {
				paper: "58mm",
				lineSpacing: 80,
			})
			expect(scopedCss).toContain(
				`line-height:${Math.round(16 * DPI_SCALE * 100) / 100}px`,
			)
		})

		it("tightens the frame's own baseline line-height as well", () => {
			const { scopedCss } = composeReceiptFrame("<div/>", {
				paper: "58mm",
				lineSpacing: 80,
			})
			expect(scopedCss).toContain("line-height:1.08")
		})

		it("leaves line-height untouched when the knob is absent (default 100)", () => {
			const { scopedCss } = composeReceiptFrame(doc("body{line-height:1.4}"), {
				paper: "58mm",
			})
			expect(scopedCss).toContain("line-height:1.4")
			expect(scopedCss).toContain("line-height:1.35")
		})

		it("also reaches inline style attributes, like the font knob does", () => {
			// The real "POS Next Receipt" format sets line-height: 1.5 inline on
			// its invoice-info block, so a knob that only rewrote stylesheets
			// would tighten everything EXCEPT the densest part of the receipt.
			const { frame } = composeReceiptFrame(
				'<div style="line-height: 1.5">info</div>',
				{ paper: "58mm", lineSpacing: 80 },
			)
			const el = frame.querySelector("div:not(.pn-receipt-tail)")
			expect(el.style.lineHeight).toBe("1.2")
		})
	})

	describe("sideMarginDots knob (left/right print margin)", () => {
		// The real "POS Next Receipt" format pads its body at 96 DPI and again
		// inside `@media print` — both unwrap onto the frame and steal width.
		const padded = (css) =>
			`<!DOCTYPE html><html><head><style>${css}</style></head><body><div class="receipt">R</div></body></html>`

		it("appends a LAST frame rule pinning left/right to the knob in dots", () => {
			const { scopedCss } = composeReceiptFrame(padded("body{padding:5mm}"), {
				paper: "58mm",
				sideMarginDots: 8,
			})
			// The authored 5mm survives as 5 x 8 = 40 dots — the knob does not
			// rewrite the template, it wins the cascade after it.
			expect(scopedCss).toContain("padding:40px")
			const rule = ".pn-receipt-frame{padding-left:8px;padding-right:8px;}"
			expect(scopedCss).toContain(rule)
			// Same specificity, so being appended last is the whole mechanism.
			expect(scopedCss.indexOf(rule)).toBe(scopedCss.lastIndexOf(rule))
			expect(scopedCss.trimEnd().endsWith(rule)).toBe(true)
		})

		it("beats a template @media print body padding too", () => {
			const { scopedCss } = composeReceiptFrame(
				padded("body{padding:5mm}@media print{body{padding:5mm}}"),
				{ paper: "58mm", sideMarginDots: 0 },
			)
			expect(
				scopedCss
					.trimEnd()
					.endsWith(".pn-receipt-frame{padding-left:0px;padding-right:0px;}"),
			).toBe(true)
		})

		it("defaults to 16 dots (2 mm) when the knob is absent", () => {
			const { scopedCss } = composeReceiptFrame(padded("body{padding:5mm}"), {
				paper: "58mm",
			})
			expect(
				scopedCss
					.trimEnd()
					.endsWith(".pn-receipt-frame{padding-left:16px;padding-right:16px;}"),
			).toBe(true)
		})

		it("only touches left/right — top/bottom stay as authored", () => {
			const { scopedCss } = composeReceiptFrame(
				padded("body{padding-top:5mm;padding-bottom:5mm}"),
				{ paper: "58mm", sideMarginDots: 8 },
			)
			expect(scopedCss).toContain("padding-top:40px")
			expect(scopedCss).toContain("padding-bottom:40px")
			// The override declares the two side paddings and nothing else, so it
			// cannot clobber a vertical value the format asked for.
			const rule = scopedCss.match(
				/\.pn-receipt-frame\{[^}]*padding-left[^}]*\}/,
			)
			expect(rule).toBeTruthy()
			expect(rule[0]).not.toContain("padding-top")
			expect(rule[0]).not.toContain("padding-bottom")
		})

		it("is physical dots — NOT scaled by the fontScale knob", () => {
			const { scopedCss } = composeReceiptFrame(padded("body{padding:5mm}"), {
				paper: "58mm",
				fontScale: 200,
				sideMarginDots: 16,
			})
			expect(
				scopedCss
					.trimEnd()
					.endsWith(".pn-receipt-frame{padding-left:16px;padding-right:16px;}"),
			).toBe(true)
		})

		it("applies with no stylesheet at all (the frame base padding loses)", () => {
			const { scopedCss } = composeReceiptFrame("<div/>", {
				paper: "58mm",
				sideMarginDots: 8,
			})
			// receiptBaseCSS still ships `padding:16px` as its own baseline; the
			// appended rule narrows just the sides on top of it.
			expect(scopedCss).toContain("padding:16px")
			expect(
				scopedCss
					.trimEnd()
					.endsWith(".pn-receipt-frame{padding-left:8px;padding-right:8px;}"),
			).toBe(true)
		})
	})
})

describe("composeReceiptFrame vs server print formats", () => {
	// Real print formats ship `body{width:80mm;max-width:80mm}` plus an
	// `@media print{body{width:80mm;padding:5mm}}` block. Scoped onto the
	// frame those rules are LIVE for the first time — the inline width/max-
	// width must still win so the paper width decides, not the format.
	const serverFormatHTML = `<!DOCTYPE html><html><head><style>
		@page { size: 80mm auto; margin: 0mm; }
		body { width: 80mm; max-width: 80mm; padding: 10px; }
		@media print { body { width: 80mm; max-width: 80mm; padding: 5mm; } }
		.row { display: table; width: 100%; }
	</style></head><body><div class="row" style="font-size:10px">X</div></body></html>`

	it("keeps the frame at the paper's dots despite 80mm body rules", () => {
		const { frame, dots } = composeReceiptFrame(serverFormatHTML, {
			paper: "58mm",
		})
		expect(dots).toBe(384)
		expect(frame.style.width).toBe("384px")
		expect(frame.style.maxWidth).toBe("384px")
		// The scoped rules exist but cannot beat the inline width lock.
		const css = frame.querySelector("style").textContent
		expect(css).toContain(".pn-receipt-frame{")
		// @page dropped, @media print unwrapped into the scoped rules
		// (5mm -> 40 dots of padding, spaces kept by the rewriter).
		expect(css).not.toContain("@page")
		expect(css).toMatch(
			/\.pn-receipt-frame\{\s*width:\s*640px;[^}]*padding:\s*40px/,
		)
		// ...but the stylesheet width cannot beat the inline lock above.
	})

	it("also holds when the format is narrower than the paper (80mm device)", () => {
		const { frame } = composeReceiptFrame(serverFormatHTML, { paper: "80mm" })
		expect(frame.style.width).toBe("576px")
		expect(frame.style.maxWidth).toBe("576px")
	})
})

describe("composeReceiptFrame vs the real POS Next Receipt format", () => {
	// Verbatim excerpt of pos_next/pos_next/print_format/pos_next_receipt —
	// px typography inside an 80mm body. This is the shape the Direct Print
	// sample feeds the bitmap path now that it renders the real server
	// template, so the font-scale knob has to land on THIS css.
	const posNextReceiptHTML = `<!DOCTYPE html><html><head><style>
		@page { size: 80mm auto; margin: 0mm; }
		body {
			font-family: 'DejaVu Sans', 'Arial', sans-serif;
			width: 80mm;
			max-width: 80mm;
			margin: 0 auto;
			padding: 10px;
			font-size: 11px;
			line-height: 1.4;
		}
		.item-name { font-weight: bold; margin-bottom: 3px; font-size: 11px; }
		@media print { body { width: 80mm; max-width: 80mm; padding: 5mm; } }
	</style></head><body><div class="item-name">Kopi Susu</div></body></html>`

	it("scales the format's px typography by the font-scale knob", () => {
		const { scopedCss } = composeReceiptFrame(posNextReceiptHTML, {
			paper: "58mm",
			fontScale: 60,
		})
		// 11px authored at 96 DPI -> 11 x (205/96) x 0.6 dots.
		const expected = Math.round(11 * DPI_SCALE * 0.6 * 100) / 100
		expect(scopedCss).toContain(`font-size: ${expected}px`)
	})

	it("keeps the 80mm body width physical — mm never follows the font knob", () => {
		for (const fontScale of [60, 100, 250]) {
			const { scopedCss } = composeReceiptFrame(posNextReceiptHTML, {
				paper: "80mm",
				fontScale,
			})
			// 80mm x 8 dots/mm, identical at every scale ...
			expect(scopedCss).toContain("width: 640px")
			expect(scopedCss).toContain("max-width: 640px")
			// ... otherwise the paper width would follow the text size.
			expect(scopedCss).not.toContain("1280px")
			expect(scopedCss).not.toContain("1600px")
		}
	})
})
