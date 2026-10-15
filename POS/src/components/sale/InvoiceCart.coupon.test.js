// A coupon behaves like an applied offer: the Coupon button carries a visible
// "applied" badge showing the code (falling back to the coupon name when a
// restored draft has no code), instead of the gift-card count badge.
import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"
import { createPinia, setActivePinia } from "pinia"
import { defineComponent, reactive } from "vue"

vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

const mocks = vi.hoisted(() => ({ call: vi.fn() }))

vi.mock("@/utils/apiWrapper", () => ({ call: mocks.call }))

vi.mock("@/utils/offline", () => ({ isOffline: () => false }))

vi.mock("@/utils/offline/workerClient", () => ({
	offlineWorker: new Proxy(
		{},
		{
			get: (t, prop) =>
				prop in t ? t[prop] : vi.fn().mockRejectedValue(new Error("offline")),
		},
	),
}))

vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({
		formatCurrency: (v) => String(v ?? ""),
		formatQuantity: (v) => String(v ?? ""),
		formatDate: (v) => String(v ?? ""),
		formatTime: (v) => String(v ?? ""),
		formatDateTime: (v) => String(v ?? ""),
	}),
}))

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		FeatherIcon: stub,
		Button: stub,
		Input: stub,
		TextInput: stub,
		createResource: () =>
			reactive({
				loading: false,
				data: null,
				error: null,
				reload: vi.fn(async () => {}),
			}),
	}
})

import { usePOSCartStore } from "@/stores/posCart"

import InvoiceCart from "./InvoiceCart.vue"

const couponButton = (wrapper) =>
	wrapper.find('button[aria-label="Apply coupon code"]')

function mountCart() {
	const pinia = createPinia()
	setActivePinia(pinia)
	const items = reactive([
		{
			item_code: "ITEM-1",
			item_name: "Barang Satu",
			quantity: 1,
			rate: 1000,
			uom: "Nos",
		},
	])
	const Host = defineComponent({
		components: { InvoiceCart },
		setup() {
			return { items }
		},
		template: `
			<InvoiceCart
				:items="items"
				:customer="null"
				:subtotal="1000"
				:tax-amount="0"
				:discount-amount="0"
				:grand-total="1000"
				pos-profile="Kasir 1"
				currency="IDR"
				:applied-offers="[]"
				:warehouses="[]"
			/>
		`,
	})
	const wrapper = mount(Host, {
		global: {
			plugins: [pinia],
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
	return { wrapper, cartStore: usePOSCartStore() }
}

describe("coupon badge on the Coupon button", () => {
	it("shows the coupon code when a coupon is applied", async () => {
		const { wrapper, cartStore } = mountCart()
		cartStore.appliedCoupon = { name: "Summer Coupon", code: "SAVE10" }
		await flushPromises()

		expect(couponButton(wrapper).text()).toContain("SAVE10")
		wrapper.unmount()
	})

	it("falls back to the coupon name when the code is empty", async () => {
		const { wrapper, cartStore } = mountCart()
		cartStore.appliedCoupon = { name: "DRAFT-COUPON", code: "" }
		await flushPromises()

		expect(couponButton(wrapper).text()).toContain("DRAFT-COUPON")
		wrapper.unmount()
	})

	it("shows no coupon badge when none is applied", async () => {
		const { wrapper, cartStore } = mountCart()
		expect(cartStore.appliedCoupon).toBeNull()
		await flushPromises()

		expect(couponButton(wrapper).text()).not.toContain("SAVE10")
		wrapper.unmount()
	})
})
