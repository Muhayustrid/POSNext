import { beforeAll, describe, expect, it } from "vitest"

import {
	isAllocationSnapshot,
	pickedQty,
	quotePackageLocally,
	selectionsToChoices,
	validateGroup,
} from "./packageQuote"

const pkg = {
	name: "Paket Laptop Akhir Tahun",
	package_name: "Paket Laptop Akhir Tahun",
	parent_item: "PKG-LAPTOP-AKHIR-TAHUN",
	base_price: 12_000_000,
	items: [
		{
			item_code: "LAPTOP",
			item_name: "Laptop",
			qty: 1,
			uom: "Nos",
			is_stock_item: 1,
		},
	],
	groups: [
		{ group_key: "aksesori", label: "Aksesori", min_qty: 1, max_qty: 1 },
		{ group_key: "voucher", label: "Voucher", min_qty: 0, max_qty: 3 },
	],
	options: [
		{
			option_id: "backpack",
			group_key: "aksesori",
			item_code: "BACKPACK",
			item_name: "Backpack",
			qty_per_unit: 1,
			uom: "Nos",
			price_adjustment: 0,
			max_qty: 1,
			is_stock_item: 1,
		},
		{
			option_id: "headphone",
			group_key: "aksesori",
			item_code: "HEADPHONE",
			item_name: "Headphone",
			qty_per_unit: 1,
			uom: "Nos",
			price_adjustment: 350_000,
			max_qty: 1,
			is_stock_item: 1,
		},
		{
			option_id: "pulsa",
			group_key: "voucher",
			item_code: "PULSA",
			item_name: "Voucher Pulsa",
			qty_per_unit: 1,
			uom: "Nos",
			price_adjustment: 45_000,
			max_qty: 3,
			is_stock_item: 0,
		},
		{
			option_id: "listrik",
			group_key: "voucher",
			item_code: "LISTRIK",
			item_name: "Voucher Listrik",
			qty_per_unit: 1,
			uom: "Nos",
			price_adjustment: 48_000,
			max_qty: 3,
			is_stock_item: 0,
		},
	],
}

beforeAll(() => {
	globalThis.__ = (message, replacements = []) =>
		message.replace(
			/\{(\d+)\}/g,
			(_match, index) => replacements[Number(index)] ?? "",
		)
})

describe("quotePackageLocally", () => {
	it("prices one required accessory and a mixed three-unit voucher selection", () => {
		const selections = {
			aksesori: { headphone: 1 },
			voucher: { pulsa: 2, listrik: 1 },
		}

		const quote = quotePackageLocally(pkg, selections)

		expect(quote.valid).toBe(true)
		expect(quote.total).toBe(12_488_000)
		expect(
			quote.lines.map(({ item_code, qty, rate }) => ({ item_code, qty, rate })),
		).toEqual([
			{ item_code: "PKG-LAPTOP-AKHIR-TAHUN", qty: 1, rate: 12_488_000 },
			{ item_code: "HEADPHONE", qty: 1, rate: 0 },
			{ item_code: "PULSA", qty: 2, rate: 0 },
			{ item_code: "LISTRIK", qty: 1, rate: 0 },
			{ item_code: "LAPTOP", qty: 1, rate: 0 },
		])
	})

	it("rejects a missing required selection", () => {
		const quote = quotePackageLocally(pkg, { voucher: {} })

		expect(quote.valid).toBe(false)
		expect(quote.error).toBe("Choose at least 1 item(s) from Aksesori.")
	})

	it("rejects a group selection above its maximum", () => {
		const quote = quotePackageLocally(pkg, {
			aksesori: { backpack: 1 },
			voucher: { pulsa: 2, listrik: 2 },
		})

		expect(quote.valid).toBe(false)
		expect(quote.error).toBe("Choose at most 3 item(s) from Voucher.")
	})

	it("rejects an individual option above its maximum", () => {
		const group = pkg.groups[1]
		const options = pkg.options
			.filter(({ group_key }) => group_key === group.group_key)
			.map((option) =>
				option.option_id === "pulsa" ? { ...option, max_qty: 2 } : option,
			)

		const error = validateGroup(group, options, { pulsa: 3 })

		expect(error).toBe("You can pick at most 2 x Voucher Pulsa.")
	})
})

