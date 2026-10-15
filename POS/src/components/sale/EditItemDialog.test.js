// @vitest-environment jsdom
//
// Per-line rule: a line that carries an offer (pricing_rules) cannot also take
// a manual discount. The discount controls lock with a reason, and a save must
// never turn the offer's discount into a manual one on the row.
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, nextTick, reactive } from "vue"
import { usePOSCartStore } from "@/stores/posCart"

vi.mock("@/composables/useToast", () => ({
	useToast: () => ({
		showSuccess: vi.fn(),
		showWarning: vi.fn(),
		showInfo: vi.fn(),
		showError: vi.fn(),
	}),
}))

vi.mock("@/utils/apiWrapper", () => ({
	call: vi.fn(async () => ({})),
}))

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		FeatherIcon: stub,
		Input: stub,
		TextInput: stub,
		call: vi.fn(async () => ({})),
		createResource: () =>
			reactive({
				loading: false,
				data: null,
				error: null,
				reload: vi.fn(),
				submit: vi.fn(async () => ({})),
				reset: vi.fn(),
				update: vi.fn(),
			}),
	}
})

import EditItemDialog from "./EditItemDialog.vue"

function mountDialog(item) {
	const pinia = createPinia()
	setActivePinia(pinia)
	return mount(EditItemDialog, {
		props: {
			modelValue: true,
			item,
			warehouses: [],
			currency: "IDR",
		},
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
}

function mountDialogWithPinia(item, pinia) {
	setActivePinia(pinia)
	return mount(EditItemDialog, {
		props: {
			modelValue: true,
			item,
			warehouses: [],
			currency: "IDR",
		},
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
}

const OFFER_ROW = {
	item_code: "A",
	item_name: "A",
	quantity: 1,
	uom: "Nos",
	stock_uom: "Nos",
	rate: 100,
	price_list_rate: 100,
	discount_percentage: 10,
	discount_amount: 10,
	pricing_rules: ["PR-1"],
	is_free_item: 0,
}

beforeEach(() => {
	vi.clearAllMocks()
})

describe("offer row cannot take a manual discount", () => {
	it("locks the discount controls for an offer row", async () => {
		const wrapper = mountDialog({ ...OFFER_ROW })
		await flushPromises()

		expect(wrapper.vm.discountLockedByOffer).toBe(true)
	})

	it("does not lock a free row that merely carries pricing_rules", async () => {
		const wrapper = mountDialog({ ...OFFER_ROW, is_free_item: 1 })
		await flushPromises()

		expect(wrapper.vm.discountLockedByOffer).toBe(false)
	})

	it("does not send a manual discount when saving an offer row", async () => {
		const wrapper = mountDialog({ ...OFFER_ROW })
		await flushPromises()

		await wrapper.vm.updateItem()
		await flushPromises()

		const emitted = wrapper.emitted("update-item")
		expect(emitted).toHaveLength(1)
		// The row's offer discount must not be re-sent as a manual discount.
		expect(emitted[0][0].discount_percentage).toBe(0)
		expect(emitted[0][0].discount_amount).toBe(0)
	})
})

describe("a cart-wide transaction offer locks item discounts", () => {
	it("locks the discount controls under a transaction offer", async () => {
		const pinia = createPinia()
		setActivePinia(pinia)
		const cartStore = usePOSCartStore()
		cartStore.headerDiscountFromOffer = true

		const wrapper = mountDialogWithPinia(
			{
				item_code: "A",
				item_name: "A",
				quantity: 1,
				uom: "Nos",
				stock_uom: "Nos",
				rate: 100,
				price_list_rate: 100,
				discount_percentage: 0,
				discount_amount: 0,
				is_free_item: 0,
			},
			pinia
		)
		await flushPromises()

		expect(wrapper.vm.discountLockedByOffer).toBe(true)
	})

	it("hides the HQ code unlock while an offer locks the discount", async () => {
		const pinia = createPinia()
		setActivePinia(pinia)
		const cartStore = usePOSCartStore()
		const codeInput = 'input[placeholder="Enter the code from head office"]'
		// Earlier tests leave teleported dialogs in <body>; count relative to them.
		const before = document.body.querySelectorAll(codeInput).length

		const wrapper = mountDialogWithPinia(
			{
				item_code: "A",
				item_name: "A",
				quantity: 1,
				uom: "Nos",
				stock_uom: "Nos",
				rate: 100,
				price_list_rate: 100,
				discount_percentage: 0,
				discount_amount: 0,
				is_free_item: 0,
			},
			pinia
		)
		await flushPromises()
		expect(document.body.querySelectorAll(codeInput).length).toBe(before + 1)

		cartStore.headerDiscountFromOffer = true
		await nextTick()

		expect(document.body.querySelectorAll(codeInput).length).toBe(before)
		wrapper.unmount()
	})

	it("unlocks once the transaction offer is removed", async () => {
		const pinia = createPinia()
		setActivePinia(pinia)
		const cartStore = usePOSCartStore()
		cartStore.headerDiscountFromOffer = true

		const wrapper = mountDialogWithPinia(
			{
				item_code: "A",
				item_name: "A",
				quantity: 1,
				uom: "Nos",
				stock_uom: "Nos",
				rate: 100,
				price_list_rate: 100,
				discount_percentage: 0,
				discount_amount: 0,
				is_free_item: 0,
			},
			pinia
		)
		await flushPromises()
		expect(wrapper.vm.discountLockedByOffer).toBe(true)

		cartStore.headerDiscountFromOffer = false
		await nextTick()

		expect(wrapper.vm.discountLockedByOffer).toBe(false)
	})

	it("save under a cart-wide offer keeps the row's existing discount", async () => {
		const pinia = createPinia()
		setActivePinia(pinia)
		const cartStore = usePOSCartStore()
		cartStore.headerDiscountFromOffer = true

		const wrapper = mountDialogWithPinia(
			{
				item_code: "A",
				item_name: "A",
				quantity: 1,
				uom: "Nos",
				stock_uom: "Nos",
				rate: 100,
				price_list_rate: 100,
				discount_percentage: 10,
				discount_amount: 0,
				is_free_item: 0,
			},
			pinia
		)
		await flushPromises()
		expect(wrapper.vm.discountLockedByOffer).toBe(true)

		await wrapper.vm.updateItem()
		await flushPromises()

		// The cart offer locks the fields, but the row's own manual discount is
		// its data and must survive the save untouched.
		const emitted = wrapper.emitted("update-item")
		expect(emitted).toHaveLength(1)
		expect(emitted[0][0].discount_percentage).toBe(10)
		expect(emitted[0][0].discount_amount).toBe(0)
	})
})

describe("an applied coupon locks item discounts like a cart offer", () => {
	it("locks the discount controls (coupon reason) on a plain row", async () => {
		const pinia = createPinia()
		setActivePinia(pinia)
		const cartStore = usePOSCartStore()
		cartStore.appliedCoupon = { name: "COUP-1", code: "COUP-1" }

		const wrapper = mountDialogWithPinia(
			{
				item_code: "A",
				item_name: "A",
				quantity: 1,
				uom: "Nos",
				stock_uom: "Nos",
				rate: 100,
				price_list_rate: 100,
				discount_percentage: 0,
				discount_amount: 0,
				is_free_item: 0,
			},
			pinia
		)
		await flushPromises()

		expect(wrapper.vm.discountLockedByOffer).toBe(true)
		expect(wrapper.vm.discountLockReason).toBe("Locked (coupon applied)")
	})

	it("does not lock once the coupon is removed", async () => {
		const pinia = createPinia()
		setActivePinia(pinia)
		const cartStore = usePOSCartStore()
		cartStore.appliedCoupon = { name: "COUP-1", code: "COUP-1" }

		const wrapper = mountDialogWithPinia(
			{
				item_code: "A",
				item_name: "A",
				quantity: 1,
				uom: "Nos",
				stock_uom: "Nos",
				rate: 100,
				price_list_rate: 100,
				discount_percentage: 0,
				discount_amount: 0,
				is_free_item: 0,
			},
			pinia
		)
		await flushPromises()
		expect(wrapper.vm.discountLockedByOffer).toBe(true)

		cartStore.appliedCoupon = null
		await nextTick()

		expect(wrapper.vm.discountLockedByOffer).toBe(false)
	})
})
