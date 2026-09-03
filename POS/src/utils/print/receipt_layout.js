/**
 * Pure helpers for receipt bitmap layout.
 *
 * No DOM, no canvas, no html2canvas — safe to unit-test in any environment.
 * The bitmap width is always `dots` (from paper), and the CSS leak that used
 * to hit the live POS DOM is gone: the bitmap frame is the sole width
 * authority; body rules are only for the browser-popup path
 * (printInvoice.js) via receiptStylesFor().
 */

import { DOTS_PER_MM, dotsForPaper } from "./paper"

export const DEFAULT_TAIL_DOTS = 24
export const DEFAULT_FEED_DOTS = 160
const MAX_TAIL_DOTS = 200

const DEVICE_CONFIG_KEY = "pos_imin_device_config"
/**
 * v1 (no marker) stored copyDelayMs as a raw `Number(x) || 0`, so a field
 * that failed to parse silently saved 0 — which reads back as "no tear-off
 * pause at all" and beats the 800 ms default forever. A v1 record with
 * delay 0 is corruption from that bug, not intent, so it is dropped on load.
 * v2 stamps every save; an explicit 0 saved under v2 is honoured.
 */
const DEVICE_CONFIG_VERSION = 2

function migrateDeviceConfig(stored) {
	if (!stored || typeof stored !== "object") return {}
	if (stored._v === DEVICE_CONFIG_VERSION) return stored
	const next = { ...stored }
	if (next.copyDelayMs === 0) delete next.copyDelayMs
	next._v = DEVICE_CONFIG_VERSION
	return next
}

/**
 * Per-device print overrides, kept in localStorage on the till itself.
 * Lives here (rather than in imin_client) because the receipt builder and the
 * Direct Print preview need the same values the driver uses — three readers,
 * one source of truth. imin_client re-exports these for its existing callers.
 */
export function loadDeviceConfig() {
	try {
		const stored = JSON.parse(localStorage.getItem(DEVICE_CONFIG_KEY) || "{}")
		return migrateDeviceConfig(stored)
	} catch {
		return {}
	}
}

export function saveDeviceConfig(patch) {
	const next = {
		...loadDeviceConfig(),
		...patch,
		_v: DEVICE_CONFIG_VERSION,
	}
	localStorage.setItem(DEVICE_CONFIG_KEY, JSON.stringify(next))
	return next
}

/**
 * CSS for the off-screen bitmap frame. The frame's inline width is the single
 * width authority; overflow:hidden keeps anything the source CSS sized too
 * wide from painting outside the paper.
 *
 * max-width is locked inline for the same reason: print formats arrive with
 * `body { max-width: 80mm }`-style rules, and once scoped onto the frame a
 * *narrower* max-width (58mm format on an 80mm device) would silently shrink
 * the paper width — inline styles win over any author stylesheet.
 *
 * The receipt's own <style> blocks are NOT left inline: they are extracted,
 * rewritten to dot lengths, restricted to `.pn-receipt-frame`, and re-injected
 * as the first child of the frame. There is no such thing as a real scoped
 * <style> any more (the `scoped` attribute was removed from the spec and is
 * supported by no browser), so scoping has to be done by rewriting selectors —
 * otherwise `body { width: 80mm }` would style the live POS page while it
 * prints.
 */
export function receiptFrameStyle(dots) {
	return `width:${dots}px;max-width:${dots}px;overflow:hidden;box-sizing:border-box;`
}

/**
 * White block appended under the receipt so the last text line is not
 * exactly at the raster edge. Height-defined (not margin) so it survives
 * margin-collapse and is counted in getBoundingClientRect().
 *
 * data-pn-dots marks it as already expressed in dots: composeReceiptFrame
 * scales author-written px, and double-scaling the spacer would silently
 * change the tear-bar clearance.
 */
export function tailSpacerHTML(tailDots) {
	const n = Number(tailDots) || 0
	if (n <= 0) return ""
	const h = Math.max(0, Math.min(Math.floor(n), MAX_TAIL_DOTS))
	if (h <= 0) return ""
	return `<div class="pn-receipt-tail" data-pn-dots="1" style="height:${h}px;min-height:${h}px;width:100%;background:#fff;"></div>`
}

export function clampTailDots(v) {
	if (v == null || v === "") return DEFAULT_TAIL_DOTS
	const n = Number(v)
	if (!Number.isFinite(n)) return DEFAULT_TAIL_DOTS
	return Math.max(0, Math.min(Math.floor(n), MAX_TAIL_DOTS))
}

export function resolveReceiptLayout({
	paper = "58mm",
	customDots,
	tailDots,
} = {}) {
	const dots = dotsForPaper(paper, customDots)
	const tail = clampTailDots(tailDots)
	return { dots, tailDots: tail }
}

