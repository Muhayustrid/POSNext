/**
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from "vitest"

import { buildCopyTimeline, buildReceiptPreviewSet } from "./receipt_preview"
import { copyLabelFor } from "./receipt_layout"

describe("buildReceiptPreviewSet (preview = print, structural)", () => {
	it("renders through the shared resolver so preview cannot drift from print", async () => {
		const render = vi.fn(async () => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
		}))
		const set = await buildReceiptPreviewSet("<div/>", {
			device: { feedDots: 9999 },
			render,
		})
		// feeds are capped in the resolver, not in the renderer. If this fails
		// the preview and the driver disagree on limits.
		expect(set.feedDots).toBe(500)
	})

	it("1-copy preview has one bitmap and no delay", async () => {
		const render = vi.fn(async (_html, o) => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
		}))
		const set = await buildReceiptPreviewSet("<div/>", {
			copies: 1,
			render,
		})
		expect(set.copies).toHaveLength(1)
		expect(set.copies[0].label).toBe("Copy 1")
		expect(render).toHaveBeenCalledTimes(1)
	})

	it("2-copy preview labels exactly like the printer (customer then crew)", async () => {
		const render = vi.fn(async (html) => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
			html,
		}))
		const set = await buildReceiptPreviewSet("<body>hi</body>", {
			copies: 2,
			render,
		})
		expect(set.copies).toHaveLength(2)
		// Same ordering the driver uses: CUSTOMER COPY then CREW COPY.
		expect(set.copies[0].label).toBe("CUSTOMER COPY")
		expect(set.copies[1].label).toBe("CREW COPY")
		expect(render.mock.calls[0][0]).toContain("CUSTOMER COPY")
		expect(render.mock.calls[1][0]).toContain("CREW COPY")
	})

	it("lifts the effective fontScale through, like the real render block", async () => {
		const seen = []
		const render = vi.fn(async (_html, o) => {
			seen.push(o.fontScale)
			return { dataURL: "data:,", width: 384, height: 100 }
		})
		await buildReceiptPreviewSet("<div/>", {
			device: {},
			server: { paper: "58mm", fontScale: 140 },
			render,
		})
		expect(seen[0]).toBe(140)
	})

	it("lifts the effective tailDots through, like the real render block", async () => {
		const seen = []
		const render = vi.fn(async (_html, o) => {
			seen.push(o.tailDots)
			return { dataURL: "data:,", width: 384, height: 100 }
		})
		await buildReceiptPreviewSet("<div/>", {
			device: {},
			server: { paper: "58mm", tailDots: 32 },
			render,
		})
		expect(seen[0]).toBe(32)
	})

	it("single shared bitmap reused across rows when labels are off", async () => {
		const render = vi.fn(async () => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
		}))
		const set = await buildReceiptPreviewSet("<div/>", {
			device: { copies: 2, copyLabels: false },
			render,
		})
		// The renderer is called once (same bitmap); the preview still shows 2 rows
		// because that is what physically leaves the printer.
		expect(render).toHaveBeenCalledTimes(1)
		expect(set.copies).toHaveLength(2)
		expect(set.copies[0].bitmap).toBe(set.copies[1].bitmap)
	})
})

describe("buildCopyTimeline", () => {
	it("reveals copy 1 immediately and copy 2 after the configured delay", () => {
		const tl = buildCopyTimeline(2, 800)
		expect(tl).toHaveLength(2)
		expect(tl[0]).toEqual({ index: 0, label: "CUSTOMER COPY", delayMs: 0 })
		expect(tl[1]).toEqual({ index: 1, label: "CREW COPY", delayMs: 800 })
	})

	it("single copy never shows a crew delay", () => {
		expect(buildCopyTimeline(1, 800)).toHaveLength(1)
		expect(buildCopyTimeline(1, 800)[0].delayMs).toBe(0)
	})
})
