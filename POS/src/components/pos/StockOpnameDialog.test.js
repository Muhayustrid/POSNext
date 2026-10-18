/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Route call() by API url, same pattern as RejectRequestDialog.test.js.
const handlers = vi.hoisted(() => ({ map: {}, calls: {} }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	// Renders nothing while closed: the nested ConfirmDialog must not leak its
	// buttons into the DOM of the host dialog.
	const Dialog = defineComponent({
		name: "DialogStub",
		props: ["modelValue", "options"],
		emits: ["update:modelValue", "close"],
		setup(props, { slots }) {
			return () =>
				props.modelValue
					? [
							h("div", { "data-test": "dialog-title" }, props.options?.title),
							slots["body-content"]?.(),
							slots.actions?.(),
						]
					: null
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

const toasts = vi.hoisted(() => ({ log: [] }))
vi.mock("@/composables/useToast", () => ({
	useToast: () => ({
		showSuccess: (m) => toasts.log.push(m),
		showError: (m) => toasts.log.push(m),
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

import StockOpnameDialog from "./StockOpnameDialog.vue"
import ConfirmDialog from "../common/ConfirmDialog.vue"

const LIST_URL = "pos_next.api.stock_opname.list_opnames"
const GET_URL = "pos_next.api.stock_opname.get_opname"
const SAVE_URL = "pos_next.api.stock_opname.save_opname"
const SUBMIT_URL = "pos_next.api.stock_opname.submit_opname"
const DELETE_URL = "pos_next.api.stock_opname.delete_opname"
const BATCHES_URL = "pos_next.api.stock_opname.get_item_batches"
const SERIALS_URL = "pos_next.api.stock_opname.get_item_serials"
const IMPORT_URL = "pos_next.api.stock_opname.import_opname_template"
const ATTACH_URL = "pos_next.api.stock_opname.attach_opname_files"
const REMOVE_URL = "pos_next.api.stock_opname.remove_opname_attachment"
const SEARCH_URL = "pos_next.api.stock_opname.search_opname_items"
const GROUPS_URL = "pos_next.api.items.get_item_groups"

function mockCall(url, impl) {
	handlers.calls[url] = vi.fn(impl)
	return handlers.calls[url]
}

function findButton(wrapper, text) {
	return wrapper.findAll("button").find((b) => b.text().includes(text))
}

/** A clickable list row (list view only). */
function listRow(wrapper, name) {
	return wrapper
		.findAll('[data-testid="opname-row"]')
		.find((r) => r.text().includes(name))
}

const confirmDialog = (wrapper) => wrapper.findComponent(ConfirmDialog)

/** Click Cancel / the confirm button inside the shared ConfirmDialog. */
async function clickCancel(wrapper) {
	await confirmDialog(wrapper).findAll("button").at(0).trigger("click")
	await flushPromises()
}
async function clickConfirm(wrapper) {
	await confirmDialog(wrapper).findAll("button").at(-1).trigger("click")
	await flushPromises()
}

/** Click Add Row and return the new row's Item link input. */
async function addRow(wrapper) {
	await findButton(wrapper, "Add Row").trigger("click")
	await flushPromises()
	return wrapper.findAll('[data-testid="row-item-input"]').at(-1)
}

/** Type a term in the last added row and let the 350 ms debounce fire. */
async function searchRow(wrapper, term) {
	const input = await addRow(wrapper)
	await input.setValue(term)
	await new Promise((r) => setTimeout(r, 400))
	await flushPromises()
}

/** Click the search result whose text contains `text`. */
async function pickResult(wrapper, text) {
	const btn = wrapper
		.findAll('[data-testid="row-item-result"]')
		.find((b) => b.text().includes(text))
	await btn.trigger("click")
	await flushPromises()
}

async function mountOpenDialog() {
	const wrapper = mount(StockOpnameDialog, {
		props: { modelValue: true, posProfile: "POS-1" },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	await flushPromises()
	return wrapper
}

/** Open a saved doc from the list (list rows are clickable). */
async function openSavedDoc(wrapper, name = "SR-0001") {
	await listRow(wrapper, name).trigger("click")
	await flushPromises()
}

const PLAIN_ITEM = {
	item_code: "ITEM-A",
	item_name: "Alpha Item",
	stock_uom: "Nos",
	qty: 3,
	serial_nos: [],
	has_batch_no: 0,
	has_serial_no: 0,
}

const ROW_DRAFT = {
	name: "SR-0001",
	posting_date: "2026-01-05",
	workflow_state: "Draft",
	docstatus: 0,
	editable: true,
	item_count: 1,
	modified: "m1",
	reject_reason: null,
}

const DRAFT_DOC = {
	name: "SR-0001",
	workflow_state: "Draft",
	docstatus: 0,
	editable: true,
	modified: "m1",
	items: [PLAIN_ITEM],
	attachments: [],
	actions: [],
}

beforeEach(() => {
	handlers.map = {}
	handlers.calls = {}
	toasts.log = []
	window.confirm = vi.fn(() => true)
	mockCall(LIST_URL, () => Promise.resolve([]))
	mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC }))
	mockCall(GROUPS_URL, () => Promise.resolve([]))
	globalThis.fetch = vi.fn(() =>
		Promise.resolve({
			ok: true,
			json: () =>
				Promise.resolve({ message: { file_url: "/private/files/count.xlsx" } }),
		}),
	)
})

// a failing fake-timer test must not leak the clock into the next one
afterEach(() => vi.useRealTimers())

describe("StockOpnameDialog list", () => {
	it("renders the Desk list header and clickable rows", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		const wrapper = await mountOpenDialog()

		const header = wrapper.find('[data-testid="list-header"]')
		expect(header.exists()).toBe(true)
		for (const label of ["ID", "Status", "Posting Date", "Items"])
			expect(header.text()).toContain(label)
		expect(listRow(wrapper, "SR-0001")).toBeTruthy()
	})

	it("derives the indicator tone from the payload flags only", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-RET", docstatus: 0, workflow_state: "Rejected", returned: true, editable: true, item_count: 1 },
				{ name: "SR-STAGE", docstatus: 0, workflow_state: "Area Check", editable: false, item_count: 1 },
				{ name: "SR-PROC", docstatus: 0, workflow_state: "Approved", processing: true, editable: false, item_count: 1 },
				{ name: "SR-DONE", docstatus: 1, workflow_state: "Approved", item_count: 1 },
				{ name: "SR-VOID", docstatus: 2, workflow_state: "Cancelled", item_count: 1 },
			]),
		)
		const wrapper = await mountOpenDialog()
		const dot = (name) =>
			listRow(wrapper, name).find('[data-testid="status-dot"]')

		expect(dot("SR-RET").classes()).toContain("bg-red-500")
		expect(dot("SR-STAGE").classes()).toContain("bg-orange-400")
		expect(dot("SR-PROC").classes()).toContain("bg-blue-500")
		expect(dot("SR-DONE").classes()).toContain("bg-green-500")
		expect(dot("SR-VOID").classes()).toContain("bg-gray-400")
		// the label stays the raw workflow state, never a derived tone name
		expect(listRow(wrapper, "SR-PROC").text()).toContain("Approved")
	})

	it("shows the reject reason under a returned row", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-RET", docstatus: 0, editable: true, returned: true, workflow_state: "Rejected", reject_reason: "wrong count", item_count: 1 },
			]),
		)
		const wrapper = await mountOpenDialog()
		expect(wrapper.text()).toContain("Rejected")
		expect(wrapper.text()).toContain("wrong count")
	})

	it("shows Continue for an editable open count and hides New Count", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-DRAFT", docstatus: 0, editable: true, workflow_state: "Draft", item_count: 1 },
			]),
		)
		const wrapper = await mountOpenDialog()
		// Continue lives on the editable row itself, not in the list header
		expect(listRow(wrapper, "SR-DRAFT").find('[data-testid="row-continue"]').exists()).toBe(true)
		expect(findButton(wrapper, "Continue")).toBeTruthy()
		expect(findButton(wrapper, "New Count")).toBeUndefined()
	})

	it("pages the list 20 rows at a time", async () => {
		const rows = (start, n) =>
			Array.from({ length: n }, (_, i) => ({
				...ROW_DRAFT,
				name: `SR-${String(start + i).padStart(3, "0")}`,
				docstatus: 1,
				editable: false,
			}))
		const listCall = mockCall(LIST_URL, (params) =>
			Promise.resolve(params.start ? rows(20, 3) : rows(0, 21)),
		)
		const wrapper = await mountOpenDialog()
		expect(listCall.mock.calls[0][0]).toMatchObject({ start: 0, page_length: 21 })
		expect(wrapper.findAll('[data-testid="opname-row"]')).toHaveLength(20)

		await findButton(wrapper, "Next").trigger("click")
		await flushPromises()
		expect(listCall.mock.calls.at(-1)[0]).toMatchObject({ start: 20, page_length: 21 })
		expect(wrapper.findAll('[data-testid="opname-row"]')).toHaveLength(3)
		expect(findButton(wrapper, "Next").attributes("disabled")).toBeDefined()
		expect(findButton(wrapper, "Previous").attributes("disabled")).toBeUndefined()
	})

	it("blocks New Count while only a non-editable open count exists", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-STAGE", docstatus: 0, editable: false, workflow_state: "Area Check", item_count: 1 },
			]),
		)
		const wrapper = await mountOpenDialog()
		expect(findButton(wrapper, "Continue")).toBeUndefined()
		expect(findButton(wrapper, "New Count")).toBeUndefined()
	})
})