/**
 * Receipt stylesheet, authored at 96 DPI like every other piece of print CSS.
 *
 * Two consumers:
 *  - Browser popup (window.open + document.write): <body> is real, so the
 *    `body {...}` and `@media print` blocks apply as written.
 *  - iMin bitmap: receipt_renderer extracts these blocks, scopes them to
 *    .pn-receipt-frame and multiplies every length by 205/96 so the physical
 *    size matches what the author saw at 96 DPI.
 *
 * Spacing is deliberately tight: after the DPI multiplication every one of
 * these px is ~2.1 dots, and the old values (20px section gaps) printed as
 * 5+ mm of dead air between sections — the "too much empty space" report.
 */
export function receiptStylesFor(dots) {
	const mm = dots / 8
	return `
	* { margin: 0; padding: 0; box-sizing: border-box; }
	body {
		font-family: 'Courier New', monospace;
		padding: 4px; width: ${mm}mm; margin: 0; max-width: ${mm}mm;
		font-weight: bold; color: black;
	}
	.receipt { width: 100%; }
	.header { text-align: center; margin-bottom: 8px; border-bottom: 2px dashed #000; padding-bottom: 4px; }
	.company-name { font-size: 18px; font-weight: bold; margin-bottom: 2px; }
	.invoice-info { margin-bottom: 6px; font-size: 11px; }
	.invoice-info div { display: flex; justify-content: space-between; margin-bottom: 2px; }
	.partial-status { color: #000; font-weight: bold; margin-bottom: 2px; }
	.items-table { width: 100%; margin-bottom: 6px; border-top: 1px dashed #000; border-bottom: 1px dashed #000; padding: 4px 0; }
	.item-row { margin-bottom: 5px; font-size: 11px; }
	.item-name { font-weight: bold; margin-bottom: 2px; }
	.item-details { display: flex; justify-content: space-between; font-size: 10px; }
	.item-discount { display: flex; justify-content: space-between; font-size: 10px; margin-top: 1px; }
	.item-serials { font-size: 9px; margin-top: 2px; padding: 2px 4px; border: 1px dashed #000; border-radius: 2px; }
	.item-serials-label { font-weight: bold; margin-bottom: 1px; }
	.item-serials-list { word-break: break-all; }
	.totals { margin-top: 6px; border-top: 1px dashed #000; padding-top: 4px; }
	.total-row { display: flex; justify-content: space-between; margin-bottom: 2px; font-size: 11px; }
	.grand-total { font-size: 16px; font-weight: bold; border-top: 2px solid #000; padding-top: 4px; margin-top: 4px; }
	.payments { margin-top: 6px; border-top: 1px dashed #000; padding-top: 4px; }
	.payment-row { display: flex; justify-content: space-between; margin-bottom: 2px; font-size: 10px; }
	.total-paid { font-weight: bold; border-top: 1px solid #000; padding-top: 4px; margin-top: 4px; }
	.outstanding-row {
		display: flex; justify-content: space-between; font-size: 12px; font-weight: bold;
		border: 1px solid #000; padding: 6px; margin-top: 4px; border-radius: 4px;
	}
	.offline-badge {
		text-align: center; font-size: 10px; font-weight: bold;
		border: 1px dashed #000; padding: 3px; margin-bottom: 6px;
	}
	.footer { text-align: center; margin-top: 8px; padding-top: 4px; border-top: 2px dashed #000; font-size: 10px; }
	.pn-receipt-tail { background: #fff; }
	@media print {
		@page { size: ${mm}mm auto; margin: 0; }
		body { width: ${mm}mm; padding: 2mm; margin: 0; }
		.no-print { display: none; }
	}
`
}

// Preview helper: how many bitmaps to render and at what delay.
export function buildPreviewPlan(copies, copyDelayMs) {
	const n = Math.max(1, Math.min(Number(copies) || 1, 5))
	const d = Math.max(0, Math.min(Number(copyDelayMs) || 0, 10000))
	return { copies: n, copyDelayMs: d }
}

const MAX_COPIES = 5
const MAX_COPY_DELAY_MS = 10000
const DEFAULT_COPIES = 1
const DEFAULT_COPY_DELAY_MS = 800

function clampInt(v, lo, hi, dflt) {
	if (v == null || v === "") return dflt
	const n = Number(v)
	if (!Number.isFinite(n)) return dflt
	return Math.max(lo, Math.min(Math.floor(n), hi))
}

/**
 * Single source of truth for how device localStorage overrides the server
 * (POS Settings) print config. Both the iMin driver and the Direct Print
 * preview call this, so what you preview is exactly what prints.
 *
 * Precedence per value: device key if present (including false / "58mm")
 * wins; only an ABSENT device key falls through to the server value; then the
 * module default. `??` (not `||`) keeps that distinction.
 *
 * There is no copy-label knob here any more: nothing is printed above a copy.
 * The banners read like a second receipt the customer never asked for, so both
 * the CUSTOMER COPY and the CREW COPY label went. Copies are identical
 * bitmaps; the Direct Print preview captions its rows instead, which costs no
 * paper.
 */
