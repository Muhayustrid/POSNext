/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("./printInvoice", () => ({
	silentPrintDoc: vi.fn().mockResolvedValue(true),
}))

globalThis.__ = (m, r = []) => {
	if (!Array.isArray(r) || !r.length) return m
	let out = m
	for (const [i, v] of r.entries()) out = out.split(`{${i}}`).join(String(v))
	return out
}

import { printEODReport } from "./printEod"
import { silentPrintDoc } from "./printInvoice"

beforeEach(() => vi.clearAllMocks())

describe("printEODReport (EOD print logContext)", () => {
	it("routes through silentPrintDoc with pos_profile when given", async () => {
		await printEODReport("SHIFT-1", "POS Profile juri1")
		expect(silentPrintDoc).toHaveBeenCalledWith(
			"POS Closing Shift",
			"SHIFT-1",
			"POS Next EOD Report",
			"POS Profile juri1",
		)
	})

	it("still prints when pos_profile is absent (best-effort)", async () => {
		await printEODReport("SHIFT-2", null)
		expect(silentPrintDoc).toHaveBeenCalledWith(
			"POS Closing Shift",
			"SHIFT-2",
			"POS Next EOD Report",
			null,
		)
	})
})
