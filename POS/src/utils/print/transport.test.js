import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn().mockResolvedValue({}) }))
vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ warn: vi.fn(), info: vi.fn(), error: vi.fn() }) },
}))

function okDriver(id) {
	return {
		id,
		isAvailable: vi.fn().mockResolvedValue(true),
		getStatus: vi.fn().mockResolvedValue({ ok: true, code: 0 }),
		printHTML: vi.fn().mockResolvedValue(true),
		describe: () => ({ id }),
	}
}

function failDriver(id, err) {
	return {
		id,
		isAvailable: vi.fn().mockResolvedValue(true),
		getStatus: vi.fn().mockResolvedValue({ ok: false, code: -1 }),
		printHTML: vi.fn().mockRejectedValue(new Error(err)),
		describe: () => ({ id }),
	}
}

let createTransport
let log

beforeEach(async () => {
	const mod = await import("./transport")
	createTransport = mod.createTransport
	log = { attempts: [] }
})

it("uses the configured driver when available", async () => {
	const imin = okDriver("imin")
	const t = createTransport({
		drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") },
		config: { driver: "imin" },
		logSink: log,
	})
	await t.printHTML("<html/>")
	expect(imin.printHTML).toHaveBeenCalled()
	expect(log.attempts.at(-1).status).toBe("Success")
})

it("falls back down the chain and logs a Fallback status", async () => {
	const imin = failDriver("imin", "offline")
	const qz = failDriver("qz", "offline")
	const browser = okDriver("browser")
	const t = createTransport({
		drivers: { imin, qz, browser },
		config: { driver: "imin", fallback_enabled: true },
		logSink: log,
	})
	await t.printHTML("<html/>")
	expect(browser.printHTML).toHaveBeenCalled()
	expect(log.attempts.at(-1).status).toBe("Fallback")
})

it("rethrows when fallback is disabled", async () => {
	const imin = failDriver("imin", "offline")
	const t = createTransport({
		drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") },
		config: { driver: "imin", fallback_enabled: false },
		logSink: log,
	})
	await expect(t.printHTML("<html/>")).rejects.toThrow("offline")
	expect(log.attempts.at(-1).status).toBe("Failed")
})

it("skips an unavailable driver and moves to the next", async () => {
	const imin = okDriver("imin")
	imin.isAvailable.mockResolvedValue(false)
	const qz = okDriver("qz")
	qz.isAvailable.mockResolvedValue(false)
	const browser = okDriver("browser")
	const t = createTransport({
		drivers: { imin, qz, browser },
		config: { driver: "imin", fallback_enabled: true },
		logSink: log,
	})
	await t.printHTML("<html/>")
	expect(imin.printHTML).not.toHaveBeenCalled()
	expect(browser.printHTML).toHaveBeenCalled()
})

it("passes the server transport config to the driver and logs the effective paper", async () => {
	const imin = {
		id: "imin",
		isAvailable: vi.fn().mockResolvedValue(true),
		getStatus: vi.fn().mockResolvedValue({ ok: true, code: 0 }),
		printHTML: vi.fn().mockResolvedValue({ paper: "80mm", dots: 576 }),
		describe: () => ({ id: "imin" }),
	}
	const t = createTransport({
		drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") },
		config: {
			driver: "imin",
			paper: "80mm",
			custom_dots: undefined,
			cut: true,
		},
		logSink: log,
	})
	await t.printHTML("<html/>")
	expect(imin.printHTML).toHaveBeenCalledWith(
		"<html/>",
		expect.objectContaining({
			config: { paper: "80mm", customDots: undefined, cut: true },
		}),
	)
	expect(log.attempts.at(-1).paper_width).toBe("80mm")
})

it("maps the server print knobs the driver resolves (no copy labels)", async () => {
	const imin = {
		id: "imin",
		isAvailable: vi.fn().mockResolvedValue(true),
		getStatus: vi.fn().mockResolvedValue({ ok: true, code: 0 }),
		printHTML: vi.fn().mockResolvedValue({ paper: "58mm", dots: 384 }),
		describe: () => ({ id: "imin" }),
	}
	const t = createTransport({
		drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") },
		config: {
			driver: "imin",
			copies: 2,
			copy_delay_ms: 900,
			tail_dots: 40,
			// Obsolete server knob: must not reach the driver as a config key.
			copy_labels: true,
			font_scale: 110,
			crew_font_scale: 145,
			line_spacing: 80,
			side_margin: 24,
		},
		logSink: log,
	})
	await t.printHTML("<html/>")
	const config = imin.printHTML.mock.calls[0][1].config
	expect(config.crewFontScale).toBe(145)
	expect(config.fontScale).toBe(110)
	expect(config.copies).toBe(2)
	expect(config.copyDelayMs).toBe(900)
	expect(config.tailDots).toBe(40)
	expect(config.lineSpacing).toBe(80)
	expect(config.sideMarginDots).toBe(24)
	expect("copyLabels" in config).toBe(false)
})

