import html2canvas from "html2canvas"

import { dotsForPaper } from "./paper"
import {
	DEFAULT_FONT_SCALE,
	DEFAULT_TAIL_DOTS,
	receiptBaseCSS,
	receiptFrameStyle,
	scaleCssLengths,
	scopeReceiptCSS,
	splitStyleBlocks,
	tailSpacerHTML,
} from "./receipt_layout"

const FRAME_SCOPE = ".pn-receipt-frame"

const DEFAULT_THRESHOLD = 128

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
 * @returns {{host:HTMLElement, frame:HTMLElement, dots:number, tailDots:number, scopedCss:string}}
 */
export function composeReceiptFrame(html, opts = {}) {
	const dots = dotsForPaper(opts.paper, opts.customDots)
	const fontScale = (opts.fontScale ?? DEFAULT_FONT_SCALE) / 100
	const tail = opts.tailDots ?? DEFAULT_TAIL_DOTS
	const tailHTML = tailSpacerHTML(tail)

	// Styles in print HTML were written for 96 DPI preview, not for a 205 DPI
	// head. Scoping prevents them from touching the POS page, and the length
	// rewrite makes `11px` mean the same physical size it had at 96 DPI.
	const { css, html: stripped } = splitStyleBlocks(html)
	const base = receiptBaseCSS(FRAME_SCOPE, fontScale)
	const scoped = css
		? `${base}\n${scopeReceiptCSS(css, FRAME_SCOPE, fontScale)}`
		: base

	const host = document.createElement("div")
	host.style.cssText =
		"position:fixed;left:-10000px;top:0;pointer-events:none;overflow:hidden;"
	const frame = document.createElement("div")
	frame.className = "pn-receipt-frame"
	frame.style.cssText = receiptFrameStyle(dots)
	frame.innerHTML = `<style>${scoped}</style>${stripped}${tailHTML}`
	host.appendChild(frame)

	// Attributes like `style="font-size: 11px"` that survived the split were
	// never in a stylesheet, so the rewrite above missed them. Walk once:
	for (const el of frame.querySelectorAll("[style]")) {
		// The synthetic tail / any element marked as already-in-dots keeps its
		// value; otherwise the report's footer would be double-scaled together
		// with the spacer height.
		if (el.matches("[data-pn-dots]")) continue
		el.style.cssText = scaleCssLengths(el.style.cssText, fontScale)
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
		const canvas = await html2canvas(frame, {
			scale: 1,
			backgroundColor: "#ffffff",
			windowWidth: dots,
		})

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
