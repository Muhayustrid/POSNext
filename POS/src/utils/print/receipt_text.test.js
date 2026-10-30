import { beforeAll, describe, expect, it } from "vitest"

import {
	buildCrewSlipTextJob,
	buildEodTextJob,
	buildReceiptTextJob,
	colsFor,
	formatQty,
	formatRupiah,
	leftRight,
	textSizes,
	wrapText,
} from "./receipt_text"

beforeAll(() => {
	globalThis.__ = (s) => s
})

// The receipt from the field report: one item, cash, queue number.
const DOC = {
	name: "PINV-RPMD-261024001",
	company: "RS PKU MUHAMMADIYAH DELANGGU",
	company_address_line: "Jl. Raya Delanggu Utara No.19, Gatak, Kec. Delanggu",
	cashier_name: "PT. JUARA ROTI INDONESIA",
	posting_date: "2026-10-24",
	posting_time: "14:58:06.123456",
	customer_name: "Walk-in Customer",
	currency: "IDR",
	pos_queue_number: 1,
	grand_total: 12000,
	paid_amount: 12000,
	items: [{ item_name: "Americano Ice", qty: 1, rate: 12000 }],
	payments: [{ mode_of_payment: "Cash", amount: 12000 }],
}

const texts = (ops) => ops.filter((o) => o.op === "text").map((o) => o.text)

describe("layout helpers", () => {
	it("fits monospace columns to the head with one column of slack", () => {
		// 58mm head, 20px text: 384 / 12 = 32 -> 32 - 1
		expect(colsFor(384, 20)).toBe(31)
		expect(colsFor(576, 20)).toBe(47)
	})

	it("scales text sizes with the font scale", () => {
		expect(textSizes(100).base).toBe(20)
		expect(textSizes(150).base).toBe(30)
		expect(textSizes(100).queue).toBeGreaterThan(textSizes(100).big)
	})

	it("wraps words and hard-breaks words longer than the line", () => {
		expect(wrapText("aa bb cc", 5)).toEqual(["aa bb", "cc"])
		expect(wrapText("abcdefgh", 3)).toEqual(["abc", "def", "gh"])
		// A fitting line keeps its padding (label columns, indents).
		expect(wrapText("TOTAL   :", 20)).toEqual(["TOTAL   :"])
		// A newline collapses to a space, as the HTML receipt renders it.
		expect(wrapText("Jl. A\nNo. 1", 20)).toEqual(["Jl. A No. 1"])
	})

	it("puts the right value flush right on the first line", () => {
		const [line] = leftRight("1x Kopi", "Rp12.000", 20)
		expect(line).toHaveLength(20)
		expect(line.startsWith("1x Kopi")).toBe(true)
		expect(line.endsWith("Rp12.000")).toBe(true)
	})

	it("wraps a long left side under itself, clear of the price column", () => {
		const lines = leftRight(
			"1x Roti Sobek Coklat Keju Spesial",
			"Rp12.000",
			24,
			3,
		)
		expect(lines[0].endsWith("Rp12.000")).toBe(true)
		for (const l of lines) expect(l.length).toBeLessThanOrEqual(24)
		for (const l of lines.slice(1)) {
			expect(l.startsWith("   ")).toBe(true)
			expect(l.length).toBeLessThanOrEqual(24 - "Rp12.000".length - 1)
		}
	})

	it("formats rupiah exactly like Python format_rupiah", () => {
		expect(formatRupiah(12000)).toBe("Rp12.000")
		expect(formatRupiah(-1000)).toBe("-Rp1.000")
		expect(formatRupiah(0)).toBe("Rp0")
		expect(formatRupiah(12000.5)).toBe("Rp12.001")
		expect(formatRupiah(-1000.5)).toBe("-Rp1.001")
	})

	it("formats quantities like jinja %g", () => {
		expect(formatQty(1)).toBe("1")
		expect(formatQty(1.5)).toBe("1.5")
		expect(formatQty(0)).toBe("0")
		expect(formatQty(2.0)).toBe("2")
	})
})

