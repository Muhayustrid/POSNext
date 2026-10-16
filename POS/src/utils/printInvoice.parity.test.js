/**
 * Template-parity guard: the client (offline) receipt must render the same key
 * blocks as the server "POS Next Receipt" print format, so a receipt looks the
 * same whether it printed online or offline. Reading the Jinja file text and
 * the JS output for the same markers means changing one template without the
 * other fails here.
 *
 * @vitest-environment jsdom
 */
import { readFileSync } from "node:fs"
import { dirname, resolve } from "node:path"
import { fileURLToPath } from "node:url"
import { describe, expect, it, vi } from "vitest"

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
}))

import { buildReceiptHTML } from "./printInvoice"

const HERE = dirname(fileURLToPath(import.meta.url))
const jinjaHTML = JSON.parse(
	readFileSync(
		resolve(
			HERE,
			"../../../pos_next/pos_next/print_format/pos_next_receipt/pos_next_receipt.json",
		),
		"utf8",
	),
).html

const MARKERS = [
	"NO. ANTRIAN",
	"ropi-logo.png",
	'info-label">Invoice',
	'info-label">Cashier',
	'info-label">Date',
	'info-label">Buyer',
	"Total Qty",
	"Additional Discount",
	"total-group",
	'info-label">TOTAL',
	'info-label">Paid',
	'info-label">Change',
	"Thank you",
	"Powered by POS Next",
]

// The receipt the parity test renders: queue number, buyer, a free and a
// discounted item, tax, a header discount, two payments, change — but no
// outstanding balance and no partial status.
const sampleDoc = {
	name: "SINV-7",
	posting_date: "2026-10-16",
	posting_time: "14:30:00",
	company: "PT Juara Roti",
	cashier_name: "Rina",
	buyer_name: "Budi",
	customer_name: "Budi",
	currency: "IDR",
	pos_queue_number: 7,
	grand_total: 30000,
	total_taxes_and_charges: 3000,
	discount_amount: 2000,
	additional_discount_percentage: 5,
	outstanding_amount: 0,
	items: [
		{
			item_name: "Roti Coklat",
			quantity: 2,
			rate: 10000,
			price_list_rate: 10000,
		},
		{ item_name: "Roti Gratis", quantity: 1, rate: 0, is_free_item: true },
		{
			item_name: "Roti Diskon",
			quantity: 1,
			rate: 8000,
			price_list_rate: 8000,
			discount_amount: 1000,
			discount_percentage: 10,
		},
	],
	payments: [
		{ mode_of_payment: "Cash", amount: 20000 },
		{ mode_of_payment: "Card", amount: 12000 },
	],
	paid_amount: 32000,
	change_amount: 2000,
}

describe("offline receipt matches the 'POS Next Receipt' template", () => {
	const html = buildReceiptHTML(sampleDoc)

	it.each(MARKERS)("renders the marker %s in both templates", (marker) => {
		expect(jinjaHTML).toContain(marker)
		expect(html).toContain(marker)
	})

	it("glues the currency symbol to amounts like the server template", () => {
		expect(jinjaHTML).toContain("{{ currency_symbol }}{{")
		expect(html).toContain("Rp30.000")
	})

	it("keeps the server template's literal labels on a translated site", () => {
		const original = globalThis.__
		globalThis.__ = (text) => `xx-${text}`
		try {
			const translated = buildReceiptHTML(sampleDoc)
			expect(translated).toContain('info-label">Cashier')
			expect(translated).toContain("Thank you")
		} finally {
			globalThis.__ = original
		}
	})

	it("drops the legacy labels the server template never had", () => {
		expect(html).not.toContain("TAX INVOICE")
		expect(html).not.toContain("BrainWise")
	})
})
