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
			printed: true,
		})
		expect(window.open).not.toHaveBeenCalled()
	})

	it("marks the print as failed (no automatic fallback) when silent fails", async () => {
		const driverError = new Error("iMin SDK not loaded yet")
		silentPrintDoc.mockRejectedValue(driverError)

		// NO automatic fallback anymore: the caller gets a printed:false marker
		// and shows the retry panel (Print Ulang / Export PDF / Tutup).
		await expect(
			printEODReport("SHIFT-1", "POS Profile juri1"),
		).resolves.toEqual({
			method: "silent",
			success: false,
			printed: false,
			error: driverError,
		})

		// The driver failure must still be logged before returning.
		expect(logWarn).toHaveBeenCalledTimes(1)
		expect(logWarn).toHaveBeenCalledWith(
			"Silent print failed:",
			"iMin SDK not loaded yet",
		)

		expect(window.open).not.toHaveBeenCalled()
	})

	it("downloadEodPDF posts to the Frappe download_pdf endpoint", async () => {
		const fetchMock = vi.fn().mockResolvedValue({
			ok: true,
			blob: async () => new Blob(["%PDF"]),
		})
		vi.stubGlobal("fetch", fetchMock)
		URL.createObjectURL = vi.fn(() => "blob:x")
		URL.revokeObjectURL = vi.fn()

		const { downloadEodPDF } = await import("./printEod")
		await downloadEodPDF("SHIFT-9")

		expect(fetchMock.mock.calls[0][0]).toContain(
			"frappe.utils.print_format.download_pdf",
		)
		const body = fetchMock.mock.calls[0][1].body
		expect(body.get("doctype")).toBe("POS Closing Shift")
		expect(body.get("name")).toBe("SHIFT-9")
		expect(body.get("format")).toBe("POS Next EOD Report")
		vi.unstubAllGlobals()
	})
})
