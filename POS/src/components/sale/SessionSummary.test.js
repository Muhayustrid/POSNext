/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, expect, it, vi } from "vitest"
import { flushPromises, mount } from "@vue/test-utils"

// Controllable createResource double: components register options, tests
// resolve/reject by mutating the reactive resource the component holds.
const resources = vi.hoisted(() => ({ instances: [] }))

vi.mock("frappe-ui", async () => {
	const { defineComponent, reactive } = await import("vue")
	const Button = defineComponent({
		name: "Button",
		emits: ["click"],
		template: `<button v-bind="$attrs" @click="$emit('click')"><slot /></button>`,
	})
	// renders the body so dialog-internal content (tabs, summary) mounts
	const Dialog = defineComponent({
		name: "Dialog",
		props: {
			modelValue: { type: Boolean, default: false },
			options: { type: Object, default: () => ({}) },
		},
		template: `<div><slot name="body" /></div>`,
	})
	const stub = defineComponent({ name: "FrappeUIStub", render: () => null })
	return {
		Button,
		Dialog,
		Input: stub,
		createResource: (opts) => {
			const reload = vi.fn(() => {
				r.loading = true
			})
			const r = reactive({ loading: false, data: null, error: null, reload })
			resources.instances.push({ url: opts.url, opts, resource: r })
			return r
		},
	}
})

vi.mock("@/composables/useFormatters", () => ({
	useFormatters: () => ({
		formatDate: (d) => `D:${d}`,
		formatTime: (t) => `T:${t}`,
		formatDateTime: (d) => `DT:${d}`,
	}),
}))

vi.mock("@/utils/currency", () => ({
	DEFAULT_CURRENCY: "IDR",
	formatCurrency: (amount, currency) => `${amount} ${currency}`,
}))

// DialogTitle needs a reka-ui DialogRoot context the stubbed Dialog lacks.
vi.mock("reka-ui", () => ({
	DialogTitle: { name: "DialogTitle", template: "<h2><slot /></h2>" },
}))

// The app installs __() as a global property; templates need it.
globalThis.__ = (message, replacements = []) => {
	if (!Array.isArray(replacements) || !replacements.length) return message
	let out = message
	for (const [i, v] of replacements.entries())
		out = out.split(`{${i}}`).join(String(v))
	return out
}

import InvoiceHistoryDialog from "./InvoiceHistoryDialog.vue"
import SessionSummary from "./SessionSummary.vue"

const SUMMARY = {
	opening_shift: "POS-SH-1",
	pos_profile: "Kasir 1",
	shift_name: "Shift Pagi",
	cashier: "Session",
	company_currency: "IDR",
	cash_mode_of_payment: "Cash",
	period_start_date: "2026-09-08 06:32:09",
	generated_at: "2026-09-08 09:10:36",
	status: "Open",
	closing_time: null,
	closing_source: null,
	counted_invoices: 4,
	invoice_count: 4,
	sales_count: 3,
	returns_count: 1,
	gross_sales: 52500,
	returns_total: 2000,
	net_sales: 50500,
	net_total: 50500,
	total_qty: 5,
	average_sale: 16833.333,
	credit_outstanding: 0,
	currency_breakdown: [{ currency: "IDR", amount: 50500 }],
	payments: [
		{ mode_of_payment: "Cash", amount: 40500, is_cash: true, configured: true },
		{
			mode_of_payment: "QRIS",
			amount: 10000,
			is_cash: false,
			configured: true,
		},
		{ mode_of_payment: "Debit", amount: 0, is_cash: false, configured: true },
		{
			mode_of_payment: "EWallet Lama",
			amount: 500,
			is_cash: false,
			configured: false,
		},
	],
	opening_cash: 100000,
	cash_collected: 40500,
	cash_in_hand: 140500,
	cash_expected: 140500,
	expense: null,
	expense_supported: false,
	total_cash: 40500,
	total_non_cash: 10500,
	methods_grand_total: 51000,
	items: [
		{ item_code: "I1", item_name: "Kopi", qty: 2, base_net_amount: 1000 },
	],
	packages: [
		{
			item_code: "P1",
			item_name: "Paket Hemat",
			qty: 1,
			base_net_amount: 50000,
		},
	],
	items_shown: 2,
	items_total_groups: 2,
	items_truncated: false,
	tax_total: 110,
	total_tax: 110,
	other_charges: [
		{
			account_head: "Service Charge - J",
			label: "Service Charge",
			amount: 50,
		},
	],
	other_charges_total: 50,
	categories: [
		{ category: "Makanan", qty: 1, base_net_amount: 40000 },
		{ category: "Minuman", qty: 4, base_net_amount: 10500 },
	],
	categories_shown: 2,
	categories_total_groups: 2,
	categories_truncated: false,
	item_discount: 0,
	total_discount: 1250,
}