describe("buildReceiptTextJob", () => {
	const cols = { base: 31, big: 23, queue: 12 }

	it("prints the same rows as the HTML receipt, in order", () => {
		const ops = buildReceiptTextJob(DOC, { cols })
		const t = texts(ops)
		const order = [
			"NO. ANTRIAN",
			"001",
			"RS PKU MUHAMMADIYAH DELANGGU",
			"Invoice : PINV-RPMD-261024001",
			"Date    : 2026-10-24 14:58:06",
			"Thank you",
			"Powered by POS Next",
		]
		let at = -1
		for (const want of order) {
			const idx = t.findIndex((x, i) => i > at && x.includes(want))
			expect(idx, want).toBeGreaterThan(at)
			at = idx
		}
		expect(t.some((x) => x.startsWith("Customer: Walk-in Customer"))).toBe(true)
		expect(
			t.some((x) => x.startsWith("1x Americano Ice") && /12\.000$/.test(x)),
		).toBe(true)
		expect(t.some((x) => x.startsWith("TOTAL   :") && /12\.000$/.test(x))).toBe(
			true,
		)
	})

	it("never prints a line wider than the paper", () => {
		const ops = buildReceiptTextJob(DOC, { cols })
		for (const o of ops) {
			if (o.op === "text" && o.size === "base") {
				expect(o.text.length, o.text).toBeLessThanOrEqual(cols.base)
			}
		}
	})

	it("prints the logo after the queue block and before the company", () => {
		const ops = buildReceiptTextJob(DOC, { cols })
		const logoAt = ops.findIndex((o) => o.op === "logo")
		const queueAt = ops.findIndex((o) => o.text === "001")
		const companyAt = ops.findIndex((o) => o.text === DOC.company)
		expect(queueAt).toBeLessThan(logoAt)
		expect(logoAt).toBeLessThan(companyAt)
		expect(ops[queueAt]).toMatchObject({ size: "queue", bold: true, align: 1 })
	})

	it("skips the queue block without a queue number", () => {
		const t = texts(
			buildReceiptTextJob({ ...DOC, pos_queue_number: null }, { cols }),
		)
		expect(t).not.toContain("NO. ANTRIAN")
	})

	it("uses feed op for queueGapDots when provided", () => {
		const opsWithGap = buildReceiptTextJob(DOC, { cols, queueGapDots: 40 })
		expect(opsWithGap).toContainEqual({ op: "feed", dots: 40 })

		const opsZeroGap = buildReceiptTextJob(DOC, { cols, queueGapDots: 0 })
		expect(opsZeroGap.some((o) => o.op === "feed")).toBe(false)
		const queueIdx = opsZeroGap.findIndex((o) => o.text === "001")
		expect(opsZeroGap[queueIdx + 1].op).toBe("logo")
	})
})

describe("buildCrewSlipTextJob", () => {
	const cols = { base: 31, big: 23, queue: 12 }

	it("lists what was ordered, bold and a size up, with no money on it", () => {
		const ops = buildCrewSlipTextJob(DOC, { cols })
		const item = ops.find((o) => o.text?.startsWith("1x Americano Ice"))
		expect(item).toMatchObject({ size: "big", bold: true })
		expect(texts(ops).join("\n")).not.toMatch(/12\.000/)
		expect(texts(ops)).toContain("ORDER")
	})

	it("indents package components under the package line", () => {
		const ops = buildCrewSlipTextJob(
			{
				items: [
					{
						item_name: "Paket Hemat",
						qty: 1,
						pos_package_instance: "P1",
						pos_package_role: "Package",
					},
					{
						item_name: "Kopi Susu",
						qty: 2,
						pos_package_instance: "P1",
						pos_package_role: "Package Item",
					},
				],
			},
			{ cols },
		)
		expect(texts(ops)).toContain("1x Paket Hemat")
		expect(texts(ops)).toContain("  - 2x Kopi Susu")
	})
})

