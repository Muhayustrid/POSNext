import { describe, expect, it } from "vitest"
import { computeScheduleStatus, isScheduleBlocking, parseServerDatetime } from "./shiftSchedule"

// Shift opened "2026-09-07 21:00:00" server time; tests anchor the local
// clock 5 seconds after the server payload was received.
const SERVER_NOW = parseServerDatetime("2026-09-07 21:00:00")
const RECEIVED_AT = 1_000_000
const clock = (localNowMs) => ({ serverNowMs: SERVER_NOW, receivedAtMs: RECEIVED_AT, localNowMs })

const shift = (overrides = {}) => ({
	pos_schedule_enabled: 1,
	pos_schedule_enforce_closing: 1,
	pos_schedule_warning_minutes: 15,
	pos_schedule_deadline: "2026-09-08 05:00:00",
	...overrides,
})

describe("parseServerDatetime", () => {
	it("parses naive server datetimes (space separator, fractional seconds)", () => {
		expect(parseServerDatetime("2026-09-07 21:00:00.123456")).toBe(
			new Date("2026-09-07T21:00:00").getTime()
		)
	})

	it("returns null for empty or garbage values", () => {
		expect(parseServerDatetime("")).toBeNull()
		expect(parseServerDatetime(null)).toBeNull()
		expect(parseServerDatetime("not a date")).toBeNull()
	})
})

describe("computeScheduleStatus", () => {
	it("is null when the schedule is disabled", () => {
		expect(computeScheduleStatus(shift({ pos_schedule_enabled: 0 }), clock(RECEIVED_AT))).toBeNull()
		expect(computeScheduleStatus(null, clock(RECEIVED_AT))).toBeNull()
	})

	it("is null without a server clock anchor", () => {
		expect(
			computeScheduleStatus(shift(), { serverNowMs: 0, receivedAtMs: RECEIVED_AT, localNowMs: RECEIVED_AT })
		).toBeNull()
	})

	it("counts down and warns inside the warning window (overnight deadline)", () => {
		// Deadline is 480 min after the anchor; 10 min left is inside the
		// 15-minute warning window
		const before = clock(RECEIVED_AT + 470 * 60_000)
		const status = computeScheduleStatus(shift(), before)
		expect(status.expired).toBe(false)
		expect(status.warning).toBe(true)
		expect(status.enforce).toBe(true)
		expect(status.minutesLeft).toBe(10)
	})

	it("does not warn outside the warning window", () => {
		const early = clock(RECEIVED_AT + 2 * 60 * 60_000)
		const status = computeScheduleStatus(shift(), early)
		expect(status.warning).toBe(false)
		expect(status.expired).toBe(false)
		expect(status.minutesLeft).toBe(360)
	})

	it("marks the shift expired after the deadline regardless of the client's absolute clock", () => {
		// status is computed from server_now + elapsed-since-receipt, so a
		// client clock that is off by any fixed offset cannot delay it
		const after = clock(RECEIVED_AT + 9 * 60 * 60_000)
		const status = computeScheduleStatus(shift(), after)
		expect(status.expired).toBe(true)
		expect(status.warning).toBe(false)
		expect(status.minutesLeft).toBe(0)
		expect(isScheduleBlocking(status)).toBe(true)
	})

	it("does not block when mandatory closing is off", () => {
		const after = clock(RECEIVED_AT + 9 * 60 * 60_000)
		const status = computeScheduleStatus(shift({ pos_schedule_enforce_closing: 0 }), after)
		expect(status.expired).toBe(true)
		expect(isScheduleBlocking(status)).toBe(false)
	})

	it("falls back to enforce-only status when the deadline snapshot is missing", () => {
		const status = computeScheduleStatus(shift({ pos_schedule_deadline: null }), clock(RECEIVED_AT))
		expect(status.enabled).toBe(true)
		expect(status.expired).toBe(false)
	})
})

describe("isScheduleBlocking", () => {
	it("blocks only when enforcing and expired", () => {
		expect(isScheduleBlocking({ enforce: true, expired: true })).toBe(true)
		expect(isScheduleBlocking({ enforce: true, expired: false })).toBe(false)
		expect(isScheduleBlocking({ enforce: false, expired: true })).toBe(false)
		expect(isScheduleBlocking(null)).toBe(false)
	})
})
