import html2canvas from "html2canvas"

import { dotsForPaper } from "./paper"

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
 * Render print HTML to a monochrome bitmap whose width is exactly the paper's
 * dot count. 1 CSS px == 1 printer dot, so html2canvas scale is forced to 1
 * (never left to the device pixel ratio — that is what skewed the old app).
 *
 * @param {string} html - full HTML document or fragment to print.
 * @param {object} opts
 * @param {string} opts.paper - "58mm" | "80mm" | "custom"
 * @param {number} [opts.customDots] - when paper is "custom"
 * @param {number} [opts.threshold] - binarize threshold (default 128)
 * @returns {Promise<{dataURL:string,width:number,height:number}>}
 */
export async function renderHTMLToBitmap(html, opts) {
	const dots = dotsForPaper(opts.paper, opts.customDots)

	const host = document.createElement("div")
	host.style.cssText =
		"position:fixed;left:-10000px;top:0;pointer-events:none;overflow:hidden;"
	const frame = document.createElement("div")
	frame.style.cssText = `width:${dots}px;background:#fff;color:#000;`
	frame.innerHTML = html
	host.appendChild(frame)
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