function summaryResource() {
	const found = resources.instances.find(
		(i) => i.url === "pos_next.api.shifts.get_session_summary",
	)
	return found
}

function mountSummary(props = {}) {
	const wrapper = mount(SessionSummary, {
		props: { openingShift: "OS-1", ...props },
		global: { config: { globalProperties: { __: globalThis.__ } } },
	})
	return wrapper
}

async function mountWithSummary(overrides = {}) {
	resources.instances.length = 0
	const wrapper = mountSummary()
	const entry = summaryResource()
	entry.resource.data = { ...SUMMARY, ...overrides }
	entry.resource.loading = false
	await flushPromises()
	return { wrapper, entry }
}

beforeEach(() => {
	resources.instances.length = 0
})

describe("SessionSummary", () => {
	it("shows the loading state before data arrives", async () => {
		const wrapper = mountSummary()
		const entry = summaryResource()
		entry.resource.loading = true
		await flushPromises()

		expect(wrapper.text()).toContain("Loading session summary...")
	})

	it("registers the session summary endpoint with the shift", async () => {
		mountSummary({ openingShift: "OS-9" })
		const entry = summaryResource()
		expect(entry).toBeTruthy()
		expect(entry.opts.makeParams()).toEqual({ opening_shift: "OS-9" })
	})

	it("shows an error state with retry when there is no cached data", async () => {
		const wrapper = mountSummary()
		const entry = summaryResource()
		entry.resource.loading = false
		await flushPromises()

		expect(wrapper.text()).toContain("Could not load the session summary.")

		entry.resource.reload.mockClear()
		await wrapper.find("button").trigger("click")
		expect(entry.resource.reload).toHaveBeenCalled()
	})

	it("renders every report section in the required order", async () => {
		const { wrapper } = await mountWithSummary()
		const html = wrapper.html()

		const markers = [
			"Shift Pagi",
			"Cashier",
			"Sales Summary",
			"Cash Summary",
			"Payment Methods",
			"Charges & Discounts",
			"Refund",
			"Sales per Category",
			"Package Detail", // "Item & Package Detail" (html-escaped in html())
		]
		let last = -1
		for (const marker of markers) {
			const at = html.indexOf(marker)
			expect(at, `${marker} missing`).toBeGreaterThan(-1)
			expect(at, `${marker} out of order`).toBeGreaterThan(last)
			last = at
		}
	})

	it("renders the shift info: name, cashier, opening, closing", async () => {
		const { wrapper } = await mountWithSummary()
		// shift name sits in the header (frozen group, else profile)
		expect(wrapper.text()).toContain("Shift Pagi")
		const info = wrapper.find('[data-test="shift-info"]').text()
		expect(info).toContain("Session")
		expect(info).toContain("D:2026-09-08 06:32:09")
		// no closing time yet: explicit dash + note, never a guessed time
		expect(info).toContain("Not closed yet")

		// profile fallback when no group is frozen on the shift
		const noGroup = await mountWithSummary({ shift_name: null })
		expect(noGroup.wrapper.text()).toContain("Kasir 1")
	})

	it("marks the closing time as actual or estimate", async () => {
		const actual = await mountWithSummary({
			closing_time: "2026-09-08 17:00:00",
			closing_source: "actual",
		})
		expect(actual.wrapper.find('[data-test="shift-info"]').text()).toContain(
			"Actual",
		)
		expect(actual.wrapper.find('[data-test="shift-info"]').text()).toContain(
			"D:2026-09-08 17:00:00",
		)

		const estimate = await mountWithSummary({
			closing_time: "2026-09-08 22:00:00",
			closing_source: "estimate",
		})
		expect(estimate.wrapper.find('[data-test="shift-info"]').text()).toContain(
			"estimate",
		)
	})

	it("shows the sales summary with clear captions", async () => {
		const { wrapper } = await mountWithSummary()
		const sales = wrapper.find('[data-test="sales-summary"]').text()

		expect(sales).toContain("50500 IDR") // total sales (after returns)
		expect(sales).toContain("3") // total orders: sales-only count
		expect(sales).toContain("16833.333 IDR") // average per order
		expect(sales).toContain("After returns, tax included")
		expect(sales).toContain("Submitted sales invoices (returns not counted)")
		expect(sales).toContain("Total Sales ÷ Total Orders")
		expect(sales).toContain("52500 IDR") // gross sales secondary line
	})

	it("shows the cash summary with the honest expense placeholder", async () => {
		const { wrapper } = await mountWithSummary()
		const cash = wrapper.find('[data-test="cash-summary"]').text()

		expect(cash).toContain("100000 IDR") // opening balance
		expect(cash).toContain("40500 IDR") // cash receipts
		expect(cash).toContain("Not recorded") // expense: unsupported, not zero
		expect(cash).toContain("140500 IDR") // cash in hand
		expect(cash).toContain("Cash in Hand is the balance before any expenses")
	})

	it("shows a recorded expense when one exists", async () => {
		const { wrapper } = await mountWithSummary({ expense: 25000 })
		expect(wrapper.find('[data-test="cash-summary"]').text()).toContain(
			"25000 IDR",
		)
		expect(wrapper.find('[data-test="cash-summary"]').text()).not.toContain(
			"Not recorded",
		)
	})

	it("lists all configured payment methods (zero included) plus unconfigured ones", async () => {
		const { wrapper } = await mountWithSummary()
		const table = wrapper.find('[data-test="payments-table"]')
		const text = table.text()

		// every method appears, zero included
		expect(text).toContain("Cash")
		expect(text).toContain("QRIS")
		expect(text).toContain("Debit")
		expect(text).toContain("EWallet Lama")
		expect(text).toContain("Unused") // Debit at 0
		expect(text).toContain("Not on the POS Profile") // used, unconfigured
		expect(text).toContain("40500 IDR")
		expect(text).toContain("500 IDR")
		// totals
		expect(text).toContain("Total Cash")
		expect(text).toContain("40500 IDR")
		expect(text).toContain("Total Non-Cash")
		expect(text).toContain("10500 IDR")
		expect(text).toContain("Grand Total")
		expect(text).toContain("51000 IDR")
	})

	it("shows charges and discounts without fabricating a split", async () => {
		const { wrapper } = await mountWithSummary()
		const charges = wrapper.find('[data-test="charges-section"]').text()

		expect(charges).toContain("Service Charge") // listed under its real name
		expect(charges).toContain("50 IDR")
		expect(charges).toContain("Tax / PPN")
		expect(charges).toContain("110 IDR")
		expect(charges).toContain("Discount")
		expect(charges).toContain("1250 IDR")
	})

	it("shows the refund total with the return count", async () => {
		const { wrapper } = await mountWithSummary()
		const refund = wrapper.find('[data-test="refund-section"]').text()
		expect(refund).toContain("Total Refund")
		expect(refund).toContain("-2000 IDR")
		expect(refund).toContain("1 return invoices")
	})

	it("never renders a negative zero for refunds", async () => {
		const zero = await mountWithSummary({
			returns_total: 0,
			returns_count: 0,
		})
		const text = zero.wrapper.find('[data-test="refund-section"]').text()
		expect(text).toContain("0 IDR")
		expect(text).not.toContain("-0")
	})

	it("renders sales per category as the primary table", async () => {
		const { wrapper } = await mountWithSummary()
		const table = wrapper.find('[data-test="categories-table"]')
		expect(table.exists()).toBe(true)
		expect(table.text()).toContain("Makanan")
		expect(table.text()).toContain("Minuman")
		expect(table.text()).toContain("40000 IDR")

		const capped = await mountWithSummary({
			categories_truncated: true,
			categories_shown: 2,
			categories_total_groups: 30,
		})
		expect(capped.wrapper.text()).toContain(
			"Showing top 2 of 30 categories; session totals above cover everything.",
		)
	})

	it("labels uncategorised revenue explicitly", async () => {
		const { wrapper } = await mountWithSummary({
			categories: [{ category: null, qty: 1, base_net_amount: 900 }],
		})
		expect(wrapper.find('[data-test="categories-table"]').text()).toContain(
			"No category",
		)
	})

	it("keeps item and package detail inside a secondary collapsible", async () => {
		const { wrapper } = await mountWithSummary()
		const details = wrapper.find('[data-test="items-details"]')
		expect(details.exists()).toBe(true)
		expect(details.element.tagName.toLowerCase()).toBe("details")
		// still rendered in the DOM (closed by default)
		expect(details.text()).toContain("Paket Hemat")
		expect(details.find('[data-test="items-table"]').exists()).toBe(true)
	})

	it("shows item codes only to distinguish duplicate item names", async () => {
		const duplicates = await mountWithSummary({
			items: [
				{
					item_code: "I1",
					item_name: "Kopi Susu",
					qty: 2,
					base_net_amount: 1000,
				},
				{
					item_code: "I2",
					item_name: "Kopi Susu",
					qty: 1,
					base_net_amount: 2000,
				},
			],
		})
		const dupTable = duplicates.wrapper.find('[data-test="items-table"]')
		expect(dupTable.text()).toContain("I1")
		expect(dupTable.text()).toContain("I2")

		const unique = await mountWithSummary({
			items: [
				{ item_code: "I1", item_name: "Kopi", qty: 2, base_net_amount: 1000 },
				{ item_code: "I2", item_name: "Teh", qty: 1, base_net_amount: 2000 },
			],
		})
		const uniqueTable = unique.wrapper.find('[data-test="items-table"]')
		expect(uniqueTable.text()).toContain("Kopi")
		expect(uniqueTable.text()).not.toContain("I1")
	})

	it("discloses a capped item table", async () => {
		const { wrapper } = await mountWithSummary({
			items_truncated: true,
			items_shown: 2,
			items_total_groups: 120,
		})
		expect(wrapper.text()).toContain(
			"Showing top 2 of 120 items; the totals above cover the whole session.",
		)
	})

	it("keeps sections rendered with a notice when the session has no sales", async () => {
		const { wrapper } = await mountWithSummary({ counted_invoices: 0 })
		expect(wrapper.text()).toContain("No sales yet in this session.")
		// cash summary still matters (opening balance) — not hidden
		expect(wrapper.find('[data-test="cash-summary"]').exists()).toBe(true)
	})

	it("stacks the sales metrics on mobile and keeps tabular numbers", async () => {
		const { wrapper } = await mountWithSummary()
		const sales = wrapper.find('[data-test="sales-summary"]')
		const grid = sales.find(".grid")
		// base layout is a stacked single column (mobile 360px), 3-up on sm+
		expect(grid.classes()).toContain("grid-cols-1")
		expect(grid.classes()).toContain("sm:grid-cols-3")
		// shift info follows the same pattern
		const info = wrapper.find('[data-test="shift-info"]')
		expect(info.classes()).toContain("grid-cols-1")
		expect(info.classes()).toContain("sm:grid-cols-3")
		// numeric values use tabular numerals
		expect(sales.html()).toContain("tabular-nums")
	})

	it("keeps the last snapshot visible and flags it stale on refresh errors", async () => {
		const { wrapper, entry } = await mountWithSummary()
		expect(wrapper.text()).not.toContain(
			"Showing data from the last successful refresh.",
		)

		entry.opts.onError()
		await flushPromises()
		expect(wrapper.text()).toContain(
			"Showing data from the last successful refresh.",
		)
		// cached numbers still on screen — not fake-live
		expect(wrapper.text()).toContain("50500 IDR")
	})

	it("reloads on manual refresh and clears the stale flag", async () => {
		const { wrapper, entry } = await mountWithSummary()
		entry.opts.onError()
		await flushPromises()

		entry.resource.reload.mockClear()
		await wrapper.find('button[aria-label="Refresh"]').trigger("click")

		expect(entry.resource.reload).toHaveBeenCalledTimes(1)
		await flushPromises()
		expect(wrapper.text()).not.toContain(
			"Showing data from the last successful refresh.",
		)
	})

	it("shows the no-shift empty state without an opening shift", () => {
		resources.instances.length = 0
		const wrapper = mountSummary({ openingShift: "" })
		expect(wrapper.text()).toContain("No open shift for this session.")
		// no fetch attempted without a shift
		const entry = summaryResource()
		expect(entry.resource.reload).not.toHaveBeenCalled()
	})
})

