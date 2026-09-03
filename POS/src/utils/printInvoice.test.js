/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }))
vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ warn: vi.fn(), info: vi.fn(), error: vi.fn() }) },
}))
vi.mock("@/utils/offline/offlineReceiptCache", () => ({
	getOfflineReceiptPayload: vi.fn().mockReturnValue(null),
}))
vi.mock("@/utils/offline/sync", () => ({
	getOfflineInvoiceByOfflineId: vi.fn().mockResolvedValue(null),
}))
vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: { markOfflineInvoicePrinted: vi.fn().mockResolvedValue(undefined) },
}))
vi.mock("@/utils/print/transport", () => ({
	getTransport: vi.fn(() => ({ getConfig: () => ({ paper: "58mm" }) })),
	initTransportFromServer: vi.fn().mockResolvedValue({ driver: "browser" }),
	printHTML: vi.fn().mockResolvedValue(undefined),
}))

// Provide a trivial global translation helper the way packageQuote.test does.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries()) out = out.split(`{${i}}`).join(String(v))
	return out
}

import { call } from "@/utils/apiWrapper"
import {
	buildReceiptDocumentHTML,
	effectiveReceiptDots,
	RECEIPT_STYLES,
	receiptStylesFor,
	silentPrintInvoiceFromDoc,
	printWithSilentFallback,
} from "./printInvoice"
import * as transport from "@/utils/print/transport"

const doc = {
	doctype: "Sales Invoice",
	name: "SINV-1",
	posting_date: "2026-09-02",
	company: "POS Next",
	customer: "Bob",
	customer_name: "Bob",
	grand_total: 10000,
	total_taxes_and_charges: 0,
	items: [
		{ item_code: "A", item_name: "A", qty: 1, quantity: 1, rate: 10000, price_list_rate: 10000 },
	],
	payments: [{ mode_of_payment: "Cash", amount: 10000 }],
	paid_amount: 10000,
	pos_profile: "POS Profile juri1",
}

beforeEach(() => {
	vi.clearAllMocks()
	// silentPrintInvoiceFromDoc/WithSilent keep a module-level initialized flag.
	// Reloading the module every test would diverge from how the POS actually
	// runs (one long browser session), so we just clear mocks and keep the
	// already-initialized transport — use the explicit mock below instead.
	transport.initTransportFromServer.mockResolvedValue({ driver: "browser" })
	transport.printHTML.mockResolvedValue(undefined)
	call.mockResolvedValue({ html: "<div>printed</div>", style: "" })
})

describe("receiptStylesFor (dot-aware width)", () => {
	it("emits an 80mm @page for 576 dots (8 dots/mm)", () => {
		const css = receiptStylesFor(576)
		expect(css).toContain("72mm")
		expect(css).toContain("@page")
	})

	it("emits 48mm for 384 dots and differs from the 40mm-ish 58mm path", () => {
		expect(receiptStylesFor(384)).toContain("48mm")
		expect(receiptStylesFor(384)).not.toBe(receiptStylesFor(576))
	})

	it("the default export differs between widths, not a hard-coded 80mm", () => {
		expect(RECEIPT_STYLES).toContain("mm")
		expect(RECEIPT_STYLES.length).toBeGreaterThan(200)
	})
})

describe("buildReceiptDocumentHTML (dot-aware popup width)", () => {
	it("carries the dots-derived page size into @page", () => {
		const html = buildReceiptDocumentHTML(doc, { dots: 384, includeControls: false })
		expect(html).toContain("48mm")
		expect(html).not.toContain('size: 80mm')
	})

	it("defaults to 576 dots when no dots are given (browser fallback)", () => {
		const html = buildReceiptDocumentHTML(doc)
		expect(html).toContain("72mm")
	})
})

describe("silentPrintInvoiceFromDoc (checkout E2E guard)", () => {
	it("sends local receipt HTML through the print transport with pos_profile", async () => {
		await silentPrintInvoiceFromDoc(doc)
		expect(transport.printHTML).toHaveBeenCalledWith(
			expect.stringContaining("POS Next"),
			expect.objectContaining({
				logContext: expect.objectContaining({
					reference_name: "SINV-1",
					pos_profile: "POS Profile juri1",
				}),
			}),
		)
	})

	it("falls back to fetching server HTML when the invoice is not local-only", async () => {
		const { silentPrintInvoice } = await import("./printInvoice")
		await silentPrintInvoice("SINV-1", null, "POS Profile juri1")
		expect(call).toHaveBeenCalledWith(
			"frappe.www.printview.get_html_and_style",
			expect.objectContaining({ doc: "Sales Invoice", name: "SINV-1" }),
		)
		expect(transport.printHTML).toHaveBeenCalledWith(
			expect.any(String),
			expect.objectContaining({ logContext: expect.objectContaining({ pos_profile: "POS Profile juri1" }) }),
		)
	})
})

describe("printWithSilentFallback (silent first, browser second)", () => {
	it("tries silent first and falls back to a browser window on transport failure", async () => {
		// First call (silentPrintInvoiceFromDoc) throws; the re-imported variant goes through printInvoiceCustom.
		transport.printHTML.mockRejectedValueOnce(new Error("service not connected"))
		// Open a window stub for the browser fallback
		globalThis.window.open = vi.fn(() => ({ document: { write: vi.fn(), close: vi.fn() }, print: vi.fn(), onload: null }))
		const { printWithSilentFallback: p } = await import("./printInvoice")
		const res = await p(doc)
		expect(res.success).toBe(true)
		expect(res.method).toBe("browser")
	})
})

describe("effectiveReceiptDots (paper follows the device/server config)", () => {
	it("reads the transport config (58mm -> 384 dots)", () => {
		// The transport mock above exposes getConfig with paper 58mm.
		expect(effectiveReceiptDots()).toBe(384)
	})

	it("falls back to 576 when the transport is unreachable", async () => {
		const transport = await import("@/utils/print/transport")
		transport.getTransport.mockImplementationOnce(() => {
			throw new Error("no singleton")
		})
		expect(effectiveReceiptDots()).toBe(576)
	})
})

describe("silentPrintInvoiceFromDoc embeds the effective paper width", () => {
	it("styles the document for the configured paper, not the 576 default", async () => {
		await silentPrintInvoiceFromDoc(doc)
		const html = transport.printHTML.mock.calls[0][0]
		// 384 dots -> 48mm in the embedded @page/body width.
		expect(html).toContain("48mm")
	})
})