export function resolvePrintConfig(device = {}, server = {}) {
	const paper = device.paper ?? server.paper ?? "58mm"
	const customDots = device.customDots ?? server.customDots ?? undefined
	const cut = device.cut ?? server.cut ?? false
	const copies = clampInt(
		device.copies ?? server.copies ?? DEFAULT_COPIES,
		1,
		MAX_COPIES,
		DEFAULT_COPIES,
	)
	const copyDelayMs = clampInt(
		device.copyDelayMs ?? server.copyDelayMs ?? DEFAULT_COPY_DELAY_MS,
		0,
		MAX_COPY_DELAY_MS,
		DEFAULT_COPY_DELAY_MS,
	)
	const feedDots = clampInt(
		device.feedDots ?? server.feedDots ?? DEFAULT_FEED_DOTS,
		8,
		500,
		DEFAULT_FEED_DOTS,
	)
	const tailDots = clampInt(
		device.tailDots ?? server.tailDots ?? DEFAULT_TAIL_DOTS,
		0,
		MAX_TAIL_DOTS,
		DEFAULT_TAIL_DOTS,
	)
	// Percent (100 = as authored). Extra headroom on top of the fixed 205/96
	// translation, for tills where the operator wants chunkier text still.
	const fontScale = clampFontScale(device.fontScale ?? server.fontScale)
	// The crew slip has its own knob: it is read across a counter, so it
	// defaults chunkier than the receipt itself.
	const crewFontScale = clampFontScale(
		device.crewFontScale ?? server.crewFontScale,
		DEFAULT_CREW_FONT_SCALE,
	)

	const dots = dotsForPaper(paper, customDots)

	return {
		paper,
		customDots,
		cut,
		copies,
		copyDelayMs,
		feedDots,
		tailDots,
		fontScale,
		crewFontScale,
		dots,
	}
}

// ---------------------------------------------------------------------------
// DPI translation (the reason receipts printed small)
// ---------------------------------------------------------------------------
//
// Receipt CSS — ours and every Frappe print format — is authored in CSS px,
// i.e. 96 DPI. renderHTMLToBitmap rasterises at 1 CSS px = 1 printer dot, and
// the head is 205 DPI. So an 11px line the author intended as ~2.9mm came out
// 11 dots = 1.4mm: half size, which is exactly the "font too small, too much
// empty space" report (whitespace kept its own px values, so the ratio of gap
// to glyph looked wrong too).
//
// Lengths are therefore converted to dots before rasterising: px/pt via the
// DPI ratio, mm/cm/in by their true physical dot count (8 dots/mm at 205 DPI).

export const DOT_DPI = 205
export const CSS_DPI = 96
/** ~2.135 — how many dots one authored CSS px should occupy. */
export const DPI_SCALE = DOT_DPI / CSS_DPI

const UNIT_DOTS = {
	px: DPI_SCALE,
	pt: DOT_DPI / 72,
	mm: DOTS_PER_MM,
	cm: DOTS_PER_MM * 10,
	in: DOT_DPI,
}
// Only typographic units follow the user's font-scale knob; a width given in
// mm is a physical measurement and must stay that size.
const SCALABLE_UNITS = new Set(["px", "pt"])
const LENGTH_RE = /(-?\d*\.?\d+)(px|pt|mm|cm|in)\b/g

export const DEFAULT_FONT_SCALE = 100
/** The crew slip starts chunkier: it is read across a counter, not handed to
 * the customer, and it carries no prices to crowd the line. */
export const DEFAULT_CREW_FONT_SCALE = 130
const MIN_FONT_SCALE = 60
const MAX_FONT_SCALE = 250

/**
 * Parse one numeric settings field for saving.
 *
 * Empty/whitespace -> default (the "unset = use server/default" contract).
 * Garbage -> THROWS. The old `Number(x) || 0` silently saved 0, which for
 * copyDelayMs meant "no tear-off pause at all" — the "sometimes the delay
 * happens, sometimes it doesn't" report from the device. Zero stays a legal
 * explicit value where the range allows it.
 */
export function parseNumericField(label, raw, { min, max, dflt }) {
	const s = String(raw ?? "").trim()
	if (s === "") return dflt
	const n = Number(s)
	if (!Number.isFinite(n)) {
		throw new Error(`${label} must be a number (got "${s}")`)
	}
	return Math.max(min, Math.min(Math.floor(n) === n ? n : Math.round(n), max))
}

