import { beforeAll, describe, expect, it } from "vitest"

import {
	buildCrewSlipTextJob,
	buildReceiptTextJob,
	colsFor,
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
})
