/**
 * @vitest-environment jsdom
 */
import { describe, expect, it } from "vitest"
import { buildRecapHTML, periodRange, toISODate } from "./salesRecap"

// The app installs __() as a global; the sheet builder uses it for labels.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

const PERIOD_SUMMARY = {
	pos_profile: "Kasir 1",
	company: "PT Roti",
	company_currency: "IDR",
	period_from: "2026-09-01",
	period_to: "2026-09-07",
	shift_count: 9,
	generated_at: "2026-09-08 09:10:36",
	counted_invoices: 40,
	sales_count: 38,
	returns_count: 2,
	gross_sales: 5200000,
	returns_total: 200000,
	net_sales: 5000000,
	average_sale: 131578.9,
	payments: [
		{
			mode_of_payment: "Cash",
			amount: 3000000,
			is_cash: true,
			configured: true,
		},
		{
			mode_of_payment: "QRIS",
			amount: 2000000,
			is_cash: false,
			configured: true,
		},
	],
	opening_cash: 900000,
	cash_collected: 3000000,
	cash_in_hand: 3900000,
	expense: null,
	total_cash: 3000000,
	total_non_cash: 2000000,
	methods_grand_total: 5000000,
	tax_total: 500000,
	other_charges: [
		{ account_head: "SC - R", label: "Service Charge", amount: 250000 },
	],
	item_discount: 50000,
	invoice_discount: 10000,
	categories: [
		{ category: "Makanan", qty: 30, base_net_amount: 3500000 },
		{ category: "Minuman", qty: 25.5, base_net_amount: 1000000 },
	],
	categories_truncated: false,
	packages: [
		{
			item_code: "P1",
			item_name: "Paket Hemat",
			qty: 3,
			base_net_amount: 150000,
		},
	],
	items: [
		{
			item_code: "I1",
			item_name: "<b>Kopi</b> & Susu",
			qty: 12,
			base_net_amount: 240000,
		},
	],
	items_shown: 2,
	items_total_groups: 2,
	items_truncated: false,
}

describe("periodRange", () => {
	const today = new Date(2026, 8, 11) // 11 Sep 2026

	it("resolves every preset into explicit local dates", () => {
		expect(periodRange("today", { today })).toEqual({
			from: "2026-09-11",
			to: "2026-09-11",
		})
		expect(periodRange("yesterday", { today })).toEqual({
			from: "2026-09-10",
			to: "2026-09-10",
		})
		expect(periodRange("last7", { today })).toEqual({
			from: "2026-09-05",
			to: "2026-09-11",
		})
		expect(periodRange("month", { today })).toEqual({
			from: "2026-09-01",
			to: "2026-09-11",
		})
		expect(periodRange("year", { today })).toEqual({
			from: "2026-01-01",
			to: "2026-09-11",
		})
	})

	it("rolls over month and year boundaries", () => {
		const jan3 = new Date(2026, 0, 3)
		expect(periodRange("yesterday", { today: jan3 })).toEqual({
			from: "2026-01-02",
			to: "2026-01-02",
		})
		expect(periodRange("last7", { today: jan3 })).toEqual({
			from: "2025-12-28",
			to: "2026-01-03",
		})
		expect(periodRange("month", { today: jan3 })).toEqual({
			from: "2026-01-01",
			to: "2026-01-03",
		})
	})

	it("passes a complete custom range through and refuses incomplete or reversed ones", () => {
		expect(
			periodRange("custom", { from: "2026-08-01", to: "2026-08-31" }),
		).toEqual({
			from: "2026-08-01",
			to: "2026-08-31",
		})
		expect(periodRange("custom", { from: "2026-08-01", to: "" })).toBeNull()
		expect(
			periodRange("custom", { from: "2026-08-31", to: "2026-08-01" }),
		).toBeNull()
		expect(periodRange("nonsense", { today })).toBeNull()
	})

	it("formats local calendar dates, never UTC-shifted ones", () => {
		expect(toISODate(new Date(2026, 11, 31, 23, 59))).toBe("2026-12-31")
		expect(toISODate(new Date(2026, 0, 1, 0, 0))).toBe("2026-01-01")
	})
})

