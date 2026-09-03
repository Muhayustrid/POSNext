/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
	clampFontScale,
	copyLabelFor,
	loadDeviceConfig,
	parseNumericField,
	saveDeviceConfig,
	DEFAULT_FEED_DOTS,
	DEFAULT_FONT_SCALE,
	DEFAULT_TAIL_DOTS,
	DPI_SCALE,
	receiptBaseCSS,
	receiptFrameStyle,
	resolvePrintConfig,
	scaleCssLengths,
	scopeReceiptCSS,
	splitStyleBlocks,
	tailSpacerHTML,
	withCopyLabel,
} from "./receipt_layout"
import { dotsForPaper } from "./paper"

describe("receiptFrameStyle", () => {
	it("carries the dot width and clips overflow; padding lives in the scoped CSS", () => {
		expect(receiptFrameStyle(384)).toContain("width:384px")
		expect(receiptFrameStyle(384)).toContain("max-width:384px")
		expect(receiptFrameStyle(384)).toContain("overflow:hidden")
		expect(receiptFrameStyle(576)).toContain("width:576px")
		// Inline padding was removed on purpose: the scoped stylesheet sets it,
		// so the value honours the DPI translation instead of being hardcoded.
		expect(receiptFrameStyle(384)).not.toContain("padding")
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

describe("scaleCssLengths (96 DPI -> 205 DPI dots)", () => {
	it("multiplies px by the DPI ratio", () => {
		expect(scaleCssLengths("font-size:11px", 1)).toBe(
			`font-size:${Math.round(11 * DPI_SCALE * 100) / 100}px`,
		)
	})

	it("converts physical units by their true dot count (not the ratio)", () => {
		// 1mm = 8 dots at 205 DPI; a mm must not follow the font knob.
		expect(scaleCssLengths("width:5mm", 1)).toBe("width:40px")
		expect(scaleCssLengths("width:1in", 1)).toBe("width:205px")
		expect(scaleCssLengths("width:10pt", 1)).toBe(
			`width:${Math.round(((10 * 205) / 72) * 100) / 100}px`,
		)
	})

	it("applies the font-scale factor only to typographic units", () => {
		expect(scaleCssLengths("font-size:10px", 1.5)).toBe(
			`font-size:${Math.round(15 * DPI_SCALE * 100) / 100}px`,
		)
		expect(scaleCssLengths("width:5mm", 1.5)).toBe("width:40px")
	})

	it("leaves unitless and % values alone", () => {
		expect(scaleCssLengths("width:100%;line-height:1.35", 1)).toBe(
			"width:100%;line-height:1.35",
		)
	})

	it("ignores numbers without a css length unit", () => {
		expect(scaleCssLengths("color:#102030", 1)).toBe("color:#102030")
	})
})

describe("splitStyleBlocks", () => {
	it("separates style blocks from markup", () => {
		const { css, html } = splitStyleBlocks(
			'<style>a{color:red}</style><div>x</div><style type="text/css">b{}</style>',
		)
		expect(css).toContain("a{color:red}")
		expect(css).toContain("b{}")
		expect(html).toBe("<div>x</div>")
	})
})

describe("scopeReceiptCSS", () => {
	it("maps body/html/:root onto the frame (they match nothing otherwise)", () => {
		const out = scopeReceiptCSS("body{padding:4px}", ".pn-receipt-frame")
		expect(out).toContain(".pn-receipt-frame{")
		expect(out).not.toMatch(/(^|\s)body\s*\{/)
	})

	it("prefixes ordinary selectors and keeps selector lists intact", () => {
		const out = scopeReceiptCSS(".a, .b p{margin:0}", ".pn")
		// One rule, every selector scoped — not split into separate rules.
		expect(out).toBe(".pn .a, .pn .b p{margin:0}")
	})

	it("drops @page (meaningless for a bitmap)", () => {
		expect(scopeReceiptCSS("@page{size:80mm}", ".pn")).toBe("")
	})

	it("unwraps @media print and drops screen-only blocks", () => {
		const out = scopeReceiptCSS(
			"@media print{.f{color:#000}}@media screen{.g{color:#00f}}",
			".pn",
		)
		expect(out).toContain(".pn .f{")
		expect(out).not.toContain(".g")
	})

	it("scales lengths while scoping", () => {
		const out = scopeReceiptCSS(".a{font-size:10px}", ".pn", 1)
		expect(out).toContain(
			`font-size:${Math.round(10 * DPI_SCALE * 100) / 100}px`,
		)
	})

	it("does not double-scope an already-scoped selector", () => {
		const out = scopeReceiptCSS(".pn .a{margin:0}", ".pn")
		expect(out).toBe(".pn .a{margin:0}")
	})
})

describe("clampFontScale", () => {
	it("defaults to 100 and clamps to 60..250", () => {
		expect(clampFontScale(null)).toBe(100)
		expect(clampFontScale("")).toBe(100)
		expect(clampFontScale(40)).toBe(60)
		expect(clampFontScale(999)).toBe(250)
		expect(clampFontScale("130")).toBe(130)
	})
})

describe("receiptBaseCSS", () => {
	it("sets an explicit monospace baseline in dots, scaled by the knob", () => {
		const css = receiptBaseCSS(".pn", 1)
		expect(css).toContain("font-family")
		expect(css).toMatch(/font-size:\d+px/)
		const bigger = receiptBaseCSS(".pn", 2)
		expect(bigger).not.toBe(css)
	})
})

describe("resolvePrintConfig fontScale", () => {
	it("defaults to 100 and honours device over server", () => {
		expect(resolvePrintConfig({}, {}).fontScale).toBe(DEFAULT_FONT_SCALE)
		expect(
			resolvePrintConfig({ fontScale: 150 }, { fontScale: 90 }).fontScale,
		).toBe(150)
		expect(resolvePrintConfig({}, { fontScale: 90 }).fontScale).toBe(90)
	})

	it("clamps absurd values", () => {
		expect(resolvePrintConfig({ fontScale: 9999 }, {}).fontScale).toBe(250)
	})
})

describe("parseNumericField (settings fields must not silently become 0)", () => {
	const opts = { min: 0, max: 10000, dflt: 800 }

	it("empty / whitespace -> default", () => {
		expect(parseNumericField("Delay", "", opts)).toBe(800)
		expect(parseNumericField("Delay", "  ", opts)).toBe(800)
		expect(parseNumericField("Delay", null, opts)).toBe(800)
	})

	it("explicit numbers (incl. 0) parse and clamp", () => {
		expect(parseNumericField("Delay", "800", opts)).toBe(800)
		expect(parseNumericField("Delay", "0", opts)).toBe(0)
		expect(parseNumericField("Delay", "99999", opts)).toBe(10000)
	})

	it("garbage THROWS instead of saving a silent 0", () => {
		expect(() => parseNumericField("Delay", "abc", opts)).toThrow(
			/Delay must be a number/,
		)
		expect(() => parseNumericField("Delay", "8 00", opts)).toThrow(
			/Delay must be a number/,
		)
	})
})

describe("resolvePrintConfig copyDelayMs robustness", () => {
	it("garbage stored on the device falls back to 800, not 0", () => {
		expect(resolvePrintConfig({ copyDelayMs: "abc" }, {}).copyDelayMs).toBe(800)
	})

	it("an explicit 0 is honoured (operator really wants no pause)", () => {
		expect(resolvePrintConfig({ copyDelayMs: 0 }, {}).copyDelayMs).toBe(0)
	})
})

describe("device config migration (corrupted delay 0 from v1)", () => {
	beforeEach(() => localStorage.clear())

	it("drops a v1 copyDelayMs of 0 so the 800 default works again", () => {
		localStorage.setItem(
			"pos_imin_device_config",
			JSON.stringify({ paper: "58mm", copies: 2, copyDelayMs: 0 }),
		)
		expect(loadDeviceConfig().copyDelayMs).toBeUndefined()
		expect(resolvePrintConfig(loadDeviceConfig(), {}).copyDelayMs).toBe(800)
	})

	it("keeps non-zero v1 values untouched", () => {
		localStorage.setItem(
			"pos_imin_device_config",
			JSON.stringify({ copyDelayMs: 1200 }),
		)
		expect(loadDeviceConfig().copyDelayMs).toBe(1200)
	})

	it("an explicit 0 saved under v2 is honoured (operator chose no pause)", () => {
		saveDeviceConfig({ copyDelayMs: 0, copies: 2 })
		expect(loadDeviceConfig().copyDelayMs).toBe(0)
		expect(resolvePrintConfig(loadDeviceConfig(), {}).copyDelayMs).toBe(0)
	})

	it("stamps v2 on every save so migration runs at most once", () => {
		saveDeviceConfig({ paper: "58mm" })
		expect(JSON.parse(localStorage.getItem("pos_imin_device_config"))._v).toBe(
			2,
		)
	})
})