describe("StockOpnameDialog doc page", () => {
	it("opens from a row with breadcrumb and details", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				posting_date: "2026-01-05",
				posting_time: "09:30:00",
				set_warehouse: "Stores - TC",
				owner_fullname: "Budi Cashier",
				creation: "2026-01-04 08:00:00",
				modified_by_fullname: "Budi Cashier",
				items: [],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		const header = wrapper.find('[data-testid="doc-header"]')
		expect(header.text()).toContain("Stock Opname")
		expect(header.text()).toContain("SR-0001")

		const details = wrapper.find('[data-testid="opname-detail"]')
		expect(details.exists()).toBe(true)
		expect(details.text()).toContain("Stores - TC")
		expect(details.text()).toContain("Budi Cashier")
		// editable doc: posting date renders as an input, prefilled from the payload
		expect(details.find('[data-testid="posting-date-input"]').element.value).toBe(
			"2026-01-05",
		)
	})

	it("never renders system quantities", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				items: [{ ...PLAIN_ITEM, current_qty: 9999, valuation_rate: 8888 }],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		expect(wrapper.text()).toContain("Alpha Item")
		expect(wrapper.find('[data-testid="qty-input"]').exists()).toBe(true)
		// blind count: the system qty / valuation rate must never reach the DOM
		expect(wrapper.text()).not.toContain("9999")
		expect(wrapper.text()).not.toContain("8888")
	})

	it("renders a non-editable doc as text", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-0002", docstatus: 0, editable: false, workflow_state: "Area Check", item_count: 1 },
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-0002",
				workflow_state: "Area Check",
				docstatus: 0,
				editable: false,
				modified: "m1",
				set_warehouse: "Stores - TC",
				items: [PLAIN_ITEM],
				attachments: [],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper, "SR-0002")

		expect(wrapper.text()).toContain("Alpha Item")
		expect(wrapper.findAll('[data-testid="qty-input"]')).toHaveLength(0)
		expect(wrapper.find('[data-testid="uom-select"]').exists()).toBe(false)
		expect(wrapper.find('[data-testid="row-item-input"]').exists()).toBe(false)
		expect(wrapper.text()).not.toContain("Add Row")
	})

	it("renders the activity timeline newest last", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				items: [],
				activity: [
					{ type: "created", user: "a@x", user_fullname: "Budi Cashier", at: "2026-01-04 08:00:00" },
					{ type: "workflow", state: "Area Check", user: "a@x", user_fullname: "Budi Cashier", at: "2026-01-04 09:00:00" },
					{ type: "comment", content: "count again", user: "m@x", user_fullname: "Siti Manager", at: "2026-01-05 10:00:00" },
				],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		const entries = wrapper.findAll('[data-testid="activity-entry"]')
		expect(entries).toHaveLength(3)
		expect(entries[0].text()).toContain("Budi Cashier")
		expect(entries[2].text()).toContain("Siti Manager")
		expect(entries[2].text()).toContain("count again")
	})

	it("hides empty metadata on a new count and shows No Data in the empty grid", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Count").trigger("click")
		await flushPromises()

		const details = wrapper.find('[data-testid="opname-detail"]')
		expect(details.text()).not.toContain("Created By")
		expect(details.text()).not.toContain("Last Modified")
		expect(details.text()).not.toContain("Status")
		expect(wrapper.text()).toContain("No Data")
	})
})

