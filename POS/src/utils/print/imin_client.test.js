import { beforeEach, describe, expect, it, vi } from "vitest"

// Shared so the assertions can read what the driver logged, whichever logger
// instance createIminDriver() ended up holding.
const logInfo = vi.hoisted(() => vi.fn())

vi.mock("@/utils/logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			info: logInfo,
			warn: vi.fn(),
			error: vi.fn(),
		}),
	},
}))

import { createIminDriver, PRINT_DOTS_PER_SECOND } from "./imin_client"
import { DEFAULT_FEED_DOTS, DEFAULT_TAIL_DOTS } from "./receipt_layout"

// Clock slack for the wall-clock gap assertions. The mock stamps its time a
// few microseconds AFTER the driver captured tQueued, so the ms clock can
// tick across that boundary and shave 1 ms off the measured gap.
const CLOCK_SLACK_MS = 2

// These cases sleep real wall-clock time (no fake timers), which is measured
// against vitest's 5 s default per-test timeout.
const SLOW_TEST_TIMEOUT_MS = 15000

function makeFakePrinter(overrides = {}) {
	return {
		ws: { readyState: 1 },
		close: vi.fn(),
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
	logInfo.mockClear()
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

	it("tags post-print failures after the first sheet is queued (COR-FE-10)", async () => {
		printer.printAndFeedPaper = vi.fn(() => {
			throw new Error("feed died")
		})
		// The sheet was queued (printSingleBitmap resolved) before the feed
		// failed: paper is already moving, so the error must carry the
		// postPrint flag for the transport to stop the chain.
		await expect(
			driver.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
			}),
		).rejects.toMatchObject({ postPrint: true })
	})

	it("leaves pre-queue failures untagged", async () => {
		printer.printSingleBitmap.mockRejectedValue(new Error("queue refused"))
		let caught
		await driver
			.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
			})
			.catch((err) => {
				caught = err
			})
		expect(caught?.message).toBe("queue refused")
		expect(caught).not.toHaveProperty("postPrint")
	})

	it("advances paper after the bitmap so the receipt clears the tear bar (probe v3)", async () => {		await driver.printHTML("<div/>", {
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
		expect(printer.printAndFeedPaper.mock.calls.length).toBeGreaterThanOrEqual(
			2,
		)
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
		// Two copies reach the printer, both of the same bitmap.
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
				tailDots: DEFAULT_TAIL_DOTS,
				fontScale: 100,
				lineSpacing: 100,
				sideMarginDots: 16,
				topMarginDots: 0,
				queueGapDots: 40,
			})
			expect(res.paper).toBe("80mm")
		})

		it("device paper overrides the server paper", async () => {
			// beforeEach device config sets paper "58mm"
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })
			const res = await driver.printHTML("<div/>", {
				render,
				config: { paper: "80mm", cut: true },
			})
			expect(printer.setPageFormat).toHaveBeenCalledWith(1) // device 58mm wins
			expect(res.paper).toBe("58mm")
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
				tailDots: DEFAULT_TAIL_DOTS,
				fontScale: 100,
				lineSpacing: 100,
				sideMarginDots: 16,
				topMarginDots: 0,
				queueGapDots: 40,
			})
			expect(res.paper).toBe("custom")
		})
	})
	describe("tail spacer, copies and scales", () => {
		it("feeds the safe default of 160 dots when nothing is configured", async () => {
			const bare = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({}),
			})
			await bare.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
			})
			// 160 dots = 20 mm, above the typical head->tear-bar gap. The old
			// 100 (12.5 mm) left the last line inside the mechanism.
			expect(printer.printAndFeedPaper).toHaveBeenCalledWith(DEFAULT_FEED_DOTS)
			expect(DEFAULT_FEED_DOTS).toBe(160)
		})

		it("device feedDots still overrides the safer default", async () => {
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ feedDots: 90 }),
			})
			await d.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
			})
			expect(printer.printAndFeedPaper).toHaveBeenCalledWith(90)
		})

		it("passes the effective fontScale to the renderer", async () => {
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })
			await driver.printHTML("<div/>", {
				render,
				config: { fontScale: 170 },
			})
			expect(render).toHaveBeenCalledWith(
				expect.any(String),
				expect.objectContaining({ fontScale: 170 }),
			)
		})

		it("passes the effective lineSpacing to the renderer", async () => {
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })
			await driver.printHTML("<div/>", {
				render,
				config: { lineSpacing: 80 },
			})
			expect(render).toHaveBeenCalledWith(
				expect.any(String),
				expect.objectContaining({ lineSpacing: 80 }),
			)
		})

		it("passes the effective sideMarginDots to the renderer", async () => {
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })
			await driver.printHTML("<div/>", {
				render,
				config: { sideMarginDots: 8 },
			})
			expect(render).toHaveBeenCalledWith(
				expect.any(String),
				expect.objectContaining({ sideMarginDots: 8 }),
			)
		})

		it("passes the effective tailDots to the renderer", async () => {
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })
			await driver.printHTML("<div/>", {
				render,
				config: { tailDots: 40 },
			})
			expect(render).toHaveBeenCalledWith(
				expect.any(String),
				expect.objectContaining({ tailDots: 40 }),
			)
		})

		it(
			"reserves the copy's print time even when the status gate returns instantly",
			async () => {
				// Device behaviour: getPrinterStatus() reports 0 while the head is
				// still printing, so waitIdle is a no-op. The pause must survive it.
				const stamps = []
				const t0 = Date.now()
				printer.printSingleBitmap = vi.fn(async () => {
					stamps.push(Date.now() - t0)
					return 1
				})
				printer.printAndFeedPaper = vi.fn()
				printer.getPrinterStatus = async () => ({ value: 0 })
				const d = createIminDriver({
					factory: () => printer,
					loadConfig: () => ({ paper: "58mm", cut: false }),
				})
				// 800 dots at the measured ~200 dots/s -> ~4000 ms reserved. The
				// rate is read off the driver, so this test fails the moment the
				// reservation quietly reverts to an optimistic throughput: that is
				// exactly what swallowed the pause on device. Two copies only — a
				// single gap already costs the full window of real sleep, hence the
				// explicit timeout above.
				const SETTLE_MS = 200
				const HEIGHT_DOTS = 800
				const ESTIMATE_MS = Math.round(
					(HEIGHT_DOTS / PRINT_DOTS_PER_SECOND) * 1000,
				)
				const COPY_DELAY_MS = 300
				await d.printHTML("<div/>", {
					render: async () => ({
						dataURL: "x",
						width: 384,
						height: HEIGHT_DOTS,
					}),
					config: { copies: 2, copyDelayMs: COPY_DELAY_MS },
				})
				expect(stamps).toHaveLength(2)
				for (let i = 1; i < stamps.length; i++) {
					// settle 200 + reserved print + delay 300, measured from queue
					// time. CLOCK_SLACK_MS because the mock stamps a tick after
					// tQueued, so the measured gap can sit 1 ms under the exact sum.
					const expectedMs = SETTLE_MS + ESTIMATE_MS + COPY_DELAY_MS
					expect(stamps[i] - stamps[i - 1]).toBeGreaterThanOrEqual(
						expectedMs - CLOCK_SLACK_MS,
					)
					// ... and it must not overshoot either: the driver sleeps exactly
					// reserve + delay, so a much larger gap would mean it is
					// double-counting the print time. Generous, jitter-only ceiling.
					expect(stamps[i] - stamps[i - 1]).toBeLessThanOrEqual(
						expectedMs + 1000,
					)
				}
			},
			SLOW_TEST_TIMEOUT_MS,
		)

		it("logs one line per sheet with the reservation numbers", async () => {
			// The pause cannot be observed remotely (POS Print Log only sees the
			// whole print), so the driver has to say per copy how the wall clock
			// was spent — otherwise a swallowed pause on site is undiagnosable.
			const SETTLE_MS = 200
			const HEIGHT_DOTS = 200
			const ESTIMATE_MS = Math.round(
				(HEIGHT_DOTS / PRINT_DOTS_PER_SECOND) * 1000,
			)
			const COPY_DELAY_MS = 250
			printer.printAndFeedPaper = vi.fn()
			printer.getPrinterStatus = async () => ({ value: 0 })
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<div/>", {
				render: async () => ({
					dataURL: "x",
					width: 384,
					height: HEIGHT_DOTS,
				}),
				config: { copies: 2, copyDelayMs: COPY_DELAY_MS },
			})
			const sheets = logInfo.mock.calls
				.filter((call) => call[0] === "sheet printed")
				.map((call) => call[1])
			expect(sheets).toHaveLength(2)
			for (const [idx, sheet] of sheets.entries()) {
				// 1-based sheet index and the bitmap height the estimate came from.
				expect(sheet).toMatchObject({
					sheet: idx + 1,
					kind: "receipt",
					heightDots: HEIGHT_DOTS,
				})
				// The reservation is what is left of settle + estimated print time
				// after the pipeline already ran, so reserve + elapsed reconstructs
				// the full estimate exactly — and pins it to the exported rate.
				expect(sheet.reserveMs + sheet.elapsedMs).toBe(SETTLE_MS + ESTIMATE_MS)
				if (idx < sheets.length - 1) {
					// The logged pause is the one actually slept out.
					expect(sheet.pauseMs).toBe(sheet.reserveMs + COPY_DELAY_MS)
				}
			}
			// The final sheet has no successor, so nothing is slept regardless of
			// what the reservation computed.
			expect(sheets[1].pauseMs).toBe(0)
			expect(sheets[0].pauseMs).toBeGreaterThan(0)
		})

		it(
			"a taller bitmap widens the inter-copy pause (reproduces fontScale report)",
			async () => {
				const runCopies = async (height) => {
					const stamps = []
					const t0 = Date.now()
					printer.printSingleBitmap = vi.fn(async () => {
						stamps.push(Date.now() - t0)
						return 1
					})
					printer.printAndFeedPaper = vi.fn()
					printer.getPrinterStatus = async () => ({ value: 0 })
					const d = createIminDriver({
						factory: () => printer,
						loadConfig: () => ({ paper: "58mm", cut: false }),
					})
					await d.printHTML("<div/>", {
						render: async () => ({ dataURL: "x", width: 384, height }),
						config: { copies: 2, copyDelayMs: 300 },
					})
					return stamps[1] - stamps[0]
				}
				const shortBitmap = await runCopies(200) // ~1 s print at 200 dots/s
				const tallBitmap = await runCopies(800) // ~4 s print
				expect(tallBitmap).toBeGreaterThan(shortBitmap + 800)
			},
			SLOW_TEST_TIMEOUT_MS,
		)

		it("keeps the tear-off pause when the pipeline overruns the estimate", async () => {
			// Device evidence (2026-09-03): a slow bitmap upload plus the waitIdle
			// polling overshoot can take LONGER than settle + estimate + delay, so
			// the old `remaining = cycle - elapsed` reservation went negative and
			// the pause silently vanished — copies printed back-to-back. The
			// configured delay must be ADDED on top of whatever the pipeline took,
			// never carved out of it.
			const SETTLE_MS = 200
			const QUEUE_MS = 700
			const STATUS_MS = 400
			const COPY_DELAY_MS = 400
			// 100 dots at 200 dots/s -> ~500 ms estimate, far under the real cycle.
			const stamps = []
			const t0 = Date.now()
			printer.printSingleBitmap = vi.fn(async () => {
				stamps.push(Date.now() - t0)
				await new Promise((r) => setTimeout(r, QUEUE_MS))
				return 1
			})
			printer.getPrinterStatus = vi.fn(async () => {
				await new Promise((r) => setTimeout(r, STATUS_MS))
				return { value: 0 }
			})
			printer.printAndFeedPaper = vi.fn()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384, height: 100 }),
				config: { copies: 2, copyDelayMs: COPY_DELAY_MS },
			})
			expect(stamps).toHaveLength(2)
			// Lower bound on the real cycle: queue + settle + status round trip.
			const cycleMs = QUEUE_MS + SETTLE_MS + STATUS_MS
			// The delay survives the overrun, so it is >= the delay alone ...
			for (let i = 1; i < stamps.length; i++) {
				expect(stamps[i] - stamps[i - 1]).toBeGreaterThanOrEqual(COPY_DELAY_MS)
				// ... and it is never swallowed by the pipeline: the gap must
				// exceed the cycle itself by at least the configured delay.
				// CLOCK_SLACK_MS because the mock stamps a tick after tQueued.
				expect(stamps[i] - stamps[i - 1]).toBeGreaterThanOrEqual(
					cycleMs + COPY_DELAY_MS - CLOCK_SLACK_MS,
				)
			}
		})

		it("delays between EVERY pair of copies, not only the first gap", async () => {
			const timeline = []
			const t0 = Date.now()
			printer.printSingleBitmap = vi.fn(async () => {
				timeline.push({ e: "bitmap", t: Date.now() - t0 })
				return 1
			})
			printer.printAndFeedPaper = vi.fn(() => timeline.push({ e: "feed" }))
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			const res = await d.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
				config: { copies: 3, copyDelayMs: 250 },
			})
			expect(res.copies).toBe(3)
			const bitmaps = timeline.filter((x) => x.e === "bitmap")
			expect(bitmaps).toHaveLength(3)
			// Two inter-copy gaps must each cover the 250 ms tear-off pause
			// (plus the 200 ms settle). A 0 ms delay would collapse these.
			const gaps = [bitmaps[1].t - bitmaps[0].t, bitmaps[2].t - bitmaps[1].t]
			for (const gap of gaps) expect(gap).toBeGreaterThanOrEqual(250)
		})

		it("renders one shared bitmap for a multi-copy run and never labels it", async () => {
			const render = vi.fn(async (html) => ({
				dataURL: "x",
				width: 384,
				html,
			}))
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>hi</body>", {
				render,
				config: { copies: 2, copyDelayMs: 0 },
			})
			// Copies are identical now — nothing is printed above the receipt —
			// so one render serves both sheets.
			expect(render).toHaveBeenCalledTimes(1)
			expect(render.mock.calls[0][0]).toBe("<body>hi</body>")
			expect(printer.printSingleBitmap).toHaveBeenCalledTimes(2)
		})

		it("renders the receipt plain for a single copy", async () => {
			const render = vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<div/>", { render, config: { copies: 1 } })
			expect(render).toHaveBeenCalledTimes(1)
			expect(render.mock.calls[0][0]).toBe("<div/>")
		})
	})

	describe("crew slip (own toggle, prints once after the copies)", () => {
		// Stand-in for buildCrewSlipHTML output: a document that is NOT the
		// main receipt. Distinct dataURLs so the assertions can tell which
		// bitmap reached the printer in which order.
		const crewHTML = '<div class="crew">crew</div>'
		const renderFor = () =>
			vi.fn(async (html) => ({
				dataURL: html === crewHTML ? "data:crew" : "data:receipt",
				width: 384,
			}))

		it("prints one crew slip after a single copy when enabled", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			const res = await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { crewSlipEnabled: true, copies: 1, copyDelayMs: 0 },
			})
			// Two renders only: the receipt once and the slip once, even though
			// copies is 1 — the slip is no longer copy 2.
			const sources = render.mock.calls.map((call) => call[0])
			expect(sources).toEqual(["<body>receipt</body>", crewHTML])
			const urls = printer.printSingleBitmap.mock.calls.map((call) => call[0])
			expect(urls).toEqual(["data:receipt", "data:crew"])
			expect(res.copies).toBe(2)
		})

		it("prints identical copies and no slip when disabled", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			const res = await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { crewSlipEnabled: false, copies: 2, copyDelayMs: 0 },
			})
			// The toggle gates the slip: crewHTML is handed over but never
			// rendered, and every sheet is the receipt.
			expect(render.mock.calls.map((call) => call[0])).toEqual([
				"<body>receipt</body>",
			])
			const urls = printer.printSingleBitmap.mock.calls.map((call) => call[0])
			expect(urls).toEqual(["data:receipt", "data:receipt"])
			expect(res.copies).toBe(2)
		})

		it("prints copies then crew when both are on", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			const res = await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { crewSlipEnabled: true, copies: 2, copyDelayMs: 0 },
			})
			// Order: every identical receipt copy first, the slip strictly last.
			const urls = printer.printSingleBitmap.mock.calls.map((call) => call[0])
			expect(urls).toEqual(["data:receipt", "data:receipt", "data:crew"])
			expect(res.copies).toBe(3)
		})
	})

	describe("connection and status resilience", () => {
		it("reconnects when cached ws is not open", async () => {
			let instanceCount = 0
			const p1 = makeFakePrinter({ ws: { readyState: 1 } })
			const p2 = makeFakePrinter({ ws: { readyState: 1 } })
			const d = createIminDriver({
				factory: () => {
					instanceCount++
					return instanceCount === 1 ? p1 : p2
				},
				loadConfig: () => ({ paper: "58mm" }),
			})

			await d.getStatus()
			expect(instanceCount).toBe(1)
			expect(p1.initPrinter).toHaveBeenCalledWith("SPI")

			// Simulate socket disconnection
			p1.ws.readyState = 3 // CLOSED
			await d.getStatus()
			expect(p1.close).toHaveBeenCalled()
			expect(instanceCount).toBe(2)
			expect(p2.initPrinter).toHaveBeenCalledWith("SPI")
		})

		it("failed connect closes instance", async () => {
			const badPrinter = makeFakePrinter({
				connect: vi.fn().mockResolvedValue(false),
			})
			const d = createIminDriver({
				factory: () => badPrinter,
				loadConfig: () => ({ paper: "58mm" }),
			})

			await expect(
				d.printHTML("<div/>", {
					render: async () => ({ dataURL: "x", width: 384 }),
				}),
			).rejects.toThrow(/Could not connect/)
			expect(badPrinter.close).toHaveBeenCalled()
		})

		it("status reply arriving after old 2s but before new timeout is ok", async () => {
			printer.getPrinterStatus = vi.fn(
				() =>
					new Promise((resolve) => {
						setTimeout(() => resolve({ value: 0 }), 2200)
					}),
			)
			const res = await driver.getStatus()
			expect(res).toEqual({ ok: true, code: 0 })
		}, 10000)

		it("drops a silent open socket so the next status opens a fresh one", async () => {
			// Socket OPEN but the service never answers (e.g. it was opened before
			// the operator allowed this origin): reusing it would fail forever.
			const silent = makeFakePrinter({
				getPrinterStatus: vi.fn(() => new Promise(() => {})),
			})
			const healthy = makeFakePrinter()
			const factory = vi.fn().mockReturnValueOnce(silent).mockReturnValueOnce(healthy)
			const d = createIminDriver({ factory, statusReplyMs: 20 })

			expect((await d.getStatus()).ok).toBe(false)
			expect(silent.close).toHaveBeenCalled()
			expect((await d.getStatus()).ok).toBe(true)
			expect(factory).toHaveBeenCalledTimes(2)
		})

		it("keeps the socket when a status times out during a print", async () => {
			let releasePrint
			const p = makeFakePrinter({
				printSingleBitmap: vi.fn(() => new Promise((r) => (releasePrint = r))),
				getPrinterStatus: vi.fn(() => new Promise(() => {})),
			})
			const d = createIminDriver({ factory: () => p, statusReplyMs: 20 })
			const printing = d.printHTML("<div/>", {
				render: async () => ({ dataURL: "x", width: 384 }),
			})
			await vi.waitFor(() => expect(releasePrint).toBeTypeOf("function"))
			await d.getStatus()
			expect(p.close).not.toHaveBeenCalled()
			p.getPrinterStatus = vi.fn().mockResolvedValue({ value: 0 })
			releasePrint(1)
			await printing
		})

		it("recovers from a hung bitmap upload: rejects untagged, drops the socket, next print reconnects", async () => {
			// The SDK uploads the bitmap with a bare fetch that never settles when
			// the service is wedged; the driver must not wait forever (the whole
			// session then stays "printing" and every probe fails until reload).
			const hung = makeFakePrinter({
				printSingleBitmap: vi.fn(() => new Promise(() => {})),
			})
			const healthy = makeFakePrinter()
			const factory = vi
				.fn()
				.mockReturnValueOnce(hung)
				.mockReturnValueOnce(healthy)
			const d = createIminDriver({
				factory,
				loadConfig: () => ({ paper: "58mm", cut: false }),
				sendTimeoutMs: 20,
			})
			let caught
			await d
				.printHTML("<div/>", {
					render: async () => ({ dataURL: "x", width: 384 }),
				})
				.catch((err) => {
					caught = err
				})
			// Nothing left the printer, so the transport may fall back safely.
			expect(caught).toBeTruthy()
			expect(caught).not.toHaveProperty("postPrint")
			expect(hung.close).toHaveBeenCalled()
			// The next print starts on a fresh socket instead of the wedged one.
			await d.printHTML("<div/>", {
				render: async () => ({ dataURL: "y", width: 384 }),
			})
			expect(factory).toHaveBeenCalledTimes(2)
			expect(healthy.printSingleBitmap).toHaveBeenCalled()
		})

		it("tags postPrint when a later sheet hangs after the first one was queued", async () => {
			const p = makeFakePrinter({
				printSingleBitmap: vi
					.fn()
					.mockResolvedValueOnce(1)
					.mockImplementationOnce(() => new Promise(() => {})),
			})
			const d = createIminDriver({
				factory: () => p,
				loadConfig: () => ({ paper: "58mm", cut: false }),
				sendTimeoutMs: 20,
			})
			await expect(
				d.printHTML("<div/>", {
					render: async () => ({ dataURL: "x", width: 384 }),
					config: { copies: 2, copyDelayMs: 0 },
				}),
			).rejects.toMatchObject({ postPrint: true })
			expect(p.close).toHaveBeenCalled()
		})

		it("treats a synchronously throwing getPrinterStatus as a timeout and drops the socket", async () => {
			// A dead socket makes the SDK's send() throw before it ever returns a
			// promise. That must ride the timeout path, not escape as a raw error:
			// only the timeout path drops the socket so the next probe reconnects.
			const dead = makeFakePrinter({
				getPrinterStatus: vi.fn(() => {
					throw new Error("ws is null")
				}),
			})
			const healthy = makeFakePrinter()
			const factory = vi
				.fn()
				.mockReturnValueOnce(dead)
				.mockReturnValueOnce(healthy)
			const d = createIminDriver({ factory, statusReplyMs: 20 })
			const first = await d.getStatus()
			expect(first.ok).toBe(false)
			expect(first.message).toBe("no status reply (device busy?)")
			expect(dead.close).toHaveBeenCalled()
			expect((await d.getStatus()).ok).toBe(true)
			expect(factory).toHaveBeenCalledTimes(2)
		})

		it("concurrent getStatus both resolve", async () => {
			let currentCallback = null
			const p = makeFakePrinter({
				getPrinterStatus: vi.fn(() => {
					return new Promise((resolve) => {
						currentCallback = resolve
						setTimeout(() => {
							if (currentCallback) {
								currentCallback({ value: 0 })
							}
						}, 50)
					})
				}),
			})
			const d = createIminDriver({
				factory: () => p,
				statusReplyMs: 1000,
			})
			const [s1, s2] = await Promise.all([d.getStatus(), d.getStatus()])
			expect(s1).toEqual({ ok: true, code: 0 })
			expect(s2).toEqual({ ok: true, code: 0 })
		})

		it("deduplicates concurrent ensurePrinter calls so factory is called once", async () => {
			const factory = vi.fn(() => makeFakePrinter())
			const d = createIminDriver({
				factory,
				loadConfig: () => ({ paper: "58mm" }),
			})

			const [avail, status] = await Promise.all([d.isAvailable(), d.getStatus()])
			expect(avail).toBe(true)
			expect(status.ok).toBe(true)
			expect(factory).toHaveBeenCalledTimes(1)
		})
	})

	describe("text mode (printMode: text)", () => {
		const doc = {
			name: "PINV-1",
			company: "ROPI",
			posting_date: "2026-10-24",
			posting_time: "14:58:06",
			pos_queue_number: 7,
			grand_total: 12000,
			items: [{ item_name: "Americano Ice", qty: 1, rate: 12000 }],
			payments: [{ mode_of_payment: "Cash", amount: 12000 }],
		}
		const textPrinter = () => {
			const calls = []
			const rec =
				(name) =>
				(...args) =>
					calls.push([name, ...args])
			const p = makeFakePrinter({
				setTextTypeface: rec("typeface"),
				setAlignment: rec("align"),
				setTextSize: rec("size"),
				setTextStyle: rec("style"),
				printText: rec("text"),
				printAndFeedPaper: rec("feed"),
				partialCut: rec("cut"),
				printSingleBitmap: vi.fn(async (url) => {
					calls.push(["bitmap", url])
					return 1
				}),
			})
			return { p, calls }
		}

		it("sends text commands, the logo as the only bitmap, and never renders", async () => {
			const { p, calls } = textPrinter()
			const render = vi.fn()
			const d = createIminDriver({
				factory: () => p,
				loadLogo: async () => ({ dataURL: "data:logo", height: 80 }),
			})
			const res = await d.printHTML("<div/>", {
				render,
				receiptDoc: doc,
				config: { printMode: "text", paper: "58mm", cut: true, copies: 1 },
			})
			expect(render).not.toHaveBeenCalled()
			expect(res.copies).toBe(1)
			const bitmaps = calls.filter((c) => c[0] === "bitmap")
			expect(bitmaps).toEqual([["bitmap", "data:logo"]])
			const texts = calls.filter((c) => c[0] === "text").map((c) => c[1])
			expect(texts).toContain("007")
			expect(texts.some((t) => t.trim().startsWith("1x Americano Ice"))).toBe(true)
			// Monospace on, default restored at the end; feed + cut after the text.
			expect(calls[0]).toEqual(["typeface", 1])
			const lastText = calls.map((c) => c[0]).lastIndexOf("text")
			const tail = calls.slice(lastText + 1).map((c) => c[0])
			expect(tail).toContain("cut")
			expect(calls.filter((c) => c[0] === "typeface").at(-1)).toEqual(["typeface", 0])
		})

		it("restates monospace right before every text line", async () => {
			const { p, calls } = textPrinter()
			const d = createIminDriver({ factory: () => p, loadLogo: async () => null })
			await d.printHTML("<div/>", {
				receiptDoc: doc,
				config: { printMode: "text", copies: 1 },
			})
			calls.forEach((c, i) => {
				if (c[0] === "text") expect(calls[i - 1]).toEqual(["typeface", 1])
			})
		})

		it("prints the logo before the company line", async () => {
			const { p, calls } = textPrinter()
			const d = createIminDriver({
				factory: () => p,
				loadLogo: async () => ({ dataURL: "data:logo", height: 80 }),
			})
			await d.printHTML("<div/>", {
				receiptDoc: doc,
				config: { printMode: "text", copies: 1 },
			})
			const logoAt = calls.findIndex((c) => c[0] === "bitmap")
			const companyAt = calls.findIndex((c) => c[0] === "text" && c[1] === "ROPI")
			expect(logoAt).toBeGreaterThan(-1)
			expect(logoAt).toBeLessThan(companyAt)
		})

		it("tags a failure after text went out as postPrint (COR-FE-10)", async () => {
			const { p, calls } = textPrinter()
			p.printSingleBitmap = vi.fn(async () => {
				throw new Error("upload failed")
			})
			const d = createIminDriver({
				factory: () => p,
				loadLogo: async () => ({ dataURL: "data:logo", height: 80 }),
			})
			await expect(
				d.printHTML("<div/>", {
					receiptDoc: doc,
					config: { printMode: "text", copies: 1 },
				}),
			).rejects.toMatchObject({ postPrint: true })
			expect(calls.some((c) => c[0] === "text")).toBe(true)
		})

		it("tags a timed-out logo upload after text went out as postPrint (COR-FE-10)", async () => {
			// The queue number is sent before the logo bitmap: a hang on that
			// upload leaves paper partially out, so the timeout must stop the
			// chain instead of letting another driver print the receipt again.
			const { p } = textPrinter()
			p.printSingleBitmap = vi.fn(() => new Promise(() => {}))
			const d = createIminDriver({
				factory: () => p,
				loadLogo: async () => ({ dataURL: "data:logo", height: 80 }),
				sendTimeoutMs: 20,
			})
			await expect(
				d.printHTML("<div/>", {
					receiptDoc: doc,
					config: { printMode: "text", copies: 1 },
				}),
			).rejects.toMatchObject({ postPrint: true })
		})

		it("leaves a timed-out first-command logo untagged so the fallback can print", async () => {
			// No queue number: the logo is the first command, so nothing has
			// reached the paper and another driver may still print safely.
			const { p } = textPrinter()
			p.printSingleBitmap = vi.fn(() => new Promise(() => {}))
			const d = createIminDriver({
				factory: () => p,
				loadLogo: async () => ({ dataURL: "data:logo", height: 80 }),
				sendTimeoutMs: 20,
			})
			let caught
			await d
				.printHTML("<div/>", {
					receiptDoc: { ...doc, pos_queue_number: null },
					config: { printMode: "text", copies: 1 },
				})
				.catch((err) => {
					caught = err
				})
			expect(caught).toBeTruthy()
			expect(caught).not.toHaveProperty("postPrint")
		})

		it("prints without a logo when it cannot be loaded", async () => {
			const { p, calls } = textPrinter()
			const d = createIminDriver({ factory: () => p, loadLogo: async () => null })
			await d.printHTML("<div/>", {
				receiptDoc: doc,
				config: { printMode: "text", copies: 1 },
			})
			expect(calls.some((c) => c[0] === "bitmap")).toBe(false)
			expect(calls.some((c) => c[0] === "text" && c[1] === "ROPI")).toBe(true)
		})

		it("asks for the logo padded to the full head width so it prints centred", async () => {
			const { p } = textPrinter()
			const loadLogo = vi.fn(async () => null)
			const d = createIminDriver({ factory: () => p, loadLogo })
			await d.printHTML("<div/>", {
				receiptDoc: doc,
				config: { printMode: "text", paper: "58mm", copies: 1 },
			})
			expect(loadLogo).toHaveBeenCalledWith(112, 384)
		})

		it("adds the crew slip as a second text sheet without needing crewHTML", async () => {
			const { p, calls } = textPrinter()
			const d = createIminDriver({ factory: () => p, loadLogo: async () => null })
			const res = await d.printHTML("<div/>", {
				receiptDoc: doc,
				config: {
					printMode: "text",
					crewSlipEnabled: true,
					copies: 1,
					copyDelayMs: 0,
					crewDelayMs: 0,
					printSpeed: 2000,
				},
			})
			expect(res.copies).toBe(2)
			const texts = calls.filter((c) => c[0] === "text").map((c) => c[1])
			expect(texts.indexOf("ORDER")).toBeGreaterThan(texts.indexOf("Thank you"))
		}, SLOW_TEST_TIMEOUT_MS)

		it("pauses crewDelayMs, not copyDelayMs, before the crew slip", async () => {
			const { p } = textPrinter()
			const d = createIminDriver({ factory: () => p, loadLogo: async () => null })
			const spy = vi.spyOn(globalThis, "setTimeout")
			try {
				await d.printHTML("<div/>", {
					receiptDoc: doc,
					config: {
						printMode: "text",
						crewSlipEnabled: true,
						copies: 1,
						copyDelayMs: 7000,
						crewDelayMs: 300,
						printSpeed: 2000,
					},
				})
				const waits = spy.mock.calls.map((c) => c[1] || 0)
				expect(waits.some((ms) => ms >= 300 && ms < 7000)).toBe(true)
				expect(waits).not.toContain(7000)
			} finally {
				spy.mockRestore()
			}
		}, SLOW_TEST_TIMEOUT_MS)

		it("stays bitmap when no doc is passed (closing report, test page)", async () => {
			const render = vi.fn(async () => ({ dataURL: "data:x", width: 384 }))
			const d = createIminDriver({ factory: () => printer })
			await d.printHTML("<div/>", {
				render,
				config: { printMode: "text", copies: 1 },
			})
			expect(render).toHaveBeenCalledTimes(1)
					expect(printer.printSingleBitmap).toHaveBeenCalledWith("data:x", 1)
		})

		it("prints EOD closing report in text mode when kind === 'eod' and doc is passed", async () => {
			const { p, calls } = textPrinter()
			const d = createIminDriver({ factory: () => p, loadLogo: async () => null })
			const eodDoc = {
				company: "ROPI",
				cashier: "Kasir 1",
				shift: "POS-CLS-001",
				recap: {
					total_sales: 100000,
				total_order: 5,
					average_per_order: 20000,
					cash: { opening_balance: 50000, cash_payment: 100000, total_expense: 0, cash_in_hand: 150000 },
					payment_methods: [{ mode_of_payment: "Cash", amount: 100000 }],
					total_cash: 100000,
					total_non_cash: 0,
					methods_grand_total: 100000,
					categories: [],
				},
			}
			const res = await d.printHTML("<div/>", {
				kind: "eod",
				eodDoc,
				config: { eodPrintMode: "text", copies: 1, cut: true },
			})
			expect(res.copies).toBe(1)
			const texts = calls.filter((c) => c[0] === "text").map((c) => c[1])
			expect(texts.some((t) => t.includes("SALES RECAP"))).toBe(true)
			expect(texts.some((t) => t.includes("ROPI"))).toBe(true)
			expect(texts.some((t) => t.includes("Total Sales"))).toBe(true)
			expect(calls.some((c) => c[0] === "feed")).toBe(true)
			expect(calls.some((c) => c[0] === "cut")).toBe(true)
		})

		it("applies sideMarginDots, topMarginDots, and lineSpacing in text mode", async () => {
			const { p, calls } = textPrinter()
			const d = createIminDriver({ factory: () => p, loadLogo: async () => null })
			p.setTextLineSpacing = vi.fn()
			await d.printHTML("<div/>", {
				receiptDoc: doc,
				config: {
					printMode: "text",
					sideMarginDots: 24,
					topMarginDots: 30,
					lineSpacing: 120,
					copies: 1,
				},
			})
			expect(p.setTextLineSpacing).toHaveBeenCalledWith(1.2)
			const feeds = calls.filter((c) => c[0] === "feed")
			expect(feeds.some((f) => f[1] === 30)).toBe(true)
			const texts = calls.filter((c) => c[0] === "text").map((c) => c[1])
			expect(texts.some((t) => t.startsWith("  "))).toBe(true)
		})
	})
})
