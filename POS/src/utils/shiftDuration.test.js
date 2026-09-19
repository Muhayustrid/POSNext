import { afterEach, describe, expect, it } from "vitest"
import { formatShiftDuration } from "./shiftDuration"

const H = 3_600_000
const M = 60_000
const S = 1000
const DAY = 24 * H

const passthrough = (msg) => msg

afterEach(() => {
	globalThis.__ = passthrough
})

describe("formatShiftDuration", () => {
	it("formats sub-second and zero durations zero-padded", () => {
		expect(formatShiftDuration(0)).toBe("00:00:00")
		expect(formatShiftDuration(500)).toBe("00:00:00")
	})

	it("formats seconds-only durations", () => {
		expect(formatShiftDuration(5 * S)).toBe("00:00:05")
		expect(formatShiftDuration(59 * S)).toBe("00:00:59")
	})

	it("zero-pads hours, minutes and seconds", () => {
		expect(formatShiftDuration(4 * H + 5 * M + 6 * S)).toBe("04:05:06")
	})

	it("formats exactly 24h with a singular Day label", () => {
		expect(formatShiftDuration(DAY)).toBe("1 Day 00:00:00")
	})

	it("formats one day plus an odd offset", () => {
		expect(formatShiftDuration(DAY + 12 * H + 9 * M + 43 * S)).toBe("1 Day 12:09:43")
	})

	it("formats multiple days with a plural Days label", () => {
		expect(formatShiftDuration(2 * DAY + 4 * H + 12 * M + 21 * S)).toBe("2 Days 04:12:21")
	})

	it("returns empty for negative or invalid input", () => {
		expect(formatShiftDuration(-1)).toBe("")
		expect(formatShiftDuration(NaN)).toBe("")
		expect(formatShiftDuration(undefined)).toBe("")
		expect(formatShiftDuration(Number.POSITIVE_INFINITY)).toBe("")
	})

	it("translates the Day/Days labels through __()", () => {
		globalThis.__ = (msg) => (msg === "Day" ? "يوم" : msg === "Days" ? "أيام" : msg)
		expect(formatShiftDuration(DAY)).toBe("1 يوم 00:00:00")
		expect(formatShiftDuration(2 * DAY)).toBe("2 أيام 00:00:00")
	})
})
