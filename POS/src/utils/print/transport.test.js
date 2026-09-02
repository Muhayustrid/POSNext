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
