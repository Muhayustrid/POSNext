import { beforeEach, describe, expect, it, vi } from "vitest"
import { createPinia, setActivePinia } from "pinia"

const callMock = vi.hoisted(() => vi.fn())

vi.mock("@/utils/apiWrapper", () => ({ call: callMock }))
vi.mock("@/utils/offline", () => ({ isOffline: () => true }))
vi.mock("@/utils/offline/db", () => ({
	getOneTimeRedemptions: vi.fn(async () => []),
	setOneTimeRedemptions: vi.fn(async () => {}),
	addOneTimeRedemptions: vi.fn(async () => {}),
}))
// posShift pulls in frappe-ui resources which cannot resolve under vitest;
// ensureOffersFetched (the only consumer) is not exercised by these tests.
vi.mock("@/stores/posShift", () => ({
	usePOSShiftStore: () => ({ currentProfile: null }),
}))

import { usePOSOffersStore } from "./posOffers"

const baseOffer = {
	name: "PR-A",
	title: "Promo Gula 50%",
	offer: "Item Price",
	auto: 1,
	coupon_based: 0,
	discount_type: "Discount Percentage",
	discount_percentage: 50,
	eligible_items: ["GULA-1"],
	min_qty: 0,
	max_qty: 0,
	min_amt: 0,
	max_amt: 0,
}

describe("posOffers store quota normalization", () => {
	beforeEach(() => {
		setActivePinia(createPinia())
	})

	function snapshotWith(itemCode = "GULA-1") {
		const store = usePOSOffersStore()
		store.updateCartSnapshot({
			subtotal: 100000,
			itemCount: 2,
			itemCodes: [itemCode],
			itemGroups: [],
			brands: [],
		})
		return store
	}

	it("coerces quota fields and flags exhausted offers", () => {
		const store = usePOSOffersStore()
		store.setAvailableOffers([{ ...baseOffer, quota_limit: "50", quota_remaining: 12, quota_used: "38" }])

		expect(store.availableOffers[0].quota_limit).toBe(50)
		expect(store.availableOffers[0].quota_remaining).toBe(12)
		expect(store.availableOffers[0].quota_used).toBe(38)
		expect(store.availableOffers[0].quota_exhausted).toBe(false)
	})

	it("treats zero limit as unlimited (no quota badge)", () => {
		const store = usePOSOffersStore()
		store.setAvailableOffers([{ ...baseOffer, quota_limit: 0, quota_remaining: null }])

		expect(store.availableOffers[0].quota_exhausted).toBe(false)
	})

	it("marks exhausted when remaining is 0", () => {
		const store = usePOSOffersStore()
		store.setAvailableOffers([{ ...baseOffer, quota_limit: 50, quota_remaining: 0 }])

		expect(store.availableOffers[0].quota_exhausted).toBe(true)
	})

	it("keeps null remaining (unlimited) offers eligible", () => {
		const store = snapshotWith()
		store.setAvailableOffers([{ ...baseOffer, quota_limit: 0, quota_remaining: null }])

		expect(store.allEligibleOffers.some((o) => o.name === "PR-A")).toBe(true)
	})

	it("excludes exhausted offers from eligible lists", () => {
		const store = snapshotWith()
		store.setAvailableOffers([
			{ ...baseOffer, quota_limit: 50, quota_remaining: 0 },
			{ ...baseOffer, name: "PR-B", quota_limit: 50, quota_remaining: 3 },
		])

		expect(store.allEligibleOffers.map((o) => o.name)).toEqual(["PR-B"])
		expect(store.autoEligibleOffers.map((o) => o.name)).toEqual(["PR-B"])
	})
})
