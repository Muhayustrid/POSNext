// COR-FE-14: a "YYYY-MM-DD" posting date parsed via new Date(str) is read as
// UTC midnight, so a negative-offset timezone renders the PREVIOUS day
// (2026-01-01 -> Dec 31 2025). The TZ override must sit at the very top,
// before any import, so every Date in this file uses America/New_York.
process.env.TZ = "America/New_York"

import { describe, expect, it, vi } from "vitest"

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }))
vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ debug: vi.fn(), warn: vi.fn(), info: vi.fn(), error: vi.fn() }) },
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

import { buildReceiptHTML, parseDateOnly } from "./printInvoice"

globalThis.__ = (message) => message

describe("date-only posting dates stay on the same calendar day (COR-FE-14)", () => {
	it("parseDateOnly builds a local-midnight date for YYYY-MM-DD", () => {
		const d = parseDateOnly("2026-01-01")
		expect(d.getFullYear()).toBe(2026)
		expect(d.getMonth()).toBe(0)
		expect(d.getDate()).toBe(1)
	})

	it("passes datetimes and timestamps through new Date", () => {
		const ts = new Date(2026, 5, 15, 10, 30).getTime()
		expect(parseDateOnly(ts).getTime()).toBe(ts)
		expect(parseDateOnly("2026-06-15 10:30:00").getMonth()).toBe(5)
	})

	it("renders 2026-01-01 as January 1st, never December 31st 2025", () => {
		const html = buildReceiptHTML({
			name: "SINV-TZ",
			posting_date: "2026-01-01",
			company: "POS Next",
			customer: "Bob",
			grand_total: 10000,
			items: [{ item_code: "A", item_name: "A", quantity: 1, rate: 10000 }],
			payments: [{ mode_of_payment: "Cash", amount: 10000 }],
			paid_amount: 10000,
		})

		expect(html).toContain("2026")
		// The old new Date("2026-01-01") parse shifted the day back to
		// 2025 in UTC-negative timezones.
		expect(html).not.toContain("2025")
	})
})
