/**
 * Print preview for the Direct Print page.
 *
 * The point of a preview is that it *is* the print. So this mirrors the iMin
 * driver's render block exactly — same `resolvePrintConfig`, same
 * `renderHTMLToBitmap`, same tailDots/paper/customDots, and the same two
 * bitmaps: one for every plain copy, plus the crew slip (at its own font
 * scale) as a final sheet whenever the crew switch is on. There is no second
 * renderer; if the two ever disagree, the preview lies. Keeping the copy loop
 * here (rather than in the Vue page) is what makes "preview = print"
 * structural.
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
 * @param {string} [opts.crewHTML] - the crew slip printed as the final sheet,
 *   same contract as the driver's opts.crewHTML.
 * @param {string} [opts.kind] - print lane ("receipt" or "eod"), forwarded to
 *   the resolver so an EOD preview shows the eod knobs. An EOD print has no
 *   crew slip, so crewHTML is ignored entirely for that lane.
 * @param {(html, o) => Promise<{dataURL,width,height}>} [opts.render]
 *   injected for tests; defaults to the real bitmap renderer.
 * @returns {Promise<{dots:number, paper:string, tailDots:number,
 *   feedDots:number, copyDelayMs:number,
 *   copies:Array<{index:number,label:string,visible:boolean,
 *     delayMs:number,bitmap:{dataURL,width,height}}>}>}
 */
export async function buildReceiptPreviewSet(html, opts = {}) {
	const eod = opts.kind === "eod"
	const device = { ...(opts.device || {}) }
	// The override lands on the key the lane actually reads, or it would be
	// silently dropped by the resolver's eod branch.
	if (opts.copies != null) device[eod ? "eodCopies" : "copies"] = opts.copies
	const r = resolvePrintConfig(device, opts.server || {}, { kind: opts.kind })
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
		lineSpacing: r.lineSpacing,
		sideMarginDots: r.sideMarginDots,
	}
	// Mirrors imin_client: the crew slip is a separate sheet appended after all
	// N copies, gated only by the crew switch, and never exists on the EOD lane.
	const crewApplies = Boolean(opts.crewHTML) && !eod && r.crewSlipEnabled
	const [shared, crewBitmap] = await Promise.all([
		render(html, renderOpts),
		crewApplies
			? render(opts.crewHTML, { ...renderOpts, fontScale: r.crewFontScale })
			: null,
	])
	const bitmaps = Array.from({ length: r.copies }, () => shared)
	if (crewApplies) bitmaps.push(crewBitmap)

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
			label: index < r.copies ? `Copy ${index + 1}` : "CREW COPY",
			// Copy 1 shows now; later sheets are revealed after their delay by
			// the caller, so the tear-off pause between copies is visible.
			visible: index === 0,
			// The crew row sits at index N, so this is naturally N * copyDelayMs
			// — the same wait the driver inserts before the slip prints.
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
