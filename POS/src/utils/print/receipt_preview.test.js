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

	it("lifts the effective lineSpacing through, like the real render block", async () => {
		const seen = []
		const render = vi.fn(async (_html, o) => {
			seen.push(o.lineSpacing)
			return { dataURL: "data:,", width: 384, height: 100 }
		})
		await buildReceiptPreviewSet("<body>receipt</body>", {
			device: { lineSpacing: 70, crewSlipEnabled: true },
			copies: 2,
			crewHTML: '<div class="crew">crew</div>',
			render,
		})
		// One knob for everything direct printed: the slip's bitmap inherits it.
		expect(seen).toEqual([70, 70])
	})

	it("lifts the effective sideMarginDots through to every bitmap", async () => {
		const seen = []
		const render = vi.fn(async (_html, o) => {
			seen.push(o.sideMarginDots)
			return { dataURL: "data:,", width: 384, height: 100 }
		})
		await buildReceiptPreviewSet("<body>receipt</body>", {
			device: { sideMarginDots: 8, crewSlipEnabled: true },
			copies: 2,
			crewHTML: '<div class="crew">crew</div>',
			render,
		})
		// A property of the paper, so the slip shares it too.
		expect(seen).toEqual([8, 8])
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

describe("buildReceiptPreviewSet (crew slip as the last sheet)", () => {
	const crewHTML = '<div class="crew">crew</div>'
	const render = () =>
		vi.fn(async (html) => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
			html,
		}))

	it("appends the crew slip after a single copy, exactly like the driver", async () => {
		const r = render()
		const set = await buildReceiptPreviewSet("<body>receipt</body>", {
			device: { crewSlipEnabled: true, crewFontScale: 90 },
			copies: 1,
			crewHTML,
			render: r,
		})
		// Two sheets even at one copy: the slip is its own switch, not "copy 2".
		expect(set.copies.map((c) => c.label)).toEqual(["Copy 1", "CREW COPY"])
		expect(r.mock.calls[0][0]).toBe("<body>receipt</body>")
		// The slip's bitmap is rendered from crewHTML at its own font knob,
		// the same call the driver makes.
		expect(r.mock.calls[1][0]).toBe(crewHTML)
		expect(r.mock.calls[1][1]).toMatchObject({ fontScale: 90 })
	})

	it("prints plain copies only when the crew switch is off", async () => {
		const r = render()
		const set = await buildReceiptPreviewSet("<body>receipt</body>", {
			copies: 2,
			crewHTML,
			render: r,
		})
		// No crewSlipEnabled -> no slip render at all, just identical receipts.
		expect(r).toHaveBeenCalledTimes(1)
		expect(r.mock.calls[0][0]).toBe("<body>receipt</body>")
		expect(set.copies.map((c) => c.label)).toEqual(["Copy 1", "Copy 2"])
	})

	it("appends the crew slip after all N copies, with the summed delay", async () => {
		const r = render()
		const set = await buildReceiptPreviewSet("<body>receipt</body>", {
			device: { crewSlipEnabled: true, copyDelayMs: 800 },
			copies: 2,
			crewHTML,
			render: r,
		})
		expect(set.copies.map((c) => c.label)).toEqual([
			"Copy 1",
			"Copy 2",
			"CREW COPY",
		])
		// The two receipts share one bitmap; the slip is its own.
		expect(set.copies[0].bitmap).toBe(set.copies[1].bitmap)
		expect(set.copies[2].bitmap).not.toBe(set.copies[0].bitmap)
		// The slip leaves the printer after both copies' tear-off pauses.
		expect(set.copies.map((c) => c.delayMs)).toEqual([0, 800, 1600])
	})

	it("previews the crew slip at the crew font scale, the receipt at its own", async () => {
		const seen = []
		const r = vi.fn(async (_html, o) => {
			seen.push(o.fontScale)
			return { dataURL: "data:,", width: 384, height: 100 }
		})
		await buildReceiptPreviewSet("<body>receipt</body>", {
			device: { fontScale: 110, crewFontScale: 90, crewSlipEnabled: true },
			copies: 2,
			crewHTML,
			render: r,
		})
		// What you preview is what prints: the slip's knob drives its bitmap.
		expect(seen).toEqual([110, 90])
	})
})

describe("buildReceiptPreviewSet (eod lane)", () => {
	it("resolves the eod knobs through the same resolver the driver uses", async () => {
		const seen = []
		const render = vi.fn(async (_html, o) => {
			seen.push(o.fontScale)
			return { dataURL: "data:,", width: 384, height: 100 }
		})
		const set = await buildReceiptPreviewSet("<div/>", {
			device: { eodCopies: 2, eodFontScale: 120 },
			kind: "eod",
			render,
		})
		expect(set.copies).toHaveLength(2)
		expect(seen).toEqual([120])
	})

	it("never renders crewHTML for an eod preview — there is no crew copy", async () => {
		const render = vi.fn(async (html) => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
			html,
		}))
		const set = await buildReceiptPreviewSet("<body>receipt</body>", {
			device: { crewSlipEnabled: true },
			copies: 2,
			crewHTML: '<div class="crew">crew</div>',
			kind: "eod",
			render,
		})
		// Even with the switch on, the eod lane resolves crewSlipEnabled to off.
		expect(render).toHaveBeenCalledTimes(1)
		expect(render.mock.calls[0][0]).toBe("<body>receipt</body>")
		expect(set.copies.map((c) => c.label)).toEqual(["Copy 1", "Copy 2"])
	})

	it("applies the explicit copies override to the eod copy count", async () => {
		const render = vi.fn(async () => ({
			dataURL: "data:,",
			width: 384,
			height: 100,
		}))
		const set = await buildReceiptPreviewSet("<div/>", {
			device: { eodCopies: 2 },
			copies: 1,
			kind: "eod",
			render,
		})
		expect(set.copies).toHaveLength(1)
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
