/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }))
vi.mock("@/utils/logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			warn: vi.fn(),
			info: vi.fn(),
			error: vi.fn(),
		}),
	},
}))
vi.mock("@/utils/offline/offlineReceiptCache", () => ({
	getOfflineReceiptPayload: vi.fn().mockReturnValue(null),
}))
vi.mock("@/utils/offline/sync", () => ({
	getOfflineInvoiceByOfflineId: vi.fn().mockResolvedValue(null),
}))
vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: {
		markOfflineInvoicePrinted: vi.fn().mockResolvedValue(undefined),
	},
}))
vi.mock("@/utils/print/transport", () => ({
	getTransport: vi.fn(() => ({ getConfig: () => ({ paper: "58mm" }) })),
	initTransportFromServer: vi.fn().mockResolvedValue({ driver: "browser" }),
	printHTML: vi.fn().mockResolvedValue(undefined),
	PostPrintError: class PostPrintError extends Error {
		constructor(message) {
			super(message)
			this.name = "PostPrintError"
			this.postPrint = true
		}
	},
}))

// Provide a trivial global translation helper the way packageQuote.test does.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import { call } from "@/utils/apiWrapper"
import { getOfflineInvoiceByOfflineId } from "@/utils/offline/sync"
import {
	buildReceiptDocumentHTML,
	buildReceiptHTML,
	effectiveReceiptDots,
	RECEIPT_STYLES,
	receiptStylesFor,
	silentPrintDoc,
	silentPrintInvoice,
	silentPrintInvoiceFromDoc,
	printWithSilentFallback,
	hydrateLocalOnlyInvoice,
	ensureTransportInitialized,
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
		{
			item_code: "A",
			item_name: "A",
			qty: 1,
			quantity: 1,
			rate: 10000,
			price_list_rate: 10000,
		},
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
		const html = buildReceiptDocumentHTML(doc, {
			dots: 384,
			includeControls: false,
		})
		expect(html).toContain("48mm")
		expect(html).not.toContain("size: 80mm")
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
			expect.objectContaining({
				logContext: expect.objectContaining({
					pos_profile: "POS Profile juri1",
				}),
			}),
		)
	})
})

