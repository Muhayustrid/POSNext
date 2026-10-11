/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Route call() and createResource submits by API url, same pattern as
// ProductionDialog.test.js.
const handlers = vi.hoisted(() => ({ map: {}, calls: {} }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	const Dialog = defineComponent({
		name: "DialogStub",
		props: ["modelValue", "options"],
		setup(_, { slots }) {
			return () => slots["body-content"]?.()
		},
	})
	const Button = defineComponent({
		name: "ButtonStub",
		props: ["loading", "disabled", "variant", "theme", "size"],
		setup(props, { slots }) {
			return () =>
				h("button", { disabled: !!props.disabled }, slots.default?.())
		},
	})
	const FeatherIcon = defineComponent({
		name: "FeatherIconStub",
		props: ["name"],
	})
	return {
		Dialog,
		Button,
		FeatherIcon,
		createResource: (opts) => ({
			submit: (params) => handlers.map[opts.url]?.(params, opts),
		}),
		call: (url, params) =>
			handlers.calls[url]?.(params) ??
			Promise.reject(new Error(`no call mock for ${url}`)),
	}
})

// Attachment helper is stubbed: jsdom has no usable FileReader + the real
// module drags in the whole apiWrapper chain.
vi.mock("@/utils/attachments", () => ({
	ATTACHMENT_ACCEPT: "image/*,.pdf",
	fileToBase64: () => Promise.resolve("Zm9vYmFy"),
	sanitizePickedFiles: (files) => ({
		ok: Array.from(files || []),
		tooBig: [],
	}),
}))

vi.hoisted(() => {
	globalThis.__ = (message, replacements = []) => {
		if (!Array.isArray(replacements) || !replacements.length) return message
		let out = message
		for (const [i, v] of replacements.entries())
			out = out.split(`{${i}}`).join(String(v))
		return out
	}
})

import RejectRequestDialog from "./RejectRequestDialog.vue"

const LIST_URL = "pos_next.api.reject_request.get_reject_requests"
const DETAIL_URL = "pos_next.api.reject_request.get_reject_request"
const SAVE_URL = "pos_next.api.reject_request.save_reject_request"
const ATTACH_URL = "pos_next.api.reject_request.attach_reject_files"
const CANCEL_URL = "pos_next.api.reject_request.cancel_reject_request"
const CLOSE_URL = "pos_next.api.reject_request.close_reject_request"
const ITEMS_URL = "pos_next.api.items.get_items"

const ROWS = {
	requests: [
		{
			name: "PRJ-2601-0001",
			purpose: "Unsold",
			status: "Pending",
			incident_date: "2026-01-05",
			deadline_date: "2026-01-08",
			total_amount: 5000,
			is_late: true,
			over_limit: false,
			decision: null,
			work_order: null,
			evidence_count: 2,
			destruction_count: 0,
		},
	],
	has_more: false,
}

const LIST_ROWS = (names) => ({
	requests: names.map((name) => ({
		name,
		purpose: "Unsold",
		status: "Pending",
		incident_date: "2026-01-05",
		deadline_date: "2026-01-08",
		total_amount: 5000,
		is_late: false,
		over_limit: false,
		decision: null,
		work_order: null,
		evidence_count: 1,
		destruction_count: 0,
	})),
	has_more: false,
})

const UNSOLD_DETAIL = {
	name: "PRJ-2601-0002",
	purpose: "Unsold",
	status: "Draft",
	docstatus: 0,
	pos_profile: "POS-1",
	company: "Co",
	incident_date: "2026-01-05",
	deadline_date: "2026-01-08",
	reason: "",
	video_link: null,
	work_order: null,
	is_system_generated: 0,
	is_late: false,
	total_amount: 0,
	items: [],
	evidence: [],
	destruction: [],
}

const PL_DRAFT_DETAIL = {
	...UNSOLD_DETAIL,
	name: "PRJ-2601-0003",
	purpose: "Production Loss",
	is_system_generated: 1,
	reason: "",
	items: [
		{
			item_code: "ICED-LATTE",
			item_name: "Iced Latte",
			qty: 3,
			uom: "Nos",
			amount: 9000,
		},
	],
	evidence: [],
}

const APPROVED_DETAIL = {
	...UNSOLD_DETAIL,
	name: "PRJ-2601-0004",
	status: "Approved",
	docstatus: 1,
	reason: "rusak",
	items: [
		{ item_code: "ICED-LATTE", item_name: "Iced Latte", qty: 2, uom: "Nos" },
	],
	evidence: [{ name: "File-1", file_name: "a.jpg" }],
	destruction: [],
}

const PENDING_DETAIL = {
	...UNSOLD_DETAIL,
	name: "PRJ-2601-0001",
	status: "Pending",
	docstatus: 1,
	reason: "rusak",
	items: [
		{ item_code: "ICED-LATTE", item_name: "Iced Latte", qty: 2, uom: "Nos" },
	],
	evidence: [{ name: "File-1", file_name: "a.jpg" }],
}

