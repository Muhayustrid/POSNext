import html2canvas from "html2canvas"

import { dotsForPaper } from "./paper"
import {
	DEFAULT_FONT_SCALE,
	DEFAULT_LINE_SPACING,
	DEFAULT_SIDE_MARGIN_DOTS,
	DEFAULT_TAIL_DOTS,
	MAX_SIDE_MARGIN_DOTS,
	clampInt,
	receiptBaseCSS,
	receiptFrameStyle,
	scaleCssLengths,
	scaleLineHeights,
	scopeReceiptCSS,
	splitStyleBlocks,
	tailSpacerHTML,
} from "./receipt_layout"

const FRAME_SCOPE = ".pn-receipt-frame"

const DEFAULT_THRESHOLD = 128

/**
 * Slack added on top of the measured frame height when sizing the capture.
 *
 * html2canvas paints the CLONE of the frame, and on a device the clone can
 * reflow a line or two taller than the host measured it (font fallback
 * metrics differ between the page and the cloned document). When the clone is
 * taller than the canvas, the last painted line is sliced mid-glyph — the
 * "-- Akhir Laporan --" cut on the Closing report preview. The slack is blank
 * paper at the bitmap's foot: ~5 mm, invisible under the tear.
 */
export const CAPTURE_SLACK_DOTS = 40

/**
 * html2canvas options for one receipt capture.
 *
 * windowHeight is set EXPLICITLY from the frame: without it html2canvas uses
 * the real window's height, and on a phone webview that is shorter than a
 * tall report — the cloned document then rendered against a too-small
 * viewport and the bitmap lost its bottom.
 */
export function html2canvasOptions(frame, dots) {
	const captureH = Math.ceil(frame.scrollHeight) + CAPTURE_SLACK_DOTS
	return {
		scale: 1,
		backgroundColor: "#ffffff",
		windowWidth: dots,
		windowHeight: captureH,
		height: captureH,
	}
}

/**
 * Decide how to bring a source canvas of `sourceWidth` px to exactly
 * `targetDots` px. Padding/trimming is centred so the receipt stays on the
 * paper's optical centre line.
 */
export function normalizeWidthPlan(sourceWidth, targetDots) {
	if (sourceWidth === targetDots) {
		return { action: "none", targetWidth: targetDots, offsetX: 0 }
	}
	if (sourceWidth < targetDots) {
		return {
			action: "pad",
			targetWidth: targetDots,
			offsetX: Math.floor((targetDots - sourceWidth) / 2),
		}
	}
	return {
		action: "trim",
		targetWidth: targetDots,
		offsetX: Math.floor((sourceWidth - targetDots) / 2),
	}
}

/**
 * Force an ImageData buffer to pure black/white. Thermal heads have no grey
 * levels, so dithered grey prints muddy.
 *
 * The luminance is rounded to an integer so the ITU-R BT.601 coefficients'
 * float error (they sum to 0.999...) cannot misclassify a pixel sitting
 * exactly on the threshold.
 */
export function binarize(imageData, threshold = DEFAULT_THRESHOLD) {
	const { data } = imageData
	for (let i = 0; i < data.length; i += 4) {
		const luminance = Math.round(
			0.299 * data[i] + 0.587 * data[i + 1] + 0.114 * data[i + 2],
		)
		const v = luminance < threshold ? 0 : 255
		data[i] = v
		data[i + 1] = v
		data[i + 2] = v
	}
}

/**
 * Build the off-screen frame that html2canvas rasterises.
 *
 * Three things here are load-bearing and were broken before this patch:
 *
 *  1. Leaking `<style>`s. `html` is often a FULL document (<!DOCTYPE> +
 *     <html><head><style>...). Assigning it via `frame.innerHTML = html` makes
 *     the browser extract those <style> blocks, drop <!DOCTYPE>/<html>/<head>
 *     /<body> *but keep the style elements as children of the frame* — so a
 *     receipt `<style>` that says `body{...}` was live global CSS while the
 *     print ran. Extracting + scoping those blocks to `.pn-receipt-frame` is
 *     what actually fixes it.
 *  2. DPI translation. Those blocks are authored at 96 DPI px. The frame is
 *     rasterised at 1 px = 1 printer dot at 205 DPI. So every receipt printed
 *     half its intended size. Lengths are rewritten to dots and then squeezed
 *     together with the scoping pass.
 *  3. Tail spacer: a white block under the receipt so the last printed line is
 *     not the raster's last row — purely a render-side guard, not a substitute
 *     for printAndFeedPaper(feedDots).
 *
 * Pure DOM composition apart from the html2canvas render call in the caller,
 * so it is unit-testable.
 *
 * @param {string} html
 * @param {object} [opts]
 * @param {string} [opts.paper] - "58mm" | "80mm" | "custom"
 * @param {number} [opts.customDots] - when paper is "custom"
 * @param {number} [opts.tailDots] - trailing white space in dots
 * @param {number} [opts.fontScale] - 100 = as-authored; 180 = +80%.
 *   Stored per-device and overridable from the /pos/direct-print page.
 * @param {number} [opts.lineSpacing] - percent, 100 = as-authored; 80 tightens
 *   every line-height by 20%. Same device/server resolution as fontScale.
 * @param {number} [opts.sideMarginDots] - left/right print margin in printer
 *   dots, applied to BOTH sides. Pins the frame's side padding on top of
 *   whatever the source CSS set there; 16 = 2 mm.
 * @returns {{host:HTMLElement, frame:HTMLElement, dots:number, tailDots:number, scopedCss:string}}
 */
