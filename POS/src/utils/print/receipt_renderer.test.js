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
