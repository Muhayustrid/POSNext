/**
 * Print preview for the Direct Print page.
 *
 * The point of a preview is that it *is* the print. So this mirrors the iMin
 * driver's render block exactly — same `resolvePrintConfig`, same
 * `withCopyLabel(html, labels[i])`, same `renderHTMLToBitmap`, same
 * tailDots/paper/customDots. There is no second renderer and no second label
 * path; if the two ever disagree, the preview lies. Keeping the copy loop here
 * (rather than in the Vue page) is what makes "preview = print" structural.
 *
 * Nothing here touches the printer, so it works on a laptop with no iMin
 * attached. What it cannot show is the physical part: where the tear bar
 * actually falls relative to the last line. That still needs the device.
 */
import { renderHTMLToBitmap } from "./receipt_renderer"
import { resolvePrintConfig, withCopyLabel } from "./receipt_layout"

/**
 * Render the full copy set exactly as the driver would.
 *
 * @param {string} html - receipt document HTML (no copy label yet).
 * @param {object} [opts]
 * @param {object} [opts.device] - per-device localStorage config.
 * @param {object} [opts.server] - transport/POS Settings config.
 * @param {number} [opts.copies] - override the configured copy count
 *   (the preview buttons pass 1 or 2 explicitly).
 * @param {(html, o) => Promise<{dataURL,width,height}>} [opts.render]
 *   injected for tests; defaults to the real bitmap renderer.
 * @returns {Promise<{dots:number, paper:string, tailDots:number,
 *   feedDots:number, copyDelayMs:number,
 *   copies:Array<{index:number,label:string,visible:boolean,
 *     delayMs:number,bitmap:{dataURL,width,height}}>}>}
 */
export async function buildReceiptPreviewSet(html, opts = {}) {
	const device = { ...(opts.device || {}) }
	if (opts.copies != null) device.copies = opts.copies
	const r = resolvePrintConfig(device, opts.server || {})
	const render = opts.render || ((h, o) => renderHTMLToBitmap(h, o))

	// Same branch the driver takes: labelled per-copy bitmaps, or one shared.
	// The preview still has to SHOW `r.copies` sheets when labels are off — the
	// printer will physically eject that many — so a shared bitmap is reused
	// per index rather than collapsing the list to one row.
	const renderOpts = {
		paper: r.paper,
		customDots: r.customDots,
		tailDots: r.tailDots,
	}
	let bitmaps
	if (r.useLabels) {
		bitmaps = await Promise.all(
			Array.from({ length: r.copies }, (_, idx) =>
				render(withCopyLabel(html, r.labels[idx]), renderOpts),
			),
		)
	} else {
		// One render, reused for every sheet — matches the driver, which sends
		// the same bitmap for each copy when labels are off.
		const shared = await render(html, renderOpts)
		bitmaps = Array.from({ length: r.copies }, () => shared)
	}

	return {
		dots: r.dots,
		paper: r.paper,
		tailDots: r.tailDots,
		feedDots: r.feedDots,
		copyDelayMs: r.copyDelayMs,
		copies: bitmaps.map((bitmap, index) => ({
			index,
			label: r.useLabels ? r.labels[index] : `Copy ${index + 1}`,
			// Copy 1 shows now; later copies are revealed after their delay by
			// the caller, so the tear-off pause between copies is visible.
			visible: index === 0,
			delayMs: index * r.copyDelayMs,
			bitmap,
		})),
	}
}

/**
 * When more than one copy prints, copy N only leaves the printer after
 * (N-1) x copyDelayMs. This describes when each preview copy should be
 * revealed without a printer. Kept separate + pure so it is trivially
 * unit-testable.
 *
 * @returns {Array<{index:number,label:string,delayMs:number}>}
 */
export function buildCopyTimeline(copies, copyDelayMs) {
	const r = resolvePrintConfig({ copies, copyDelayMs }, {})
	return Array.from({ length: r.copies }, (_, i) => ({
		index: i,
		label: r.useLabels ? r.labels[i] : `Copy ${i + 1}`,
		delayMs: i * r.copyDelayMs,
	}))
}
