/**
 * iMin thermal paper geometry.
 *
 * Hardware facts (official iMin documentation, version-independent):
 *   205 DPI = 8 dots/mm.
 *   58mm paper -> 48mm effective print width -> 384 dots.
 *   80mm paper -> 72mm effective print width -> 576 dots.
 * The full paper width is never the print area; the content budget is dots.
 */

export const DOTS_PER_MM = 8
export const MAX_DOTS = 576

export const PAPER_PROFILES = {
	"58mm": { label: "58mm", paperMm: 58, effectiveMm: 48, dots: 384 },
	"80mm": { label: "80mm", paperMm: 80, effectiveMm: 72, dots: 576 },
}

/**
 * Resolve a paper setting to a dot count.
 *
 * @param {string} paper - "58mm" | "80mm" | "custom"
 * @param {number} [customDots] - required when paper is "custom"; snapped to a
 *   multiple of 8 and clamped to MAX_DOTS.
 * @returns {number} dot count, always a multiple of 8 in [8, 576].
 */
export function dotsForPaper(paper, customDots) {
	if (paper === "custom") {
		const raw = Number(customDots ?? 384)
		if (!Number.isFinite(raw) || raw <= 0) {
			throw new Error(`Invalid custom dot count: ${customDots}`)
		}
		const snapped = Math.min(MAX_DOTS, Math.floor(raw / 8) * 8)
		if (snapped < 8)
			throw new Error(`Custom dot count too small: ${customDots}`)
		return snapped
	}

	if (!Object.hasOwn(PAPER_PROFILES, paper))
		throw new Error(`Unknown paper profile: ${paper}`)
	return PAPER_PROFILES[paper].dots
}