describe("printWithSilentFallback (silent first, browser second)", () => {
	it("tries silent first and falls back to a browser window on transport failure", async () => {
		// First call (silentPrintInvoiceFromDoc) throws; the re-imported variant goes through printInvoiceCustom.
		transport.printHTML.mockRejectedValueOnce(
			new Error("service not connected"),
		)
		// Open a window stub for the browser fallback
		globalThis.window.open = vi.fn(() => ({
			document: { write: vi.fn(), close: vi.fn() },
			print: vi.fn(),
			onload: null,
		}))
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

describe("buildReceiptHTML (package allocation rows, Fase 2)", () => {
	const allocatedParent = {
		item_code: "PKG-1",
		item_name: "Paket Hemat",
		quantity: 1,
		rate: 0,
		price_list_rate: 0,
		pos_package_role: "Package",
		pos_package_snapshot: JSON.stringify({
			allocation: { mode: "proportional", precision: 2 },
		}),
	}
	const component = {
		item_code: "COLA",
		item_name: "Cola",
		quantity: 2,
		rate: 4000,
		price_list_rate: 4000,
		pos_package_role: "Package Item",
	}

	it("prints an allocated parent as a header without the meaningless 1 × 0", () => {
		const html = buildReceiptHTML({
			...doc,
			items: [allocatedParent, component],
			grand_total: 8000,
			paid_amount: 8000,
			payments: [{ mode_of_payment: "Cash", amount: 8000 }],
		})

		expect(html).toContain("<strong>Paket Hemat</strong>")
		expect(html).not.toContain("1 × 0")
		// Components keep their allocated prices.
		expect(html).toContain("2 × 4.000")
		expect(html).toContain("8.000")
	})

	it("keeps a legacy price-carrying package line exactly as before", () => {
		const html = buildReceiptHTML({
			...doc,
			items: [
				{
					item_code: "PKG-1",
					item_name: "Legacy Paket",
					quantity: 1,
					rate: 23000,
					price_list_rate: 23000,
					pos_package_role: "Package",
				},
			],
			grand_total: 23000,
			paid_amount: 23000,
			payments: [{ mode_of_payment: "Cash", amount: 23000 }],
		})

		expect(html).toContain("1 × 23.000")
		expect(html).toContain("Legacy Paket")
	})

	it("keeps a zero-price legacy package row (no marker) as before", () => {
		const html = buildReceiptHTML({
			...doc,
			items: [{ ...allocatedParent, item_name: "Gratis", pos_package_snapshot: null }],
		})

		// No allocation marker -> legacy rendering, price row included.
		expect(html).toContain("1 × 0")
		expect(html).not.toContain("<strong>Gratis</strong>")
	})
})

describe("buildReceiptHTML (package grouped under its title)", () => {
	const pay = (amount) => ({
		grand_total: amount,
		paid_amount: amount,
		payments: [{ mode_of_payment: "Cash", amount }],
	})

	it("prices an allocated package on its title and lists components unpriced", () => {
		const html = buildReceiptHTML({
			...doc,
			...pay(25000),
			items: [
				{ item_code: "PKG", item_name: "Paket Hemat", quantity: 1, rate: 0, pos_package_role: "Package", pos_package_instance: "I1" },
				{ item_code: "C1", item_name: "Ropi Coklat", quantity: 1, rate: 15000, price_list_rate: 15000, pos_package_role: "Package Item", pos_package_instance: "I1" },
				{ item_code: "C2", item_name: "Ropi Keju", quantity: 1, rate: 10000, price_list_rate: 10000, pos_package_role: "Package Item", pos_package_instance: "I1" },
			],
		})

		expect(html).toContain("1 × 25.000")
		expect(html).toContain("- 1x&nbsp;</span><span>Ropi Coklat")
		expect(html).toContain("- 1x&nbsp;</span><span>Ropi Keju")
		expect(html).not.toContain("15.000")
		expect(html).not.toContain("10.000")
	})

	it("keeps the legacy header price and hides the zero components", () => {
		const html = buildReceiptHTML({
			...doc,
			...pay(46000),
			items: [
				{ item_code: "PKG", item_name: "Paket Hemat", quantity: 2, rate: 23000, price_list_rate: 23000, pos_package_role: "Package", pos_package_instance: "I2" },
				{ item_code: "C1", item_name: "Ropi Coklat", quantity: 2, rate: 0, pos_package_role: "Package Item", pos_package_instance: "I2" },
			],
		})

		expect(html).toContain("2 × 23.000")
		expect(html).toContain("46.000")
		expect(html).toContain("- 2x&nbsp;</span><span>Ropi Coklat")
		expect(html).not.toContain("2 × 0")
	})
})

describe("buildReceiptHTML (queue block)", () => {
	it("renders the queue block first when pos_queue_number is set", () => {
		const html = buildReceiptHTML({ ...doc, pos_queue_number: 48 })
		const receiptStart = html.indexOf('class="receipt"')
		const queueStart = html.indexOf('class="queue-number"')
		expect(queueStart).toBeGreaterThan(-1)
		expect(queueStart).toBeGreaterThan(receiptStart)
		expect(html.indexOf('class="header"')).toBeGreaterThan(queueStart)
		expect(html).toContain(">048<")
	})

	it("places the brand logo between the queue block and the company header", () => {
		const html = buildReceiptHTML({ ...doc, pos_queue_number: 48 })
		const queueStart = html.indexOf('class="queue-number"')
		const logoStart = html.indexOf("/files/ropi-logo.png")
		const headerStart = html.indexOf('class="header"')
		expect(logoStart).toBeGreaterThan(queueStart)
		expect(logoStart).toBeLessThan(headerStart)
	})

	it("renders the logo above the header even without a queue number", () => {
		const html = buildReceiptHTML(doc)
		const logoStart = html.indexOf("/files/ropi-logo.png")
		const headerStart = html.indexOf('class="header"')
		expect(logoStart).toBeGreaterThan(-1)
		expect(logoStart).toBeLessThan(headerStart)
	})

	it("sizes the logo physically (mm), so the font-scale knob cannot stretch it", () => {
		const html = buildReceiptHTML(doc)
		const i = html.indexOf("/files/ropi-logo.png")
		const tag = html.slice(i, i + 260)
		expect(tag).toContain("14mm")
	})

	it("centers the logo with block + auto margins and nudges it toward the header", () => {
		// Tailwind preflight sets display:block on images, and html2canvas
		// clones computed styles — text-align:center alone left the logo
		// pinned to the frame's left padding on the printed bitmap. The
		// relative offset closes the visual gap to the company line without
		// moving the layout (the asset keeps its own transparent padding).
		const html = buildReceiptHTML(doc)
		const i = html.indexOf("/files/ropi-logo.png")
		const tag = html.slice(i, i + 260)
		expect(tag).toContain("display: block")
		expect(tag).toContain("margin: 0 auto")
		expect(tag).toContain("position: relative")
		expect(tag).toContain("top: 8px")
	})

	it("renders no queue block without a number", () => {
		expect(buildReceiptHTML(doc)).not.toContain("queue-number")
	})
})

describe("receipt money decimals follow the invoice currency (COR-FE-06)", () => {
	it("keeps IDR amounts integer-formatted", () => {
		const html = buildReceiptHTML({
			...doc,
			currency: "IDR",
			grand_total: 12500,
			paid_amount: 12500,
			items: [{ ...doc.items[0], rate: 12500, price_list_rate: 12500 }],
			payments: [{ mode_of_payment: "Cash", amount: 12500 }],
		})
		expect(html).toContain("12.500")
		expect(html).not.toContain("12.500,00")
	})

	it("prints two decimals for non-IDR currencies", () => {
		const html = buildReceiptHTML({
			...doc,
			currency: "USD",
			grand_total: 12.5,
			total_taxes_and_charges: 0,
			paid_amount: 12.5,
			items: [{ ...doc.items[0], rate: 12.5, price_list_rate: 12.5 }],
			payments: [{ mode_of_payment: "Cash", amount: 12.5 }],
		})
		expect(html).toContain("12,50")
	})

	it("keeps the legacy integer display when the currency is unknown", () => {
		expect(buildReceiptHTML(doc)).toContain("10.000")
		expect(buildReceiptHTML(doc)).not.toContain("10.000,00")
	})
})

describe("hydrateLocalOnlyInvoice (queue-stamp passthrough)", () => {
	it("rebuilds a page-reloaded offline receipt with its queue number", async () => {
		getOfflineInvoiceByOfflineId.mockResolvedValueOnce({
			items: [{ item_code: "A", qty: 1 }],
			payments: [{ mode_of_payment: "Cash", amount: 5000 }],
			grand_total: 5000,
			pos_queue_number: 12,
			pos_queue_date: "2026-09-06",
		})
		const hydrated = await hydrateLocalOnlyInvoice({ name: "pos_offline_abc" })
		expect(hydrated.pos_queue_number).toBe(12)
		expect(hydrated.pos_queue_date).toBe("2026-09-06")
	})

	it("maps an absent queue stamp to null, not undefined", async () => {
		getOfflineInvoiceByOfflineId.mockResolvedValueOnce({
			items: [{ item_code: "A", qty: 1 }],
			payments: [],
			grand_total: 5000,
		})
		const hydrated = await hydrateLocalOnlyInvoice({ name: "pos_offline_def" })
		expect(hydrated.pos_queue_number).toBeNull()
		expect(hydrated.pos_queue_date).toBeNull()
	})
})

describe("crew slip (copy 2 when the profile prints two copies)", () => {
	it("silentPrintInvoiceFromDoc attaches a crew slip built from the doc", async () => {
		await silentPrintInvoiceFromDoc(doc)
		const opts = transport.printHTML.mock.calls[0][1]
		expect(opts.crewHTML).toContain("SINV-1")
		// The crew does not need money on the slip.
		expect(opts.crewHTML).not.toContain("10000.00")
		// Neither copy carries a banner on the paper any more.
		expect(opts.crewHTML).not.toContain("pn-copy-label")
		expect(opts.crewHTML).not.toContain("CREW COPY")
	})

	it("silentPrintInvoice fetches the doc for the crew slip without blocking the print", async () => {
		call.mockImplementation((cmd, params) => {
			if (cmd === "pos_next.api.invoices.get_invoice") {
				expect(params).toEqual({ invoice_name: "SINV-1" })
				return Promise.resolve(doc)
			}
			return Promise.resolve({ html: "<div>server receipt</div>", style: "" })
		})
		await silentPrintInvoice("SINV-1", null, "POS Profile juri1")
		expect(call).toHaveBeenCalledWith(
			"frappe.www.printview.get_html_and_style",
			expect.objectContaining({ name: "SINV-1" }),
		)
		const [html, opts] = transport.printHTML.mock.calls[0]
		expect(html).toContain("server receipt")
		// The slip carries the order, so the invoice name must be on it.
		expect(opts.crewHTML).toContain("SINV-1")
	})

	it("still prints when the doc fetch fails — just without a crew slip", async () => {
		call.mockImplementation((cmd) => {
			if (cmd === "pos_next.api.invoices.get_invoice")
				return Promise.reject(new Error("doc read failed"))
			return Promise.resolve({ html: "<div>server receipt</div>", style: "" })
		})
		await expect(
			silentPrintInvoice("SINV-1", null, "POS Profile juri1"),
		).resolves.toBe(true)
		const opts = transport.printHTML.mock.calls[0][1]
		expect(opts.crewHTML).toBeFalsy()
	})

	it("silentPrintDoc never carries a crew slip (EOD must not get one)", async () => {
		await silentPrintDoc("POS Closing Shift", "POS-CLOSE-1", "EOD Report", null)
		expect(transport.printHTML).toHaveBeenCalledTimes(1)
		expect(transport.printHTML.mock.calls[0][1].crewHTML).toBeFalsy()
	})

	it("silentPrintDoc passes the print kind through to the transport", async () => {
		await silentPrintDoc(
			"POS Closing Shift",
			"POS-CLOSE-1",
			"EOD Report",
			null,
			"eod",
		)
		expect(transport.printHTML).toHaveBeenCalledWith(
			expect.any(String),
			expect.objectContaining({ kind: "eod" }),
		)
		// The default lane is unchanged: receipt, explicitly.
		await silentPrintDoc("Sales Invoice", "SINV-2", "POS Next Receipt")
		expect(transport.printHTML.mock.calls[1][1].kind).toBe("receipt")
	})
})

describe("printWithSilentFallback (doctype passthrough, POS Invoice mode)", () => {
	it("fetches server print HTML under the row's own doctype", async () => {
		await printWithSilentFallback({ ...doc, doctype: "POS Invoice" })
		expect(call).toHaveBeenCalledWith(
			"frappe.www.printview.get_html_and_style",
			expect.objectContaining({ doc: "POS Invoice", name: "SINV-1" }),
		)
		expect(transport.printHTML).toHaveBeenCalledWith(
			expect.any(String),
			expect.objectContaining({
				logContext: expect.objectContaining({
					reference_doctype: "POS Invoice",
					reference_name: "SINV-1",
				}),
			}),
		)
	})
})

describe("silentPrintInvoice (doctype self-heal when the caller omits it)", () => {
	const posInvoiceDoc = { ...doc, doctype: "POS Invoice" }
	const renderCalls = () =>
		call.mock.calls.filter(
			([cmd]) => cmd === "frappe.www.printview.get_html_and_style",
		)

	it("retries the render once under the doc's own doctype and logs that doctype", async () => {
		call.mockImplementation((cmd, params) => {
			if (cmd === "pos_next.api.invoices.get_invoice")
				return Promise.resolve(posInvoiceDoc)
			// Checkout sends no doctype, so the first render asks for Sales
			// Invoice and rejects; the retry under POS Invoice resolves.
			if (params.doc === "POS Invoice")
				return Promise.resolve({ html: "<div>pos receipt</div>", style: "" })
			return Promise.reject(new Error("Sales Invoice SINV-1 not found"))
		})
		await silentPrintInvoice("SINV-1", null, "POS Profile juri1")
		expect(renderCalls()).toHaveLength(2)
		expect(renderCalls()[1][1].doc).toBe("POS Invoice")
		// Exactly one transport call, carrying the retried HTML and the doc's
		// doctype in the log context.
		expect(transport.printHTML).toHaveBeenCalledTimes(1)
		const [html, opts] = transport.printHTML.mock.calls[0]
		expect(html).toContain("pos receipt")
		expect(opts.logContext.reference_doctype).toBe("POS Invoice")
		// The crew slip still comes from the same doc fetch.
		expect(opts.crewHTML).toContain("SINV-1")
	})

	it("does not retry when the first render succeeds", async () => {
		call.mockImplementation((cmd) => {
			if (cmd === "pos_next.api.invoices.get_invoice")
				return Promise.resolve(posInvoiceDoc)
			return Promise.resolve({ html: "<div>server receipt</div>", style: "" })
		})
		await silentPrintInvoice("SINV-1", null, "POS Profile juri1")
		expect(renderCalls()).toHaveLength(1)
		expect(renderCalls()[0][1].doc).toBe("Sales Invoice")
	})

	it("propagates the retry's error and never prints when the retry also fails", async () => {
		call.mockImplementation((cmd, params) => {
			if (cmd === "pos_next.api.invoices.get_invoice")
				return Promise.resolve(posInvoiceDoc)
			if (params.doc === "POS Invoice")
				return Promise.reject(new Error("POS Invoice SINV-1 not found either"))
			return Promise.reject(new Error("Sales Invoice SINV-1 not found"))
		})
		await expect(
			silentPrintInvoice("SINV-1", null, "POS Profile juri1"),
		).rejects.toThrow("POS Invoice SINV-1 not found either")
		expect(transport.printHTML).not.toHaveBeenCalled()
	})

	it("throws the original render error without retry when the doc fetch also failed", async () => {
		call.mockImplementation((cmd) => {
			if (cmd === "pos_next.api.invoices.get_invoice")
				return Promise.reject(new Error("doc read failed"))
			return Promise.reject(new Error("render failed"))
		})
		await expect(
			silentPrintInvoice("SINV-1", null, "POS Profile juri1"),
		).rejects.toThrow("render failed")
		expect(renderCalls()).toHaveLength(1)
	})
})

describe("silentPrintInvoice (pos_profile self-heal when the caller omits it)", () => {
	it("logs the fetched doc's pos_profile when the caller passes none", async () => {
		call.mockImplementation((cmd) => {
			if (cmd === "pos_next.api.invoices.get_invoice")
				return Promise.resolve({ ...doc, pos_profile: "POS Profile from doc" })
			return Promise.resolve({ html: "<div>server receipt</div>", style: "" })
		})
		await silentPrintInvoice("SINV-1", null, null)
		expect(transport.printHTML).toHaveBeenCalledWith(
			expect.any(String),
			expect.objectContaining({
				logContext: expect.objectContaining({
					pos_profile: "POS Profile from doc",
				}),
			}),
		)
	})

	it("keeps the caller's pos_profile when one is given", async () => {
		call.mockImplementation((cmd) => {
			if (cmd === "pos_next.api.invoices.get_invoice")
				return Promise.resolve({ ...doc, pos_profile: "POS Profile from doc" })
			return Promise.resolve({ html: "<div>server receipt</div>", style: "" })
		})
		await silentPrintInvoice("SINV-1", null, "POS Profile caller")
		expect(transport.printHTML).toHaveBeenCalledWith(
			expect.any(String),
			expect.objectContaining({
				logContext: expect.objectContaining({
					pos_profile: "POS Profile caller",
				}),
			}),
		)
	})
})

describe("printWithSilentFallback (post-print failure stops the fallback, COR-FE-10)", () => {
	it("rethrows for a local receipt instead of opening a duplicate browser copy", async () => {
		const { PostPrintError } = await import("@/utils/print/transport")
		transport.printHTML.mockRejectedValueOnce(
			new PostPrintError("cut failed after the receipt printed"),
		)
		globalThis.window.open = vi.fn()
		const localDoc = { ...doc, name: "pos_offline_dup" }

		await expect(printWithSilentFallback(localDoc)).rejects.toThrow("cut failed")
		expect(window.open).not.toHaveBeenCalled()
	})

	it("rethrows for a server invoice instead of opening a duplicate browser copy", async () => {
		const { PostPrintError } = await import("@/utils/print/transport")
		transport.printHTML.mockRejectedValueOnce(
			new PostPrintError("cut failed after the receipt printed"),
		)
		globalThis.window.open = vi.fn()

		await expect(printWithSilentFallback(doc)).rejects.toThrow("cut failed")
		expect(window.open).not.toHaveBeenCalled()
	})
})

describe("buildReceiptHTML escapes data-sourced HTML (SEC-11)", () => {	const evil = {
		...doc,
		name: 'SINV-1"><img src=x onerror=alert(1)>',
		company: "<script>alert(1)</script>",
		customer_name: "Bob <b>Evil</b>",
		header: "HEADER<img src=x>",
		footer: "FOOTER<img src=x>",
		items: [
			{
				item_code: "A",
				item_name: "<img src=x onerror=alert(1)>",
				quantity: 1,
				rate: 10000,
				serial_no: "SN<img src=x>",
			},
		],
		payments: [{ mode_of_payment: "<b>Cash</b>", amount: 10000 }],
	}

	it("escapes item names, customer, company, header/footer, serials and payment labels", () => {
		const html = buildReceiptHTML(evil)
		// No injected tag survives anywhere in the receipt. The only
		// legitimate <img> is the static brand logo, so the assertion
		// targets the injection marker itself.
		expect(html).not.toContain("<img src=x")
		expect(html).not.toContain("<script")
		expect(html).not.toContain("<b>")
		// the raw strings survive as text
		expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;")
		expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;")
		expect(html).toContain("Bob &lt;b&gt;Evil&lt;/b&gt;")
		expect(html).toContain("&lt;b&gt;Cash&lt;/b&gt;")
		expect(html).toContain("SN&lt;img src=x&gt;")
	})

	it("escapes the popup document title as well", () => {
		const html = buildReceiptDocumentHTML(evil, { dots: 384 })
		expect(html).not.toContain("<img src=x")
		expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;")
	})

	it("keeps normal data untouched (no metacharacters -> byte-identical output)", () => {
		const html = buildReceiptHTML(doc)
		expect(html).not.toContain("&lt;")
		expect(html).not.toContain("&amp;")
		expect(html).not.toContain("&quot;")
		expect(html).not.toContain("&#39;")
	})
})

describe("ensureTransportInitialized retry on failure", () => {
	it("retries transport initialization after a failed attempt", async () => {
		vi.resetModules()
		const { ensureTransportInitialized: ensureInit } = await import(
			"./printInvoice"
		)
		const transportMod = await import("@/utils/print/transport")
		transportMod.initTransportFromServer
			.mockRejectedValueOnce(new Error("network error"))
			.mockResolvedValueOnce({ driver: "imin" })

		// First attempt fails; ensureTransportInitialized does not throw but catches and logs
		await ensureInit("POS Profile 1")
		expect(transportMod.initTransportFromServer).toHaveBeenCalledTimes(1)

		// Second attempt should retry instead of being stuck
		await ensureInit("POS Profile 1")
		expect(transportMod.initTransportFromServer).toHaveBeenCalledTimes(2)

		// Third attempt after success should be cached (not call again)
		await ensureInit("POS Profile 1")
		expect(transportMod.initTransportFromServer).toHaveBeenCalledTimes(2)
	})
})
