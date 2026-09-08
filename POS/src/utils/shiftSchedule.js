/**
 * Shift schedule helpers.
 *
 * Pure functions — consumed by the posShift store (reactive tick), the
 * useShiftSchedule composable, and vitest. All datetimes are naive server
 * timezone strings; they are parsed identically on both sides of the anchor
 * so the browser timezone cancels out (same trick as the shift-duration
 * timer in stores/posShift.js).
 */

export const MINUTE_MS = 60_000

/**
 * Parse a naive server datetime string ("YYYY-MM-DD HH:mm:ss[.ffffff]").
 * Uses the "T" separator form so Safari parses it too. Returns ms or null.
 */
export function parseServerDatetime(value) {
	if (!value) return null
	const ms = new Date(String(value).replace(" ", "T").split(".")[0]).getTime()
	return Number.isFinite(ms) ? ms : null
}

/**
 * Compute the schedule status of an open shift from its snapshot.
 *
 * @param {Object} shift POS Opening Shift doc (pos_schedule_* snapshot fields)
 * @param {Object} clock { serverNowMs, receivedAtMs, localNowMs }
 * @returns {Object|null} { enabled, enforce, warning, expired, minutesLeft, deadlineMs }
 */
export function computeScheduleStatus(shift, clock) {
	if (!shift || !Number(shift.pos_schedule_enabled)) return null
	if (!clock || !clock.serverNowMs || !clock.receivedAtMs) return null

	const deadlineMs = parseServerDatetime(shift.pos_schedule_deadline)
	if (deadlineMs == null) {
		return {
			enabled: true,
			enforce: !!Number(shift.pos_schedule_enforce_closing),
			warning: false,
			expired: false,
			minutesLeft: null,
			deadlineMs: null,
		}
	}

	// Server-anchored clock: server_now at fetch time + locally elapsed ms
	const nowMs = clock.serverNowMs + Math.max(0, clock.localNowMs - clock.receivedAtMs)
	const warningMinutes = Number(shift.pos_schedule_warning_minutes) || 0
	const expired = nowMs >= deadlineMs

	return {
		enabled: true,
		enforce: !!Number(shift.pos_schedule_enforce_closing),
		deadlineMs,
		expired,
		warning: warningMinutes > 0 && !expired && nowMs >= deadlineMs - warningMinutes * MINUTE_MS,
		minutesLeft: expired ? 0 : Math.ceil((deadlineMs - nowMs) / MINUTE_MS),
	}
}

/** True when sales / payments / refunds must be refused right now. */
export function isScheduleBlocking(status) {
	return !!(status && status.enforce && status.expired)
}
