import { beforeEach, describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"
import PackageSelectionDialog from "./PackageSelectionDialog.vue"

vi.mock("@/stores/posPackages", () => ({
	usePOSPackagesStore: () => ({ quote: vi.fn() }),
}))
vi.mock("frappe-ui", () => ({
	Dialog: { template: '<div><slot name="body-content" /><slot name="actions" /></div>' },
	Button: { props: ["label"], template: '<button :aria-label="label"><slot /></button>' },
}))

beforeEach(() => {
	globalThis.__ = (text, args = []) => text.replace(/\{(\d+)\}/g, (_, n) => args[n] ?? "")
})

function openDialog(extraPkg = {}) {
	return mount(PackageSelectionDialog, {
		props: {
			modelValue: true,
			currency: "IDR",
			pkg: {
				name: "BREAKFAST",
				package_name: "Breakfast",
				base_price: 25000,
				items: [{ item_code: "ROTI", item_name: "Roti", qty: 1 }],
				groups: [
					{ group_key: "drink", label: "Drink", min_qty: 1, max_qty: 1 },
					{ group_key: "extra", label: "Extras", min_qty: 0, max_qty: 2 },
				],
				options: [
					{
						option_id: "coffee",
						group_key: "drink",
						item_code: "COFFEE",
						item_name: "Coffee",
						image: "coffee.png",
					},
					{
						option_id: "tea",
						group_key: "drink",
						item_code: "TEA",
						item_name: "Tea",
					},
					{
						option_id: "bread",
						group_key: "extra",
						item_code: "BREAD",
						item_name: "Bread",
						price_adjustment: 5000,
					},
				],
				...extraPkg,
			},
		},
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
}

describe("package option controls", () => {
	it("exposes named groups and announces single-pick selection without changing toggle behavior", async () => {
		const wrapper = openDialog()
		expect(wrapper.find('[role="group"]').attributes("aria-labelledby")).toBe("package-group-drink")
		const coffee = wrapper.find('[aria-label="Choose: Coffee"]')
		expect(coffee.attributes("aria-pressed")).toBe("false")
		await coffee.trigger("click")
		expect(coffee.attributes("aria-pressed")).toBe("true")
		await wrapper.find('[aria-label="Choose: Tea"]').trigger("click")
		expect(coffee.attributes("aria-pressed")).toBe("false")
		await wrapper.find('[aria-label="Choose: Tea"]').trigger("click")
		expect(wrapper.find('[role="status"]').text()).toContain("Choose at least")
	})

	it("names the quantity controls by item and preserves limits and running price", async () => {
		const wrapper = openDialog()
		const increase = wrapper.find('[aria-label="Increase: Bread"]')
		const decrease = wrapper.find('[aria-label="Decrease: Bread"]')
		expect(decrease.attributes("disabled")).toBeDefined()
		await increase.trigger("click")
		expect(wrapper.text()).toContain("30.000")
		await increase.trigger("click")
		expect(increase.attributes("disabled")).toBeDefined()
		await decrease.trigger("click")
		expect(increase.attributes("disabled")).toBeUndefined()
	})

	it("renders thumbnail when image is available and falls back to placeholder icon", () => {
		const wrapper = openDialog()
		const coffeeImg = wrapper.find('img[src="coffee.png"]')
		expect(coffeeImg.exists()).toBe(true)
		expect(coffeeImg.attributes("alt")).toBe("Coffee")

		const optionRows = wrapper.findAll('.package-options-list [role="group"] > div')
		const teaOption = optionRows[1]
		expect(teaOption.find("img").exists()).toBe(false)
		expect(teaOption.find("svg").exists()).toBe(true)
	})

	it("renders summary panel with included items, running selections, and sticky layout", async () => {
		const wrapper = openDialog()
		const summary = wrapper.find(".package-summary-panel")
		expect(summary.exists()).toBe(true)
		expect(summary.classes()).toContain("sm:sticky")
		expect(summary.text()).toContain("Breakfast")
		expect(summary.text()).toContain("Roti")
		expect(summary.text()).toContain("x1")

		expect(summary.text()).toContain("None selected")

		await wrapper.find('[aria-label="Choose: Coffee"]').trigger("click")
		expect(summary.text()).toContain("Coffee")
		expect(summary.text()).toContain("x1")

		await wrapper.find('[aria-label="Increase: Bread"]').trigger("click")
		expect(summary.text()).toContain("Bread")
		expect(summary.text()).toContain("x1")
	})
})
