// COR-FE-12: updateItemQuantity treated 0 as "default to 1", silently selling
// one unit when the caller asked for none. Absent values still default to 1;
// an explicit 0 or negative is rejected instead.
import { beforeEach, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"

const { invoicePayloads, stockValidationMock, shiftStoreState } = vi.hoisted(() => ({
	invoicePayloads: [],
	stockValidationMock: { calls: [], result: [] },
	shiftStoreState: { profileCompany: null, profileWarehouse: "Outlet Warehouse - RR" },
}))

vi.mock("frappe-ui", () => ({
	createResource: vi.fn((opts) => {
		if (opts?.url === "pos_next.api.invoices.validate_cart_items") {
			return {
				loading: false,
				data: null,
				error: null,
				reload: vi.fn(),
				submit: vi.fn(async (arg) => {
					stockValidationMock.calls.push(arg)
					if (typeof stockValidationMock.impl === "function") {
						return stockValidationMock.impl(arg)
					}
					return stockValidationMock.result
				}),
			}
		}
		return {
			loading: false,
			data: null,
			error: null,
			reload: vi.fn(),
			// updateInvoiceResource.submit({ data: invoiceData }) — record every
			// doctype-bearing payload. submitInvoiceResource.submit rides the same
			// factory but its data carries no doctype, so it never lands here.
			submit: vi.fn(async (arg) => {
				if (arg?.data?.doctype) invoicePayloads.push(arg.data)
				return { data: { name: "POS-INV-0001" } }
			}),
		}
	}),
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
	usePOSShiftStore: () => shiftStoreState,
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

// A1: ERPNext throws "Please select Apply Discount On" inside
// calculate_taxes_and_totals whenever discount_amount is set without
// apply_discount_on, so BOTH checkout payload blocks (saveDraft and
// submitInvoice) must always carry it: the active coupon's apply_on, or the
// server default "Grand Total" for a header discount without a coupon.
describe("checkout payload apply_discount_on (A1)", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		invoicePayloads.length = 0
	})

	function setupInvoice() {
		const invoice = useInvoice()
		invoice.invoiceItems.value = [
			{
				item_code: "ITEM-1",
				item_name: "Item 1",
				quantity: 2,
				rate: 10000,
				price_list_rate: 10000,
				amount: 20000,
			},
		]
		// subtotal is fed by the incremental cache (useInvoice.js:202); assigning
		// invoiceItems directly does not rebuild it. Without this, applyDiscount
		// clamps the coupon amount against a subtotal of 0.
		invoice.rebuildIncrementalCache()
		return invoice
	}

	it("saveDraft with an active coupon sends apply_discount_on = coupon.apply_on", async () => {
		const { applyDiscount, saveDraft } = setupInvoice()

		applyDiscount({
			name: "KOUPON-NET",
			code: "NET10",
			amount: 5000,
			apply_on: "Net Total",
		})
		await saveDraft()

		expect(invoicePayloads).toHaveLength(1)
		expect(invoicePayloads[0].coupon_code).toBe("NET10")
		expect(invoicePayloads[0].discount_amount).toBe(5000)
		expect(invoicePayloads[0].apply_discount_on).toBe("Net Total")
	})

	it("submitInvoice with an active coupon sends apply_discount_on = coupon.apply_on", async () => {
		const { applyDiscount, submitInvoice } = setupInvoice()

		applyDiscount({
			name: "KOUPON-NET",
			code: "NET10",
			amount: 5000,
			apply_on: "Net Total",
		})
		await submitInvoice()

		expect(invoicePayloads).toHaveLength(1)
		expect(invoicePayloads[0].coupon_code).toBe("NET10")
		expect(invoicePayloads[0].apply_discount_on).toBe("Net Total")
	})

	it("header discount without a coupon sends apply_discount_on 'Grand Total'", async () => {
		const { additionalDiscount, saveDraft } = setupInvoice()

		additionalDiscount.value = 2500
		await saveDraft()

		expect(invoicePayloads).toHaveLength(1)
		expect(invoicePayloads[0].coupon_code).toBeNull()
		expect(invoicePayloads[0].discount_amount).toBe(2500)
		expect(invoicePayloads[0].apply_discount_on).toBe("Grand Total")
	})
})

// A header discount (offer or coupon) stacked on rows already discounted to
// zero must not push the grand total below zero — the cashier could not
// check out a negative total.
describe("header discount clamp", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		invoicePayloads.length = 0
	})

	it("caps the header discount at what is left after item discounts", async () => {
		const invoice = useInvoice()
		invoice.invoiceItems.value = [
			{
				item_code: "ITEM-1",
				item_name: "Item 1",
				quantity: 1,
				rate: 0,
				price_list_rate: 12000,
				discount_percentage: 100,
				discount_amount: 12000,
				amount: 0,
			},
		]
		invoice.rebuildIncrementalCache()
		invoice.additionalDiscount.value = 12000

		expect(invoice.grandTotal.value).toBe(0)
		expect(invoice.totalDiscount.value).toBe(12000)

		await invoice.saveDraft()
		expect(invoicePayloads[0].discount_amount).toBe(0)
	})
})

