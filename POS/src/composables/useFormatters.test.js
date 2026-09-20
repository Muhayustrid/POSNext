import { describe, expect, it } from "vitest"

import { useFormatters } from "./useFormatters"

// Un-mocked: the composable is pure string logic. __() runs untranslated
// (no dictionary loaded), so AM/PM come through as-is.
const { formatTime, formatDate, formatPercentage } = useFormatters()

describe("formatTime with datetime strings", () => {
	it("strips the date prefix and renders the time of day", () => {
		expect(formatTime("2026-09-20 14:23:00")).toBe("2:23 PM")
		expect(formatTime("2026-09-20 09:05:00")).toBe("9:05 AM")
	})

	it("keeps bare time strings working", () => {
		expect(formatTime("14:23:00")).toBe("2:23 PM")
		expect(formatTime("09:05:00")).toBe("9:05 AM")
	})

	it("handles fractional seconds after the date prefix", () => {
		expect(formatTime("2026-09-20 15:31:22.975239")).toBe("3:31 PM")
	})

	it("returns empty for empty input", () => {
		expect(formatTime("")).toBe("")
	})
})

describe("formatDate with datetime strings", () => {
	it("renders date-only input as DD/MM/YY", () => {
		expect(formatDate("2026-09-20")).toBe("20/09/26")
	})

	it("returns the same date for datetime input", () => {
		expect(formatDate("2026-09-20 14:23:00")).toBe("20/09/26")
	})

	it("returns empty for empty input", () => {
		expect(formatDate("")).toBe("")
	})
})

describe("remaining helpers", () => {
	it("formats percentages", () => {
		expect(formatPercentage(12.5)).toBe("12.5%")
	})
})