describe("buildRecapHTML", () => {
	const printedAt = new Date(2026, 8, 8, 9, 15)

	it("mirrors the EOD sheet: same section titles in the same order", () => {
		const html = buildRecapHTML(PERIOD_SUMMARY, { printedAt })
		const markers = [
			"SALES RECAP",
			"PT Roti",
			"SALES SUMMARY",
			"CASH SUMMARY",
			"PAYMENT METHOD",
			"GRAND TOTAL",
			"CHARGE",
			"DISCOUNT",
			"REFUND",
			"SALES PER CATEGORY",
			"ITEMS SOLD",
			"-- Akhir Laporan --",
		]
		let last = -1
		for (const marker of markers) {
			const at = html.indexOf(marker)
			expect(at, `${marker} missing`).toBeGreaterThan(-1)
			expect(at, `${marker} out of order`).toBeGreaterThan(last)
			last = at
		}
		// EOD typography travels with the sheet so both print alike
		expect(html).toContain("DejaVu Sans")
		expect(html).toContain("border-top: 2px dashed #333")
	})

	it("prints the period header for a date-range recap", () => {
		const html = buildRecapHTML(PERIOD_SUMMARY, { printedAt })
		expect(html).toContain("Outlet")
		expect(html).toContain("Kasir 1")
		expect(html).toContain("01/09/2026 - 07/09/2026")
		expect(html).toContain("<span>Shifts</span><span>9</span>")
		expect(html).toContain("08/09/2026 09:15") // print date
		expect(html).not.toContain("Cashier")
	})

	it("prints the shift header for a session summary", () => {
		const html = buildRecapHTML(
			{
				...PERIOD_SUMMARY,
				opening_shift: "POS-SH-1",
				cashier: "Budi",
				period_start_date: "2026-09-08 06:32:09",
				closing_time: null,
			},
			{ printedAt },
		)
		expect(html).toContain("<span>Cashier</span><span>Budi</span>")
		expect(html).toContain("<span>Shift</span><span>POS-SH-1</span>")
		expect(html).toContain("08/09/2026 06:32")
		expect(html).toContain("<span>Closing</span><span>-</span>") // never a guessed time
		expect(html).not.toContain("Outlet")
	})

	it("carries the money figures in the company currency", () => {
		const html = buildRecapHTML(PERIOD_SUMMARY, { printedAt })
		expect(html).toMatch(/Total Sales<\/span><span>Rp\s?5[.,]000[.,]000</)
		expect(html).toMatch(/Opening Balance<\/span><span>Rp\s?900[.,]000</)
		expect(html).toContain("<span>Total Order</span><span>38</span>")
		expect(html).toContain("Total Refund (2)")
		expect(html).toContain("Service Charge")
	})

	it("prints an honest dash for the unsupported expense ledger", () => {
		const html = buildRecapHTML(PERIOD_SUMMARY, { printedAt })
		expect(html).toContain("<span>Total Expense</span><span>-</span>")
	})

	it("escapes names coming from the data", () => {
		const html = buildRecapHTML(PERIOD_SUMMARY, { printedAt })
		expect(html).toContain("&lt;b&gt;Kopi&lt;/b&gt; &amp; Susu")
		expect(html).not.toContain("<b>Kopi</b>")
	})

	it("lists categories with quantities and discloses capped tables", () => {
		const html = buildRecapHTML(
			{
				...PERIOD_SUMMARY,
				categories_truncated: true,
				categories_shown: 2,
				categories_total_groups: 25,
				items_truncated: true,
				items_shown: 2,
				items_total_groups: 140,
			},
			{ printedAt },
		)
		expect(html).toContain("25.5x Minuman")
		expect(html).toContain("3x Paket Hemat")
		expect(html).toContain("Top 2 of 25 categories")
		expect(html).toContain("Top 2 of 140 items")
	})

	it("keeps the sheet readable when a window has no sales", () => {
		const html = buildRecapHTML(
			{
				...PERIOD_SUMMARY,
				payments: [],
				categories: [],
				items: [],
				packages: [],
			},
			{ printedAt },
		)
		expect(html).toContain("SALES PER CATEGORY")
		expect(html).not.toContain("ITEMS SOLD")
		expect(html).toContain("-- Akhir Laporan --")
	})
})