function mockCall(url, impl) {
	handlers.calls[url] = vi.fn(impl)
	return handlers.calls[url]
}

async function mountOpenDialog() {
	const wrapper = mount(RejectRequestDialog, {
		props: { modelValue: false, posProfile: "POS-1", company: "Co" },
		global: {
			config: { globalProperties: { __: globalThis.__ } },
		},
	})
	await wrapper.setProps({ modelValue: true })
	await flushPromises()
	return wrapper
}

function findButton(wrapper, text) {
	return wrapper.findAll("button").find((b) => b.text().includes(text))
}

/** Simulate picking files on a hidden <input type="file">. */
async function pickFiles(wrapper, selector, names) {
	const input = wrapper.find(selector)
	const files = names.map(
		(n) => new File(["hello"], n, { type: "image/png" }),
	)
	Object.defineProperty(input.element, "files", { value: files })
	await input.trigger("change")
	await flushPromises()
}

describe("RejectRequestDialog", () => {
	beforeEach(() => {
		handlers.map = {}
		handlers.calls = {}
		mockCall(LIST_URL, () => Promise.resolve(ROWS))
		mockCall(DETAIL_URL, () => Promise.resolve(UNSOLD_DETAIL))
		mockCall(ITEMS_URL, () =>
			Promise.resolve([
				{ item_code: "ICED-LATTE", item_name: "Iced Latte" },
			]),
		)
	})

	it("submit disabled until reason and 1 photo present", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		let submit = findButton(wrapper, "Submit")
		expect(submit.attributes("disabled")).toBeDefined()

		// reason alone is not enough
		await wrapper.find("textarea").setValue("expired stock")
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeDefined()

		// add an item and one photo -> enabled
		await wrapper.find('input[placeholder="Search item..."]').setValue("latte")
		await flushPromises()
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, 'input[data-testid="evidence-input"]', ["a.png"])
		expect(
			findButton(wrapper, "Submit").attributes("disabled"),
		).toBeUndefined()
	})

	it("submit sends save_reject_request with submit=1 after attach", async () => {
		const saveCall = mockCall(SAVE_URL, (_params) =>
			Promise.resolve({ name: "PRJ-2601-0002", status: "Pending", docstatus: 1 }),
		)
		const attachCall = mockCall(ATTACH_URL, (_params) =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find("textarea").setValue("expired stock")
		await wrapper.find('input[placeholder="Search item..."]').setValue("latte")
		await flushPromises()
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, 'input[data-testid="evidence-input"]', ["a.png"])

		await findButton(wrapper, "Submit").trigger("click")
		await flushPromises()

		expect(attachCall).toHaveBeenCalledTimes(1)
		expect(attachCall).toHaveBeenCalledWith(
			expect.objectContaining({ name: "PRJ-2601-0002", kind: "evidence" }),
		)
		// first save is a draft (submit=0), final save submits (submit=1)
		expect(saveCall.mock.calls[0][0]).toEqual(
			expect.objectContaining({ submit: 0 }),
		)
		expect(JSON.parse(saveCall.mock.calls[0][0].data)).toEqual(
			expect.objectContaining({ pos_profile: "POS-1", reason: "expired stock" }),
		)
		expect(saveCall.mock.calls[1][0]).toEqual(
			expect.objectContaining({ submit: 1 }),
		)
	})

	it("production loss draft shows qty read-only", async () => {
		mockCall(LIST_URL, () => Promise.resolve(LIST_ROWS(["PRJ-2601-0003"])))
		mockCall(DETAIL_URL, () => Promise.resolve(PL_DRAFT_DETAIL))
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0003").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Production Loss")
		expect(wrapper.text()).toContain("Iced Latte")
		// qty is plain text, never an editable number input
		expect(wrapper.findAll('input[type="number"]')).toHaveLength(0)
		expect(wrapper.text()).toContain("3")
		// reason is editable for the crew
		const reason = wrapper.find("textarea")
		expect(reason.exists()).toBe(true)
	})

	it("close button only for Approved and requires destruction photo", async () => {
		// Pending detail: Cancel, no Close
		mockCall(LIST_URL, () => Promise.resolve(LIST_ROWS(["PRJ-2601-0001"])))
		mockCall(DETAIL_URL, () => Promise.resolve(PENDING_DETAIL))
		let wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()
		expect(findButton(wrapper, "Close")).toBeUndefined()
		expect(findButton(wrapper, "Cancel")).toBeDefined()

		// Approved detail: Close disabled until a destruction photo exists
		mockCall(LIST_URL, () => Promise.resolve(LIST_ROWS(["PRJ-2601-0004"])))
		mockCall(DETAIL_URL, () => Promise.resolve(APPROVED_DETAIL))
		wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()
		const close = findButton(wrapper, "Close")
		expect(close).toBeDefined()
		expect(close.attributes("disabled")).toBeDefined()

		await pickFiles(wrapper, 'input[data-testid="destruction-input"]', ["d.png"])
		expect(
			findButton(wrapper, "Close").attributes("disabled"),
		).toBeUndefined()
	})
})