describe("StockOpnameDialog primary action", () => {
	it("is Save on a new count, the workflow action after save, and Save again once dirty", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Count").trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="primary-action"]').text()).toContain("Save")

		mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				editable: true,
				modified: "m2",
				actions: ["Submit"],
				items: [PLAIN_ITEM],
			}),
		)
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		expect(wrapper.find('[data-testid="primary-action"]').text()).toContain("Submit")

		// editing a qty makes the doc dirty: the primary action goes back to Save
		await wrapper.find('[data-testid="qty-input"]').setValue(7)
		await flushPromises()
		expect(wrapper.find('[data-testid="primary-action"]').text()).toContain("Save")
	})

	it("renders an Actions dropdown when several workflow actions exist", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({ ...DRAFT_DOC, items: [], actions: ["Send", "Escalate"] }),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		const primary = wrapper.find('[data-testid="primary-action"]')
		expect(primary.text()).toContain("Actions")
		await primary.trigger("click")
		const menu = wrapper.find('[data-testid="actions-menu"]')
		expect(menu.exists()).toBe(true)
		expect(menu.text()).toContain("Send")
		expect(menu.text()).toContain("Escalate")
	})

	it("submits through the ConfirmDialog with the chosen action", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({ ...DRAFT_DOC, actions: ["Submit"] }),
		)
		const submitCall = mockCall(SUBMIT_URL, () =>
			Promise.resolve({ name: "SR-0001", workflow_state: "Area Check" }),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()

		expect(confirmDialog(wrapper).props("modelValue")).toBe(true)
		expect(window.confirm).not.toHaveBeenCalled()

		await clickConfirm(wrapper)
		expect(submitCall).toHaveBeenCalledTimes(1)
		expect(submitCall.mock.calls[0][0]).toMatchObject({
			name: "SR-0001",
			action: "Submit",
		})
	})

	it("submits the action picked from the Actions dropdown", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({ ...DRAFT_DOC, items: [], actions: ["Send", "Escalate"] }),
		)
		const submitCall = mockCall(SUBMIT_URL, () =>
			Promise.resolve({ name: "SR-0001", workflow_state: "Area Check" }),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await wrapper
			.findAll('[data-testid="actions-menu"] button')
			.find((b) => b.text() === "Escalate")
			.trigger("click")
		await flushPromises()
		await clickConfirm(wrapper)

		expect(submitCall.mock.calls[0][0]).toMatchObject({ action: "Escalate" })
	})
})

describe("StockOpnameDialog delete", () => {
	it("deletes a saved editable count from the ⋯ menu after confirmation", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		const deleteCall = mockCall(DELETE_URL, () => Promise.resolve(null))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await wrapper.find('[data-testid="doc-menu-button"]').trigger("click")
		const menu = wrapper.find('[data-testid="doc-menu"]')
		expect(menu.exists()).toBe(true)
		await menu.find('[data-testid="doc-menu-delete"]').trigger("click")
		await flushPromises()

		expect(confirmDialog(wrapper).props("modelValue")).toBe(true)
		expect(confirmDialog(wrapper).props("destructive")).toBe(true)

		await clickConfirm(wrapper)
		expect(deleteCall).toHaveBeenCalledTimes(1)
		expect(deleteCall.mock.calls[0][0]).toMatchObject({ name: "SR-0001" })
		expect(window.confirm).not.toHaveBeenCalled()
	})

	it("hides the ⋯ menu on a non-editable count", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-0002", docstatus: 0, editable: false, workflow_state: "Area Check", item_count: 1 },
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-0002",
				workflow_state: "Area Check",
				docstatus: 0,
				editable: false,
				modified: "m1",
				items: [],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper, "SR-0002")
		expect(wrapper.find('[data-testid="doc-menu-button"]').exists()).toBe(false)
	})
})

