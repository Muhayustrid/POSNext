import { describe, expect, it, vi } from "vitest"

import sharedCases from "./packageAllocation.cases.json"

import {
	allocatePackageRates,
	componentPriceListRate,
} from "./packageAllocation"

/** sum(rate_i * qty_per_package_i * package_qty) — the server's invariant. */
function allocatedSum(rates, children, packageQty = 1) {
	return rates.reduce(
		(sum, rate, index) =>
			sum + rate * (children[index]?.qty_per_package || 0) * packageQty,
		0,
	)
}

describe("allocatePackageRates (mirror of packages.py)", () => {
	it("splits 23k over weights 20k + 10k, remainder to the last line", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 20000 },
			{ qty_per_package: 1, price_list_rate: 10000 },
		]

		const rates = allocatePackageRates(23000, children)

		expect(rates).toEqual([15333, 7667])
		expect(allocatedSum(rates, children)).toBe(23000)
	})

	it("keeps the invariant at package qty 2 (total money doubles)", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 20000 },
			{ qty_per_package: 1, price_list_rate: 10000 },
		]

		const rates = allocatePackageRates(23000, children, 2)

		expect(rates).toEqual([15333, 7667])
		expect(allocatedSum(rates, children, 2)).toBe(46000)
	})

	it("splits evenly when quantities and prices are all 1:1", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 4000 },
			{ qty_per_package: 1, price_list_rate: 3000 },
			{ qty_per_package: 1, price_list_rate: 3000 },
		]

		const rates = allocatePackageRates(10000, children)

		expect(rates).toEqual([4000, 3000, 3000])
		expect(allocatedSum(rates, children)).toBe(10000)
	})

	it("falls back to quantity weights when every price-list rate is 0", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 0 },
			{ qty_per_package: 1, price_list_rate: 0 },
		]
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {})

		const rates = allocatePackageRates(9999, children)

		expect(rates).toEqual([5000, 4999])
		expect(allocatedSum(rates, children)).toBe(9999)
		expect(warn).toHaveBeenCalled()
		warn.mockRestore()
	})

	it("falls back to an even split when quantities are 0 too", () => {
		const children = [
			{ qty_per_package: 0, price_list_rate: 0 },
			{ qty_per_package: 0, price_list_rate: 0 },
		]
		const warn = vi.spyOn(console, "warn").mockImplementation(() => {})

		const rates = allocatePackageRates(7, children)

		// Every line_qty is 0, so every per-unit rate is 0; the money stays
		// unallocatable rather than dividing by zero.
		expect(rates).toEqual([0, 0])
		expect(warn).toHaveBeenCalled()
		warn.mockRestore()
	})

	it("returns 0 for a zero-quantity line without disturbing the others", () => {
		const children = [
			{ qty_per_package: 2, price_list_rate: 5000 },
			{ qty_per_package: 1, price_list_rate: 0 },
		]

		const rates = allocatePackageRates(10000, children)

		expect(rates).toEqual([5000, 0])
		expect(allocatedSum(rates, children)).toBe(10000)
	})

	it("carries the remainder so qty>1 weights still sum exactly", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 2000 },
			{ qty_per_package: 3, price_list_rate: 1000 },
		]

		const rates = allocatePackageRates(100, children, 5)

		expect(rates).toEqual([40, 20])
		expect(allocatedSum(rates, children, 5)).toBe(500)
	})

	// B1 review repro: the qty-1 line absorbs the rounding remainder; a qty>1
	// last line (16667 x 1 + 4166 x 2 = 24999) would break the invariant.
	it("B1: the last qty-1 line carries the remainder, not the qty>1 last line", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 20000 },
			{ qty_per_package: 2, price_list_rate: 5000 },
		]

		const rates = allocatePackageRates(25000, children)

		expect(rates).toEqual([16666, 4167])
		expect(allocatedSum(rates, children)).toBe(25000)
	})

	it("all-multiples lines: the last line carries it, exact only when it divides", () => {
		const children = [
			{ qty_per_package: 2, price_list_rate: 5000 },
			{ qty_per_package: 2, price_list_rate: 5000 },
		]

		expect(allocatePackageRates(20000, children)).toEqual([5000, 5000])
		expect(allocatedSum(allocatePackageRates(20000, children), children)).toBe(20000)
		// 25001 cannot divide across qty-2 lines; the server fails closed here.
		expect(allocatePackageRates(25001, children)).toEqual([6250, 6250])
	})

	it("rounds 7 across three equal lines with the remainder last", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 1 },
			{ qty_per_package: 1, price_list_rate: 1 },
			{ qty_per_package: 1, price_list_rate: 1 },
		]

		const rates = allocatePackageRates(7, children)

		expect(rates).toEqual([2, 2, 3])
		expect(allocatedSum(rates, children)).toBe(7)
	})

	// frappe's default "Banker's Rounding": a .5 tie goes to the even integer.
	it("rounds a .5 tie to even, matching frappe flt (Banker's Rounding)", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 1 },
			{ qty_per_package: 1, price_list_rate: 1 },
		]

		expect(allocatePackageRates(1, children)).toEqual([0, 1])
		expect(allocatePackageRates(3, children)).toEqual([2, 1])
	})

	it("honours a caller-supplied precision", () => {
		const children = [
			{ qty_per_package: 1, price_list_rate: 20 },
			{ qty_per_package: 1, price_list_rate: 30 },
			{ qty_per_package: 1, price_list_rate: 50 },
		]

		const rates = allocatePackageRates(100, children, 1, 2)

		expect(rates).toEqual([20, 30, 50])
		expect(allocatedSum(rates, children)).toBe(100)
	})

	it("returns [] for no children", () => {
		expect(allocatePackageRates(23000, [])).toEqual([])
		expect(allocatePackageRates(23000, null)).toEqual([])
	})
})

