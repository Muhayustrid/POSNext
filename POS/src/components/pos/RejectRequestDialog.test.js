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

// capture toasts so cap/validation messages can be asserted
const toasts = vi.hoisted(() => ({ log: [] }))
vi.mock("@/composables/useToast", () => ({
	useToast: () => ({
		showSuccess: (m) => toasts.log.push(m),
		showError: (m) => toasts.log.push(m),
	}),
}))

vi.mock("@/utils/attachments", async (importOriginal) => {
	const actual = await importOriginal()
	return {
		...actual,
		fileToBase64: () => Promise.resolve("Zm9vYmFy"),
		sanitizePickedFiles: (files) => ({
			ok: Array.from(files || []),
			tooBig: [],
		}),
	}
})

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

const ROW = (name, extra = {}) => ({
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
	...extra,
})

const ROWS = { requests: [ROW("PRJ-2601-0001", { is_late: true })], has_more: false }

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
	decision: null,
	decision_notes: null,
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
}

const APPROVED_DETAIL = {
	...UNSOLD_DETAIL,
	name: "PRJ-2601-0004",
	status: "Approved",
	docstatus: 1,
	reason: "rusak",
	video_link: "https://youtu.be/x",
	decision: null,
	decision_notes: null,
	items: [
		{ item_code: "ICED-LATTE", item_name: "Iced Latte", qty: 2, uom: "Nos" },
	],
	evidence: [{ name: "File-1", file_name: "a.jpg", file_url: "/f/1" }],
	destruction: [],
}

const APPROVED_DECIDED = {
	...APPROVED_DETAIL,
	decision: "Write Off",
	decision_notes: "stock habis",
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
	evidence: [{ name: "File-1", file_name: "a.jpg", file_url: "/f/1" }],
}

function mockCall(url, impl) {
	handlers.calls[url] = vi.fn(impl)
	return handlers.calls[url]
}

function file(name) {
	return new File(["hello"], name, { type: "image/png" })
}

