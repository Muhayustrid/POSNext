/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Route call() by API url, same pattern as RejectRequestDialog.test.js.
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

const LIST_URL = "pos_next.api.stock_opname.list_opnames"
const GET_URL = "pos_next.api.stock_opname.get_opname"
const SAVE_URL = "pos_next.api.stock_opname.save_opname"
const BATCHES_URL = "pos_next.api.stock_opname.get_item_batches"
const SERIALS_URL = "pos_next.api.stock_opname.get_item_serials"
const IMPORT_URL = "pos_next.api.stock_opname.import_opname_template"
const ITEMS_URL = "pos_next.api.items.get_items"
const GROUPS_URL = "pos_next.api.items.get_item_groups"

function mockCall(url, impl) {
	handlers.calls[url] = vi.fn(impl)
	return handlers.calls[url]
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

async function mountOpenDialog() {
	const wrapper = mount(StockOpnameDialog, {
		props: { modelValue: true, posProfile: "POS-1" },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	await flushPromises()
	return wrapper
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

beforeEach(() => {
	handlers.map = {}
	handlers.calls = {}
	toasts.log = []
	window.confirm = vi.fn(() => true)
	mockCall(LIST_URL, () => Promise.resolve([]))
	mockCall(GET_URL, () =>
		Promise.resolve({
			name: "SR-0001",
			workflow_state: "Draft",
			docstatus: 0,
			modified: "m1",
			items: [],
		}),
	)
	mockCall(GROUPS_URL, () => Promise.resolve([]))
	globalThis.fetch = vi.fn(() =>
		Promise.resolve({
			ok: true,
			json: () =>
				Promise.resolve({ message: { file_url: "/private/files/count.xlsx" } }),
		}),
	)
})

describe("StockOpnameDialog", () => {
	it("lists opnames with state and reject reason", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Rejected",
					docstatus: 0,
					item_count: 3,
					modified: "m1",
					reject_reason: "wrong count",
				},
				{
					name: "SR-0002",
					posting_date: "2026-01-06",
					workflow_state: "Approved",
					docstatus: 0,
					item_count: 2,
					modified: "m2",
					reject_reason: null,
				},
			]),
		)
		const wrapper = await mountOpenDialog()

		expect(wrapper.text()).toContain("Rejected")
		expect(wrapper.text()).toContain("wrong count")
		// Approved but still docstatus 0 -> background submit in progress
		expect(wrapper.text()).toContain("Processing")
	})

	it("editor never renders system quantities", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Draft",
					docstatus: 0,
					item_count: 1,
					modified: "m1",
					reject_reason: null,
				},
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				modified: "m1",
				items: [{ ...PLAIN_ITEM, current_qty: 99, valuation_rate: 5 }],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "SR-0001").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Alpha Item")
		expect(wrapper.find('[data-testid="qty-input"]').exists()).toBe(true)
		// blind count: the system qty / valuation rate must never reach the DOM
		expect(wrapper.text()).not.toContain("99")
		expect(wrapper.text()).not.toContain("5")
	})

	it("batch item adds one row per picked batch", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Draft",
					docstatus: 0,
					item_count: 0,
					modified: "m1",
					reject_reason: null,
				},
			]),
		)
		mockCall(ITEMS_URL, () =>
			Promise.resolve([
				{
					item_code: "MILK",
					item_name: "Milk",
					stock_uom: "Nos",
					has_batch_no: 1,
					has_serial_no: 0,
				},
			]),
		)
		mockCall(BATCHES_URL, () =>
			Promise.resolve([
				{ batch_no: "B1", expiry_date: "2026-01-01" },
				{ batch_no: "B2", expiry_date: "2026-02-01" },
			]),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "SR-0001").trigger("click")
		await flushPromises()

		await searchItem(wrapper, "milk")
		await findButton(wrapper, "Milk").trigger("click")
		await flushPromises()

		// a batch picker lists the item's batches, no free-text batch field
		const options = wrapper.findAll('[data-testid="batch-option"]')
		expect(options).toHaveLength(2)
		expect(wrapper.find('input[placeholder*="Batch"]').exists()).toBe(false)

		for (const opt of options) await opt.setValue(true)
		await findButton(wrapper, "Add").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("B1")
		expect(wrapper.text()).toContain("B2")
		expect(wrapper.findAll('[data-testid="qty-input"]')).toHaveLength(2)
	})

	it("serial item qty equals ticked serials", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Draft",
					docstatus: 0,
					item_count: 0,
					modified: "m1",
					reject_reason: null,
				},
			]),
		)
		mockCall(ITEMS_URL, () =>
			Promise.resolve([
				{
					item_code: "SER-1",
					item_name: "Serial Item",
					stock_uom: "Nos",
					has_serial_no: 1,
					has_batch_no: 0,
				},
			]),
		)
		mockCall(SERIALS_URL, () => Promise.resolve(["A", "B", "C"]))
		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				modified: "m2",
				items: [],
			}),
		)

		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "SR-0001").trigger("click")
		await flushPromises()

		await searchItem(wrapper, "ser")
		await findButton(wrapper, "Serial Item").trigger("click")
		await flushPromises()

		const serials = wrapper.findAll('[data-testid="serial-option"]')
		expect(serials).toHaveLength(3)
		await serials[0].setValue(true)
		await serials[1].setValue(true)
		await flushPromises()

		expect(wrapper.find('[data-testid="item-qty"]').text().trim()).toBe("2")

		await findButton(wrapper, "Save").trigger("click")
		await flushPromises()
		expect(saveCall.mock.calls[0][0].data.items[0].serial_nos).toHaveLength(2)
	})

	it("serial item with zero ticked serials saves serial_nos empty and reloads as serial", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Draft",
					docstatus: 0,
					item_count: 0,
					modified: "m1",
					reject_reason: null,
				},
			]),
		)
		mockCall(ITEMS_URL, () =>
			Promise.resolve([
				{
					item_code: "SER-1",
					item_name: "Serial Item",
					stock_uom: "Nos",
					has_serial_no: 1,
					has_batch_no: 0,
				},
			]),
		)
		mockCall(SERIALS_URL, () => Promise.resolve(["A", "B"]))
		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				modified: "m2",
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
		await findButton(wrapper, "SR-0001").trigger("click")
		await flushPromises()
		await searchItem(wrapper, "ser")
		await findButton(wrapper, "Serial Item").trigger("click")
		await flushPromises()

		// nothing ticked: qty is 0 but the row is still a serial row, not a qty input
		expect(wrapper.find('[data-testid="item-qty"]').text().trim()).toBe("0")
		expect(wrapper.findAll('[data-testid="qty-input"]')).toHaveLength(0)

		await findButton(wrapper, "Save").trigger("click")
		await flushPromises()
		expect(saveCall.mock.calls[0][0].data.items[0].serial_nos).toEqual([])

		// survives the reload as a serial row
		expect(wrapper.find('[data-testid="item-qty"]').exists()).toBe(true)
		expect(wrapper.findAll('[data-testid="qty-input"]')).toHaveLength(0)
	})

	it("after Save the unticked serial is still offered", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Draft",
					docstatus: 0,
					item_count: 1,
					modified: "m1",
					reject_reason: null,
				},
			]),
		)
		mockCall(ITEMS_URL, () =>
			Promise.resolve([
				{
					item_code: "SER-1",
					item_name: "Serial Item",
					stock_uom: "Nos",
					has_serial_no: 1,
					has_batch_no: 0,
				},
			]),
		)
		mockCall(SERIALS_URL, () => Promise.resolve(["A", "B"]))
		mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				modified: "m2",
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
		await findButton(wrapper, "SR-0001").trigger("click")
		await flushPromises()
		await searchItem(wrapper, "ser")
		await findButton(wrapper, "Serial Item").trigger("click")
		await flushPromises()

		await wrapper.findAll('[data-testid="serial-option"]')[0].setValue(true) // tick A
		await flushPromises()

		await findButton(wrapper, "Save").trigger("click")
		await flushPromises()

		// the save response only carries the ticked serial; B must stay tickable
		const serialLabels = wrapper
			.findAll("label")
			.filter((l) => l.find('[data-testid="serial-option"]').exists())
		expect(serialLabels.map((l) => l.text())).toEqual(["A", "B"])
	})

	it("pending opname is read-only", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0002",
					posting_date: "2026-01-05",
					workflow_state: "Approval Pending by Stock Manager",
					docstatus: 0,
					item_count: 1,
					modified: "m1",
					reject_reason: null,
				},
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-0002",
				workflow_state: "Approval Pending by Stock Manager",
				docstatus: 0,
				modified: "m1",
				items: [PLAIN_ITEM],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "SR-0002").trigger("click")
		await flushPromises()

		expect(wrapper.text()).toContain("Alpha Item")
		expect(findButton(wrapper, "Save")).toBeUndefined()
		expect(findButton(wrapper, "Submit")).toBeUndefined()
		expect(findButton(wrapper, "Delete")).toBeUndefined()
	})

	it("upload shows per-row errors", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Draft",
					docstatus: 0,
					item_count: 0,
					modified: "m1",
					reject_reason: null,
				},
			]),
		)
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
		await findButton(wrapper, "SR-0001").trigger("click")
		await flushPromises()

		const input = wrapper.find('input[type="file"]')
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

	it("save sends modified for conflict detection", async () => {
		mockCall(LIST_URL, () =>
			Promise.resolve([
				{
					name: "SR-0001",
					posting_date: "2026-01-05",
					workflow_state: "Draft",
					docstatus: 0,
					item_count: 1,
					modified: "2026-01-01 09:00:00",
					reject_reason: null,
				},
			]),
		)
		mockCall(GET_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				modified: "2026-01-01 09:00:00",
				items: [PLAIN_ITEM],
			}),
		)
		const saveCall = mockCall(SAVE_URL, () =>
			Promise.resolve({
				name: "SR-0001",
				workflow_state: "Draft",
				docstatus: 0,
				modified: "2026-01-01 09:30:00",
				items: [PLAIN_ITEM],
			}),
		)
		const wrapper = await mountOpenDialog()
		await findButton(wrapper, "SR-0001").trigger("click")
		await flushPromises()

		await findButton(wrapper, "Save").trigger("click")
		await flushPromises()

		const payload = saveCall.mock.calls[0][0]
		expect(payload.data.name).toBe("SR-0001")
		expect(payload.data.modified).toBe("2026-01-01 09:00:00")
		expect(payload.data.items).toHaveLength(1)
	})
})