describe("shared expectation table (Python server <-> JS mirror)", () => {
	// Same JSON file is read by pos_next/api/test_package_allocation_gate.py
	// (TestAllocatePackageRates), so an edit to either allocator that drifts
	// from the table fails on both sides.
	it.each(sharedCases.cases)("$name", (testCase) => {
		const rates = allocatePackageRates(
			testCase.package_price,
			testCase.children,
			testCase.package_qty,
			testCase.precision,
		)

		expect(rates).toEqual(testCase.expected_rates)
		// A negative component rate must never be booked, even on
		// fail-closed shapes (the server sum check rejects those).
		for (const rate of rates) expect(rate).toBeGreaterThanOrEqual(0)

		const allocated = rates.reduce(
			(sum, rate, index) =>
				sum +
				rate * testCase.children[index].qty_per_package * testCase.package_qty,
			0,
		)
		const expected = testCase.package_price * testCase.package_qty
		if (testCase.exact_sum ?? true) {
			expect(Number(allocated.toFixed(testCase.precision))).toBe(
				Number(expected.toFixed(testCase.precision)),
			)
		} else {
			// Pinned fail-closed shape: the invoice sum check must reject this.
			expect(Number(allocated.toFixed(testCase.precision))).not.toBe(
				Number(expected.toFixed(testCase.precision)),
			)
		}
	})

	// User repro: a zero-weight qty-1 last line must not absorb the leftover
	// as a negative rate ([6667, -1] before the fix).
	it("zero-weight qty-1 line never carries the remainder (no negative rates)", () => {
		const children = [
			{ qty_per_package: 3, price_list_rate: 5000 },
			{ qty_per_package: 1, price_list_rate: 0 },
		]

		const rates = allocatePackageRates(20000, children)

		expect(rates).toEqual([6667, 0])
		for (const rate of rates) expect(rate).toBeGreaterThanOrEqual(0)
		expect(allocatedSum(rates, children)).not.toBe(20000)
	})

	it("covers every brief case at least once", () => {
		const names = sharedCases.cases.map((c) => c.name).join(" | ")
		for (const needle of [
			"20k + 10k",
			"package qty 2",
			"3-item rounding",
			"zero-priced child",
			"IDR 0-decimal",
		]) {
			expect(names).toContain(needle)
		}
	})
})

describe("componentPriceListRate (uom_prices lookup mirror)", () => {
	it("prefers the row's UOM", () => {
		expect(componentPriceListRate({ Nos: 10000, Box: 190000 }, "Box")).toBe(190000)
	})

	it("falls back to the no-UOM price", () => {
		expect(componentPriceListRate({ "": 12000, Nos: 999 }, "Box")).toBe(12000)
	})

	it("uses the only price when it is unambiguous", () => {
		expect(componentPriceListRate({ Box: 50000 }, "Nos")).toBe(50000)
	})

	it("resolves unknown or missing prices to 0 (weight fallback)", () => {
		expect(componentPriceListRate({ Box: 1, Nos: 2 }, "Carton")).toBe(0)
		expect(componentPriceListRate(null, "Nos")).toBe(0)
	})
})
