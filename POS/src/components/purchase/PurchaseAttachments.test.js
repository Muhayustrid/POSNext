/**
 * @vitest-environment jsdom
 */
import { mount } from "@vue/test-utils"
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest"
import { nextTick } from "vue"

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	const FeatherIcon = defineComponent({
		name: "FeatherIcon",
		props: ["name"],
		setup(props) {
			return () => h("span", { class: `feather feather-${props.name}` })
		},
	})
	return { FeatherIcon }
})

vi.mock("@/utils/apiWrapper", () => ({ call: vi.fn() }))

import PurchaseAttachments from "./PurchaseAttachments.vue"

beforeAll(() => {
	globalThis.__ = (m) => m
	URL.createObjectURL = vi.fn(() => "blob:preview-1")
	URL.revokeObjectURL = vi.fn()
})

let wrapper
afterEach(() => {
	wrapper?.unmount()
	document.body.innerHTML = ""
})

function mountIt(props) {
	wrapper = mount(PurchaseAttachments, {
		props,
		attachTo: document.body,
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	return wrapper
}

const photo = { file_name: "nota.JPG", file_url: "/private/files/nota.JPG", file_size: 2048 }
const pdf = { file_name: "faktur.pdf", file_url: "/private/files/faktur.pdf", file_size: 4096 }

describe("PurchaseAttachments preview", () => {
	it("shows a thumbnail for an attached image and previews it in place", async () => {
		mountIt({ attached: [photo] })
		const thumb = wrapper.find('[data-test="attachment-thumb"]')
		expect(thumb.attributes("src")).toBe(photo.file_url)

		await wrapper.find('[data-test="attachment-open"]').trigger("click")
		const preview = document.querySelector('[data-test="attachment-preview"]')
		expect(preview).not.toBeNull()
		expect(preview.querySelector("img").getAttribute("src")).toBe(photo.file_url)

		document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))
		await nextTick()
		expect(document.querySelector('[data-test="attachment-preview"]')).toBeNull()
	})

	it("opens a non-image file in a new tab", async () => {
		mountIt({ attached: [pdf] })
		expect(wrapper.find('[data-test="attachment-thumb"]').exists()).toBe(false)
		const link = wrapper.find('a[data-test="attachment-open"]')
		expect(link.attributes("href")).toBe(pdf.file_url)
		expect(link.attributes("target")).toBe("_blank")
	})

	it("previews a picked image before it is uploaded", async () => {
		const file = new File(["x"], "bukti.png", { type: "image/png" })
		mountIt({ editable: true, pending: [file] })
		expect(wrapper.find('[data-test="attachment-thumb"]').attributes("src")).toBe("blob:preview-1")
		await wrapper.find('[data-test="attachment-open"]').trigger("click")
		expect(document.querySelector('[data-test="attachment-preview"] img').getAttribute("src")).toBe(
			"blob:preview-1",
		)
	})
})
