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
			return () => [slots["body-content"]?.(), slots.actions?.()]
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
		setup() {
			return () => h("span", { class: "feather-icon" })
		},
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
const APPLY_ACTION_URL = "pos_next.api.reject_request.apply_reject_action"
const SAVE_VIDEO_URL = "pos_next.api.reject_request.save_reject_video"
const PURPOSES_URL = "pos_next.api.reject_request.get_reject_purposes"
const ITEMS_URL = "pos_next.api.items.get_items"

const ROW = (name, extra = {}) => ({
	name,
	purpose: "Unsold",
	workflow_state: "Pending Approval",
	incident_date: "2026-01-05",
	deadline_date: "2026-01-08",
	total_amount: 5000,
	is_late: false,
	over_limit: false,
	approval_code: null,
	borne_by: null,
	work_order: null,
	evidence_count: 1,
	destruction_count: 0,
	...extra,
})

const ROWS = {
	requests: [ROW("PRJ-2601-0001", { is_late: true })],
	has_more: false,
}

const UNSOLD_DETAIL = {
	name: "PRJ-2601-0002",
	purpose: "Unsold",
	workflow_state: "Draft",
	docstatus: 0,
	pos_profile: "POS-1",
	company: "Co",
	incident_date: "2026-01-05",
	deadline_date: "2026-01-08",
	reason: "",
	return_reason: null,
	approval_code: null,
	borne_by: null,
	letter_head: "Standard Letter Head",
	video_link: null,
	work_order: null,
	is_system_generated: 0,
	is_late: false,
	total_amount: 0,
	actions: ["Submit"],
	items: [],
	evidence: [],
	destruction: [],
	liabilities: [],
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

const PENDING_DETAIL = {
	...UNSOLD_DETAIL,
	name: "PRJ-2601-0001",
	workflow_state: "Pending Approval",
	docstatus: 0,
	reason: "rusak",
	actions: ["Withdraw"],
	items: [
		{
			item_code: "ICED-LATTE",
			item_name: "Iced Latte",
			qty: 2,
			uom: "Nos",
			production_date: "2026-01-04",
			reject_reason: "basi",
		},
	],
	evidence: [{ name: "File-1", file_name: "a.jpg", file_url: "/f/1" }],
}

const AWAITING_DETAIL = {
	...UNSOLD_DETAIL,
	name: "PRJ-2601-0004",
	workflow_state: "Awaiting Destruction",
	docstatus: 1,
	reason: "rusak",
	approval_code: "ABC12345",
	borne_by: "Company",
	decided_by: "approver@example.com",
	decided_on: "2026-01-06",
	actions: ["Send Proof"],
	items: [
		{ item_code: "ICED-LATTE", item_name: "Iced Latte", qty: 2, uom: "Nos" },
	],
	evidence: [{ name: "File-1", file_name: "a.jpg", file_url: "/f/1" }],
	destruction: [],
}

const RETURNED_DRAFT_DETAIL = {
	...UNSOLD_DETAIL,
	name: "PRJ-2601-0007",
	workflow_state: "Draft",
	reason: "rusak",
	return_reason: "Foto bukti kurang jelas",
	items: [
		{ item_code: "ICED-LATTE", item_name: "Iced Latte", qty: 2, uom: "Nos" },
	],
	evidence: [{ name: "File-1", file_name: "a.jpg", file_url: "/f/1" }],
}

const COMPLETED_CREW_DETAIL = {
	...AWAITING_DETAIL,
	name: "PRJ-2601-0005",
	workflow_state: "Completed",
	actions: [],
	borne_by: "Crew",
	video_link: "https://youtu.be/proof",
	destruction: [{ name: "File-2", file_name: "dest.jpg", file_url: "/f/2" }],
	liabilities: [
		{ user: "kru1@example.com", amount: 3000 },
		{ user: "kru2@example.com", amount: 2000 },
	],
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

/** Type a search term in the row input and let the 350 ms trailing debounce fire. */
async function searchRowItem(wrapper, term) {
	const input = wrapper.find('[data-testid="row-item-input"]')
	await input.setValue(term)
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
		mockCall(PURPOSES_URL, () => Promise.resolve(["Unsold", "Damaged"]))
		mockCall(ITEMS_URL, () =>
			Promise.resolve([
				{ item_code: "ICED-LATTE", item_name: "Iced Latte", stock_uom: "Nos" },
			]),
		)
		mockCall(APPLY_ACTION_URL, () => Promise.resolve(UNSOLD_DETAIL))
		mockCall(SAVE_VIDEO_URL, () => Promise.resolve({ name: "PRJ-2601-0004" }))
	})

	it("submit disabled until reason and 1 photo present", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		const submit = findButton(wrapper, "Submit")
		expect(submit.attributes("disabled")).toBeDefined()

		// reason alone is not enough
		await wrapper.find("textarea#rr-reason").setValue("expired stock")
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeDefined()

		// add row and pick item
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		// still disabled without photo
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeDefined()

		// add photo -> enabled
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeUndefined()
	})

	it("add row appends a blank row and picking an item fills it", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		// initially no rows or blank row
		const addBtn = wrapper.find('[data-testid="add-row"]')
		expect(addBtn.exists()).toBe(true)

		await addBtn.trigger("click")
		await flushPromises()

		const rowInput = wrapper.find('[data-testid="row-item-input"]')
		expect(rowInput.exists()).toBe(true)

		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Iced Latte")
	})

	it("empty trailing rows are not sent on save", async () => {
		const saveCall = mockCall(SAVE_URL, (params) =>
			Promise.resolve({
				name: JSON.parse(params.data).name || "PRJ-2601-0002",
				workflow_state: "Pending Approval",
				docstatus: 0,
			}),
		)
		mockCall(ATTACH_URL, () =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find("textarea#rr-reason").setValue("expired stock")

		// Add first row and pick item
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		// Add second row and leave it empty
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()

		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])
		await findButton(wrapper, "Submit").trigger("click")
		await flushPromises()

		expect(saveCall).toHaveBeenCalled()
		const firstSavePayload = JSON.parse(saveCall.mock.calls[0][0].data)
		expect(firstSavePayload.items).toHaveLength(1)
		expect(firstSavePayload.items[0].item_code).toBe("ICED-LATTE")
	})

	it("submit sends production_date and reject_reason per row", async () => {
		const saveCall = mockCall(SAVE_URL, (params) =>
			Promise.resolve({
				name: JSON.parse(params.data).name || "PRJ-2601-0002",
				workflow_state: "Pending Approval",
				docstatus: 0,
			}),
		)
		mockCall(ATTACH_URL, () =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find("textarea#rr-reason").setValue("expired stock")
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		// fill production date and reason in the row
		await wrapper.find('[data-testid="prod-date-input"]').setValue("2026-01-04")
		await wrapper
			.find('[data-testid="item-reason-input"]')
			.setValue("Stale taste")
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])

		await findButton(wrapper, "Submit").trigger("click")
		await flushPromises()

		expect(saveCall).toHaveBeenCalled()
		const payload = JSON.parse(saveCall.mock.calls[0][0].data)
		expect(payload.items[0]).toEqual(
			expect.objectContaining({
				item_code: "ICED-LATTE",
				production_date: "2026-01-04",
				reject_reason: "Stale taste",
			}),
		)
	})

	it("submit sends save_reject_request with submit=1 after attach; second save carries the draft name", async () => {
		const saveCall = mockCall(SAVE_URL, (params) =>
			Promise.resolve({
				name: JSON.parse(params.data).name || "PRJ-2601-0002",
				workflow_state: "Pending Approval",
				docstatus: 0,
			}),
		)
		const attachCall = mockCall(ATTACH_URL, (_params) =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find("textarea#rr-reason").setValue("expired stock")
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
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
				workflow_state: "Pending Approval",
				docstatus: 0,
			})
		})
		const attachCall = mockCall(ATTACH_URL, (_params) =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()
		await wrapper.find("textarea#rr-reason").setValue("expired stock")
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
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
		expect(inserts).toHaveLength(1)
		expect(attachCall).toHaveBeenCalledTimes(1)
		const submitSaves = saveCall.mock.calls.filter(([p]) => p.submit)
		expect(submitSaves).toHaveLength(2)
		expect(JSON.parse(submitSaves[1][0].data).name).toBe("PRJ-DRAFT-1")
		expect(submitSaves[1][0].submit).toBe(1)
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
		expect(wrapper.findAll('input[type="number"]')).toHaveLength(0)
		const qtyCells = wrapper.findAll('[data-testid="item-qty"]')
		expect(qtyCells).toHaveLength(1)
		expect(qtyCells[0].text().trim()).toBe("3")
		const reason = wrapper.find("textarea#rr-reason, textarea")
		expect(reason.exists()).toBe(true)
	})

	it("renders return reason banner on returned draft", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0007")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(RETURNED_DRAFT_DETAIL))

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0007").trigger("click")
		await flushPromises()

		const banner = wrapper.find('[data-testid="return-reason-banner"]')
		expect(banner.exists()).toBe(true)
		expect(banner.text()).toContain("Returned by Head Office")
		expect(banner.text()).toContain("Foto bukti kurang jelas")
	})

	it("Withdraw calls apply_reject_action", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0001")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(PENDING_DETAIL))
		const actionCall = mockCall(APPLY_ACTION_URL, () =>
			Promise.resolve({ ...PENDING_DETAIL, workflow_state: "Draft" }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()

		const withdrawBtn = wrapper.find('[data-testid="withdraw-button"]')
		expect(withdrawBtn.exists()).toBe(true)
		await withdrawBtn.trigger("click")
		await flushPromises()

		expect(window.confirm).toHaveBeenCalled()
		expect(actionCall).toHaveBeenCalledWith({
			name: "PRJ-2601-0001",
			action: "Withdraw",
		})
	})

	it("shows Send Proof only in Awaiting Destruction and disables it without proof", async () => {
		// Pending detail: no Send Proof button
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0001")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(PENDING_DETAIL))
		let wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="send-proof"]').exists()).toBe(false)

		// Awaiting Destruction detail: Send Proof exists and disabled without proof
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(AWAITING_DETAIL))
		wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()

		const sendProofBtn = wrapper.find('[data-testid="send-proof"]')
		expect(sendProofBtn.exists()).toBe(true)
		expect(sendProofBtn.attributes("disabled")).toBeDefined()

		// Add photo -> enabled
		await pickFiles(wrapper, '[data-test="attach-input"]', ["proof.png"])
		expect(
			wrapper.find('[data-testid="send-proof"]').attributes("disabled"),
		).toBeUndefined()
	})

	it("Berita Acara link targets download_pdf with format and letterhead", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(AWAITING_DETAIL))

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()

		const link = wrapper.find('[data-testid="download-berita-acara"]')
		expect(link.exists()).toBe(true)
		const href = link.attributes("href")
		expect(href).toContain("download_pdf")
		expect(href).toContain("doctype=POS%20Reject%20Request")
		expect(href).toContain("name=PRJ-2601-0004")
		expect(href).toContain("format=Berita%20Acara%20Reject")
		expect(href).toContain("letterhead=Standard%20Letter%20Head")
		expect(link.attributes("target")).toBe("_blank")
	})

	it("Send Proof sequence attaches destruction photos, saves video, and applies workflow action", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(AWAITING_DETAIL))
		const attachCall = mockCall(ATTACH_URL, () =>
			Promise.resolve({ evidence_count: 1, destruction_count: 1 }),
		)
		const videoCall = mockCall(SAVE_VIDEO_URL, () =>
			Promise.resolve({
				name: "PRJ-2601-0004",
				video_link: "https://youtu.be/proof",
			}),
		)
		const actionCall = mockCall(APPLY_ACTION_URL, () =>
			Promise.resolve({
				...AWAITING_DETAIL,
				workflow_state: "Destruction Submitted",
			}),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()

		// enter video link and attach destruction photo
		await wrapper
			.find('[data-testid="video-link"]')
			.setValue("https://youtu.be/proof")
		await pickFiles(wrapper, '[data-test="attach-input"]', ["destruct.png"])

		await wrapper.find('[data-testid="send-proof"]').trigger("click")
		await flushPromises()

		expect(attachCall).toHaveBeenCalledWith(
			expect.objectContaining({ name: "PRJ-2601-0004", kind: "destruction" }),
		)
		expect(videoCall).toHaveBeenCalledWith({
			name: "PRJ-2601-0004",
			video_link: "https://youtu.be/proof",
		})
		expect(actionCall).toHaveBeenCalledWith({
			name: "PRJ-2601-0004",
			action: "Send Proof",
		})
	})

	it("step indicator highlights the current workflow state", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(AWAITING_DETAIL))

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()

		const stepIndicator = wrapper.find('[data-testid="step-indicator"]')
		expect(stepIndicator.exists()).toBe(true)
		const activeStep = stepIndicator.find('[data-step="Awaiting Destruction"]')
		expect(activeStep.exists()).toBe(true)
		expect(activeStep.attributes("data-current")).toBe("true")
	})

	it("draft form has no video link field (proof comes after approval)", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="video-link"]').exists()).toBe(false)
	})

	it("keeps only the first 5 evidence photos when more are picked", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await pickFiles(wrapper, '[data-test="attach-input"]', [
			"1.png",
			"2.png",
			"3.png",
			"4.png",
			"5.png",
			"6.png",
			"7.png",
		])
		expect(wrapper.findAll('[data-test="attachment-list"] li')).toHaveLength(5)
		expect(toasts.log).toContain("At most 5 evidence photos are allowed")
	})

	it("searches items with a trailing debounce and ignores stale responses", async () => {
		let resolveA
		mockCall(
			ITEMS_URL,
			() =>
				new Promise((r) => {
					resolveA = r
				}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()

		const input = wrapper.find('[data-testid="row-item-input"]')
		await input.setValue("lat")
		await input.setValue("latt")
		await input.setValue("latte")

		let calls = handlers.calls[ITEMS_URL].mock.calls.length
		expect(calls).toBe(0)

		await searchRowItem(wrapper, "latte2")
		calls = handlers.calls[ITEMS_URL].mock.calls.length
		expect(calls).toBe(1)

		resolveA([{ item_code: "STALE", item_name: "Stale" }])
		await flushPromises()

		let resolveB
		mockCall(
			ITEMS_URL,
			() =>
				new Promise((r) => {
					resolveB = r
				}),
		)
		await searchRowItem(wrapper, "milk")

		resolveA([{ item_code: "STALE-2", item_name: "Stale 2" }])
		await flushPromises()
		expect(findButton(wrapper, "Stale 2")).toBeUndefined()

		resolveB([{ item_code: "MILK", item_name: "Milk" }])
		await flushPromises()
		await findButton(wrapper, "Milk").trigger("click")
		await flushPromises()

		resolveA([{ item_code: "STALE-3", item_name: "Stale 3" }])
		resolveB([{ item_code: "MILK-2", item_name: "Milk 2" }])
		await flushPromises()
		expect(findButton(wrapper, "Stale 3")).toBeUndefined()
		expect(findButton(wrapper, "Milk 2")).toBeUndefined()
	})

	it("status filter change reloads with the new filter even while loading", async () => {
		let resolveList
		mockCall(
			LIST_URL,
			() =>
				new Promise((r) => {
					resolveList = r
				}),
		)
		const wrapper = await mountOpenDialog()
		expect(wrapper.text()).toContain("Loading")

		await findButton(wrapper, "Pending Approval").trigger("click")
		resolveList({ requests: [], has_more: false })
		await flushPromises()

		const calls = handlers.calls[LIST_URL].mock.calls
		expect(calls).toHaveLength(2)
		expect(calls[1][0].status).toBe("Pending Approval")
	})

	it("purpose select lists API purposes and defaults to the first", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		const select = wrapper.find('select[data-testid="purpose-select"]')
		expect(select.exists()).toBe(true)
		const options = select.findAll("option")
		expect(options.map((o) => o.element.value)).toEqual(["Unsold", "Damaged"])
		expect(select.element.value).toBe("Unsold")

		await select.setValue("Damaged")
		await wrapper.find("textarea#rr-reason").setValue("expired stock")
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])

		mockCall(SAVE_URL, (params) =>
			Promise.resolve({
				name: JSON.parse(params.data).name || "PRJ-2601-0002",
				workflow_state: "Pending Approval",
				docstatus: 0,
			}),
		)
		mockCall(ATTACH_URL, () =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)
		await findButton(wrapper, "Submit").trigger("click")
		await flushPromises()

		const firstSave = JSON.parse(handlers.calls[SAVE_URL].mock.calls[0][0].data)
		expect(firstSave.purpose).toBe("Damaged")
	})

	it("submit stays disabled when no purpose is available", async () => {
		mockCall(PURPOSES_URL, () => Promise.resolve([]))
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()
		await wrapper.find("textarea#rr-reason").setValue("expired stock")
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeDefined()
	})

	it("Completed crew-borne detail shows liabilities, company-borne shows none", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({
				requests: [ROW("PRJ-2601-0005")],
				has_more: false,
			}),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(COMPLETED_CREW_DETAIL))
		let wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0005").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("kru1@example.com")
		expect(wrapper.text()).toContain("kru2@example.com")
		expect(wrapper.text()).toContain("3.000")
		expect(wrapper.text()).toContain("2.000")
		expect(wrapper.text()).toContain("Borne by Crew")

		// Company-borne
		mockCall(LIST_URL, () =>
			Promise.resolve({
				requests: [ROW("PRJ-2601-0004")],
				has_more: false,
			}),
		)
		mockCall(DETAIL_URL, () =>
			Promise.resolve({
				...COMPLETED_CREW_DETAIL,
				borne_by: "Company",
				liabilities: [],
			}),
		)
		wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Borne by Company")
		expect(wrapper.text()).not.toContain("kru1@example.com")
	})

	it("draft detail has no video link field", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0002")], has_more: false }),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0002").trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="video-link"]').exists()).toBe(false)
	})

	it("duplicate item with same production date shows error and blocks submit, different date is allowed", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find("textarea#rr-reason").setValue("expired stock")
		await pickFiles(wrapper, '[data-test="attach-input"]', ["a.png"])

		// Row 1: ICED-LATTE
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		// Row 2: ICED-LATTE with same (empty) production date
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		const inputs = wrapper.findAll('[data-testid="row-item-input"]')
		await inputs[inputs.length - 1].setValue("latte")
		await new Promise((r) => setTimeout(r, 400))
		await flushPromises()
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		// Should show duplicate error and disable submit
		expect(wrapper.text()).toContain("Duplicate item with same production date")
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeDefined()

		// Change production date on row 2 to something different
		const prodDateInputs = wrapper.findAll('[data-testid="prod-date-input"]')
		await prodDateInputs[0].setValue("2026-01-03")
		await prodDateInputs[1].setValue("2026-01-04")
		await flushPromises()

		// Error should disappear and submit should be enabled
		expect(wrapper.text()).not.toContain(
			"Duplicate item with same production date",
		)
		expect(findButton(wrapper, "Submit").attributes("disabled")).toBeUndefined()
	})

	it("initialRequest prop opens that request's detail", async () => {
		mockCall(LIST_URL, () => Promise.resolve({ requests: [], has_more: false }))
		mockCall(DETAIL_URL, () => Promise.resolve(PENDING_DETAIL))
		const wrapper = mount(RejectRequestDialog, {
			props: {
				modelValue: true,
				posProfile: "POS-1",
				initialRequest: "PRJ-2601-0001",
			},
			global: {
				config: { globalProperties: { __: globalThis.__ } },
			},
		})
		await flushPromises()

		expect(handlers.calls[DETAIL_URL]).toHaveBeenCalledWith({
			name: "PRJ-2601-0001",
		})
		expect(wrapper.text()).toContain("PRJ-2601-0001")
	})

	it("send proof button requires 'Send Proof' in detail.actions", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0004")], has_more: false }),
		)
		// Awaiting Destruction but without "Send Proof" in actions
		mockCall(DETAIL_URL, () =>
			Promise.resolve({ ...AWAITING_DETAIL, actions: [] }),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0004").trigger("click")
		await flushPromises()

		expect(wrapper.find('[data-testid="send-proof"]').exists()).toBe(false)
	})

	it("read-only video link anchor is not rendered when video_link is invalid javascript URL", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0001")], has_more: false }),
		)
		mockCall(DETAIL_URL, () =>
			Promise.resolve({
				...PENDING_DETAIL,
				video_link: "javascript:alert('xss')",
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()

		const anchor = wrapper.find('a[href^="javascript:"]')
		expect(anchor.exists()).toBe(false)
	})

	it("detail items loop renders multiple rows of same item_code with different production dates", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0001")], has_more: false }),
		)
		mockCall(DETAIL_URL, () =>
			Promise.resolve({
				...PENDING_DETAIL,
				items: [
					{
						name: "row-1",
						item_code: "ICED-LATTE",
						item_name: "Iced Latte",
						qty: 2,
						production_date: "2026-01-01",
						amount: 2000,
					},
					{
						name: "row-2",
						item_code: "ICED-LATTE",
						item_name: "Iced Latte",
						qty: 3,
						production_date: "2026-01-02",
						amount: 3000,
					},
				],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()

		const qtySpans = wrapper.findAll('[data-testid="item-qty"]')
		expect(qtySpans).toHaveLength(2)
		expect(qtySpans[0].text()).toBe("2")
		expect(qtySpans[1].text()).toBe("3")
	})

	it("pressing enter on reason input of the last row appends a new blank row", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		expect(wrapper.findAll('[data-testid="remove-row"]')).toHaveLength(1)

		const reasonInput = wrapper.find('[data-testid="item-reason-input"]')
		await reasonInput.setValue("Basi")
		await reasonInput.trigger("keydown.enter")
		await flushPromises()

		// A second row was added
		expect(wrapper.findAll('[data-testid="remove-row"]')).toHaveLength(2)
	})

	it("row checkbox allows selecting and deleting rows with Delete button", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		// Add two rows
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()

		expect(wrapper.findAll('[data-testid="row-checkbox"]')).toHaveLength(2)
		expect(wrapper.find('[data-testid="delete-selected-rows"]').exists()).toBe(
			false,
		)

		// Select first row
		const checkboxes = wrapper.findAll('[data-testid="row-checkbox"]')
		await checkboxes[0].setValue(true)
		await flushPromises()

		const deleteBtn = wrapper.find('[data-testid="delete-selected-rows"]')
		expect(deleteBtn.exists()).toBe(true)
		expect(deleteBtn.text()).toContain("Delete")
		expect(deleteBtn.text()).toContain("1")

		// Click delete
		await deleteBtn.trigger("click")
		await flushPromises()

		expect(wrapper.findAll('[data-testid="row-checkbox"]')).toHaveLength(1)
		expect(wrapper.find('[data-testid="delete-selected-rows"]').exists()).toBe(
			false,
		)
	})

	it("select-all checkbox selects all rows and bulk deletes them", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()

		const selectAll = wrapper.find('[data-testid="select-all-rows"]')
		expect(selectAll.exists()).toBe(true)
		await selectAll.setValue(true)
		await flushPromises()

		const deleteBtn = wrapper.find('[data-testid="delete-selected-rows"]')
		expect(deleteBtn.exists()).toBe(true)
		expect(deleteBtn.text()).toContain("2")

		await deleteBtn.trigger("click")
		await flushPromises()

		expect(wrapper.findAll('[data-testid="row-checkbox"]')).toHaveLength(0)
		expect(wrapper.text()).toContain("No Data")
	})

	it("clicking a picked item switches it back to search input for editing", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Request").trigger("click")
		await flushPromises()

		await wrapper.find('[data-testid="add-row"]').trigger("click")
		await flushPromises()
		await searchRowItem(wrapper, "latte")
		await findButton(wrapper, "Iced Latte").trigger("click")
		await flushPromises()

		// Item input is replaced by the picked item display
		expect(wrapper.find('[data-testid="row-item-input"]').exists()).toBe(false)
		const itemBox = wrapper.find('[title="Click to change item"]')
		expect(itemBox.exists()).toBe(true)
		expect(itemBox.text()).toContain("Iced Latte")

		// Click to edit
		await itemBox.trigger("click")
		await flushPromises()

		// Input returns with search prefilled to previous item_code
		const input = wrapper.find('[data-testid="row-item-input"]')
		expect(input.exists()).toBe(true)
		expect(input.element.value).toBe("ICED-LATTE")
	})
	it("draft opened in detail allows editing items, purpose, incident_date and sends them", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0007")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(RETURNED_DRAFT_DETAIL))
		const saveCall = mockCall(SAVE_URL, (params) =>
			Promise.resolve({
				name: "PRJ-2601-0007",
				workflow_state: "Pending Approval",
				docstatus: 0,
			}),
		)
		mockCall(ATTACH_URL, () =>
			Promise.resolve({ evidence_count: 1, destruction_count: 0 }),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0007").trigger("click")
		await flushPromises()

		// Check editable purpose & incident date
		const purposeSelect = wrapper.find('[data-testid="detail-purpose-select"]')
		expect(purposeSelect.exists()).toBe(true)
		await purposeSelect.setValue("Damaged")

		const incidentInput = wrapper.find(
			'[data-testid="detail-incident-date-input"]',
		)
		expect(incidentInput.exists()).toBe(true)
		await incidentInput.setValue("2026-01-06")

		// Check items table is editable (add row exists)
		const addRowBtn = wrapper.find('[data-testid="add-row"]')
		expect(addRowBtn.exists()).toBe(true)

		// Modify first row qty
		const qtyInput = wrapper.find('[data-testid="qty-input"]')
		expect(qtyInput.exists()).toBe(true)
		await qtyInput.setValue(5)

		// Submit detail
		const submitBtn = findButton(wrapper, "Submit")
		expect(submitBtn.exists()).toBe(true)
		expect(submitBtn.attributes("disabled")).toBeUndefined()

		await submitBtn.trigger("click")
		await flushPromises()

		expect(saveCall).toHaveBeenCalled()
		const payload = JSON.parse(saveCall.mock.calls[0][0].data)
		expect(payload.name).toBe("PRJ-2601-0007")
		expect(payload.purpose).toBe("Damaged")
		expect(payload.incident_date).toBe("2026-01-06")
		expect(payload.items[0]).toEqual(
			expect.objectContaining({
				item_code: "ICED-LATTE",
				qty: 5,
				uom: "Nos",
			}),
		)
	})

	it("Berita Acara button is shown in detail when approval_code is set in Completed state", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0005")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(COMPLETED_CREW_DETAIL))

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0005").trigger("click")
		await flushPromises()

		const downloadBtn = wrapper.find('[data-testid="download-berita-acara"]')
		expect(downloadBtn.exists()).toBe(true)
		expect(downloadBtn.attributes("href")).toContain("Berita%20Acara%20Reject")
	})

	it("detail items table displays UOM column for read-only view", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve({ requests: [ROW("PRJ-2601-0001")], has_more: false }),
		)
		mockCall(DETAIL_URL, () => Promise.resolve(PENDING_DETAIL))

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "PRJ-2601-0001").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("UOM")
		expect(wrapper.text()).toContain("Nos")
	})
})