export function clampFontScale(v, dflt = DEFAULT_FONT_SCALE) {
	if (v == null || v === "") return dflt
	const n = Number(v)
	if (!Number.isFinite(n)) return dflt
	return Math.max(MIN_FONT_SCALE, Math.min(Math.round(n), MAX_FONT_SCALE))
}

/** Rewrite every absolute length in a CSS string into printer dots. */
export function scaleCssLengths(text, fontScale = 1) {
	if (!text) return text
	return String(text).replace(LENGTH_RE, (_m, num, unit) => {
		const factor = UNIT_DOTS[unit] * (SCALABLE_UNITS.has(unit) ? fontScale : 1)
		const dots = Number(num) * factor
		return `${Math.round(dots * 100) / 100}px`
	})
}

/** Pull <style> blocks out of print HTML, returning the CSS and the rest. */
export function splitStyleBlocks(html) {
	const css = []
	const stripped = String(html || "").replace(
		/<style\b[^>]*>([\s\S]*?)<\/style>/gi,
		(_m, inner) => {
			css.push(inner)
			return ""
		},
	)
	return { css: css.join("\n"), html: stripped }
}

/** Split CSS into top-level {prelude, body} pairs by brace depth. */
function splitTopLevelRules(css) {
	const out = []
	let depth = 0
	let start = 0
	let prelude = ""
	for (let i = 0; i < css.length; i++) {
		const c = css[i]
		if (c === "{") {
			if (depth === 0) {
				prelude = css.slice(start, i)
				start = i + 1
			}
			depth++
		} else if (c === "}") {
			depth--
			if (depth === 0) {
				out.push({ prelude: prelude.trim(), body: css.slice(start, i) })
				start = i + 1
				prelude = ""
			}
		}
	}
	return out
}

/**
 * Rewrite one selector so it can only match inside the bitmap frame.
 *
 * `body { ... }` rules are the important case: the frame is a <div>, and
 * `frame.innerHTML = fullDocument` makes the parser drop <body> entirely, so
 * those rules previously matched nothing at all inside the frame — while the
 * <style> itself went live and leaked onto the real POS page. Mapping body to
 * the frame fixes both halves.
 */
function scopeSelector(sel, scope) {
	const s = sel.trim()
	if (!s) return ""
	if (s === "*") return `${scope}, ${scope} *`
	const m = s.match(/^(?:body|html|:root)\b\s*([\s\S]*)$/i)
	if (m) return m[1] ? `${scope} ${m[1]}` : scope
	if (s.startsWith(scope)) return s
	return `${scope} ${s}`
}

/**
 * Scope a receipt stylesheet to the bitmap frame and convert its lengths to
 * dots. `@page` is dropped (meaningless for a bitmap) and `@media print` is
 * unwrapped, because rasterising for the thermal head *is* the print context.
 */
export function scopeReceiptCSS(css, scope, fontScale = 1) {
	const clean = String(css || "").replace(/\/\*[\s\S]*?\*\//g, "")
	const parts = []
	for (const { prelude, body } of splitTopLevelRules(clean)) {
		if (prelude.startsWith("@")) {
			const at = prelude
				.slice(1)
				.split(/[\s({]/)[0]
				.toLowerCase()
			if (at === "page") continue
			if (at === "media") {
				// Screen-only blocks never apply to a receipt; print blocks do.
				if (!/\bscreen\b/i.test(prelude) || /\bprint\b/i.test(prelude)) {
					parts.push(scopeReceiptCSS(body, scope, fontScale))
				}
				continue
			}
			if (at === "supports") {
				parts.push(scopeReceiptCSS(body, scope, fontScale))
				continue
			}
			// @font-face / @keyframes carry no selectors to scope.
			parts.push(`${prelude}{${scaleCssLengths(body, fontScale)}}`)
			continue
		}
		const selectors = prelude
			.split(",")
			.map((s) => scopeSelector(s, scope))
			.filter(Boolean)
			.join(", ")
		if (!selectors) continue
		parts.push(`${selectors}{${scaleCssLengths(body, fontScale)}}`)
	}
	return parts.join("\n")
}

/**
 * Baseline typography for the bitmap, authored directly in DOTS (unlike the
 * scoped receipt CSS above, which is converted from 96 DPI px). It only has to
 * cover what the receipt stylesheet leaves unset — a print format's own rules
 * come after this and win.
 *
 * 22 dots ~= 2.7mm, and monospace at that size gives ~32 characters across a
 * 58mm line, which is the iMin Font-A budget.
 */
export function receiptBaseCSS(scope, fontScale = 1) {
	const base = Math.round(22 * fontScale)
	return `${scope}{font-family:'Courier New',Courier,monospace;font-weight:bold;color:#000;background:#fff;font-size:${base}px;line-height:1.35;padding:16px;}
${scope} .pn-receipt-tail{background:#fff;}`
}