describe("StockOpnameDialog posting date", () => {
	it("keeps posting inputs disabled until ticked and omits them from the payload", async () => {
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Count").trigger("click")
		await flushPromises()

		expect(wrapper.find('[data-testid="posting-toggle"]').element.checked).toBe(false)
		expect(
			wrapper.find('[data-testid="posting-date-input"]').element.disabled,
		).toBe(true)
		expect(
			wrapper.find('[data-testid="posting-time-input"]').element.disabled,
		).toBe(true)

		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				editable: true,
				modified: "m2",
				actions: [],
				items: [],
			}),
		)
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		let data = saveCall.mock.calls[0][0].data
		expect(data.posting_date).toBeUndefined()
		expect(data.posting_time).toBeUndefined()
		expect(data.set_posting_time).toBeUndefined()

		await wrapper.find('[data-testid="posting-toggle"]').setValue(true)
		await flushPromises()
		expect(
			wrapper.find('[data-testid="posting-date-input"]').element.disabled,
		).toBe(false)

		await wrapper.find('[data-testid="posting-date-input"]').setValue("2026-01-05")
		await wrapper.find('[data-testid="posting-time-input"]').setValue("09:30")
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		data = saveCall.mock.calls[1][0].data
		expect(data.set_posting_time).toBe(1)
		expect(data.posting_date).toBe("2026-01-05")
		expect(data.posting_time).toBe("09:30")
	})

	it("starts ticked for a loaded backdated count", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				items: [],
				posting_date: "2026-01-05",
				posting_time: "09:30:00",
				set_posting_time: 1,
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		expect(wrapper.find('[data-testid="posting-toggle"]').element.checked).toBe(true)
		const dateInput = wrapper.find('[data-testid="posting-date-input"]')
		expect(dateInput.element.disabled).toBe(false)
		expect(dateInput.element.value).toBe("2026-01-05")
	})
})

describe("StockOpnameDialog dirty guard", () => {
	it("asks before leaving a doc with unsaved changes", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await wrapper.find('[data-testid="qty-input"]').setValue(9)
		await flushPromises()
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()

		expect(confirmDialog(wrapper).props("modelValue")).toBe(true)
		expect(confirmDialog(wrapper).props("title")).toContain("Discard unsaved changes?")

		// cancelling keeps the doc page
		await clickCancel(wrapper)
		expect(wrapper.find('[data-testid="opname-detail"]').exists()).toBe(true)
		expect(listRow(wrapper, "SR-0001")).toBeUndefined()

		// confirming leaves for the list
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		await clickConfirm(wrapper)
		expect(wrapper.find('[data-testid="opname-detail"]').exists()).toBe(false)
		expect(listRow(wrapper, "SR-0001")).toBeTruthy()
	})

	it("leaves without asking when nothing changed", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		expect(confirmDialog(wrapper).props("modelValue")).toBe(false)
		expect(listRow(wrapper, "SR-0001")).toBeTruthy()
	})
})

