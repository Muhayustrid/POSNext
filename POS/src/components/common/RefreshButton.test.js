/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"

vi.mock("frappe-ui", async () => {
	const { defineComponent } = await import("vue")
	const FeatherIcon = defineComponent({
		name: "FeatherIcon",
		props: ["name"],
		template: `<i :data-icon="name" />`,
	})
	return { FeatherIcon }
})

globalThis.__ = (message) => message

import RefreshButton from "./RefreshButton.vue"

const icon = (wrapper) => wrapper.find('[data-test="refresh-button"] i')
const button = (wrapper) => wrapper.find('[data-test="refresh-button"]')

function mountButton(props = {}) {
	return mount(RefreshButton, {
		props,
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
}

describe("RefreshButton", () => {
	beforeEach(() => vi.useFakeTimers())
	afterEach(() => vi.useRealTimers())

	it("spins while loading, then holds a short floor before stopping", async () => {
		const wrapper = mountButton({ loading: false })
		expect(icon(wrapper).classes()).not.toContain("animate-spin")

		await wrapper.setProps({ loading: true })
		expect(icon(wrapper).classes()).toContain("animate-spin")

		// A reload that finishes instantly must still show a visible spin.
		await wrapper.setProps({ loading: false })
		expect(icon(wrapper).classes()).toContain("animate-spin")
		vi.advanceTimersByTime(600)
		await wrapper.vm.$nextTick()
		expect(icon(wrapper).classes()).not.toContain("animate-spin")
	})

	it("disables the button only while loading is real", async () => {
		const wrapper = mountButton({ loading: false })
		expect(button(wrapper).attributes("disabled")).toBeUndefined()

		await wrapper.setProps({ loading: true })
		expect(button(wrapper).attributes("disabled")).toBeDefined()

		await wrapper.setProps({ loading: false })
		expect(button(wrapper).attributes("disabled")).toBeUndefined()
	})
})
