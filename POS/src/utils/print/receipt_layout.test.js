/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
	copyLabelFor,
	DEFAULT_FEED_DOTS,
	DEFAULT_TAIL_DOTS,
	receiptFrameStyle,
	resolvePrintConfig,
	tailSpacerHTML,
	withCopyLabel,
} from "./receipt_layout"
import { dotsForPaper } from "./paper"

describe("receiptFrameStyle", () => {
	it("carries the dot width as frame width, plus inner padding", () => {
		expect(receiptFrameStyle(384)).toContain("width:384px")
		expect(receiptFrameStyle(384)).toContain("padding:8px")
		expect(receiptFrameStyle(576)).toContain("width:576px")
	})
})

describe("tailSpacerHTML", () => {
	it("emits a white block with the requested height", () => {
		const h = tailSpacerHTML(24)
		expect(h).toContain('class="pn-receipt-tail"')
		expect(h).toContain("height:24px")
	})

	it("is empty when 0 / absent (no spacer for callers that already pad)", () => {
		expect(tailSpacerHTML(0)).toBe("")
		expect(tailSpacerHTML(null)).toBe("")
	})

	it("clamps too-large values to the operational maximum", () => {
		expect(tailSpacerHTML(999)).toContain("height:200px")
	})
})

describe("copyLabelFor", () => {
	it("returns empty for a single copy (no label needed)", () => {
		expect(copyLabelFor(0, 1)).toBe("")
		expect(copyLabelFor(1, 1)).toBe("")
	})

	it("labels customer vs crew for the common 2-copy case", () => {
		expect(copyLabelFor(0, 2)).toBe("CUSTOMER COPY")
		expect(copyLabelFor(1, 2)).toBe("CREW COPY")
	})

	it("keeps going for copy 3, 4, ...", () => {
		expect(copyLabelFor(2, 3)).toBe("COPY 3")
	})
})

describe("withCopyLabel", () => {
	it("prepends a banner when labelled", () => {
		expect(withCopyLabel("<div>body</div>", "CREW COPY")).toMatch(
			/^<div class="pn-copy-label"[^>]*>CREW COPY<\/div><div>body<\/div>/,
		)
	})

	it("is identity when the label is empty", () => {
		expect(withCopyLabel("<div>x</div>", "")).toBe("<div>x</div>")
	})
})

describe("resolvePrintConfig", () => {
	it("device keys win over server keys", () => {
		const r = resolvePrintConfig({ paper: "58mm" }, { paper: "80mm" })
		expect(r.paper).toBe("58mm")
		expect(r.dots).toBe(384)
	})

	it("falls back to server keys when the device has none", () => {
		const r = resolvePrintConfig({}, { paper: "80mm", cut: true })
		expect(r.paper).toBe("80mm")
		expect(r.dots).toBe(576)
		expect(r.cut).toBe(true)
	})

	it("defaults feed to 160 (safer than the old 100)", () => {
		expect(resolvePrintConfig({}, {}).feedDots).toBe(160)
		expect(DEFAULT_FEED_DOTS).toBe(160)
	})

	it("defaults tail to a small non-zero spacer", () => {
		expect(DEFAULT_TAIL_DOTS).toBeGreaterThan(0)
		expect(resolvePrintConfig({}, {}).tailDots).toBe(DEFAULT_TAIL_DOTS)
	})

	it("does not label a single copy", () => {
		const r = resolvePrintConfig({ copies: 1 }, {})
		expect(r.useLabels).toBe(false)
		expect(r.labels).toEqual([])
	})

	it("labels both copies by default when copies>1", () => {
		const r = resolvePrintConfig({ copies: 2 }, {})
		expect(r.useLabels).toBe(true)
		expect(r.labels).toEqual(["CUSTOMER COPY", "CREW COPY"])
	})

	it("honours a device copyLabels:false even when copies>1", () => {
		const r = resolvePrintConfig({ copies: 2, copyLabels: false }, {})
		expect(r.useLabels).toBe(false)
	})

	it("maps through dotsForPaper for custom widths", () => {
		const r = resolvePrintConfig({ paper: "custom", customDots: 512 }, {})
		expect(r.dots).toBe(512)
		expect(() => dotsForPaper("custom", r.customDots)).not.toThrow()
	})

	it("clamps an absurd feed down to the SDK ceiling", () => {
		expect(resolvePrintConfig({ feedDots: 9999 }, {}).feedDots).toBe(500)
	})
})