describe("StockOpnameDialog item grid", () => {
	it("Add Row appends a link-search row that fills from a picked result", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ITEM-A", item_name: "Alpha Item", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		// the old bottom search box is gone
		expect(wrapper.find('input[aria-label="Add item"]').exists()).toBe(false)

		await searchRow(wrapper, "alpha")
		expect(wrapper.findAll('[data-testid="row-item-result"]')).toHaveLength(1)
		await pickResult(wrapper, "Alpha Item")

		expect(wrapper.text()).toContain("Alpha Item")
		expect(wrapper.find('[data-testid="uom-select"]').exists()).toBe(true)
		expect(wrapper.find('[data-testid="qty-input"]').element.value).toBe("1")
		// picking resolves the link field: the input is replaced by the item text
		expect(wrapper.find('[data-testid="row-item-input"]').exists()).toBe(false)
	})

	it("debounces the per-row item search", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		const searchCall = mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ROTI", item_name: "Roti", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await addRow(wrapper)

		vi.useFakeTimers()
		await wrapper.find('[data-testid="row-item-input"]').setValue("ROTI")
		expect(searchCall).not.toHaveBeenCalled()
		await vi.advanceTimersByTimeAsync(350)
		expect(searchCall).toHaveBeenCalledTimes(1)
		expect(searchCall.mock.calls[0][0]).toMatchObject({ search_term: "ROTI" })
		vi.useRealTimers()
		await flushPromises()

		expect(wrapper.findAll('[data-testid="row-item-result"]')).toHaveLength(1)
	})

	it("Enter on an exact item_code match fills the row (barcode)", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ROTI-001", item_name: "Roti Manis", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await searchRow(wrapper, "ROTI-001")

		await wrapper.find('[data-testid="row-item-input"]').trigger("keyup.enter")
		await flushPromises()

		expect(wrapper.text()).toContain("Roti Manis")
		expect(wrapper.find('[data-testid="qty-input"]').element.value).toBe("1")
	})

	it("Enter fills the row when it lands before the debounce (barcode)", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		const searchCall = mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ROTI-001", item_name: "Roti Manis", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await addRow(wrapper)

		vi.useFakeTimers()
		await wrapper.find('[data-testid="row-item-input"]').setValue("ROTI-001")
		// the debounce has not fired: Enter must look the exact code up itself
		expect(searchCall).not.toHaveBeenCalled()
		await wrapper.find('[data-testid="row-item-input"]').trigger("keyup.enter")
		await vi.advanceTimersByTimeAsync(0)
		expect(searchCall).toHaveBeenCalledTimes(1)
		vi.useRealTimers()
		await flushPromises()

		expect(wrapper.text()).toContain("Roti Manis")
		expect(wrapper.find('[data-testid="qty-input"]').element.value).toBe("1")
	})

	it("keeps only one row dropdown open", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, (params) =>
			Promise.resolve([
				{
					item_code: params.search_term.toUpperCase(),
					item_name: `${params.search_term} item`,
					stock_uom: "Nos",
					has_batch_no: 0,
					has_serial_no: 0,
					uoms: [],
				},
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "alpha")
		expect(wrapper.findAll('[data-testid="row-item-result"]')).toHaveLength(1)

		// searching in a second row closes the first row's dropdown
		await searchRow(wrapper, "beta")
		const results = wrapper.findAll('[data-testid="row-item-result"]')
		expect(results).toHaveLength(1)
		expect(results[0].text()).toContain("beta item")
	})

	it("closes the dropdown on Escape and, after a delay, on blur", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ITEM-A", item_name: "Alpha Item", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "alpha")
		await wrapper.find('[data-testid="row-item-input"]').trigger("keyup.escape")
		await flushPromises()
		expect(wrapper.findAll('[data-testid="row-item-result"]')).toHaveLength(0)

		// blur closes too, but only after a delay so a result click still lands
		await wrapper.find('[data-testid="row-item-input"]').setValue("alpha")
		await new Promise((r) => setTimeout(r, 400))
		await flushPromises()
		expect(wrapper.findAll('[data-testid="row-item-result"]')).toHaveLength(1)

		vi.useFakeTimers()
		await wrapper.find('[data-testid="row-item-input"]').trigger("blur")
		expect(wrapper.findAll('[data-testid="row-item-result"]')).toHaveLength(1)
		await vi.advanceTimersByTimeAsync(200)
		vi.useRealTimers()
		await flushPromises()
		expect(wrapper.findAll('[data-testid="row-item-result"]')).toHaveLength(0)
	})

	it("batch item uses a row select and blocks Save until a batch is picked", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "MILK", item_name: "Milk", stock_uom: "Nos", has_batch_no: 1, has_serial_no: 0, uoms: [] },
			]),
		)
		mockCall(BATCHES_URL, () =>
			Promise.resolve([
				{ batch_no: "B1", expiry_date: "2026-01-01" },
				{ batch_no: "B2", expiry_date: "2026-02-01" },
			]),
		)
		const saveCall = mockCall(SAVE_URL, () => Promise.resolve({ ...DRAFT_DOC }))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await searchRow(wrapper, "milk")
		await pickResult(wrapper, "Milk")

		// the separate batch picker panel is gone; the row itself selects the batch
		expect(wrapper.find('[data-testid="batch-picker"]').exists()).toBe(false)
		const select = wrapper.find('[data-testid="row-batch-select"]')
		expect(select.exists()).toBe(true)
		expect(select.findAll("option").map((o) => o.text())).toEqual(["Select batch", "B1", "B2"])
		// a batch row starts at qty 0
		expect(wrapper.find('[data-testid="qty-input"]').element.value).toBe("0")

		// no batch picked: error toast, no save call
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		expect(saveCall).not.toHaveBeenCalled()
		expect(toasts.log.some((m) => m.includes("Choose a batch"))).toBe(true)

		// picking a batch unblocks the save
		await select.setValue("B1")
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		expect(saveCall).toHaveBeenCalledTimes(1)
		expect(saveCall.mock.calls[0][0].data.items[0]).toMatchObject({
			item_code: "MILK",
			batch_no: "B1",
		})
	})

	it("rejects a duplicate plain item and clears the row", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ITEM-A", item_name: "Alpha Item", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "alpha")
		await pickResult(wrapper, "Alpha Item")
		await searchRow(wrapper, "alpha")
		await pickResult(wrapper, "Alpha Item")

		expect(toasts.log.some((m) => m.includes("already in the list"))).toBe(true)
		// one filled row plus the cleared link row
		expect(wrapper.findAll('[data-testid="qty-input"]')).toHaveLength(1)
		expect(wrapper.findAll('[data-testid="row-item-input"]')).toHaveLength(1)
	})

	it("rejects a duplicate item+batch and clears the row", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "MILK", item_name: "Milk", stock_uom: "Nos", has_batch_no: 1, has_serial_no: 0, uoms: [] },
			]),
		)
		mockCall(BATCHES_URL, () => Promise.resolve([{ batch_no: "B1" }, { batch_no: "B2" }]))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "milk")
		await pickResult(wrapper, "Milk")
		await wrapper.findAll('[data-testid="row-batch-select"]')[0].setValue("B1")

		// same item, another row: allowed until the same batch is picked
		await searchRow(wrapper, "milk")
		await pickResult(wrapper, "Milk")
		await wrapper.findAll('[data-testid="row-batch-select"]')[1].setValue("B1")
		await flushPromises()

		expect(toasts.log.some((m) => m.includes("already in the list"))).toBe(true)
		expect(wrapper.findAll('[data-testid="row-batch-select"]')).toHaveLength(1)
		expect(wrapper.findAll('[data-testid="row-item-input"]')).toHaveLength(1)
	})

	it("drops blank rows from the save payload", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ITEM-A", item_name: "Alpha Item", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const saveCall = mockCall(SAVE_URL, () => Promise.resolve({ ...DRAFT_DOC }))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "alpha")
		await pickResult(wrapper, "Alpha Item")
		await addRow(wrapper) // left blank
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()

		expect(saveCall.mock.calls[0][0].data.items).toHaveLength(1)
		expect(saveCall.mock.calls[0][0].data.items[0].item_code).toBe("ITEM-A")
	})

	it("pick reuses the cached search row", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		const searchCall = mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{
					item_code: "ITEM-A",
					item_name: "Alpha Item",
					stock_uom: "Nos",
					has_batch_no: 0,
					has_serial_no: 0,
					uoms: [],
				},
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "alpha")
		expect(searchCall).toHaveBeenCalledTimes(1)
		await pickResult(wrapper, "Alpha Item")

		// the picked row is reused from the cached results — no second search request
		expect(searchCall).toHaveBeenCalledTimes(1)
		expect(wrapper.text()).toContain("Alpha Item")
	})

	it("serial item qty equals ticked serials", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{
					item_code: "SER-1",
					item_name: "Serial Item",
					stock_uom: "Nos",
					has_serial_no: 1,
					has_batch_no: 0,
					uoms: [],
				},
			]),
		)
		mockCall(SERIALS_URL, () => Promise.resolve(["A", "B", "C"]))
		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				editable: true,
				modified: "m2",
				actions: [],
				items: [],
			}),
		)

		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "ser")
		await pickResult(wrapper, "Serial Item")
		await flushPromises()

		const serials = wrapper.findAll('[data-testid="serial-option"]')
		expect(serials).toHaveLength(3)
		await serials[0].setValue(true)
		await serials[1].setValue(true)
		await flushPromises()

		expect(wrapper.find('[data-testid="item-qty"]').text().trim()).toBe("2")

		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		expect(saveCall.mock.calls[0][0].data.items[0].serial_nos).toHaveLength(2)
	})

	it("serial item with zero ticked serials saves serial_nos empty and reloads as serial", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{
					item_code: "SER-1",
					item_name: "Serial Item",
					stock_uom: "Nos",
					has_serial_no: 1,
					has_batch_no: 0,
					uoms: [],
				},
			]),
		)
		mockCall(SERIALS_URL, () => Promise.resolve(["A", "B"]))
		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				editable: true,
				modified: "m2",
				actions: [],
				items: [
					{
						item_code: "SER-1",
						item_name: "Serial Item",
						stock_uom: "Nos",
						qty: 0,
						batch_no: null,
						serial_nos: [],
						has_serial_no: 1,
						has_batch_no: 0,
					},
				],
			}),
		)

		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await searchRow(wrapper, "ser")
		await pickResult(wrapper, "Serial Item")
		await flushPromises()

		// nothing ticked: qty is 0 but the row is still a serial row, not a qty input
		expect(wrapper.find('[data-testid="item-qty"]').text().trim()).toBe("0")
		expect(wrapper.findAll('[data-testid="qty-input"]')).toHaveLength(0)

		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		expect(saveCall.mock.calls[0][0].data.items[0].serial_nos).toEqual([])

		// survives the reload as a serial row
		expect(wrapper.find('[data-testid="item-qty"]').exists()).toBe(true)
		expect(wrapper.findAll('[data-testid="qty-input"]')).toHaveLength(0)
	})

	it("after Save the unticked serial is still offered", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{
					item_code: "SER-1",
					item_name: "Serial Item",
					stock_uom: "Nos",
					has_serial_no: 1,
					has_batch_no: 0,
					uoms: [],
				},
			]),
		)
		mockCall(SERIALS_URL, () => Promise.resolve(["A", "B"]))
		mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				editable: true,
				modified: "m2",
				actions: [],
				items: [
					{
						item_code: "SER-1",
						item_name: "Serial Item",
						stock_uom: "Nos",
						qty: 1,
						batch_no: null,
						serial_nos: ["A"],
						has_serial_no: 1,
						has_batch_no: 0,
					},
				],
			}),
		)

		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await searchRow(wrapper, "ser")
		await pickResult(wrapper, "Serial Item")
		await flushPromises()

		await wrapper.findAll('[data-testid="serial-option"]')[0].setValue(true) // tick A
		await flushPromises()

		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()

		// the save response only carries the ticked serial; B must stay tickable
		const serialLabels = wrapper
			.findAll("label")
			.filter((l) => l.find('[data-testid="serial-option"]').exists())
		expect(serialLabels.map((l) => l.text())).toEqual(["A", "B"])
	})

	it("uom select changes the sent custom fields", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{
					item_code: "MILK",
					item_name: "Milk",
					stock_uom: "Nos",
					has_batch_no: 0,
					has_serial_no: 0,
					uoms: [{ uom: "Pack", conversion_factor: 12 }],
				},
			]),
		)
		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				editable: true,
				modified: "m2",
				actions: [],
				items: [],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await searchRow(wrapper, "milk")
		await pickResult(wrapper, "Milk")
		await flushPromises()

		const select = wrapper.find('[data-testid="uom-select"]')
		expect(select.exists()).toBe(true)
		await select.setValue("Pack")
		await wrapper.find('[data-testid="qty-input"]').setValue(2)

		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		const item = saveCall.mock.calls[0][0].data.items[0]
		expect(item.custom_uom).toBe("Pack")
		expect(item.custom_qty_after).toBe(2)
	})

	it("a reloaded alt-UOM row can still switch UOM", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				items: [
					{
						...PLAIN_ITEM,
						custom_uom: "Pack",
						custom_qty_after: 2,
						uoms: [{ uom: "Pack", conversion_factor: 12 }],
					},
				],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		const select = wrapper.find('[data-testid="uom-select"]')
		expect(select.findAll("option").map((o) => o.text())).toEqual(["Nos", "Pack"])
		expect(select.element.value).toBe("Pack")
	})

	it("serial rows show uom read-only", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{
					item_code: "SER-1",
					item_name: "Serial Item",
					stock_uom: "Nos",
					has_serial_no: 1,
					has_batch_no: 0,
					uoms: [{ uom: "Pack", conversion_factor: 12 }],
				},
			]),
		)
		mockCall(SERIALS_URL, () => Promise.resolve(["A", "B"]))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await searchRow(wrapper, "ser")
		await pickResult(wrapper, "Serial Item")
		await flushPromises()

		// serials are counted by ticking, never in a converted UOM
		expect(wrapper.find('[data-testid="uom-select"]').exists()).toBe(false)
		const readOnly = wrapper.find('[data-testid="uom-readonly"]')
		expect(readOnly.exists()).toBe(true)
		expect(readOnly.text()).toBe("Nos")
	})

	it("removing a middle row keeps per-row state", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{ item_code: "ITEM-A", item_name: "Alpha Item", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
				{ item_code: "ITEM-B", item_name: "Beta Item", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
				{ item_code: "ITEM-C", item_name: "Gamma Item", stock_uom: "Nos", has_batch_no: 0, has_serial_no: 0, uoms: [] },
			]),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		for (const [term, name] of [
			["alpha", "Alpha Item"],
			["beta", "Beta Item"],
			["gamma", "Gamma Item"],
		]) {
			await searchRow(wrapper, term)
			await pickResult(wrapper, name)
		}

		let qtyInputs = wrapper.findAll('[data-testid="qty-input"]')
		expect(qtyInputs).toHaveLength(3)
		await qtyInputs[0].setValue(11)
		await qtyInputs[1].setValue(22)
		await qtyInputs[2].setValue(33)
		await flushPromises()

		const firstEl = qtyInputs[0].element
		const thirdEl = qtyInputs[2].element

		// remove the middle row (Beta)
		await wrapper.findAll('button[aria-label="Remove item"]')[1].trigger("click")
		await flushPromises()

		qtyInputs = wrapper.findAll('[data-testid="qty-input"]')
		expect(qtyInputs).toHaveLength(2)
		// keyed rows survive the splice: Alpha keeps its node, Gamma moves up intact
		expect(qtyInputs[0].element).toBe(firstEl)
		expect(qtyInputs[1].element).toBe(thirdEl)
		expect(qtyInputs[0].element.value).toBe("11")
		expect(qtyInputs[1].element.value).toBe("33")
	})
})

