/**
 * @vitest-environment jsdom
 */
import { describe, expect, it, vi } from "vitest"
import { mount } from "@vue/test-utils"

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	const Dialog = defineComponent({
		name: "DialogStub",
		props: ["modelValue", "options"],
		emits: ["update:modelValue", "close"],
		setup(props, { slots }) {
			return () => [
				h("div", { "data-test": "dialog-title" }, props.options?.title),
				slots["body-content"]?.(),
				slots.actions?.(),
			]
		},
	})
	const Button = defineComponent({
		name: "ButtonStub",
		props: ["variant", "theme", "size", "loading", "disabled"],
		setup(props, { slots }) {
			return () =>
				h(
					"button",
					{
						"data-variant": props.variant,
						"data-theme": props.theme,
						disabled: !!props.disabled,
					},
					slots.default?.(),
				)
		},
	})
	return { Dialog, Button }
})

globalThis.__ = (message) => message

import ConfirmDialog from "./ConfirmDialog.vue"

function mountDialog(props = {}) {
	return mount(ConfirmDialog, {
		props: { modelValue: true, ...props },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
}

const buttonByText = (wrapper, text) =>
	wrapper.findAll("button").find((b) => b.text() === text)

describe("ConfirmDialog", () => {
	it("renders the title and message", () => {
		const wrapper = mountDialog({
			title: "Delete Draft?",
			message: "This cannot be undone",
		})
		expect(wrapper.get('[data-test="dialog-title"]').text()).toBe(
			"Delete Draft?",
		)
		expect(wrapper.text()).toContain("This cannot be undone")
	})

	it("emits confirm and closes on confirm click", async () => {
		const wrapper = mountDialog({ confirmLabel: "Delete" })
		await buttonByText(wrapper, "Delete").trigger("click")
		expect(wrapper.emitted("confirm")).toHaveLength(1)
		expect(wrapper.emitted("update:modelValue")).toEqual([[false]])
	})

	it("emits cancel on Cancel click", async () => {
		const wrapper = mountDialog()
		await buttonByText(wrapper, "Cancel").trigger("click")
		expect(wrapper.emitted("cancel")).toHaveLength(1)
	})

	it("emits cancel when the dialog is closed by overlay or Esc", async () => {
		const wrapper = mountDialog()
		const dialog = wrapper.findComponent({ name: "DialogStub" })
		dialog.vm.$emit("update:modelValue", false)
		dialog.vm.$emit("close")
		await wrapper.vm.$nextTick()
		expect(wrapper.emitted("cancel")).toHaveLength(1)
		expect(wrapper.emitted("update:modelValue")).toEqual([[false]])
	})

	it("marks the confirm button destructive with theme red", () => {
		const wrapper = mountDialog({ confirmLabel: "Delete", destructive: true })
		expect(buttonByText(wrapper, "Delete").attributes("data-theme")).toBe("red")
	})

	it("leaves the confirm button non-destructive by default", () => {
		const wrapper = mountDialog({ confirmLabel: "Save" })
		expect(
			buttonByText(wrapper, "Save").attributes("data-theme"),
		).toBeUndefined()
	})
})
