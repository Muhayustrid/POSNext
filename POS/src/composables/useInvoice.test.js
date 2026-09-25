// COR-FE-12: updateItemQuantity treated 0 as "default to 1", silently selling
// one unit when the caller asked for none. Absent values still default to 1;
// an explicit 0 or negative is rejected instead.
import { beforeEach, describe, expect, it, vi } from "vitest"

vi.mock("frappe-ui", () => ({
	createResource: vi.fn(() => ({ loading: false, data: null, error: null, reload: vi.fn() })),
}))
vi.mock("@/utils/offline", () => ({
	isOffline: () => false,
	getCachedItem: vi.fn(),
}))
vi.mock("@/stores/serialNumber", () => ({
	useSerialNumberStore: () => ({ returnSerials: vi.fn() }),
}))
vi.mock("@/stores/discountRestriction", () => ({
	useDiscountRestrictionStore: () => ({ code: "", clearCode: vi.fn() }),
}))
vi.mock("@/stores/posSettings", () => ({
	usePOSSettingsStore: () => ({ queueEnabled: false }),
}))
vi.mock("@/stores/posShift", () => ({
	usePOSShiftStore: () => ({ profileCompany: null }),
}))
vi.mock("@/utils/queue/queueNumber", () => ({
	acquireQueueNumber: vi.fn(),
}))
vi.mock("@/utils/logger", () => ({
	logger: {
		create: () => ({
			debug: vi.fn(),
			info: vi.fn(),
			warn: vi.fn(),
			error: vi.fn(),
			success: vi.fn(),
		}),
	},
}))

import { useInvoice } from "./useInvoice"

function setupCartWithQuantity(quantity) {
	const invoice = useInvoice()
	invoice.invoiceItems.value = [
		{
			item_code: "ITEM-1",
			item_name: "Item 1",
			quantity,
			rate: 10000,
			price_list_rate: 10000,
			amount: quantity * 10000,
		},
	]
	return invoice
}

describe("updateItemQuantity quantity validation (COR-FE-12)", () => {
	beforeEach(() => {
		vi.clearAllMocks()
	})

	it("rejects an explicit 0 instead of silently selling 1 unit", () => {
		const { invoiceItems, updateItemQuantity } = setupCartWithQuantity(2)

		expect(() => updateItemQuantity("ITEM-1", 0)).toThrow()
		// The cart row keeps its previous quantity.
		expect(invoiceItems.value[0].quantity).toBe(2)
	})

	it("rejects a negative quantity", () => {
		const { invoiceItems, updateItemQuantity } = setupCartWithQuantity(2)

		expect(() => updateItemQuantity("ITEM-1", -3)).toThrow()
		expect(invoiceItems.value[0].quantity).toBe(2)
	})

	it("still defaults an absent value to 1 (legacy behaviour)", () => {
		const { invoiceItems, updateItemQuantity } = setupCartWithQuantity(2)

		updateItemQuantity("ITEM-1", null)

		expect(invoiceItems.value[0].quantity).toBe(1)
	})

	it("applies a valid positive quantity", () => {
		const { invoiceItems, updateItemQuantity } = setupCartWithQuantity(2)

		updateItemQuantity("ITEM-1", 5)

		expect(invoiceItems.value[0].quantity).toBe(5)
	})
})
