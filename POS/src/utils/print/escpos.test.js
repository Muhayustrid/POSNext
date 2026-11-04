import { describe, expect, it, vi } from "vitest"

vi.mock("@/utils/logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			warn: vi.fn(),
			info: vi.fn(),
			error: vi.fn(),
		}),
	},
}))

import { buildEscPosPrint, escFeed, escInit } from "./escpos"

const image = (w, h) => ({
	width: w,
	height: h,
	data: new Uint8ClampedArray(w * h * 4).fill(255),
})

describe("buildEscPosPrint", () => {
	it("bitmap: one job with every copy and the crew slip", async () => {
		const render = vi.fn(async () => ({ imageData: image(8, 1) }))
		const out = await buildEscPosPrint(
			"receipt",
			{
				crewHTML: "crew",
				config: { copies: 2, crewSlipEnabled: true, cut: true },
			},
			{ render },
		)
		expect(out.textMode).toBe(false)
		expect(out.sheets).toBe(3)
		expect(Array.from(out.bytes.slice(0, 2))).toEqual(escInit())
		// init + 3 x (raster header 8 + 1 row + resolved feed + cut 3)
		const feed = escFeed(out.r.feedDots).length
		expect(out.bytes.length).toBe(2 + 3 * (8 + 1 + feed + 3))
	})

	it("text mode needs a doc; without one it renders the bitmap", async () => {
		const render = vi.fn(async () => ({ imageData: image(8, 1) }))
		const out = await buildEscPosPrint(
			"<html/>",
			{ config: { printMode: "text" } },
			{ render },
		)
		expect(out.textMode).toBe(false)
		expect(render).toHaveBeenCalledTimes(1)
	})

	it("text mode encodes the receipt doc without rendering", async () => {
		const render = vi.fn()
		const out = await buildEscPosPrint(
			"",
			{
				receiptDoc: { name: "SINV-1", company: "Toko", items: [] },
				config: { printMode: "text" },
			},
			{ render, loadLogo: async () => null },
		)
		expect(out.textMode).toBe(true)
		expect(render).not.toHaveBeenCalled()
		expect(String.fromCharCode(...out.bytes)).toContain("SINV-1")
	})

	it("rejects an empty bitmap print", async () => {
		await expect(buildEscPosPrint("", { config: {} })).rejects.toThrow()
	})
})