describe("StockOpnameDialog excel upload", () => {
	it("shows per-row errors", async () => {
		mockCall(LIST_URL, () => Promise.resolve([{ ...ROW_DRAFT, item_count: 0 }]))
		mockCall(GET_URL, () => Promise.resolve({ ...DRAFT_DOC, items: [] }))
		mockCall(IMPORT_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				applied: 1,
				skipped: 2,
				errors: [
					{ row: 3, item_code: "X", message: "unknown item" },
					{ row: 7, item_code: "Y", message: "bad qty" },
				],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		const input = wrapper.find('input[accept=".xlsx"]')
		Object.defineProperty(input.element, "files", {
			value: [
				new File(["x"], "count.xlsx", { type: "application/vnd.ms-excel" }),
			],
		})
		await input.trigger("change")
		await flushPromises()

		expect(wrapper.text()).toContain("Row 3")
		expect(wrapper.text()).toContain("Row 7")
	})

	it("confirms before an upload replaces unsaved rows", async () => {
		mockCall(SEARCH_URL, () =>
			Promise.resolve([
				{
					item_code: "ITEM-A",
					item_name: "Alpha Item",
					stock_uom: "Nos",
					has_batch_no: 0,
					has_serial_no: 0,
					uoms: [],
				},
			]),
		)
		const importCall = mockCall(IMPORT_URL, () =>
			Promise.resolve({ name: null, applied: 0, skipped: 0, errors: [] }),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "New Count").trigger("click")
		await flushPromises()
		await searchRow(wrapper, "alpha")
		await pickResult(wrapper, "Alpha Item")

		const input = wrapper.find('input[accept=".xlsx"]')
		const pick = async () => {
			Object.defineProperty(input.element, "files", {
				value: [new File(["x"], "count.xlsx")],
				configurable: true,
			})
			await input.trigger("change")
			await flushPromises()
		}

		await pick()
		expect(confirmDialog(wrapper).props("modelValue")).toBe(true)
		await clickCancel(wrapper)
		expect(importCall).not.toHaveBeenCalled()

		await pick()
		await clickConfirm(wrapper)
		expect(importCall).toHaveBeenCalled()
	})

	it("confirms before an upload over a saved draft with unsaved edits", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		const importCall = mockCall(IMPORT_URL, () =>
			Promise.resolve({ name: "SR-0001", applied: 0, skipped: 0, errors: [] }),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)
		await wrapper.find('[data-testid="qty-input"]').setValue(9)
		await flushPromises()

		const input = wrapper.find('input[accept=".xlsx"]')
		Object.defineProperty(input.element, "files", {
			value: [new File(["x"], "count.xlsx")],
		})
		await input.trigger("change")
		await flushPromises()

		expect(confirmDialog(wrapper).props("modelValue")).toBe(true)
		await clickCancel(wrapper)
		expect(importCall).not.toHaveBeenCalled()
	})
})

