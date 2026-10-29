/**
 * @vitest-environment jsdom
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Route call() by API url so each test resolves, rejects or defers per
// endpoint; downloadXlsx is captured instead of writing a file.
const api = vi.hoisted(() => ({ calls: [], routes: {}, exports: [] }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, h } = await import("vue")
	return {
		Button: defineComponent({
			props: ["loading", "disabled", "variant", "size"],
			setup(props, { slots }) {
				return () =>
					h("button", { disabled: !!props.disabled }, slots.default?.())
			},
		}),
		FeatherIcon: defineComponent({
			props: ["name"],
			setup: () => () => h("i"),
		}),
		call: (url, params) => {
			api.calls.push({ url, params })
			const route = api.routes[url]
			return route
				? route(params)
				: Promise.reject(new Error(`no route for ${url}`))
		},
	}
})
vi.mock("@/utils/downloadXlsx", () => ({
	downloadXlsx: (...args) => {
		api.exports.push(args)
		return Promise.resolve()
	},
}))
vi.mock("@/composables/useToast", () => ({
	useToast: () => ({ showError: vi.fn() }),
}))
globalThis.__ = (message) => message

import ProductsView from "./ProductsView.vue"

const LIST = "pos_next.api.items.get_product_list"
const LEDGER = "pos_next.api.items.get_item_stock_ledger"

const PRODUCT = {
	item_code: "TEA",
	item_name: "Tea",
	stock_uom: "Nos",
	opening_qty: 4,
	in_qty: 6,
	out_qty: 2,
	closing_qty: 8,
	actual_qty: 999,
}

const ENTRY = {
	name: "SLE-1",
	posting_date: "2026-10-28",
	posting_datetime: "2026-10-28 09:00:00",
	voucher_type: "POS Invoice",
	voucher_no: "POS-INV-0001",
	warehouse: "Outlet - C",
	in_qty: 6,
	out_qty: 0,
	balance: 10,
}

const ENTRY_2 = {
	name: "SLE-2",
	posting_date: "2026-10-28",
	posting_datetime: "2026-10-28 11:00:00",
	voucher_type: "POS Invoice",
	voucher_no: "POS-INV-0002",
	warehouse: "Outlet - C",
	in_qty: 0,
	out_qty: 2,
	balance: 8,
}

// Local calendar dates, the same shape periodRange builds from "today".
const iso = (d) =>
	`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`
const day = (offset = 0) => {
	const d = new Date()
	d.setDate(d.getDate() + offset)
	return iso(d)
}

function deferred() {
	let resolve = () => {}
	let reject = () => {}
	const promise = new Promise((res, rej) => {
		resolve = res
		reject = rej
	})
	return { promise, resolve, reject }
}

function ledgerPayload(overrides = {}) {
	return {
		item_code: "TEA",
		from_date: day(0),
		to_date: day(0),
		opening_qty: 4,
		closing_qty: 8,
		entries: [ENTRY],
		has_more: false,
		...overrides,
	}
}

let wrappers = []
function mountView(props = {}) {
	const w = mount(ProductsView, {
		props: { posProfile: "POS-1", ...props },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	wrappers.push(w)
	return w
}

async function openLedger(w) {
	await w.find('[data-test="products-row"]').trigger("click")
	await flushPromises()
}

beforeEach(() => {
	vi.useFakeTimers({ toFake: ["Date"] })
	vi.setSystemTime(new Date(2026, 9, 28, 12))
	api.calls.length = 0
	api.exports.length = 0
	for (const key of Object.keys(api.routes)) delete api.routes[key]
	api.routes[LIST] = () => Promise.resolve([PRODUCT])
	api.routes[LEDGER] = () => Promise.resolve(ledgerPayload())
})

afterEach(() => {
	for (const w of wrappers) w.unmount()
	wrappers = []
	vi.useRealTimers()
})

describe("ProductsView", () => {
	it("loads today's balance window by default and renders its columns", async () => {
		const w = mountView()
		await flushPromises()

		expect(api.calls[0]).toMatchObject({
			url: LIST,
			params: {
				pos_profile: "POS-1",
				search: "",
				from_date: day(0),
				to_date: day(0),
				start: 0,
				limit: 50,
			},
		})
		const text = w.text()
		for (const header of [
			"Product",
			"UOM",
			"Opening Stock",
			"Stock In",
			"Stock Out",
			"Closing Balance",
		]) {
			expect(text).toContain(header)
		}
		expect(text).toContain("Tea")
		expect(text).not.toContain("Price")
		expect(w.find('[data-test="products-row"]').text()).not.toContain("999")
	})

	it("refetches the list and exports with the same selected period", async () => {
		const w = mountView()
		await flushPromises()

		await w.find('[data-test="products-period"]').setValue("last7")
		await flushPromises()
		const list = api.calls.filter((c) => c.url === LIST).at(-1)
		expect(list.params).toMatchObject({ from_date: day(-6), to_date: day(0) })

		await w.find('[data-test="products-search"]').setValue("tea")
		await w.find('[data-test="products-export"]').trigger("click")
		await flushPromises()
		expect(api.exports.at(-1)).toEqual([
			"pos_next.api.items.export_product_list",
			{
				pos_profile: "POS-1",
				search: "tea",
				from_date: day(-6),
				to_date: day(0),
			},
		])
	})

	it("opens the ledger inline with the same window and renders its entries", async () => {
		const w = mountView()
		await flushPromises()
		await openLedger(w)

		const ledger = api.calls.filter((c) => c.url === LEDGER).at(-1)
		expect(ledger.params).toMatchObject({
			item_code: "TEA",
			pos_profile: "POS-1",
			from_date: day(0),
			to_date: day(0),
			start: 0,
			limit: 50,
		})
		expect(w.find('[data-test="products-list"]').exists()).toBe(false)
		const rows = w.findAll('[data-test="ledger-row"]')
		expect(rows).toHaveLength(1)
		const text = rows[0].text()
		for (const value of [
			"2026-10-28",
			"POS-INV-0001",
			"POS Invoice",
			"6",
			"10",
		]) {
			expect(text).toContain(value)
		}
	})

	it("returns from the ledger with the search, period and scroll position intact", async () => {
		const w = mountView()
		await flushPromises()

		await w.find('[data-test="products-period"]').setValue("last7")
		await flushPromises()
		await w.find('[data-test="products-search"]').setValue("tea")
		w.find('[data-test="products-list"]').element.scrollTop = 120
		await openLedger(w)
		await w.find('[data-test="ledger-back"]').trigger("click")
		await flushPromises()

		expect(w.find('[data-test="products-search"]').element.value).toBe("tea")
		expect(w.find('[data-test="products-period"]').element.value).toBe("last7")
		expect(w.findAll('[data-test="products-row"]')).toHaveLength(1)
		expect(w.find('[data-test="products-list"]').element.scrollTop).toBe(120)
	})

	it("appends the next ledger page from the current offset", async () => {
		api.routes[LEDGER] = (params) =>
			Promise.resolve(
				ledgerPayload({
					entries: params.start ? [ENTRY_2] : [ENTRY],
					has_more: !params.start,
				}),
			)
		const w = mountView()
		await flushPromises()
		await openLedger(w)
		expect(w.findAll('[data-test="ledger-row"]')).toHaveLength(1)

		await w.find('[data-test="ledger-more"]').trigger("click")
		await flushPromises()

		expect(api.calls.filter((c) => c.url === LEDGER).at(-1).params.start).toBe(
			1,
		)
		expect(w.findAll('[data-test="ledger-row"]')).toHaveLength(2)
		expect(w.find('[data-test="ledger-more"]').exists()).toBe(false)
	})

	it("never lets a stale list response overwrite a newer one", async () => {
		const first = deferred()
		const second = deferred()
		let n = 0
		api.routes[LIST] = () => (++n === 1 ? first.promise : second.promise)

		const w = mountView()
		await flushPromises()
		await w.find('[data-test="products-period"]').setValue("last7")
		await flushPromises()

		second.resolve([{ ...PRODUCT, item_code: "NEW", item_name: "New tea" }])
		await flushPromises()
		first.resolve([{ ...PRODUCT, item_code: "OLD", item_name: "Old tea" }])
		await flushPromises()

		expect(w.text()).toContain("New tea")
		expect(w.text()).not.toContain("Old tea")
	})

	it("drops a ledger response that lands after Back", async () => {
		const pending = deferred()
		api.routes[LEDGER] = () => pending.promise

		const w = mountView()
		await flushPromises()
		await openLedger(w)
		await w.find('[data-test="ledger-back"]').trigger("click")
		await flushPromises()

		pending.reject({ messages: ["ledger exploded"] })
		await flushPromises()

		expect(w.find('[data-test="products-list"]').exists()).toBe(true)
		expect(w.find('[data-test="ledger-error"]').exists()).toBe(false)
		expect(w.text()).not.toContain("ledger exploded")
	})

	it("shows a list error with a working retry", async () => {
		let n = 0
		api.routes[LIST] = () =>
			++n === 1
				? Promise.reject({ messages: ["outlet offline"] })
				: Promise.resolve([PRODUCT])

		const w = mountView()
		await flushPromises()
		expect(w.find('[data-test="products-error"]').text()).toContain(
			"outlet offline",
		)

		await w.find('[data-test="products-error"] button').trigger("click")
		await flushPromises()

		expect(w.find('[data-test="products-error"]').exists()).toBe(false)
		expect(w.findAll('[data-test="products-row"]')).toHaveLength(1)
	})

	it("shows a ledger error with a working retry", async () => {
		let n = 0
		api.routes[LEDGER] = () =>
			++n === 1
				? Promise.reject({ messages: ["ledger offline"] })
				: Promise.resolve(ledgerPayload())

		const w = mountView()
		await flushPromises()
		await openLedger(w)
		expect(w.find('[data-test="ledger-error"]').text()).toContain(
			"ledger offline",
		)

		await w.find('[data-test="ledger-error"] button').trigger("click")
		await flushPromises()

		expect(w.find('[data-test="ledger-error"]').exists()).toBe(false)
		expect(w.findAll('[data-test="ledger-row"]')).toHaveLength(1)
	})

	it("shows the empty state when the outlet has no stock items", async () => {
		api.routes[LIST] = () => Promise.resolve([])

		const w = mountView()
		await flushPromises()

		expect(w.find('[data-test="products-empty"]').text()).toContain(
			"No stock items in this outlet.",
		)
	})

	it("shows an empty ledger for a period without movements", async () => {
		api.routes[LEDGER] = () => Promise.resolve(ledgerPayload({ entries: [] }))

		const w = mountView()
		await flushPromises()
		await openLedger(w)

		expect(w.find('[data-test="ledger-empty"]').text()).toContain(
			"No stock movements in this period.",
		)
	})

	it("clears the previous profile's rows and closes the ledger on profile change", async () => {
		api.routes[LIST] = (params) =>
			Promise.resolve([
				params.pos_profile === "POS-2"
					? { ...PRODUCT, item_code: "CHAI", item_name: "Chai" }
					: PRODUCT,
			])

		const w = mountView()
		await flushPromises()
		await openLedger(w)
		expect(w.find('[data-test="ledger-row"]').exists()).toBe(true)

		await w.setProps({ posProfile: "POS-2" })
		await flushPromises()

		expect(w.find('[data-test="products-list"]').exists()).toBe(true)
		expect(w.text()).toContain("Chai")
		expect(w.text()).not.toContain("Tea")
	})
})
