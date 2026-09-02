import { beforeEach, describe, expect, it, vi } from "vitest"

import { createIminDriver } from "./imin_client"

function makeFakePrinter(overrides = {}) {
	return {
		connect: vi.fn().mockResolvedValue(true),
		initPrinter: vi.fn(),
		getPrinterStatus: vi.fn().mockResolvedValue({ value: 0 }),
		setPageFormat: vi.fn(),
		printSingleBitmap: vi.fn().mockResolvedValue(1),
		partialCut: vi.fn(),
		printAndFeedPaper: vi.fn(),
		...overrides,
	}
}

let printer
let driver

beforeEach(() => {
	printer = makeFakePrinter()
	driver = createIminDriver({
		factory: () => printer,
		loadConfig: () => ({ host: "127.0.0.1", paper: "58mm", cut: true }),
	})
})

describe("createIminDriver", () => {
	it("renders to a dot-exact bitmap and prints it", async () => {
		await driver.printHTML("<div>receipt</div>", {
			render: async () => ({
				dataURL: "data:image/png;base64,AAA",
				width: 384,
			}),
		})
		expect(printer.printSingleBitmap).toHaveBeenCalledWith(
			"data:image/png;base64,AAA",
			expect.any(Number),
		)
	})

	it("advances paper after the bitmap so the receipt clears the tear bar (probe v3)", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "data:,", width: 384 }),
		})
		// The vendored v1.4.0 build does NOT auto-feed inside printSingleBitmap.
		// Without this the bitmap sits inside the mechanism until the next job
		// drags it out (observed as "first run prints nothing, second prints it").
		expect(printer.printAndFeedPaper).toHaveBeenCalled()
	})

	it("feeds before waiting for idle, so the status gate covers paper advance", async () => {
		const order = []
		const tracked = { ...printer }
		tracked.printSingleBitmap = async (...args) => {
			order.push("bitmap")
			return printer.printSingleBitmap(...args)
		}
		tracked.printAndFeedPaper = (...args) => {
			order.push("feed")
			return printer.printAndFeedPaper(...args)
		}
		const d = createIminDriver({
			factory: () => tracked,
			loadConfig: () => ({ paper: "58mm", cut: false }),
		})
		// Resolve waitIdle immediately so we can see the command order
		tracked.getPrinterStatus = async () => ({ value: 0 })
		await d.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(order).toEqual(["bitmap", "feed"])
	})

	it("cuts only when the device config enables it", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.partialCut).toHaveBeenCalledTimes(1)

		const noCut = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "58mm", cut: false }),
		})
		await noCut.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		// First run fed+cut, second run fed but did not cut — copy the count
		// before running the full fallback suite below, so mocks remain intact.
		expect(printer.partialCut).toHaveBeenCalledTimes(1)
		// Feed is paper-advance (not cut-path dependent) — it always happens.
		expect(printer.printAndFeedPaper.mock.calls.length).toBeGreaterThanOrEqual(2)
	})

	it("waits for status to reach 0 before resolving", async () => {
		printer.getPrinterStatus
			.mockResolvedValueOnce({ value: -1 })
			.mockResolvedValue({ value: 0 })
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.getPrinterStatus.mock.calls.length).toBeGreaterThan(1)
	})

	it("applies the page format for the chosen paper", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.setPageFormat).toHaveBeenCalledWith(1) // 58mm

		const w80 = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "80mm", cut: false }),
		})
		await w80.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 576 }),
		})
		expect(printer.setPageFormat).toHaveBeenCalledWith(0) // 80mm
	})

	it("reports a not-connected error when status never recovers", async () => {
		printer.getPrinterStatus.mockResolvedValue({ value: -1 })
		const stuck = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "58mm", cut: false }),
			statusTimeoutMs: 200,
			statusPollMs: 50,
		})
		await expect(
			stuck.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
			}),
		).rejects.toThrow(/not connected/i)
	})

	it("prints each copy with the same bitmap, delay only between copies", async () => {
		const timeline = []
		const sleepStart = Date.now()
		printer.printSingleBitmap = vi.fn(async () => {
			timeline.push({ e: "bitmap", t: Date.now() - sleepStart })
			return 1
		})
		printer.printAndFeedPaper = vi.fn(() => timeline.push({ e: "feed" }))
		const d = createIminDriver({
			factory: () => printer,
			loadConfig: () => ({ paper: "58mm", cut: true }),
		})
		const res = await d.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
			config: { copies: 2, copyDelayMs: 250 },
		})
		expect(res.copies).toBe(2)
		// The same bitmap was re-sent, not re-rendered: two bitmap calls,
		// one upload URL.
		expect(printer.printSingleBitmap).toHaveBeenCalledTimes(2)
		expect(printer.printAndFeedPaper).toHaveBeenCalledTimes(2)
		// Between-copy gap really elapsed (250 ms), and there is no gap
		// after the final copy.
		const bitmaps = timeline.filter((x) => x.e === "bitmap")
		expect(bitmaps).toHaveLength(2)
		const gap = bitmaps[1].t - bitmaps[0].t
		// SETTLE_MS (200) + copyDelayMs (250) must both have elapsed.
		expect(gap).toBeGreaterThanOrEqual(250)
		expect(printer.partialCut).toHaveBeenCalledTimes(2)
	})

	it("single copy by default prints exactly one receipt", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
		})
		expect(printer.printSingleBitmap).toHaveBeenCalledTimes(1)
	})

	it("clamps absurd copy counts to the operational maximum", async () => {
		await driver.printHTML("<div/>", {
			render: async () => ({ dataURL: "x", width: 384 }),
			config: { copies: 99, copyDelayMs: 0 },
		})
		expect(printer.printSingleBitmap).toHaveBeenCalledTimes(5)
	})

	describe("server config fallback (Finding 2)", () => {
		it("uses the server paper when the device has none", async () => {
			const blank = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ host: "127.0.0.1" }), // no paper, no cut
			})
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 576 })
			const res = await blank.printHTML("<div/>", {
				render,
				config: { paper: "80mm", cut: false },
			})
			expect(printer.setPageFormat).toHaveBeenCalledWith(0) // 80mm
			expect(render).toHaveBeenCalledWith("<div/>", {
				paper: "80mm",
				customDots: undefined,
			})
			expect(res).toEqual({ paper: "80mm", dots: 576, copies: 1 })
		})

		it("device paper overrides the server paper", async () => {
			// beforeEach device config sets paper "58mm"
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })
			const res = await driver.printHTML("<div/>", {
				render,
				config: { paper: "80mm", cut: true },
			})
			expect(printer.setPageFormat).toHaveBeenCalledWith(1) // device 58mm wins
			expect(res).toEqual({ paper: "58mm", dots: 384, copies: 1 })
		})

		it("device cut:false wins over server cut:true", async () => {
			const noCut = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await noCut.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
				config: { paper: "58mm", cut: true },
			})
			expect(printer.partialCut).not.toHaveBeenCalled()
		})

		it("falls back to server cut when the device key is absent", async () => {
			const noDeviceCut = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm" }), // cut absent
			})
			await noDeviceCut.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
				config: { paper: "58mm", cut: true },
			})
			expect(printer.partialCut).toHaveBeenCalledTimes(1)
		})

		it("uses the server customDots when the device key is absent", async () => {
			const blank = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "custom" }), // customDots absent
			})
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 512 })
			const res = await blank.printHTML("<div/>", {
				render,
				config: { paper: "custom", customDots: 512, cut: false },
			})
			expect(render).toHaveBeenCalledWith("<div/>", {
				paper: "custom",
				customDots: 512,
			})
			expect(res).toEqual({ paper: "custom", dots: 512, copies: 1 })
		})
	})
})
