import { describe, expect, it } from "vitest"

import { PAPER_PROFILES, dotsForPaper } from "./paper"

describe("dotsForPaper", () => {
	it("maps 58mm to 384 dots", () => {
		expect(dotsForPaper("58mm")).toBe(384)
	})

	it("maps 80mm to 576 dots", () => {
		expect(dotsForPaper("80mm")).toBe(576)
	})

	it("uses the custom dot count when paper is custom", () => {
		expect(dotsForPaper("custom", 416)).toBe(416)
	})

	it("defaults custom to 384 when no custom value given", () => {
		expect(dotsForPaper("custom")).toBe(384)
	})

	it("snaps a custom value down to a multiple of 8", () => {
		expect(dotsForPaper("custom", 420)).toBe(416)
	})

	it("clamps custom to the hardware maximum of 576", () => {
		expect(dotsForPaper("custom", 999)).toBe(576)
	})

	it("rejects a non-positive custom value", () => {
		expect(() => dotsForPaper("custom", 0)).toThrow()
	})

	it("rejects an unknown paper key", () => {
		expect(() => dotsForPaper("60mm")).toThrow()
	})
})

describe("PAPER_PROFILES", () => {
	it("exposes the two hardware profiles with correct dots", () => {
		expect(PAPER_PROFILES["58mm"].dots).toBe(384)
		expect(PAPER_PROFILES["58mm"].effectiveMm).toBe(48)
		expect(PAPER_PROFILES["80mm"].dots).toBe(576)
		expect(PAPER_PROFILES["80mm"].effectiveMm).toBe(72)
	})
})
