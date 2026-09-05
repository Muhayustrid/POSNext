import { describe, expect, it } from "vitest"

import { resolveOfferUnitDiscount } from "./offerDiscount"

describe("resolveOfferUnitDiscount", () => {
	const offer = { discount_percentage: 50, max_discount_amount: 20000 }

	it("returns percentage when cap does not bind", () => {
		expect(resolveOfferUnitDiscount(offer, 30000)).toEqual({
			type: "percentage",
			value: 50,
			capped: false,
		})
	})

	it("expresses a binding cap as an effective percentage of the unit price", () => {
		// 50% of 50000 = 25000 > 20000 cap -> effective pct = 20000/50000*100 = 40%,
		// so the per-unit discount equals the cap exactly.
		const result = resolveOfferUnitDiscount(offer, 50000)
		expect(result.type).toBe("percentage")
		expect(result.capped).toBe(true)
		expect(result.value).toBeCloseTo(40, 5)
		expect((50000 * result.value) / 100).toBeCloseTo(20000, 5)
	})

	it("effective percentage rescales with quantity (cart discount_amount is line-total)", () => {
		// The cart store's discount_amount is a LINE-TOTAL field, so writing a
		// per-unit cap there would discount only once regardless of qty
		// (under-charge). An effective percentage keeps each unit's discount at
		// the cap: base 60000, cap 20000 -> ~33.333333% per unit, so 3 units
		// discount ~3 * 20000 = 60000 in total.
		const result = resolveOfferUnitDiscount(offer, 60000)
		expect(result.type).toBe("percentage")
		expect(result.capped).toBe(true)
		expect(result.value).toBeCloseTo(33.333333, 5)
		expect(((60000 * result.value) / 100) * 3).toBeCloseTo(60000, 5)
	})

	it("ignores cap when zero or missing", () => {
		expect(resolveOfferUnitDiscount({ discount_percentage: 50 }, 50000).type).toBe("percentage")
		expect(resolveOfferUnitDiscount({ discount_percentage: 50, max_discount_amount: 0 }, 50000).type).toBe(
			"percentage"
		)
	})

	it("ignores cap when base rate is unknown", () => {
		expect(resolveOfferUnitDiscount(offer, 0).type).toBe("percentage")
	})
})
