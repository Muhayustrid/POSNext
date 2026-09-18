import { describe, expect, it, vi } from "vitest"

// stockValidator imports the frappe-ui barrel, which does not resolve under
// vitest; only `call` is used and is irrelevant to the helpers under test.
vi.mock("frappe-ui", () => ({ call: vi.fn() }))

import { checkStockAvailability, validationStockQty } from "./stockValidator"

describe("validationStockQty", () => {
	it("prefers the stock store's un-reserved qty over the item's display stock", () => {
		// filteredItems injects actual_qty = server - reserved (display stock).
		// Server has 5, cart already holds 3, so the item shows 2.
		const serverMap = new Map([["ROPI-COKLAT", { qty: 5, warehouse: "Store" }]])
		const item = { item_code: "ROPI-COKLAT", actual_qty: 2, stock_qty: 2 }

		expect(validationStockQty(item, serverMap)).toBe(5)
	})

	it("falls back to original_stock when the store has no entry", () => {
		const item = { item_code: "X", actual_qty: 2, original_stock: 5 }
		expect(validationStockQty(item, new Map())).toBe(5)
	})

	it("falls back to the item's own fields for raw (unregistered) items", () => {
		expect(
			validationStockQty({ item_code: "X", actual_qty: 3 }, new Map()),
		).toBe(3)
		expect(
			validationStockQty({ item_code: "X", stock_qty: 4 }, undefined),
		).toBe(4)
		expect(validationStockQty({ item_code: "X" }, new Map())).toBe(0)
	})

	it("keeps a negative server qty instead of masking it with item data", () => {
		const serverMap = new Map([["X", { qty: -1 }]])
		expect(
			validationStockQty({ item_code: "X", actual_qty: 0 }, serverMap),
		).toBe(-1)
	})
})

describe("checkStockAvailability with explicit availableQty", () => {
	it("validates against the passed qty, not the item's display stock", () => {
		// The reported bug: display says 2 left (5 minus cart of 3), so a 4th
		// click was rejected even though the server holds 5.
		const item = {
			item_code: "ROPI-COKLAT",
			item_name: "Ropi Coklat",
			actual_qty: 2,
		}
		const check = checkStockAvailability(item, 4, "Store", 5)

		expect(check.available).toBe(true)
		expect(check.actualQty).toBe(5)
	})

	it("still rejects when the server qty cannot cover the request", () => {
		const item = { item_code: "X", item_name: "X", actual_qty: 2 }
		const check = checkStockAvailability(item, 4, "Store", 3)

		expect(check.available).toBe(false)
		expect(check.error).toContain("3")
	})

	it("keeps the legacy behaviour when no explicit qty is given", () => {
		const item = { item_code: "X", item_name: "X", actual_qty: 5 }
		expect(checkStockAvailability(item, 4).available).toBe(true)
		expect(checkStockAvailability(item, 6).available).toBe(false)
	})
})
