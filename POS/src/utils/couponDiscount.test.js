// The client coupon formula must match pos_coupon.expected_coupon_discount /
// coupon_min_amount_unmet exactly, or the recomputed header discount diverges
// from the server and checkout fails ("At least one mode of payment...").
import { describe, expect, it } from "vitest"
import { couponMinAmountUnmet, expectedCouponDiscount } from "./couponDiscount"

describe("expectedCouponDiscount", () => {
	const cases = [
		{
			name: "percentage of the grand basis",
			coupon: { discount_type: "Percentage", discount_percentage: 20 },
			bases: { grand: 100000, net: 100000 },
			expected: 20000,
		},
		{
			name: "percentage capped by max_amount",
			coupon: {
				discount_type: "Percentage",
				discount_percentage: 20,
				max_amount: 5000,
			},
			bases: { grand: 100000, net: 100000 },
			expected: 5000,
		},
		{
			name: "fixed amount never exceeds the basis",
			coupon: { discount_type: "Amount", discount_amount: 30000 },
			bases: { grand: 10000, net: 10000 },
			expected: 10000,
		},
		{
			name: "net total uses the net basis",
			coupon: {
				discount_type: "Percentage",
				discount_percentage: 20,
				apply_on: "Net Total",
			},
			bases: { grand: 100000, net: 40000 },
			expected: 8000,
		},
		{
			name: "unknown type grants nothing",
			coupon: { discount_type: "Mystery", discount_percentage: 20 },
			bases: { grand: 100000, net: 100000 },
			expected: 0,
		},
		{
			name: "a zero basis yields zero",
			coupon: { discount_type: "Percentage", discount_percentage: 20 },
			bases: { grand: 0, net: 0 },
			expected: 0,
		},
	]

	for (const c of cases) {
		it(c.name, () => {
			expect(expectedCouponDiscount(c.coupon, c.bases)).toBe(c.expected)
		})
	}
})

describe("couponMinAmountUnmet", () => {
	it("is true when the basis is below the minimum", () => {
		const coupon = {
			discount_type: "Amount",
			discount_amount: 1000,
			min_amount: 50000,
		}
		expect(couponMinAmountUnmet(coupon, { grand: 40000, net: 40000 })).toBe(
			true,
		)
	})

	it("is false when the basis meets the minimum", () => {
		const coupon = {
			discount_type: "Amount",
			discount_amount: 1000,
			min_amount: 50000,
		}
		expect(couponMinAmountUnmet(coupon, { grand: 50000, net: 50000 })).toBe(
			false,
		)
	})

	it("is false when no minimum is set", () => {
		expect(
			couponMinAmountUnmet(
				{ discount_type: "Amount", discount_amount: 1000 },
				{
					grand: 0,
					net: 0,
				},
			),
		).toBe(false)
	})
})