it("maps side_margin even when the server omits it (absent, not 0)", async () => {
	const imin = {
		id: "imin",
		isAvailable: vi.fn().mockResolvedValue(true),
		printHTML: vi.fn().mockResolvedValue({ paper: "58mm", dots: 384 }),
		describe: () => ({ id: "imin" }),
	}
	const t = createTransport({
		drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") },
		config: { driver: "imin" },
		logSink: log,
	})
	await t.printHTML("<html/>")
	const config = imin.printHTML.mock.calls[0][1].config
	// An ABSENT server key must fall through to the resolver's default, so the
	// transport never invents a 0-dot margin.
	expect(config.sideMarginDots).toBeUndefined()
})

it("maps the eod server knobs camelCase for the Closing/EOD lane", async () => {
	const imin = {
		id: "imin",
		isAvailable: vi.fn().mockResolvedValue(true),
		getStatus: vi.fn().mockResolvedValue({ ok: true, code: 0 }),
		printHTML: vi.fn().mockResolvedValue({ paper: "58mm", dots: 384 }),
		describe: () => ({ id: "imin" }),
	}
	const t = createTransport({
		drivers: { imin, qz: okDriver("qz"), browser: okDriver("browser") },
		config: {
			driver: "imin",
			eod_copies: 2,
			eod_copy_delay_ms: 900,
			eod_feed_dots: 200,
			eod_tail_dots: 40,
			eod_font_scale: 120,
			eod_line_spacing: 90,
			eod_side_margin: 32,
		},
		logSink: log,
	})
	await t.printHTML("<html/>")
	expect(imin.printHTML.mock.calls[0][1].config).toMatchObject({
		eodCopies: 2,
		eodCopyDelayMs: 900,
		eodFeedDots: 200,
		eodTailDots: 40,
		eodFontScale: 120,
		eodLineSpacing: 90,
		eodSideMarginDots: 32,
	})
})

it("initTransportFromServer keeps the eod_* fields under the names the API returns", async () => {
	const { call } = await import("@/utils/apiWrapper")
	const { initTransportFromServer, getTransport } = await import("./transport")
	call.mockResolvedValueOnce({
		driver: "imin",
		eod_copies: 2,
		eod_side_margin: 32,
	})
	await initTransportFromServer("POS Profile juri1")
	const cfg = getTransport().getConfig()
	// Stored verbatim (snake_case): the transport maps them per print, so a
	// missing key must stay absent and fall through to the resolver's default.
	expect(cfg.eod_copies).toBe(2)
	expect(cfg.eod_side_margin).toBe(32)
	expect(cfg.eod_copy_delay_ms).toBeUndefined()
})

it("records why earlier drivers failed when a fallback succeeds", async () => {
	const imin = failDriver("imin", "upload rejected")
	const qz = failDriver("qz", "no qz tray")
	const browser = okDriver("browser")
	const t = createTransport({
		drivers: { imin, qz, browser },
		config: { driver: "imin", fallback_enabled: true },
		logSink: log,
	})
	await t.printHTML("<html/>")
	const row = log.attempts.at(-1)
	expect(row.status).toBe("Fallback")
	expect(row.driver).toBe("browser")
	expect(row.error_message).toContain("imin: upload rejected")
	expect(row.error_message).toContain("qz: no qz tray")
})

it("records why earlier drivers failed when a driver was skipped", async () => {
	const imin = okDriver("imin")
	imin.isAvailable.mockResolvedValue(false)
	const browser = okDriver("browser")
	const t = createTransport({
		drivers: { imin, qz: okDriver("qz"), browser },
		config: { driver: "imin", fallback_enabled: true },
		logSink: log,
	})
	await t.printHTML("<html/>")
	const row = log.attempts.at(-1)
	expect(row.status).toBe("Fallback")
	expect(row.error_message).toContain("skipped imin")
})

it("falls back to transport paper when the driver does not report the effective value", async () => {
	const imin = okDriver("imin") // mockResolvedValue(true)
	const t = createTransport({
		drivers: { imin },
		config: { driver: "imin", paper: "58mm" },
		logSink: log,
	})
	await t.printHTML("<html/>")
	expect(log.attempts.at(-1).paper_width).toBe("58mm")
})