// Fase 2: an allocation-mode package keeps its allocated component rates on
// the submitted rows, with the parent as a zero line. Tax-inclusive must not
// touch the component backend rate (the gross basis matches the package
// price), and the linkage fields must survive formatting.
describe("package allocation rows (Fase 2)", () => {
	const allocationQuote = {
		valid: true,
		error: null,
		total: 23000,
		lines: [
			{
				item_code: "PKG-1",
				item_name: "Paket",
				qty: 1,
				rate: 0,
				role: "Package",
			},
			{
				item_code: "COLA",
				item_name: "Cola",
				qty: 2,
				uom: "Nos",
				rate: 4000,
				role: "Package Item",
				is_stock_item: 1,
			},
			{
				item_code: "CHIPS",
				item_name: "Chips",
				qty: 1,
				uom: "Nos",
				rate: 15000,
				role: "Package Item",
				is_stock_item: 1,
			},
		],
		snapshot: {
			package: "PKG-1",
			package_name: "Paket",
			total: 23000,
			allocation: { mode: "proportional", precision: 2 },
		},
	}
	const pkg = { name: "PKG-1", package_name: "Paket" }

	function setupAllocatedPackage() {
		const invoice = useInvoice()
		invoice.addPackage(allocationQuote, pkg)
		invoice.rebuildIncrementalCache()
		return invoice
	}

	it("submits parent at 0 and components at their allocated rates", () => {
		const invoice = setupAllocatedPackage()
		const rows = invoice.formatItemsForSubmission(invoice.invoiceItems.value)

		expect(rows.map((r) => [r.item_code, r.rate, r.qty, r.pos_package_role])).toEqual([
			["PKG-1", 0, 1, "Package"],
			["COLA", 4000, 2, "Package Item"],
			["CHIPS", 15000, 1, "Package Item"],
		])
		expect(JSON.parse(rows[0].pos_package_snapshot).allocation).toEqual({
			mode: "proportional",
			precision: 2,
		})
		// Component rows carry the linkage; only the parent carries the snapshot.
		expect(rows[1].pos_package_instance).toBe(rows[0].pos_package_instance)
		expect(rows[1].pos_package_snapshot).toBeNull()
	})

	it("keeps the allocated gross rate in tax-inclusive mode", () => {
		const invoice = setupAllocatedPackage()
		invoice.setTaxInclusive(true)

		const rows = invoice.formatItemsForSubmission(invoice.invoiceItems.value)

		expect(rows[0].rate).toBe(0)
		expect(rows[1].rate).toBe(4000)
		expect(rows[2].rate).toBe(15000)
	})
})

