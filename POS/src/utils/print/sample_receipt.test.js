import { beforeEach, describe, expect, it, vi } from "vitest"

const logWarn = vi.hoisted(() => vi.fn())

vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ warn: logWarn, info: vi.fn(), error: vi.fn() }) },
}))
vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }))
// The server HTML wrapper is printInvoice's own (one response-shape handler
// for silentPrintDoc, silentPrintInvoice and this sample) — mocked here so
// these tests stay about WHICH calls the sample makes, not the wrapper shape.
vi.mock("@/utils/printInvoice", () => ({
	fetchServerPrintHTML: vi
		.fn()
		.mockResolvedValue("<!DOCTYPE html><html><body>server</body></html>"),
}))

import { call } from "@/utils/apiWrapper"
import { fetchServerPrintHTML } from "@/utils/printInvoice"
import { fetchSampleReceiptBundle } from "./sample_receipt"

const fallbackDoc = { name: "TEST", customer: "Walk-in Customer" }

beforeEach(() => {
	vi.clearAllMocks()
	logWarn.mockClear()
	call.mockImplementation((cmd, params) => {
		if (cmd === "pos_next.api.invoices.get_invoices") {
			return Promise.resolve([{ name: "SINV-0009", customer_name: "Kafe" }])
		}
		if (cmd === "pos_next.api.invoices.get_invoice") {
			return Promise.resolve({
				name: "SINV-0009",
				items: [{ item_name: "Kopi Susu", qty: 1 }],
			})
		}
		return Promise.reject(new Error(`unexpected call: ${cmd}`))
	})
})

describe("fetchSampleReceiptBundle (Direct Print sample receipt)", () => {
	it("uses the last invoice of the profile through the real print format", async () => {
		const bundle = await fetchSampleReceiptBundle(
			"POS Profile juri1",
			fallbackDoc,
		)
		expect(call).toHaveBeenCalledWith("pos_next.api.invoices.get_invoices", {
			pos_profile: "POS Profile juri1",
			limit: 1,
		})
		expect(call).toHaveBeenCalledWith("pos_next.api.invoices.get_invoice", {
			invoice_name: "SINV-0009",
		})
		// Same shape silentPrintDoc uses, so the sample IS a real print.
		expect(fetchServerPrintHTML).toHaveBeenCalledWith(
			"Sales Invoice",
			"SINV-0009",
			"POS Next Receipt",
		)
		expect(bundle.source).toBe("server")
		expect(bundle.serverHTML).toContain("server")
		// The doc rides along: the crew slip needs the items, not the HTML.
		expect(bundle.invoiceDoc.items[0].item_name).toBe("Kopi Susu")
	})

	it("falls back to the local test doc when the profile has no invoices", async () => {
		call.mockImplementation((_cmd, params) =>
			Promise.resolve(params.limit === 1 ? [] : { name: "x" }),
		)
		const bundle = await fetchSampleReceiptBundle("P", fallbackDoc)
		expect(bundle).toEqual({
			source: "fallback",
			serverHTML: null,
			invoiceDoc: fallbackDoc,
		})
		expect(logWarn).toHaveBeenCalled()
	})

	it("falls back on any fetch failure, never throwing", async () => {
		call.mockRejectedValue(new Error("offline"))
		const bundle = await fetchSampleReceiptBundle("P", fallbackDoc)
		expect(bundle.source).toBe("fallback")
		expect(bundle.invoiceDoc).toBe(fallbackDoc)
		expect(bundle.serverHTML).toBeNull()
	})

	it("falls back when the invoice doc cannot be read", async () => {
		call.mockImplementation((cmd) =>
			cmd === "pos_next.api.invoices.get_invoice"
				? Promise.reject(new Error("no permission"))
				: Promise.resolve([{ name: "SINV-0009" }]),
		)
		const bundle = await fetchSampleReceiptBundle("P", fallbackDoc)
		expect(bundle.source).toBe("fallback")
		expect(fetchServerPrintHTML).not.toHaveBeenCalled()
	})

	it("falls back when the print format returns no HTML", async () => {
		fetchServerPrintHTML.mockRejectedValueOnce(
			new Error("Failed to get print HTML from server"),
		)
		const bundle = await fetchSampleReceiptBundle("P", fallbackDoc)
		expect(bundle.source).toBe("fallback")
		expect(bundle.invoiceDoc).toBe(fallbackDoc)
	})

	it("falls back when there is no profile in scope", async () => {
		const bundle = await fetchSampleReceiptBundle(null, fallbackDoc)
		expect(bundle.source).toBe("fallback")
		expect(call).not.toHaveBeenCalled()
	})
})
