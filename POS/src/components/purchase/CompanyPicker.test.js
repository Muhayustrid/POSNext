/**
 * @vitest-environment jsdom
 */
import { describe, expect, it } from "vitest"
import { mount } from "@vue/test-utils"
import CompanyPicker from "./CompanyPicker.vue"

const options = [
	{ company: "JURI GROUP" },
	{ company: "RS DR SOETARTO KOTA YOGYAKARTA" },
	{ company: "RS DUSTIRA CIMAHI" },
]

const mountPicker = () =>
	mount(CompanyPicker, { props: { modelValue: "JURI GROUP", options }, attachTo: document.body })

describe("CompanyPicker", () => {
	it("filters companies by typed text and emits the picked one", async () => {
		const wrapper = mountPicker()
		const input = wrapper.find('[data-test="company-picker"] input')
		await input.trigger("focus")
		await input.setValue("soetarto")

		const items = wrapper.findAll(".item-label")
		expect(items.map((i) => i.text())).toEqual(["RS DR SOETARTO KOTA YOGYAKARTA"])
		await items[0].element.closest("button").click()
		expect(wrapper.emitted("update:modelValue").at(-1)).toEqual(["RS DR SOETARTO KOTA YOGYAKARTA"])
		wrapper.unmount()
	})

	it("never emits an empty company when the search box is cleared", async () => {
		const wrapper = mountPicker()
		await wrapper.find(".clear-btn").trigger("click")
		expect(wrapper.emitted("update:modelValue")).toBeUndefined()
		wrapper.unmount()
	})
})