describe("buildEodTextJob", () => {
	const cols = { base: 31, big: 23, queue: 12 }
	const EOD_DATA = {
		company: "PT. JUARA ROTI INDONESIA",
		cashier: "Kasir Satu",
		shift: "POS-CLS-2026-00001",
		start: "24/10/2026 08:00",
		closing: "24/10/2026 16:00",
		print: "24/10/2026 16:05",
		recap: {
			total_sales: 150000,
			total_order: 10,
			average_per_order: 15000,
			cash: {
				opening_balance: 50000,
				cash_payment: 100000,
				total_expense: 20000,
				cash_in_hand: 130000,
			},
			payment_methods: [
				{ mode_of_payment: "Cash", amount: 100000 },
				{ mode_of_payment: "BCA QRIS", amount: 50000 },
			],
			total_cash: 100000,
			total_non_cash: 50000,
			methods_grand_total: 150000,
			service_charge: 0,
			tax_total: 15000,
			product_discount: 5000,
			payment_discount: 0,
			refund_total: 10000,
			categories: [
				{
					category: "Bread",
					items: [
						{
							qty: 2,
							item_name: "Roti Coklat",
							amount: 24000,
							is_return: false,
							components: [{ qty: 2, item_name: "Plastik Roti" }],
						},
						{
							qty: 1,
							item_name: "Roti Keju",
							amount: -12000,
							is_return: true,
						},
					],
				},
			],
		},
	}

	it("prints sections in the exact order of pos_next_eod_report", () => {
		const ops = buildEodTextJob(EOD_DATA, { cols })
		const t = texts(ops)
		const order = [
			"SALES RECAP",
			"PT. JUARA ROTI INDONESIA",
			"Cashier",
			"Shift",
			"Start",
			"Closing",
			"Print Date",
			"SALES SUMMARY",
			"Total Sales",
			"Total Order",
			"Average / Order",
			"CASH SUMMARY",
			"Modal Awal",
			"Cash Payment",
			"Total Expense",
			"Cash in Hand",
			"PAYMENT METHOD",
			"Cash",
			"BCA QRIS",
			"Total Cash",
			"Total Non Cash",
			"GRAND TOTAL",
			"CHARGE",
			"Service Charge",
			"Tax / PPN",
			"DISCOUNT",
			"Product Discount",
			"Payment Discount",
			"REFUND",
			"Total Refund",
			"SALES PER CATEGORY",
			"Bread",
			"2x Roti Coklat",
			"  2x Plastik Roti",
			"1x Roti Keju (Return)",
			"-- Akhir Laporan --",
		]
		let at = -1
		for (const want of order) {
			const idx = t.findIndex((x, i) => i > at && x.includes(want))
			expect(idx, want).toBeGreaterThan(at)
			at = idx
		}
		expect(t.some((x) => x.includes("Cashier") && x.includes("Kasir Satu"))).toBe(true)
		expect(t.some((x) => x.includes("Shift") && x.includes("POS-CLS-2026-00001"))).toBe(true)
	})

	it("never prints a line wider than the paper columns", () => {
		const ops = buildEodTextJob(EOD_DATA, { cols })
		for (const o of ops) {
			if (o.op === "text") {
				expect(o.text.length, o.text).toBeLessThanOrEqual(cols.base)
			}
		}
	})

	it("prints '-' when there are no categories", () => {
		const ops = buildEodTextJob(
			{ ...EOD_DATA, recap: { ...EOD_DATA.recap, categories: [] } },
			{ cols },
		)
		const t = texts(ops)
		const catIdx = t.indexOf("SALES PER CATEGORY")
		expect(catIdx).toBeGreaterThan(-1)
		expect(t[catIdx + 1]).toBe("-")
	})
})

describe("buildEodTextJob header rows", () => {
	it("drops a long cashier name under its label instead of overflowing", () => {
		const ops = buildEodTextJob(
			{ cashier: "PT. JUARA ROTI INDONESIA CABANG DELANGGU" },
			{ cols: 31 },
		)
		for (const o of ops) {
			if (o.op === "text") expect(o.text.length, o.text).toBeLessThanOrEqual(31)
		}
		expect(texts(ops)).toContain("Cashier")
	})
})