/** Mount closed, then open via props so the modelValue watch fires loadList. */
async function mountOpenDialog() {
	const wrapper = mount(RejectRequestDialog, {
		props: { modelValue: false, posProfile: "POS-1" },
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

/** Type a search term and let the 350 ms trailing debounce fire. */
async function searchItem(wrapper, term) {
	await wrapper.find('input[placeholder="Search item..."]').setValue(term)
	await new Promise((r) => setTimeout(r, 400))
	await flushPromises()
}

async function pickFiles(wrapper, selector, names) {
	const input = wrapper.find(selector)
	Object.defineProperty(input.element, "files", {
		value: names.map(file),
	})
	await input.trigger("change")
	await flushPromises()
}

describe("RejectRequestDialog", () => {
	beforeEach(() => {
		handlers.map = {}
		handlers.calls = {}
		toasts.log = []
		window.confirm = vi.fn(() => true)
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
		await searchItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])
		expect(
			findButton(wrapper, "Submit").attributes("disabled"),
		).toBeUndefined()
	})

	it("submit sends save_reject_request with submit=1 after attach; second save carries the draft name", async () => {
		const saveCall = mockCall(SAVE_URL, (params) =>
			Promise.resolve({
				name: JSON.parse(params.data).name || "PRJ-2601-0002",
				status: "Pending",
				docstatus: 1,
			}),
		)
		const attachCall = mockCall(ATTACH_URL, (_params) =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find("textarea").setValue("expired stock")
		await searchItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])

		await findButton(wrapper, "Submit").trigger("click")
		await flushPromises()

		expect(attachCall).toHaveBeenCalledTimes(1)
		expect(attachCall).toHaveBeenCalledWith(
			expect.objectContaining({ name: "PRJ-2601-0002", kind: "evidence" }),
		)
		// first save is a draft (submit=0, no name), final save submits (submit=1)
		expect(saveCall.mock.calls[0][0]).toEqual(
			expect.objectContaining({ submit: 0 }),
		)
		expect(JSON.parse(saveCall.mock.calls[0][0].data).name).toBeUndefined()
		expect(saveCall.mock.calls[1][0]).toEqual(
			expect.objectContaining({ submit: 1 }),
		)
		expect(JSON.parse(saveCall.mock.calls[1][0].data).name).toBe(
			"PRJ-2601-0002",
		)
	})

	it("a failed submit keeps the button enabled and retry reuses the draft", async () => {
		let submitFails = true
		const saveCall = mockCall(SAVE_URL, (params) => {
			if (params.submit && submitFails) {
				submitFails = false
				return Promise.reject(new Error("boom on submit"))
			}
			return Promise.resolve({
				name: JSON.parse(params.data).name || "PRJ-DRAFT-1",
				status: "Pending",
				docstatus: 1,
			})
		})
		const attachCall = mockCall(ATTACH_URL, (_params) =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()
		await wrapper.find("textarea").setValue("expired stock")
		await searchItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])

		await findButton(wrapper, "Submit").trigger("click")
		await flushPromises()

		// the failed final save must NOT disable the button
		const submitBtn = findButton(wrapper, "Submit")
		expect(submitBtn.attributes("disabled")).toBeUndefined()

		// retry: same draft, no re-attach
		await submitBtn.trigger("click")
		await flushPromises()

		const inserts = saveCall.mock.calls.filter(
			([p]) => !JSON.parse(p.data).name,
		)
		expect(inserts).toHaveLength(1) // exactly one insert overall
		expect(attachCall).toHaveBeenCalledTimes(1) // no re-upload on retry
		const submitSaves = saveCall.mock.calls.filter(([p]) => p.submit)
		expect(submitSaves).toHaveLength(2) // both attempts submitted
		expect(JSON.parse(submitSaves[1][0].data).name).toBe("PRJ-DRAFT-1")
		expect(submitSaves[1][0].submit).toBe(1)
	})

	it("a failed close keeps the button enabled and retry does not re-attach", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(APPROVED_DETAIL))
		let closeFails = true
		const closeCall = mockCall(CLOSE_URL, () => {
			if (closeFails) {
				closeFails = false
				return Promise.reject(new Error("boom on close"))
			}
			return Promise.resolve({ status: "Closed" })
		})
		const attachCall = mockCall(ATTACH_URL, () =>
			Promise.resolve({ evidence_count: 1, destruction_count: 1 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["d.png"])

		const closeBtn = findButton(wrapper, "Close")
		await closeBtn.trigger("click")
		await flushPromises()

		// failed close must NOT disable the button
		expect(findButton(wrapper, "Close").attributes("disabled")).toBeUndefined()

		// retry: close again, no re-attach
		await findButton(wrapper, "Close").trigger("click")
		await flushPromises()

		expect(closeCall).toHaveBeenCalledTimes(2)
		expect(attachCall).toHaveBeenCalledTimes(1) // no re-upload on retry
	})

	it("production loss draft shows qty read-only", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0003")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(PL_DRAFT_DETAIL))
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0003").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Production Loss")
		expect(wrapper.text()).toContain("Iced Latte")
		// qty is plain text, never an editable number input
		expect(wrapper.findAll('input[type="number"]')).toHaveLength(0)
		// the qty cell itself carries the locked value
		const qtyCells = wrapper.findAll('[data-testid="item-qty"]')
		expect(qtyCells).toHaveLength(1)
		expect(qtyCells[0].text().trim()).toBe("3")
		// reason is editable for the crew
		const reason = wrapper.find("textarea")
		expect(reason.exists()).toBe(true)
	})

	it("close button only for Approved and requires destruction photo", async () => {
		// Pending detail: Cancel, no Close
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0001")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(PENDING_DETAIL))
		let wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()
		expect(findButton(wrapper, "Close")).toBeUndefined()
		expect(findButton(wrapper, "Cancel")).toBeDefined()

		// Approved detail: Close disabled until a destruction photo exists
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(APPROVED_DETAIL))
		wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()
		let close = findButton(wrapper, "Close")
		expect(close).toBeDefined()
		expect(close.attributes("disabled")).toBeDefined()

		await pickFiles(wrapper, '[data-test="attach-input"]', ["d.png"])
		expect(
			findButton(wrapper, "Close").attributes("disabled"),
		).toBeUndefined()
	})

	it("close sequence attaches destruction photos then closes", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(APPROVED_DETAIL))
		const order = []
		const attachCall = mockCall(ATTACH_URL, (params) => {
			order.push("attach")
			return Promise.resolve({
				evidence_count: 1,
				destruction_count: JSON.parse(params.files).length,
			})
		})
		const closeCall = mockCall(CLOSE_URL, () => {
			order.push("close")
			return Promise.resolve({ status: "Closed" })
		})

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["d.png"])

		await findButton(wrapper, "Close").trigger("click")
		await flushPromises()

		expect(window.confirm).toHaveBeenCalled()
		expect(order).toEqual(["attach", "close"])
		expect(attachCall).toHaveBeenCalledWith(
			expect.objectContaining({ name: "PRJ-2601-0004", kind: "destruction" }),
		)
		expect(closeCall).toHaveBeenCalledWith({ name: "PRJ-2601-0004" })
	})

	it("cancel is confirmed and only offered on Pending", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0001")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(PENDING_DETAIL))
		const cancelCall = mockCall(CANCEL_URL, () =>
			Promise.resolve({ status: "Cancelled" }),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()

		await findButton(wrapper, "Cancel").trigger("click")
		await flushPromises()
		expect(window.confirm).toHaveBeenCalled()
		expect(cancelCall).toHaveBeenCalledWith({ name: "PRJ-2601-0001" })
	})

	it("draft detail has no Cancel button (backend cancels only Pending)", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0002")], has_more: false }),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0002").trigger("click")
		await flushPromises()
		expect(findButton(wrapper, "Submit")).toBeDefined()
		expect(findButton(wrapper, "Cancel")).toBeUndefined()
	})

	it("invalid video link keeps submit disabled", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()
		await wrapper.find("textarea").setValue("expired stock")
		await searchItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])
		expect(
			findButton(wrapper, "Submit").attributes("disabled"),
		).toBeUndefined()

		await wrapper.find('input[type="text"]:not([placeholder])').setValue("ftp://nope")
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeDefined()
		expect(wrapper.text()).toContain(
			"Video link must start with http:// or https://",
		)
	})

	it("keeps only the first 5 evidence photos when more are picked", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await pickFiles(wrapper, '[data-test="attach-input"]', [
			"1.png", "2.png", "3.png", "4.png", "5.png", "6.png", "7.png",
		])
		expect(wrapper.findAll('[data-test="attachment-list"] li')).toHaveLength(5)
		// the cap truncation must be surfaced
		expect(toasts.log).toContain("At most 5 evidence photos are allowed")
	})

	it("searches items with a trailing debounce and ignores stale responses", async () => {
		let resolveA
		mockCall(ITEMS_URL, () => new Promise((r) => resolveA = r))
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find('input[placeholder="Search item..."]').setValue("lat")
		await wrapper.find('input[placeholder="Search item..."]').setValue("latt")
		await wrapper.find('input[placeholder="Search item..."]').setValue("latte")

		// no request fired per keystroke
		let calls = handlers.calls[ITEMS_URL].mock.calls.length
		expect(calls).toBe(0)

		await searchItem(wrapper, "latte2")
		calls = handlers.calls[ITEMS_URL].mock.calls.length
		expect(calls).toBe(1) // single trailing request

		// resolve the FIRST (now stale) request
		resolveA([{ item_code: "STALE", item_name: "Stale" }])
		await flushPromises()

		// a NEW request starts (never resolved yet)
		let resolveB
		mockCall(ITEMS_URL, () => new Promise((r) => resolveB = r))
		await searchItem(wrapper, "milk")

		// the OLD request (latte2) resolves LAST — it must not repopulate
		resolveA([{ item_code: "STALE-2", item_name: "Stale 2" }])
		await flushPromises()
		expect(findButton(wrapper, "Stale 2")).toBeUndefined()

		// the new request resolves and DOES show its results
		resolveB([{ item_code: "MILK", item_name: "Milk" }])
		await flushPromises()
		await findButton(wrapper, "Milk").trigger("click")
		await flushPromises()

		// after addItem the results are cleared and no late response repopulates
		resolveA([{ item_code: "STALE-3", item_name: "Stale 3" }])
		resolveB([{ item_code: "MILK-2", item_name: "Milk 2" }])
		await flushPromises()
		expect(wrapper.find('input[placeholder="Search item..."]').element.value).toBe("")
		expect(findButton(wrapper, "Stale 3")).toBeUndefined()
		expect(findButton(wrapper, "Milk 2")).toBeUndefined()
	})

	it("status filter change reloads with the new filter even while loading", async () => {
		let resolveList
		mockCall(LIST_URL, () => new Promise((r) => resolveList = r))
		const wrapper = await mountOpenDialog()
		expect(wrapper.text()).toContain("Loading")

		// filter click while the first load is in flight
		await findButton(wrapper, "Pending").trigger("click")
		resolveList({ requests: [], has_more: false })
		await flushPromises()

		const calls = handlers.calls[LIST_URL].mock.calls
		expect(calls).toHaveLength(2)
		expect(calls[1][0].status).toBe("Pending")
	})

	it("shows decision and notes on a decided request", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(APPROVED_DECIDED))
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Write Off")
		expect(wrapper.text()).toContain("stock habis")
		expect(wrapper.text()).toContain("rusak")
		expect(wrapper.text()).toContain("https://youtu.be/x")
	})
})
