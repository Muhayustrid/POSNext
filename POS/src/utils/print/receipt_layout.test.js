/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

import {
	clampFontScale,
	loadDeviceConfig,
	parseNumericField,
	saveDeviceConfig,
	DEFAULT_CREW_FONT_SCALE,
	DEFAULT_EOD_COPIES,
	DEFAULT_FEED_DOTS,
	DEFAULT_FONT_SCALE,
	DEFAULT_LINE_SPACING,
	DEFAULT_SIDE_MARGIN_DOTS,
	DEFAULT_TAIL_DOTS,
	DPI_SCALE,
	receiptBaseCSS,
	receiptFrameStyle,
	resolvePrintConfig,
	scaleCssLengths,
	scopeReceiptCSS,
	splitStyleBlocks,
	tailSpacerHTML,
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

	it("no longer resolves copy labels — nothing is printed above the receipt", () => {
		// The banners came off the paper: the machinery must be gone from the
		// resolver too, so no caller can quietly put a label back.
		const r = resolvePrintConfig({ copies: 2, copyLabels: true }, {})
		expect("useLabels" in r).toBe(false)
		expect("labels" in r).toBe(false)
		expect("copyLabels" in r).toBe(false)
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

describe("receiptBaseCSS", () => {
	it("sets an explicit monospace baseline in dots, scaled by the knob", () => {
		const css = receiptBaseCSS(".pn", 1)
		expect(css).toContain("font-family")
		expect(css).toMatch(/font-size:\d+px/)
		const bigger = receiptBaseCSS(".pn", 2)
		expect(bigger).not.toBe(css)
	})

	it("scales its baseline line-height by the line-spacing factor", () => {
		expect(receiptBaseCSS(".pn", 1)).toContain("line-height:1.35")
		expect(receiptBaseCSS(".pn", 1, 0.8)).toContain("line-height:1.08")
		expect(receiptBaseCSS(".pn", 2, 1.2)).toContain("line-height:1.62")
	})

	it("no longer styles a copy banner — the print path has none", () => {
		expect(receiptBaseCSS(".pn", 1)).not.toContain("pn-copy-label")
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

describe("resolvePrintConfig crewFontScale (the crew slip's own knob)", () => {
	it("defaults to 100 — the slip mirrors the customer receipt's size by default", () => {
		expect(DEFAULT_CREW_FONT_SCALE).toBe(100)
		expect(resolvePrintConfig({}, {}).crewFontScale).toBe(100)
	})

	it("device wins over server, server over the default", () => {
		expect(
			resolvePrintConfig({ crewFontScale: 150 }, { crewFontScale: 90 })
				.crewFontScale,
		).toBe(150)
		expect(resolvePrintConfig({}, { crewFontScale: 90 }).crewFontScale).toBe(90)
	})

	it("is independent of the main font scale", () => {
		const r = resolvePrintConfig({ fontScale: 200 }, {})
		expect(r.fontScale).toBe(200)
		expect(r.crewFontScale).toBe(100)
	})

	it("clamps to the same 60..250 band as the main knob", () => {
		expect(resolvePrintConfig({ crewFontScale: 10 }, {}).crewFontScale).toBe(60)
		expect(resolvePrintConfig({ crewFontScale: 9999 }, {}).crewFontScale).toBe(
			250,
		)
	})

	it("falls back to the crew default on garbage instead of 60", () => {
		expect(
			resolvePrintConfig({ crewFontScale: "garbage" }, {}).crewFontScale,
		).toBe(100)
		expect(
			resolvePrintConfig({}, { crewFontScale: "garbage" }).crewFontScale,
		).toBe(100)
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

	it("takes a custom default (the crew knob defaults to 100)", () => {
		expect(clampFontScale(null, DEFAULT_CREW_FONT_SCALE)).toBe(100)
		expect(clampFontScale("", DEFAULT_CREW_FONT_SCALE)).toBe(100)
		expect(clampFontScale("nope", DEFAULT_CREW_FONT_SCALE)).toBe(100)
		expect(clampFontScale(20, DEFAULT_CREW_FONT_SCALE)).toBe(60)
	})
})

describe("resolvePrintConfig lineSpacing (vertical density knob)", () => {
	it("defaults to 100 — the receipt prints exactly as authored", () => {
		expect(DEFAULT_LINE_SPACING).toBe(100)
		expect(resolvePrintConfig({}, {}).lineSpacing).toBe(100)
	})

	it("device wins over server, server over the default", () => {
		expect(
			resolvePrintConfig({ lineSpacing: 80 }, { lineSpacing: 120 }).lineSpacing,
		).toBe(80)
		expect(resolvePrintConfig({}, { lineSpacing: 120 }).lineSpacing).toBe(120)
	})

	it("clamps to the 50..150 percent band", () => {
		expect(resolvePrintConfig({ lineSpacing: 10 }, {}).lineSpacing).toBe(50)
		expect(resolvePrintConfig({ lineSpacing: 999 }, {}).lineSpacing).toBe(150)
		expect(resolvePrintConfig({}, { lineSpacing: 400 }).lineSpacing).toBe(150)
	})

	it("falls back to the default on garbage or an empty value", () => {
		expect(resolvePrintConfig({ lineSpacing: "garbage" }, {}).lineSpacing).toBe(
			100,
		)
		expect(resolvePrintConfig({ lineSpacing: "" }, {}).lineSpacing).toBe(100)
		expect(resolvePrintConfig({}, { lineSpacing: null }).lineSpacing).toBe(100)
	})
})

describe("scopeReceiptCSS line-height (the lineSpacing knob)", () => {
	it("scales unitless declarations (1.4 at 80% -> 1.12)", () => {
		const out = scopeReceiptCSS("body{line-height:1.4}", ".pn", 1, 0.8)
		expect(out).toContain("line-height:1.12")
	})

	it("scales px values BEFORE the DPI translation, like any other length", () => {
		const out = scopeReceiptCSS("body{line-height:20px}", ".pn", 1, 0.8)
		expect(out).toContain(
			`line-height:${Math.round(16 * DPI_SCALE * 100) / 100}px`,
		)
	})

	it("scales %, em and rem declarations too", () => {
		expect(scopeReceiptCSS("body{line-height:120%}", ".pn", 1, 0.5)).toContain(
			"line-height:60%",
		)
		expect(scopeReceiptCSS("body{line-height:1.2em}", ".pn", 1, 1.5)).toContain(
			"line-height:1.8em",
		)
		expect(scopeReceiptCSS("body{line-height:2rem}", ".pn", 1, 0.75)).toContain(
			"line-height:1.5rem",
		)
	})

	it("only touches line-height, and only when the knob moves", () => {
		expect(scopeReceiptCSS("body{line-height:1.4}", ".pn", 1)).toContain(
			"line-height:1.4",
		)
		expect(
			scopeReceiptCSS("body{line-height:normal}", ".pn", 1, 0.8),
		).toContain("line-height:normal")
		expect(scopeReceiptCSS("body{margin:4px}", ".pn", 1, 0.8)).toContain(
			`margin:${Math.round(4 * DPI_SCALE * 100) / 100}px`,
		)
	})
})

describe("resolvePrintConfig sideMarginDots (left/right print margin)", () => {
	it("defaults to 16 dots (2 mm) — narrower than the ~40 the templates ship", () => {
		expect(DEFAULT_SIDE_MARGIN_DOTS).toBe(16)
		expect(resolvePrintConfig({}, {}).sideMarginDots).toBe(16)
	})

	it("device wins over server, server over the default", () => {
		expect(
			resolvePrintConfig({ sideMarginDots: 8 }, { sideMarginDots: 32 })
				.sideMarginDots,
		).toBe(8)
		expect(resolvePrintConfig({}, { sideMarginDots: 32 }).sideMarginDots).toBe(
			32,
		)
	})

	it("clamps to the 0..64 dot band", () => {
		expect(resolvePrintConfig({ sideMarginDots: 999 }, {}).sideMarginDots).toBe(
			64,
		)
		expect(resolvePrintConfig({}, { sideMarginDots: 400 }).sideMarginDots).toBe(
			64,
		)
	})

	it("honours an explicit 0 — the operator wants the full paper width", () => {
		expect(resolvePrintConfig({ sideMarginDots: 0 }, {}).sideMarginDots).toBe(0)
		expect(resolvePrintConfig({}, { sideMarginDots: 0 }).sideMarginDots).toBe(0)
	})

	it("falls back to the default on garbage or an empty value", () => {
		expect(
			resolvePrintConfig({ sideMarginDots: "garbage" }, {}).sideMarginDots,
		).toBe(16)
		expect(resolvePrintConfig({ sideMarginDots: "" }, {}).sideMarginDots).toBe(
			16,
		)
		expect(
			resolvePrintConfig({}, { sideMarginDots: null }).sideMarginDots,
		).toBe(16)
	})
})

describe('resolvePrintConfig kind: "eod" (Closing/EOD lane)', () => {
	const device = {
		eodCopies: 2,
		eodCopyDelayMs: 900,
		eodFeedDots: 200,
		eodTailDots: 40,
		eodFontScale: 120,
		eodLineSpacing: 90,
		eodSideMarginDots: 32,
	}
	const server = {
		eodCopies: 3,
		eodCopyDelayMs: 1000,
		eodFeedDots: 210,
		eodTailDots: 50,
		eodFontScale: 130,
		eodLineSpacing: 110,
		eodSideMarginDots: 40,
	}

	it("reads the eod* device keys over server, server over the defaults", () => {
		expect(resolvePrintConfig(device, server, { kind: "eod" })).toMatchObject({
			copies: 2,
			copyDelayMs: 900,
			feedDots: 200,
			tailDots: 40,
			fontScale: 120,
			lineSpacing: 90,
			sideMarginDots: 32,
		})
		expect(resolvePrintConfig({}, server, { kind: "eod" })).toMatchObject({
			copies: 3,
			copyDelayMs: 1000,
			feedDots: 210,
			tailDots: 50,
			fontScale: 130,
			lineSpacing: 110,
			sideMarginDots: 40,
		})
	})

	it("falls back to the eod defaults when nothing is set", () => {
		expect(DEFAULT_EOD_COPIES).toBe(1)
		expect(resolvePrintConfig({}, {}, { kind: "eod" })).toMatchObject({
			copies: 1,
			copyDelayMs: 800,
			feedDots: 160,
			tailDots: 24,
			fontScale: 100,
			lineSpacing: 100,
			sideMarginDots: 16,
		})
	})

	it("clamps to the same bands as the receipt knobs", () => {
		const r = (d) => resolvePrintConfig(d, {}, { kind: "eod" })
		expect(r({ eodCopies: 99 }).copies).toBe(5)
		expect(r({ eodCopyDelayMs: 99999 }).copyDelayMs).toBe(10000)
		expect(r({ eodFeedDots: 9999 }).feedDots).toBe(500)
		expect(r({ eodTailDots: 999 }).tailDots).toBe(200)
		expect(r({ eodFontScale: 10 }).fontScale).toBe(60)
		expect(r({ eodLineSpacing: 10 }).lineSpacing).toBe(50)
		expect(r({ eodSideMarginDots: 999 }).sideMarginDots).toBe(64)
	})

	it("ignores the receipt knobs, and the receipt lane ignores eod*", () => {
		const eod = resolvePrintConfig(
			{ copies: 2, fontScale: 150, tailDots: 99, sideMarginDots: 64 },
			{},
			{ kind: "eod" },
		)
		expect(eod.copies).toBe(1)
		expect(eod.fontScale).toBe(100)
		expect(eod.tailDots).toBe(24)
		expect(eod.sideMarginDots).toBe(16)
		// No opts (or kind "receipt") must not pick the eod overrides up.
		expect(
			resolvePrintConfig({ eodCopies: 3, eodFontScale: 200 }, {}).copies,
		).toBe(1)
		expect(resolvePrintConfig({ eodFontScale: 200 }, {}).fontScale).toBe(100)
	})

	it("keeps paper/customDots/cut global — they describe the paper, not the job", () => {
		const r = resolvePrintConfig(
			{},
			{ paper: "custom", customDots: 512, cut: true },
			{ kind: "eod" },
		)
		expect(r.paper).toBe("custom")
		expect(r.customDots).toBe(512)
		expect(r.cut).toBe(true)
		expect(r.dots).toBe(512)
	})

	it("mirrors the eod fontScale into crewFontScale so the return shape holds", () => {
		expect(
			resolvePrintConfig({ eodFontScale: 120 }, {}, { kind: "eod" })
				.crewFontScale,
		).toBe(120)
		expect(
			resolvePrintConfig({ eodFontScale: 999 }, {}, { kind: "eod" })
				.crewFontScale,
		).toBe(250)
	})

	it('a call without opts resolves exactly like kind "receipt"', () => {
		const d = { paper: "58mm", copies: 2, copyDelayMs: 900, fontScale: 110 }
		const s = {
			tailDots: 32,
			lineSpacing: 80,
			sideMarginDots: 24,
			feedDots: 200,
		}
		expect(resolvePrintConfig(d, s)).toEqual(
			resolvePrintConfig(d, s, { kind: "receipt" }),
		)
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
