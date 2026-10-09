/**
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

const calls = vi.hoisted(() => [])
vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	return {
		Button: defineComponent({ setup: (_, { slots }) => () => h("button", slots.default?.()) }),
		FeatherIcon: defineComponent({ props: ["name"], setup: () => () => h("i") }),
		call: (url, params) => {
			calls.push({ url, params })
			if (url.endsWith("get_item_groups")) return Promise.resolve([{ item_group: "Drinks" }])
			return Promise.resolve([
				{ item_code: "TEA", item_name: "Tea", item_group: "Drinks", stock_uom: "Nos", actual_qty: 0, rate: 5000 },
			])
		},
	}
})
vi.mock("@/components/sale/WarehouseAvailabilityDialog.vue", () => ({ default: { render: () => null } }))
vi.mock("@/composables/useToast", () => ({ useToast: () => ({ showError: vi.fn() }) }))
globalThis.__ = (m) => m

import ProductsView from "./ProductsView.vue"

describe("ProductsView", () => {
	it("lists products and refetches with the stock filter", async () => {
		const w = mount(ProductsView, {
			props: { posProfile: "POS-1", currency: "IDR" },
			global: { config: { globalProperties: { __: globalThis.__ } } },
		})
		await flushPromises()
		expect(w.findAll('[data-test="products-row"]')).toHaveLength(1)
		expect(w.text()).toContain("Tea")
		expect(w.text()).toContain("Drinks")

		await w.findAll("button").find((b) => b.text() === "Out of stock").trigger("click")
		await flushPromises()
		const last = calls.filter((c) => c.url.endsWith("get_product_list")).at(-1)
		expect(last.params).toMatchObject({ pos_profile: "POS-1", stock: "out", start: 0 })
	})
})
