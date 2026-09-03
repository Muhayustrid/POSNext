/**
 * Print preview for the Direct Print page.
 *
 * The point of a preview is that it *is* the print. So this mirrors the iMin
 * driver's render block exactly — same `resolvePrintConfig`, same
 * `renderHTMLToBitmap`, same tailDots/paper/customDots, and the same two
 * bitmaps: one for every plain copy, the crew slip (at its own font scale) for
 * copy 2. There is no second renderer; if the two ever disagree, the preview
 * lies. Keeping the copy loop here (rather than in the Vue page) is what makes
 * "preview = print" structural.
 *
 * Nothing here touches the printer, so it works on a laptop with no iMin
 * attached. What it cannot show is the physical part: where the tear bar
 * actually falls relative to the last line. That still needs the device.
 */
import { renderHTMLToBitmap } from "./receipt_renderer"
import { resolvePrintConfig } from "./receipt_layout"

/**
 * Render the full copy set exactly as the driver would.
 *
 * @param {string} html - receipt document HTML.
 * @param {object} [opts]
 * @param {object} [opts.device] - per-device localStorage config.
 * @param {object} [opts.server] - transport/POS Settings config.
 * @param {number} [opts.copies] - override the configured copy count
 *   (the preview buttons pass 1 or 2 explicitly).
 * @param {string} [opts.crewHTML] - the crew slip that replaces copy 2, same
 *   contract as the driver's opts.crewHTML.
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

	// Same render the driver makes for every plain copy. Nothing is printed
	// above a copy any more, so the sheets are identical and one bitmap is
	// reused; the preview still shows `r.copies` rows because that is what
	// physically leaves the printer.
	const renderOpts = {
		paper: r.paper,
		customDots: r.customDots,
		tailDots: r.tailDots,
		fontScale: r.fontScale,
	}
	// Mirrors imin_client: a crew slip only replaces copy 2 of a multi-copy job.
	const crewApplies = Boolean(opts.crewHTML) && r.copies > 1
	const [shared, crewBitmap] = await Promise.all([
		render(html, renderOpts),
		crewApplies
			? render(opts.crewHTML, { ...renderOpts, fontScale: r.crewFontScale })
			: null,
	])
	const bitmaps = Array.from({ length: r.copies }, (_, idx) =>
		crewApplies && idx === 1 ? crewBitmap : shared,
	)

	return {
		dots: r.dots,
		paper: r.paper,
		tailDots: r.tailDots,
		feedDots: r.feedDots,
		copyDelayMs: r.copyDelayMs,
		copies: bitmaps.map((bitmap, index) => ({
			index,
			// Screen-only caption so the operator knows which sheet is which —
			// the paper itself carries no banner.
			label: crewApplies && index === 1 ? "CREW COPY" : `Copy ${index + 1}`,
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
		label: `Copy ${i + 1}`,
		delayMs: i * r.copyDelayMs,
	}))
}
