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

	it("converts to flat amount when cap binds", () => {
		expect(resolveOfferUnitDiscount(offer, 50000)).toEqual({
			type: "amount",
			value: 20000,
			capped: true,
		})
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
