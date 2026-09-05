import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";

const callMock = vi.hoisted(() => vi.fn());

vi.mock("@/utils/apiWrapper", () => ({ call: callMock }));
vi.mock("@/utils/logger", () => ({
	logger: { create: () => ({ debug: vi.fn(), info: vi.fn(), warn: vi.fn(), error: vi.fn(), success: vi.fn() }) },
}));

import { useDiscountRestrictionStore } from "./discountRestriction";

describe("discountRestriction store", () => {
	beforeEach(() => {
		setActivePinia(createPinia())
		callMock.mockReset()
	})

	describe("fetchStatus", () => {
		it("stores the server status", async () => {
			callMock.mockResolvedValue({ enabled: true })
			const store = useDiscountRestrictionStore()

			await store.fetchStatus("Company A")

			expect(callMock).toHaveBeenCalledWith("pos_next.api.discount_code.get_status", {
				company: "Company A",
			})
			expect(store.applicable).toBe(true)
		})

		it("defaults to enabled when the fetch fails (server still enforces)", async () => {
			callMock.mockRejectedValue(new Error("offline"))
			const store = useDiscountRestrictionStore()

			await store.fetchStatus("Company A")

			expect(store.applicable).toBe(true)
		})

		it("can be switched off by the server payload", async () => {
			callMock.mockResolvedValue({ enabled: false })
			const store = useDiscountRestrictionStore()

			await store.fetchStatus("Company A")

			expect(store.applicable).toBe(false)
		})

		it("ignores an empty company", async () => {
			const store = useDiscountRestrictionStore()

			await store.fetchStatus("")

			expect(callMock).not.toHaveBeenCalled()
		})
	})

	describe("itemHasDiscount", () => {
		it("detects explicit percentage and amount discounts", () => {
			const store = useDiscountRestrictionStore()
			expect(store.itemHasDiscount({ discount_percentage: 10 })).toBe(true)
			expect(store.itemHasDiscount({ discount_amount: 5000 })).toBe(true)
			expect(store.itemHasDiscount({})).toBe(false)
		})

		it("detects a manual rate edit below price_list_rate", () => {
			const store = useDiscountRestrictionStore()
			expect(
				store.itemHasDiscount({ is_rate_manually_edited: 1, rate: 9000, price_list_rate: 10000 })
			).toBe(true)
			expect(
				store.itemHasDiscount({ is_rate_manually_edited: 1, rate: 10000, price_list_rate: 10000 })
			).toBe(false)
		})
	})

	describe("needsCodeForCart", () => {
		it("requires a code for an additional discount", async () => {
			const store = useDiscountRestrictionStore()

			expect(store.needsCodeForCart(25000, [])).toBe(true)
		})

		it("requires a code when any item is discounted", () => {
			const store = useDiscountRestrictionStore()

			const items = [
				{ item_code: "ITEM-A" },
				{ item_code: "ITEM-B", discount_percentage: 10 },
			]
			expect(store.needsCodeForCart(0, items)).toBe(true)
		})

		it("does not require a code for undiscounted carts", () => {
			const store = useDiscountRestrictionStore()

			expect(store.needsCodeForCart(0, [{ item_code: "ITEM-A" }])).toBe(false)
			expect(store.needsCodeForCart(0, [])).toBe(false)
		})

		it("does not require a code when the gate is disabled", async () => {
			callMock.mockResolvedValue({ enabled: false })
			const store = useDiscountRestrictionStore()
			await store.fetchStatus("Company A")

			expect(store.needsCodeForCart(25000, [{ discount_percentage: 50 }])).toBe(false)
		})
	})

	describe("code handling", () => {
		it("normalizes the code (trim + uppercase) and reports hasCode", () => {
			const store = useDiscountRestrictionStore()
			expect(store.hasCode).toBe(false)

			store.setCode("  abcd2345 ")
			expect(store.code).toBe("ABCD2345")
			expect(store.hasCode).toBe(true)

			store.clearCode()
			expect(store.code).toBe("")
			expect(store.hasCode).toBe(false)
		})

		it("validateCode rejects an empty code without calling the server", async () => {
			const store = useDiscountRestrictionStore()

			const result = await store.validateCode({})

			expect(result.valid).toBe(false)
			expect(callMock).not.toHaveBeenCalled()
		})

		it("validateCode sends only discounted items", async () => {
			const store = useDiscountRestrictionStore()
			await store.fetchStatus("Company A")
			store.setCode("ABCD2345")
			callMock.mockClear()
			callMock.mockResolvedValue({ valid: true, requires_code: true })

			const items = [
				{ item_code: "ITEM-A", discount_percentage: 10 },
				{ item_code: "ITEM-C", discount_percentage: 99 },
				{ item_code: "ITEM-B" },
			]
			const result = await store.validateCode({ items, additionalDiscount: 0 })

			expect(result.valid).toBe(true)
			const [method, params] = callMock.mock.calls[0]
			expect(method).toBe("pos_next.api.discount_code.validate_confirmation_code")
			expect(params).toMatchObject({
				code: "ABCD2345",
				company: "Company A",
				additional_discount: 0,
			})
			const sentItems = JSON.parse(params.items)
			expect(sentItems).toEqual([
				{ item_code: "ITEM-A", discount_percentage: 10, discount_amount: 0, rate: 0, price_list_rate: 0, is_rate_manually_edited: 0 },
				{ item_code: "ITEM-C", discount_percentage: 99, discount_amount: 0, rate: 0, price_list_rate: 0, is_rate_manually_edited: 0 },
			])
		})

		it("validateCode returns an error payload instead of throwing", async () => {
			const store = useDiscountRestrictionStore()
			await store.fetchStatus("Company A")
			store.setCode("ABCD2345")
			callMock.mockRejectedValue(new Error("boom"))

			const result = await store.validateCode({})

			expect(result.valid).toBe(false)
			expect(result.message).toBeTruthy()
		})
	})

	describe("reset", () => {
		it("clears status and code but keeps the gate enabled", async () => {
			const store = useDiscountRestrictionStore()
			await store.fetchStatus("Company A")
			store.setCode("ABCD2345")

			store.reset()

			expect(store.applicable).toBe(true)
			expect(store.hasCode).toBe(false)
		})
	})
})