describe("InvoiceHistoryDialog tabs", () => {
	function mountDialog(posOpeningShift = "OS-1") {
		return mount(InvoiceHistoryDialog, {
			props: { modelValue: true, posProfile: "juri1", posOpeningShift },
			global: {
				config: { globalProperties: { __: globalThis.__ } },
				stubs: { ReturnInvoiceDialog: true, Teleport: true },
			},
		})
	}

	it("defaults to the session summary tab when a shift is open", async () => {
		resources.instances.length = 0
		const wrapper = mountDialog()
		await flushPromises()

		expect(wrapper.find('[role="tablist"]').exists()).toBe(true)
		const urls = resources.instances.map((i) => i.url)
		expect(urls).toContain("pos_next.api.shifts.get_session_summary")
	})

	it("defaults to the transactions tab without an open shift", async () => {
		resources.instances.length = 0
		const wrapper = mountDialog("")
		await flushPromises()

		const urls = resources.instances.map((i) => i.url)
		expect(urls).not.toContain("pos_next.api.shifts.get_session_summary")
		expect(wrapper.text()).not.toContain("Sales Summary")
		expect(wrapper.text()).toContain("Load More")
	})

	it("uses a constrained dialog with fixed header tabs, close and a minimal footer", async () => {
		resources.instances.length = 0
		const wrapper = mountDialog()
		await flushPromises()

		const dialog = wrapper.findComponent({ name: "Dialog" })
		// constrained width (~1152px) instead of full-viewport stretch
		expect(dialog.props("options").size).toBe("6xl")

		// tabs live in the fixed header, body scrolls, footer only closes
		expect(
			wrapper.find('[data-test="dialog-header"] [role="tablist"]').exists(),
		).toBe(true)
		expect(
			wrapper
				.find('[data-test="dialog-header"] button[aria-label="Close"]')
				.exists(),
		).toBe(true)
		const body = wrapper.find('[data-test="dialog-body"]')
		expect(body.classes()).toContain("overflow-y-auto")
		expect(wrapper.find('[data-test="dialog-footer"]').text()).toContain(
			"Close",
		)
	})

	it("switches between tabs on click", async () => {
		resources.instances.length = 0
		const wrapper = mountDialog()
		await flushPromises()
		expect(wrapper.text()).not.toContain("Load More")

		const tabs = wrapper.findAll('[role="tab"]')
		await tabs[1].trigger("click")
		await flushPromises()
		// transactions tab: list controls visible, summary gone
		expect(wrapper.text()).toContain("Load More")
		expect(wrapper.text()).not.toContain("Sales Summary")
		expect(tabs[1].attributes("aria-selected")).toBe("true")
		expect(tabs[0].attributes("aria-selected")).toBe("false")

		await tabs[0].trigger("click")
		await flushPromises()
		expect(tabs[0].attributes("aria-selected")).toBe("true")
		// re-entering the tab remounts the summary and refetches fresh data
		const summaryInstances = resources.instances.filter(
			(i) => i.url === "pos_next.api.shifts.get_session_summary",
		)
		expect(summaryInstances.length).toBe(2)
		expect(wrapper.text()).toContain("Loading session summary...")
	})
})