export function composeReceiptFrame(html, opts = {}) {
	const dots = dotsForPaper(opts.paper, opts.customDots)
	const fontScale = (opts.fontScale ?? DEFAULT_FONT_SCALE) / 100
	const lineSpacing = (opts.lineSpacing ?? DEFAULT_LINE_SPACING) / 100
	const sideMarginDots = clampInt(
		opts.sideMarginDots,
		0,
		MAX_SIDE_MARGIN_DOTS,
		DEFAULT_SIDE_MARGIN_DOTS,
	)
	const tail = opts.tailDots ?? DEFAULT_TAIL_DOTS
	const tailHTML = tailSpacerHTML(tail)

	// Styles in print HTML were written for 96 DPI preview, not for a 205 DPI
	// head. Scoping prevents them from touching the POS page, and the length
	// rewrite makes `11px` mean the same physical size it had at 96 DPI.
	const { css, html: stripped } = splitStyleBlocks(html)
	const base = receiptBaseCSS(FRAME_SCOPE, fontScale, lineSpacing)
	const scopedCss = css
		? `${base}\n${scopeReceiptCSS(css, FRAME_SCOPE, fontScale, lineSpacing)}`
		: base
	// THE OVERRIDE. After the scoped CSS is assembled — base + the template's
	// own rules — append one more rule on the SAME frame scope declaring only
	// padding-left/padding-right. CSS resolves equal specificity by document
	// order, so this wins over any padding the source HTML put on body or the
	// frame (the stock receipt ships `padding: 5mm` in `@media print`, ~40 dots
	// a side) with no `!important` and without rewriting the template's text.
	// Only the two sides are declared, so a top/bottom padding the format asked
	// for survives untouched.
	//
	// Authored directly in dots and deliberately NOT run through scaleCssLengths
	// or the fontScale factor: the margin is a physical measurement of the paper
	// (16 dots = 2 mm), not a typographic length that follows the text size.
	const scoped =
		`${scopedCss}\n` +
		`${FRAME_SCOPE}{padding-left:${sideMarginDots}px;padding-right:${sideMarginDots}px;}`

	const host = document.createElement("div")
	host.style.cssText =
		"position:fixed;left:-10000px;top:0;pointer-events:none;overflow:hidden;"
	const frame = document.createElement("div")
	frame.className = "pn-receipt-frame"
	frame.style.cssText = receiptFrameStyle(dots)
	frame.innerHTML = `<style>${scoped}</style>${stripped}${tailHTML}`
	host.appendChild(frame)

	// Attributes like `style="font-size: 11px"` that survived the split were
	// never in a stylesheet, so the rewrite above missed them. Walk once. The
	// line-spacing pass rides along because the real receipt format sets
	// `line-height` INLINE on its invoice-info block — a knob that only reached
	// stylesheets would tighten everything except the densest block on the page.
	for (const el of frame.querySelectorAll("[style]")) {
		// The synthetic tail / any element marked as already-in-dots keeps its
		// value; otherwise the report's footer would be double-scaled together
		// with the spacer height.
		if (el.matches("[data-pn-dots]")) continue
		el.style.cssText = scaleCssLengths(
			scaleLineHeights(el.style.cssText, lineSpacing),
			fontScale,
		)
	}

	return {
		host,
		frame,
		dots,
		tailDots: tailHTML ? Number(tail) || 0 : 0,
		scopedCss: scoped,
	}
}

export async function renderHTMLToBitmap(html, opts) {
	const dots = dotsForPaper(opts.paper, opts.customDots)
	const { host, frame } = composeReceiptFrame(html, opts)
	document.body.appendChild(host)

	try {
		const canvas = await html2canvas(frame, html2canvasOptions(frame, dots))

		const plan = normalizeWidthPlan(canvas.width, dots)
		const out = document.createElement("canvas")
		out.width = plan.targetWidth
		out.height = canvas.height
		const ctx = out.getContext("2d")
		ctx.fillStyle = "#ffffff"
		ctx.fillRect(0, 0, out.width, out.height)
		if (plan.action === "none") {
			ctx.drawImage(canvas, 0, 0)
		} else if (plan.action === "pad") {
			ctx.drawImage(canvas, plan.offsetX, 0)
		} else {
			ctx.drawImage(
				canvas,
				plan.offsetX,
				0,
				plan.targetWidth,
				canvas.height,
				0,
				0,
				plan.targetWidth,
				canvas.height,
			)
		}

		const imageData = ctx.getImageData(0, 0, out.width, out.height)
		binarize(imageData, opts.threshold)
		ctx.putImageData(imageData, 0, 0)

		return {
			dataURL: out.toDataURL("image/png"),
			width: out.width,
			height: out.height,
		}
	} finally {
		host.remove()
	}
}