describe("selection helpers", () => {
	it("counts selected units across options", () => {
		expect(pickedQty({ pulsa: 2, listrik: 1 })).toBe(3)
	})

	it("serializes only positive selections for the API", () => {
		const choices = selectionsToChoices({
			aksesori: { backpack: 1, headphone: 0 },
			voucher: { pulsa: 2, listrik: 1 },
		})

		expect(choices).toEqual([
			{ group_key: "aksesori", options: [{ option_id: "backpack", qty: 1 }] },
			{
				group_key: "voucher",
				options: [
					{ option_id: "pulsa", qty: 2 },
					{ option_id: "listrik", qty: 1 },
				],
			},
		])
	})
})

describe("quotePackageLocally allocation mode (mirror of allocate_package_rates)", () => {
	it("splits the total across components by price-list value and marks the snapshot", () => {
		const selections = {
			aksesori: { backpack: 1 },
			voucher: { pulsa: 2, listrik: 1 },
		}
		// Rate of each component's catalog row, as the offline mirror looks it up.
		const priceListRates = {
			BACKPACK: { Nos: 200_000 },
			PULSA: { Nos: 100_000 },
			LISTRIK: { Nos: 100_000 },
		}

		const quote = quotePackageLocally(pkg, selections, {
			allocate: true,
			priceListRates,
		})

		expect(quote.valid).toBe(true)
		// Parent drops to zero; components carry the split.
		expect(quote.lines[0].rate).toBe(0)
		expect(quote.lines[1].rate).toBeGreaterThan(0)

		// Invariant: sum(rate × qty) == total (the server's exact-sum check).
		const allocated = quote.lines
			.slice(1)
			.reduce((sum, line) => sum + line.rate * line.qty, 0)
		expect(allocated).toBeCloseTo(quote.total, 6)
		expect(quote.snapshot.allocation).toEqual({
			mode: "proportional",
			precision: 2,
		})
	})

	it("falls back to quantity weights when the cache carries no price", () => {
		const quote = quotePackageLocally(
			{ ...pkg, base_price: 100, groups: [], options: [], items: pkg.items },
			{},
			{ allocate: true, priceListRates: null }
		)

		expect(quote.valid).toBe(true)
		// One included line only: it absorbs the whole total.
		expect(quote.lines[0].rate).toBe(0)
		expect(quote.lines[1].rate).toBe(100)
		expect(quote.snapshot.allocation.mode).toBe("proportional")
	})

	it("stays legacy byte-for-byte when allocation is off (default)", () => {
		const selections = { aksesori: { headphone: 1 }, voucher: {} }

		const quote = quotePackageLocally(pkg, selections)

		expect(quote.lines[0].rate).toBe(12_350_000)
		expect(quote.snapshot.allocation).toBeUndefined()
		expect(quote.lines.slice(1).every((line) => line.rate === 0)).toBe(true)
	})
})

describe("isAllocationSnapshot (shared marker check)", () => {
	it("recognizes the server-written marker in object and JSON-string form", () => {
		const marker = { allocation: { mode: "proportional", precision: 2 } }
		expect(isAllocationSnapshot(marker)).toBe(true)
		expect(isAllocationSnapshot(JSON.stringify(marker))).toBe(true)
	})

	it("rejects legacy snapshots, malformed JSON and empty values", () => {
		expect(isAllocationSnapshot({ package: "P" })).toBe(false)
		expect(isAllocationSnapshot('{"package": "P"}')).toBe(false)
		expect(isAllocationSnapshot("{not json")).toBe(false)
		expect(isAllocationSnapshot(null)).toBe(false)
		expect(isAllocationSnapshot(undefined)).toBe(false)
	})
})