describe("StockOpnameDialog save and attachments", () => {
	it("save sends modified for conflict detection", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({ ...DRAFT_DOC, modified: "2026-01-01 09:00:00" }),
		)
		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				modified: "2026-01-01 09:30:00",
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await wrapper.find('[data-testid="qty-input"]').setValue(4)
		await flushPromises()
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()

		const payload = saveCall.mock.calls[0][0]
		expect(payload.data.name).toBe("SR-0001")
		expect(payload.data.modified).toBe("2026-01-01 09:00:00")
		expect(payload.data.items).toHaveLength(1)
	})

	it("adds a link and removes an attachment after confirmation", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		mockCall(GET_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				attachments: [
					{ name: "F1", file_name: "sheet.pdf", file_url: "/private/files/sheet.pdf" },
				],
			}),
		)
		const attachCall = mockCall(ATTACH_URL, () =>
			Promise.resolve([
				{ name: "F1", file_name: "sheet.pdf", file_url: "/private/files/sheet.pdf" },
				{ name: "F2", file_name: "https://drive.example/x", file_url: "https://drive.example/x" },
			]),
		)
		const removeCall = mockCall(REMOVE_URL, () => Promise.resolve([]))
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		expect(wrapper.find('[data-test="attach-input"]').attributes("accept")).toContain(".docx")
		const link = wrapper.find('[data-testid="attach-link"]')
		await link.setValue("ftp://nope")
		expect(wrapper.text()).toContain("Link must start with http:// or https://")
		await link.setValue("https://drive.example/x")
		await findButton(wrapper, "Add Link").trigger("click")
		await flushPromises()
		expect(attachCall.mock.calls[0][0]).toMatchObject({ name: "SR-0001", link: "https://drive.example/x" })
		expect(wrapper.text()).toContain("https://drive.example/x")

		await wrapper.find('button[aria-label="Remove"]').trigger("click")
		await flushPromises()
		expect(confirmDialog(wrapper).props("modelValue")).toBe(true)
		expect(window.confirm).not.toHaveBeenCalled()

		await clickCancel(wrapper)
		expect(removeCall).not.toHaveBeenCalled()

		await wrapper.find('button[aria-label="Remove"]').trigger("click")
		await flushPromises()
		await clickConfirm(wrapper)
		expect(removeCall.mock.calls[0][0]).toMatchObject({ name: "SR-0001", file: "F1" })
		expect(wrapper.find('[data-test="attachment-list"]').exists()).toBe(false)
	})

	it("submit attaches a typed link that was not added yet", async () => {
		mockCall(LIST_URL, () => Promise.resolve([ROW_DRAFT]))
		const draft = { ...DRAFT_DOC, actions: ["Submit"] }
		mockCall(GET_URL, () => Promise.resolve(draft))
		mockCall(SAVE_URL, () => Promise.resolve(draft))
		const order = []
		const attachCall = mockCall(ATTACH_URL, () => {
			order.push("attach")
			return Promise.resolve([])
		})
		mockCall(SUBMIT_URL, () => {
			order.push("submit")
			return Promise.resolve({})
		})
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper)

		await wrapper.find('[data-testid="attach-link"]').setValue("https://drive.example/x")
		await wrapper.find('[data-testid="primary-action"]').trigger("click")
		await flushPromises()
		await clickConfirm(wrapper)
		expect(attachCall.mock.calls[0][0]).toMatchObject({ name: "SR-0001", link: "https://drive.example/x" })
		expect(order).toEqual(["attach", "submit"])
	})

	it("pending count shows attachments read-only", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-0002", workflow_state: "Area Check", docstatus: 0, editable: false, item_count: 1, modified: "m1" },
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-0002",
				workflow_state: "Area Check",
				docstatus: 0,
				editable: false,
				modified: "m1",
				items: [PLAIN_ITEM],
				attachments: [
					{ name: "F1", file_name: "sheet.pdf", file_url: "/private/files/sheet.pdf" },
				],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper, "SR-0002")

		expect(wrapper.text()).toContain("sheet.pdf")
		expect(wrapper.find('[data-testid="attach-link"]').exists()).toBe(false)
		expect(wrapper.find('button[aria-label="Remove"]').exists()).toBe(false)
	})

	it("read-only doc shows header info and counted items", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-0002", workflow_state: "Area Check", docstatus: 0, editable: false, item_count: 1, modified: "m1" },
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-0002",
				workflow_state: "Area Check",
				docstatus: 0,
				editable: false,
				modified: "m1",
				posting_date: "2026-01-05",
				posting_time: "09:30:00",
				set_warehouse: "Stores - TC",
				owner_fullname: "Budi Cashier",
				items: [{ ...PLAIN_ITEM, custom_uom: "Pack", custom_qty_after: 2 }],
				attachments: [
					{ name: "F1", file_name: "sheet.pdf", file_url: "/private/files/sheet.pdf" },
				],
			}),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper, "SR-0002")

		// details: doc number, status, posting date, warehouse, created by
		expect(wrapper.text()).toContain("SR-0002")
		expect(wrapper.text()).toContain("Area Check")
		expect(wrapper.text()).toContain("05/01/26")
		expect(wrapper.text()).toContain("Stores - TC")
		expect(wrapper.text()).toContain("Budi Cashier")
		// items table shows the counted qty in its count UOM
		expect(wrapper.text()).toContain("Alpha Item")
		expect(wrapper.text()).toContain("Pack")
		// attachments read-only
		expect(wrapper.text()).toContain("sheet.pdf")
		expect(wrapper.find('[data-testid="attach-link"]').exists()).toBe(false)
	})
})

