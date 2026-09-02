import html2canvas from "html2canvas"

import { dotsForPaper } from "./paper"
import {
	DEFAULT_TAIL_DOTS,
	receiptFrameStyle,
	tailSpacerHTML,
} from "./receipt_layout"

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
 * Two things here are load-bearing and were previously broken:
 *
 *  1. Padding. `html` is usually a FULL document, and `frame.innerHTML = html`
 *     runs the fragment parser: <!DOCTYPE>, <html>, <head> and <body> are all
 *     dropped (the <style> text survives). So every `body { ... }` rule in the
 *     receipt stylesheet matched nothing — including `padding: 10px`, which is
 *     why bitmap content sat flush against the paper edge. The frame carries
 *     the padding itself now (receiptFrameStyle).
 *  2. Tail spacer. A white block under the receipt so the last printed line is
 *     not the last raster row. This is a RENDER-side guard only; it does not
 *     replace printAndFeedPaper(feedDots), which is what physically moves the
 *     paper past the tear bar.
 *
 * Pure DOM composition (no html2canvas, no canvas) so it is unit-testable.
 *
 * @param {string} html
 * @param {object} [opts]
 * @param {string} [opts.paper] - "58mm" | "80mm" | "custom"
 * @param {number} [opts.customDots] - when paper is "custom"
 * @param {number} [opts.tailDots] - trailing white space in dots
 * @returns {{host:HTMLElement, frame:HTMLElement, dots:number, tailDots:number}}
 */
export function composeReceiptFrame(html, opts = {}) {
	const dots = dotsForPaper(opts.paper, opts.customDots)
	const tail = opts.tailDots ?? DEFAULT_TAIL_DOTS
	const tailHTML = tailSpacerHTML(tail)
	const host = document.createElement("div")
	host.style.cssText =
		"position:fixed;left:-10000px;top:0;pointer-events:none;overflow:hidden;"
	const frame = document.createElement("div")
	frame.className = "pn-receipt-frame"
	frame.style.cssText = receiptFrameStyle(dots)
	frame.innerHTML = html + tailHTML
	host.appendChild(frame)
	return { host, frame, dots, tailDots: tailHTML ? Number(tail) || 0 : 0 }
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
