/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

const transportState = vi.hoisted(() => ({ fallback_enabled: undefined }))
const logWarn = vi.hoisted(() => vi.fn())

vi.mock("@/utils/logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			info: vi.fn(),
			warn: logWarn,
			error: vi.fn(),
		}),
	},
}))

vi.mock("./printInvoice", () => ({
	silentPrintDoc: vi.fn().mockResolvedValue(true),
}))

vi.mock("./print/transport", () => ({
	getTransport: () => ({
		getConfig: () => ({ fallback_enabled: transportState.fallback_enabled }),
	}),
}))

globalThis.__ = (m, r = []) => {
	if (!Array.isArray(r) || !r.length) return m
	let out = m
	for (const [i, v] of r.entries()) out = out.split(`{${i}}`).join(String(v))
	return out
}

import { printEODReport } from "./printEod"
import { silentPrintDoc } from "./printInvoice"

beforeEach(() => {
	vi.clearAllMocks()
	transportState.fallback_enabled = undefined
	silentPrintDoc.mockResolvedValue(true)
	vi.spyOn(window, "open").mockReturnValue({})
})

describe("printEODReport (EOD print logContext)", () => {
	it("routes through silentPrintDoc with pos_profile when given", async () => {
		await printEODReport("SHIFT-1", "POS Profile juri1")
		expect(silentPrintDoc).toHaveBeenCalledWith(
			"POS Closing Shift",
			"SHIFT-1",
			"POS Next EOD Report",
			"POS Profile juri1",
			// Its own print lane — the eod knobs, never a crew slip.
			"eod",
		)
	})

	it("still prints when pos_profile is absent (best-effort)", async () => {
		await printEODReport("SHIFT-2", null)
		expect(silentPrintDoc).toHaveBeenCalledWith(
			"POS Closing Shift",
			"SHIFT-2",
			"POS Next EOD Report",
			null,
			"eod",
		)
	})
})

describe("printEODReport (/printview fallback)", () => {
	it('resolves { method: "silent" } on silent success and never opens a window', async () => {
		await expect(
			printEODReport("SHIFT-1", "POS Profile juri1"),
		).resolves.toEqual({
			method: "silent",
			success: true,
		})
		expect(window.open).not.toHaveBeenCalled()
	})

	it('opens /printview when silent fails (fallback default) and resolves { method: "printview" }', async () => {
		silentPrintDoc.mockRejectedValue(new Error("iMin SDK not loaded yet"))

		await expect(
			printEODReport("SHIFT-1", "POS Profile juri1"),
		).resolves.toEqual({
			method: "printview",
			success: true,
		})

		// The driver failure must be logged before the lane switch.
		expect(logWarn).toHaveBeenCalledTimes(1)
		expect(logWarn).toHaveBeenCalledWith(
			"Silent print failed, falling back to /printview:",
			"iMin SDK not loaded yet",
		)

		expect(window.open).toHaveBeenCalledTimes(1)
		const url = window.open.mock.calls[0][0]
		expect(url).toContain("doctype=POS+Closing+Shift")
		expect(url).toContain("name=SHIFT-1")
		expect(url).toContain("format=POS+Next+EOD+Report")
		expect(url).toContain("trigger_print=1")

		const parsed = new URL(url, "http://x")
		expect(parsed.pathname).toBe("/printview")
		expect(parsed.searchParams.get("doctype")).toBe("POS Closing Shift")
		expect(parsed.searchParams.get("name")).toBe("SHIFT-1")
		expect(parsed.searchParams.get("format")).toBe("POS Next EOD Report")
		expect(parsed.searchParams.get("no_letterhead")).toBe("1")
		expect(parsed.searchParams.get("_lang")).toBe("en")
		expect(parsed.searchParams.get("trigger_print")).toBe("1")
		expect(parsed.searchParams.has("_t")).toBe(true)
		expect(window.open).toHaveBeenCalledWith(
			url,
			"_blank",
			"width=800,height=600",
		)
	})

	it("rethrows the original error when strict driver mode (fallback_enabled === false) is on", async () => {
		transportState.fallback_enabled = false
		const driverError = new Error("No print driver available")
		silentPrintDoc.mockRejectedValue(driverError)

		const caught = await printEODReport("SHIFT-3", "POS Profile juri1").catch(
			(e) => e,
		)
		// Identity, not message text: strict mode must surface the driver's own
		// error object, not a re-wrapped one.
		expect(caught).toBe(driverError)
		expect(logWarn).not.toHaveBeenCalled()
		expect(window.open).not.toHaveBeenCalled()
	})

	it("throws a popup-blocked error when window.open returns null", async () => {
		silentPrintDoc.mockRejectedValue(new Error("iMin SDK not loaded yet"))
		vi.spyOn(window, "open").mockReturnValue(null)

		await expect(
			printEODReport("SHIFT-4", "POS Profile juri1"),
		).rejects.toThrow("Popup blocked")
	})
})