describe("StockOpnameDialog navigation", () => {
	it("Continue opens the editable count, not a pending one", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-PEND", workflow_state: "Area Check", docstatus: 0, editable: false, item_count: 1, modified: "m1" },
				{ name: "SR-DRAFT", workflow_state: "Draft", docstatus: 0, editable: true, item_count: 1, modified: "m2" },
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				...DRAFT_DOC,
				name: "SR-DRAFT",
				modified: "m2",
			}),
		)
		const wrapper = await mountOpenDialog()

		const cont = findButton(wrapper, "Continue")
		expect(cont).toBeTruthy()
		expect(findButton(wrapper, "New Count")).toBeUndefined()
		await cont.trigger("click")
		await flushPromises()
		expect(wrapper.text()).toContain("Alpha Item")
	})

	it("opens a non-editable open count read-only", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{ name: "SR-PEND", workflow_state: "Area Check", docstatus: 0, editable: false, item_count: 1, modified: "m1" },
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-PEND",
				workflow_state: "Area Check",
				docstatus: 0,
				editable: false,
				modified: "m1",
				items: [PLAIN_ITEM],
			}),
		)
		const wrapper = await mountOpenDialog()

		expect(findButton(wrapper, "Continue")).toBeUndefined()
		expect(findButton(wrapper, "New Count")).toBeUndefined()
		// the row still opens read-only
		await openSavedDoc(wrapper, "SR-PEND")
		expect(wrapper.text()).toContain("Alpha Item")
		expect(findButton(wrapper, "Save")).toBeUndefined()
		expect(wrapper.find('[data-testid="primary-action"]').exists()).toBe(false)
	})

	it("clears a typed attachment link when leaving the doc", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				ROW_DRAFT,
				{ ...ROW_DRAFT, name: "SR-0002", modified: "m2" },
			]),
		)
		mockCall(GET_URL, (params) =>
			Promise.resolve(
				params.name === "SR-0002"
					? { ...DRAFT_DOC, name: "SR-0002", modified: "m2" }
					: { ...DRAFT_DOC },
			),
		)
		const wrapper = await mountOpenDialog()
		await openSavedDoc(wrapper, "SR-0001")

		await wrapper.find('[data-testid="attach-link"]').setValue("https://drive.example/x")
		await wrapper.find('[data-testid="doc-back"]').trigger("click")
		await flushPromises()
		await openSavedDoc(wrapper, "SR-0002")

		expect(wrapper.find('[data-testid="attach-link"]').element.value).toBe("")
	})
})
