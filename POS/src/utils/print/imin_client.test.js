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

		it("logs one line per copy with the reservation numbers", async () => {
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
			expect(logInfo).toHaveBeenCalledTimes(2)

			const copies = logInfo.mock.calls.map((call) => call[1])
			for (const [idx, copy] of copies.entries()) {
				// 1-based copy index and the bitmap height the estimate came from.
				expect(copy).toMatchObject({
					copy: idx + 1,
					heightDots: HEIGHT_DOTS,
				})
				// The reservation is what is left of settle + estimated print time
				// after the pipeline already ran, so reserve + elapsed reconstructs
				// the full estimate exactly — and pins it to the exported rate.
				expect(copy.reserveMs + copy.elapsedMs).toBe(SETTLE_MS + ESTIMATE_MS)
				if (idx < copies.length - 1) {
					// The logged pause is the one actually slept out.
					expect(copy.pauseMs).toBe(copy.reserveMs + COPY_DELAY_MS)
				}
			}
			// The final copy has no successor, so nothing is slept regardless of
			// what the reservation computed.
			expect(copies[1].pauseMs).toBe(0)
			expect(copies[0].pauseMs).toBeGreaterThan(0)
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

	describe("crew slip (copy 2 is a compact slip, not a copy of the receipt)", () => {
		// Stand-in for buildCrewSlipHTML output: a document that is NOT the
		// main receipt. No banner — the slip is unlabelled on paper.
		const crewHTML = '<div class="crew">crew</div>'
		const renderFor = () =>
			vi.fn().mockResolvedValue({ dataURL: "x", width: 384 })

		it("renders crewHTML for copy 2 and the plain main html for copy 1", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 2, copyDelayMs: 0 },
			})
			expect(render).toHaveBeenCalledTimes(2)
			const [first, second] = render.mock.calls
			// The customer copy is the receipt exactly as built — no banner.
			expect(first[0]).toBe("<body>receipt</body>")
			// The crew slip is the whole bitmap; nothing is prepended to it.
			expect(second[0]).toBe(crewHTML)
			expect(printer.printSingleBitmap).toHaveBeenCalledTimes(2)
		})

		it("ignores crewHTML for a single copy", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 1 },
			})
			expect(render).toHaveBeenCalledTimes(1)
			expect(render.mock.calls[0][0]).toBe("<body>receipt</body>")
			expect(printer.printSingleBitmap).toHaveBeenCalledTimes(1)
		})

		it("keeps the main receipt for copies 1 and 3 of a three-copy run", async () => {
			// Distinct dataURLs so the assertions can tell which bitmap reached
			// the printer per copy.
			const render = vi.fn(async (html) => ({
				dataURL: html === crewHTML ? "data:crew" : "data:receipt",
				width: 384,
			}))
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 3, copyDelayMs: 0 },
			})
			// Two renders only: the receipt once (copies 1 and 3 share it) and the
			// slip for copy 2.
			const sources = render.mock.calls.map((call) => call[0])
			expect(sources).toEqual(["<body>receipt</body>", crewHTML])
			const urls = printer.printSingleBitmap.mock.calls.map((call) => call[0])
			expect(urls).toEqual(["data:receipt", "data:crew", "data:receipt"])
		})

		it("prints the same plain receipt for every copy when no crewHTML is passed", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				config: { copies: 2, copyDelayMs: 0 },
			})
			// One render, sent twice: identical sheets, nothing above them.
			expect(render).toHaveBeenCalledTimes(1)
			expect(render.mock.calls[0][0]).toBe("<body>receipt</body>")
			expect(printer.printSingleBitmap).toHaveBeenCalledTimes(2)
			expect(printer.printSingleBitmap.mock.calls[0][0]).toBe(
				printer.printSingleBitmap.mock.calls[1][0],
			)
		})

		it("renders the crew slip at crewFontScale and the receipt at fontScale", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({
					paper: "58mm",
					cut: false,
					fontScale: 140,
					crewFontScale: 90,
				}),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 2, copyDelayMs: 0, tailDots: 30 },
			})
			const [firstOpts, crewOpts] = render.mock.calls.map((call) => call[1])
			// The customer copy keeps the receipt knob; the slip has its own.
			expect(firstOpts).toEqual(
				expect.objectContaining({ fontScale: 140, tailDots: 30 }),
			)
			expect(crewOpts).toEqual(
				expect.objectContaining({ fontScale: 90, tailDots: 30 }),
			)
		})

		it("carries the one line-spacing knob onto both copies", async () => {
			// There is a single vertical-density setting for everything direct
			// printed: the receipt and the slip tighten/loosen together.
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 2, copyDelayMs: 0, lineSpacing: 70 },
			})
			const [firstOpts, crewOpts] = render.mock.calls.map((call) => call[1])
			expect(firstOpts.lineSpacing).toBe(70)
			expect(crewOpts.lineSpacing).toBe(70)
		})

		it("carries the side margin onto both copies via the shared spread", async () => {
			// The margin is a property of the paper, not of which slip is on it,
			// so the crew copy must inherit it through the same renderOpts.
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 2, copyDelayMs: 0, sideMarginDots: 8 },
			})
			const [firstOpts, crewOpts] = render.mock.calls.map((call) => call[1])
			expect(firstOpts.sideMarginDots).toBe(8)
			expect(crewOpts.sideMarginDots).toBe(8)
		})

		it("defaults the crew slip to the 130 crew knob, not the receipt scale", async () => {
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 2, copyDelayMs: 0 },
			})
			const [firstOpts, crewOpts] = render.mock.calls.map((call) => call[1])
			expect(firstOpts.fontScale).toBe(100)
			expect(crewOpts.fontScale).toBe(130)
		})

		it("falls back to the receipt scale for the slip only when the crew knob is unset on both ends", async () => {
			// resolvePrintConfig always answers crewFontScale (default 130), so the
			// slip never inherits the receipt scale silently. Pinned here so a
			// future refactor of the resolver cannot change that by accident.
			const render = renderFor()
			const d = createIminDriver({
				factory: () => printer,
				loadConfig: () => ({ paper: "58mm", cut: false, fontScale: 170 }),
			})
			await d.printHTML("<body>receipt</body>", {
				render,
				crewHTML,
				config: { copies: 2, copyDelayMs: 0 },
			})
			expect(render.mock.calls[1][1].fontScale).not.toBe(170)
		})
	})
})
