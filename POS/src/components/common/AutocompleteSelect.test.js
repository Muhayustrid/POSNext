/**
 * @vitest-environment jsdom
 *
 * SEC-12: v-html highlight must escape option labels (data) before marking,
 * and a query of RegExp metacharacters like "(" must not crash the render.
 */
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import AutocompleteSelect from "./AutocompleteSelect.vue"

const mountSelect = (options) =>
	mount(AutocompleteSelect, {
		props: { options, modelValue: "" },
	})

const options = [
	{ value: "1", label: "<img src=x onerror=alert(1)>Kopi Susu" },
	{ value: "2", label: "Kopi Hitam" },
]

describe("AutocompleteSelect v-html highlight (SEC-12)", () => {
	it("renders option labels containing HTML as text, never markup", async () => {
		const wrapper = mountSelect(options)
		await wrapper.find("input.select-input").trigger("focus")

		const label = wrapper.find(".item-label")
		expect(label.element.innerHTML).not.toContain("<img")
		expect(label.element.textContent).toContain(
			"<img src=x onerror=alert(1)>Kopi Susu",
		)
		wrapper.unmount()
	})

	it("does not crash when the query is a RegExp metacharacter like '('", async () => {
		const wrapper = mountSelect(options)
		// "alert(1)" inside the injected label contains "(" so it stays listed.
		await wrapper.find("input.select-input").setValue("(")

		const labels = wrapper.findAll(".item-label")
		expect(labels).toHaveLength(1)
		expect(labels[0].element.textContent).toContain("Kopi Susu")
		wrapper.unmount()
	})

	it("still highlights plain-text matches after the query", async () => {
		const wrapper = mountSelect(options)
		await wrapper.find("input.select-input").setValue("hitam")

		// only "Kopi Hitam" matches the filter
		const labels = wrapper.findAll(".item-label")
		expect(labels).toHaveLength(1)
		expect(labels[0].element.innerHTML).toContain("<mark>Hitam</mark>")
		wrapper.unmount()
	})
})
