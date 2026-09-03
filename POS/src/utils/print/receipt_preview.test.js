/**
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from "vitest"

import { buildCopyTimeline, buildReceiptPreviewSet } from "./receipt_preview"

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

	it("2-copy preview captions the sheets like the printer prints them", async () => {
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
		// Nothing is printed above the receipt any more; the caption is UI-only
		// so the operator knows which sheet is which.
		expect(set.copies[0].label).toBe("Copy 1")
		expect(set.copies[1].label).toBe("Copy 2")
		// And the rendered bitmap is the receipt, unlabelled.
		expect(render).toHaveBeenCalledTimes(1)
		expect(render.mock.calls[0][0]).toBe("<body>hi</body>")
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

	it("single shared bitmap reused across every non-crew row", async () => {
		const render = vi.fn(async () => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
		}))
		const set = await buildReceiptPreviewSet("<div/>", {
			device: { copies: 2 },
			render,
		})
		// The renderer is called once (same bitmap); the preview still shows 2 rows
		// because that is what physically leaves the printer.
		expect(render).toHaveBeenCalledTimes(1)
		expect(set.copies).toHaveLength(2)
		expect(set.copies[0].bitmap).toBe(set.copies[1].bitmap)
	})
})

describe("buildReceiptPreviewSet (crew slip as copy 2)", () => {
	const crewHTML = '<div class="crew">crew</div>'
	const render = () =>
		vi.fn(async (html) => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
			html,
		}))

	it("renders crewHTML for copy 2 exactly like the driver does", async () => {
		const r = render()
		const set = await buildReceiptPreviewSet("<body>receipt</body>", {
			copies: 2,
			crewHTML,
			render: r,
		})
		// Same branch the driver takes, so the preview cannot claim a crew slip
		// the printer will not produce (or the other way round).
		expect(r.mock.calls[0][0]).toBe("<body>receipt</body>")
		expect(r.mock.calls[1][0]).toBe(crewHTML)
		// UI-only caption: nothing is printed above either sheet.
		expect(set.copies[1].label).toBe("CREW COPY")
		expect(set.copies[0].label).toBe("Copy 1")
	})

	it("keeps the main receipt for copies 1 and 3", async () => {
		const r = render()
		const set = await buildReceiptPreviewSet("<body>receipt</body>", {
			copies: 3,
			crewHTML,
			render: r,
		})
		// Two renders only: the receipt once (copies 1 and 3 share it) and the
		// slip for copy 2 — same as the driver.
		const sources = r.mock.calls.map((call) => call[0])
		expect(sources).toEqual(["<body>receipt</body>", crewHTML])
		expect(set.copies[0].bitmap).toBe(set.copies[2].bitmap)
		expect(set.copies[1].bitmap).not.toBe(set.copies[0].bitmap)
		expect(set.copies[2].label).toBe("Copy 3")
	})

	it("ignores crewHTML for a one-copy preview", async () => {
		const r = render()
		const set = await buildReceiptPreviewSet("<body>receipt</body>", {
			copies: 1,
			crewHTML,
			render: r,
		})
		expect(r).toHaveBeenCalledTimes(1)
		expect(r.mock.calls[0][0]).toBe("<body>receipt</body>")
		expect(set.copies).toHaveLength(1)
		expect(set.copies[0].label).toBe("Copy 1")
	})

	it("previews the crew slip at the crew font scale, the receipt at its own", async () => {
		const seen = []
		const r = vi.fn(async (_html, o) => {
			seen.push(o.fontScale)
			return { dataURL: "data:,", width: 384, height: 100 }
		})
		await buildReceiptPreviewSet("<body>receipt</body>", {
			device: { fontScale: 110, crewFontScale: 90 },
			copies: 2,
			crewHTML,
			render: r,
		})
		// What you preview is what prints: the slip's knob drives its bitmap.
		expect(seen).toEqual([110, 90])
	})
})

describe("buildCopyTimeline", () => {
	it("reveals copy 1 immediately and copy 2 after the configured delay", () => {
		const tl = buildCopyTimeline(2, 800)
		expect(tl).toHaveLength(2)
		expect(tl[0]).toEqual({ index: 0, label: "Copy 1", delayMs: 0 })
		expect(tl[1]).toEqual({ index: 1, label: "Copy 2", delayMs: 800 })
	})

	it("single copy never shows a crew delay", () => {
		expect(buildCopyTimeline(1, 800)).toHaveLength(1)
		expect(buildCopyTimeline(1, 800)[0].delayMs).toBe(0)
	})
})
