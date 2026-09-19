/**
 * Shift duration formatting for the header clock.
 *
 * Pure function — consumed by the posShift store's 1s tick and vitest.
 * Uses the global __() installed by the translation plugin (same pattern
 * as utils/errorHandler.js).
 */

const DAY_MS = 86_400_000
const HOUR_MS = 3_600_000
const MINUTE_MS = 60_000

/**
 * Format an elapsed duration.
 * < 24h → "HH:MM:SS" (zero-padded); >= 24h → "{N} Day(s) HH:MM:SS".
 * Returns "" for negative or non-finite input (clock-skew guard, same
 * behavior as the store's diff < 0 check).
 *
 * @param {number} diffMs elapsed milliseconds
 * @returns {string}
 */
export function formatShiftDuration(diffMs) {
	if (!Number.isFinite(diffMs) || diffMs < 0) return ""

	const days = Math.floor(diffMs / DAY_MS)
	const hours = Math.floor((diffMs % DAY_MS) / HOUR_MS)
	const minutes = Math.floor((diffMs % HOUR_MS) / MINUTE_MS)
	const seconds = Math.floor((diffMs % MINUTE_MS) / 1000)

	const pad = (n) => String(n).padStart(2, "0")
	const clock = `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`
	if (days === 0) return clock

	const dayLabel = days === 1 ? __("Day") : __("Days")
	return `${days} ${dayLabel} ${clock}`
}