// The coupon's header discount must follow the cart (recomputed with the
// server formula against the pre-header bases) instead of freezing at the
// amount computed when the coupon was applied — otherwise a later item
// discount leaves a stale, too-large header discount.
describe("coupon follows the cart (server formula)", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		invoicePayloads.length = 0
	})

	const twentyPct = {
		name: "C20",
		code: "C20",
		apply_on: "Grand Total",
		coupon: { discount_type: "Percentage", discount_percentage: 20, apply_on: "Grand Total" },
	}

	function setupCouponCart() {
		const invoice = useInvoice()
		invoice.invoiceItems.value = [
			{
				item_code: "ITEM-1",
				item_name: "Item 1",
				quantity: 1,
				rate: 100000,
				price_list_rate: 100000,
				amount: 100000,
			},
		]
		invoice.rebuildIncrementalCache()
		return invoice
	}

	function applyManualItemDiscount(invoice, percentage) {
		const item = invoice.invoiceItems.value[0]
		item.discount_percentage = percentage
		invoice.recalculateItem(item)
		invoice.rebuildIncrementalCache()
	}

	it("drops to zero when a 100% item discount follows a 20% coupon", async () => {
		const invoice = setupCouponCart()
		invoice.applyDiscount({ ...twentyPct })
		expect(invoice.additionalDiscount.value).toBe(20000)

		applyManualItemDiscount(invoice, 100)
		await nextTick()

		expect(invoice.additionalDiscount.value).toBe(0)
		expect(invoice.grandTotal.value).toBe(0)
	})

	it("shrinks to 20% of the basis left after a manual 90% discount", async () => {
		const invoice = setupCouponCart()
		invoice.applyDiscount({ ...twentyPct })

		applyManualItemDiscount(invoice, 90)
		await nextTick()

		// 20% of the remaining 10000 — not the 20000 frozen at apply time.
		expect(invoice.additionalDiscount.value).toBe(2000)
	})
})

describe("submitInvoice stock pre-check", () => {
	beforeEach(() => {
		vi.clearAllMocks()
		invoicePayloads.length = 0
		stockValidationMock.calls.length = 0
		stockValidationMock.result = []
		stockValidationMock.impl = null
		shiftStoreState.profileWarehouse = "Outlet Warehouse - RR"
	})

	function setupInvoiceForStockCheck() {
		const invoice = useInvoice()
		invoice.posProfile.value = "POS Profile 1"
		invoice.invoiceItems.value = [
			{
				item_code: "PJO2605",
				item_name: "Roti Coklat",
				quantity: 3,
				rate: 10000,
				price_list_rate: 10000,
				amount: 30000,
				warehouse: "Old Warehouse",
			},
		]
		invoice.payments.value = [
			{
				mode_of_payment: "Cash",
				amount: 30000,
			},
		]
		invoice.rebuildIncrementalCache()
		return invoice
	}

	it("calls stock pre-check with profile warehouse and aborts before creating draft when stock is short", async () => {
		const invoice = setupInvoiceForStockCheck()
		stockValidationMock.result = [
			{
				item_code: "PJO2605",
				item_name: "Roti Coklat",
				warehouse: "Outlet Warehouse - RR",
				requested_qty: 3,
				available_qty: 1,
			},
		]

		await expect(invoice.submitInvoice()).rejects.toThrow()

		// validate_cart_items MUST be called with pre-check items and profile warehouse
		expect(stockValidationMock.calls).toHaveLength(1)
		expect(stockValidationMock.calls[0].pos_profile).toBe("POS Profile 1")
		expect(stockValidationMock.calls[0].items[0].item_code).toBe("PJO2605")
		expect(stockValidationMock.calls[0].items[0].warehouse).toBe("Outlet Warehouse - RR")

		// NO draft invoice was created on the server
		expect(invoicePayloads).toHaveLength(0)
	})

	it("proceeds to draft creation when stock is sufficient", async () => {
		const invoice = setupInvoiceForStockCheck()
		stockValidationMock.result = []

		await invoice.submitInvoice()

		expect(stockValidationMock.calls).toHaveLength(1)
		expect(invoicePayloads).toHaveLength(1)
	})
})
