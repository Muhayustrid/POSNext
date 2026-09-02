import { describe, expect, it } from "vitest"

import { binarize, normalizeWidthPlan } from "./receipt_renderer"

describe("normalizeWidthPlan", () => {
	it("keeps an exact-width source untouched", () => {
		const plan = normalizeWidthPlan(384, 384)
		expect(plan).toEqual({ action: "none", targetWidth: 384, offsetX: 0 })
	})

	it("pads a narrower source centred", () => {
		const plan = normalizeWidthPlan(300, 384)
		expect(plan.action).toBe("pad")
		expect(plan.targetWidth).toBe(384)
		expect(plan.offsetX).toBe(Math.floor((384 - 300) / 2))
	})

	it("trims a wider source centred", () => {
		const plan = normalizeWidthPlan(576, 384)
		expect(plan.action).toBe("trim")
		expect(plan.targetWidth).toBe(384)
		expect(plan.offsetX).toBe(Math.floor((576 - 384) / 2))
	})
})

describe("binarize", () => {
	it("maps light pixels to white and dark to black", () => {
		const data = new Uint8ClampedArray([
			255,
			255,
			255,
			255, // white stays white
			10,
			10,
			10,
			255, // dark stays black
			127,
			127,
			127,
			255, // just below threshold -> black
			128,
			128,
			128,
			255, // at threshold -> white
		])
		binarize({ data, width: 4, height: 1 }, 128)
		expect(Array.from(data.slice(0, 3))).toEqual([255, 255, 255])
		expect(Array.from(data.slice(4, 7))).toEqual([0, 0, 0])
		expect(Array.from(data.slice(8, 11))).toEqual([0, 0, 0])
		expect(Array.from(data.slice(12, 15))).toEqual([255, 255, 255])
	})

	it("respects a custom threshold", () => {
		const data = new Uint8ClampedArray([200, 200, 200, 255])
		binarize({ data, width: 1, height: 1 }, 220)
		expect(Array.from(data.slice(0, 3))).toEqual([0, 0, 0])
	})
})
