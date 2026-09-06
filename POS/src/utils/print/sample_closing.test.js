import { beforeEach, describe, expect, it, vi } from "vitest"

const logWarn = vi.hoisted(() => vi.fn())

vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ warn: logWarn, info: vi.fn(), error: vi.fn() }) },
}))
vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }))
// Same mock as sample_receipt.test.js: the wrapper's response-shape handling is
// not under test here — only WHICH calls the sample makes.
vi.mock("@/utils/printInvoice", () => ({
	fetchServerPrintHTML: vi
		.fn()
		.mockResolvedValue("<!DOCTYPE html><html><body>server</body></html>"),
}))

import { call } from "@/utils/apiWrapper"
import { fetchServerPrintHTML } from "@/utils/printInvoice"
import { EOD_PRINT_FORMAT, fetchSampleClosingBundle } from "./sample_closing"

beforeEach(() => {
	vi.clearAllMocks()
	logWarn.mockClear()
	call.mockImplementation((cmd) => {
		if (cmd === "pos_next.api.printing.get_latest_closing_shift") {
			return Promise.resolve("POSCL-2026-00001")
		}
		return Promise.reject(new Error(`unexpected call: ${cmd}`))
	})
})

describe("fetchSampleClosingBundle (Direct Print sample EOD)", () => {
	// The module cache outlives a single test, so a test that needs a fresh
	// read passes refresh: true — the same knob the mounted page uses.

	it("prints the latest closing shift through the real EOD print format", async () => {
		const bundle = await fetchSampleClosingBundle({ refresh: true })
		expect(call).toHaveBeenCalledWith(
			"pos_next.api.printing.get_latest_closing_shift",
		)
		// Same shape silentPrintDoc uses, so the sample IS a real print.
		expect(fetchServerPrintHTML).toHaveBeenCalledWith(
			"POS Closing Shift",
			"POSCL-2026-00001",
			EOD_PRINT_FORMAT,
		)
		expect(bundle).toEqual({
			source: "server",
			name: "POSCL-2026-00001",
			serverHTML: expect.stringContaining("server"),
		})
	})

	it("resolves source none when no shift has been closed yet", async () => {
		call.mockResolvedValue(null)
		const bundle = await fetchSampleClosingBundle({ refresh: true })
		expect(bundle).toEqual({ source: "none", name: null, serverHTML: null })
		expect(fetchServerPrintHTML).not.toHaveBeenCalled()
		expect(logWarn).toHaveBeenCalled()
	})

	it("resolves source none on any fetch failure, never throwing", async () => {
		call.mockRejectedValue(new Error("offline"))
		const bundle = await fetchSampleClosingBundle({ refresh: true })
		expect(bundle).toEqual({ source: "none", name: null, serverHTML: null })
		expect(logWarn).toHaveBeenCalled()
	})

	it("resolves source none when the print format returns no HTML", async () => {
		fetchServerPrintHTML.mockRejectedValueOnce(
			new Error("Failed to get print HTML from server"),
		)
		const bundle = await fetchSampleClosingBundle({ refresh: true })
		expect(bundle).toEqual({ source: "none", name: null, serverHTML: null })
	})

	it("reuses the cached bundle until refresh asks for a re-fetch", async () => {
		const first = await fetchSampleClosingBundle({ refresh: true })
		// The print format changed out from under the mounted page.
		fetchServerPrintHTML.mockResolvedValue(
			"<!DOCTYPE html><html><body>changed</body></html>",
		)
		expect(await fetchSampleClosingBundle()).toBe(first)
		expect(fetchServerPrintHTML).toHaveBeenCalledTimes(1)

		const refreshed = await fetchSampleClosingBundle({ refresh: true })
		expect(refreshed.serverHTML).toContain("changed")
		expect(fetchServerPrintHTML).toHaveBeenCalledTimes(2)
	})

	it("does not cache a miss — a later call finds a shift closed since", async () => {
		call.mockResolvedValueOnce(null)
		expect(await fetchSampleClosingBundle({ refresh: true })).toEqual({
			source: "none",
			name: null,
			serverHTML: null,
		})
		// A cached miss would have answered this read with no server call.
		expect(await fetchSampleClosingBundle()).toEqual({
			source: "server",
			name: "POSCL-2026-00001",
			serverHTML: expect.any(String),
		})
		expect(call).toHaveBeenCalledTimes(2)
	})
})
